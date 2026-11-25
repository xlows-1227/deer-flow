import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useSyncExternalStore } from "react";

import {
  fetchThreadModelSettings,
  updateThreadModelSettings,
  type ThreadModelSettings,
} from "../threads/api";
import { useThreadRuns } from "../threads/query";

import {
  DEFAULT_LOCAL_SETTINGS,
  applyThreadContextOverride,
  type LocalSettings,
  type ThreadContextSettings,
} from "./local";
import {
  copyThreadContext,
  getBaseSettingsSnapshot,
  getThreadContextSnapshot,
  subscribe,
  updateLocalSettings,
  updateThreadSettings,
  type LocalSettingsSetter,
} from "./store";

export function useLocalSettings(): [LocalSettings, LocalSettingsSetter] {
  const settings = useSyncExternalStore(
    subscribe,
    getBaseSettingsSnapshot,
    () => DEFAULT_LOCAL_SETTINGS,
  );

  const setSettings = useCallback<LocalSettingsSetter>((key, value) => {
    updateLocalSettings(key, value);
  }, []);

  return [settings, setSettings];
}

export function copyThreadSettings(
  sourceThreadId: string,
  targetThreadId: string,
) {
  copyThreadContext(sourceThreadId, targetThreadId);
}

/**
 * Resolve a thread's model selection from the thread record (source of truth).
 *
 * For threads created before the model selection was stored on the thread we
 * fall back to the effective model of the latest run. Per-thread localStorage
 * is only a draft for threads that do not exist on the backend yet, so an
 * existing conversation never renders a stale local value.
 */
export function useThreadSettings(
  threadId: string,
): [LocalSettings, LocalSettingsSetter] {
  const queryClient = useQueryClient();

  const baseSettings = useSyncExternalStore(
    subscribe,
    getBaseSettingsSnapshot,
    () => DEFAULT_LOCAL_SETTINGS,
  );

  const draftContext = useSyncExternalStore(
    subscribe,
    () => getThreadContextSnapshot(threadId),
    () => undefined,
  );

  const threadQuery = useQuery({
    queryKey: ["thread-settings", threadId],
    queryFn: () => fetchThreadModelSettings(threadId),
    enabled: Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  const hasThreadModel = Boolean(
    threadQuery.data?.model_name ??
      threadQuery.data?.mode ??
      threadQuery.data?.reasoning_effort,
  );

  const isExistingThread = threadQuery.isSuccess && threadQuery.data !== null;

  // Legacy threads (created before model metadata existed) have no selection
  // on the thread record: use the latest run's effective model.
  const runsQuery = useThreadRuns(
    isExistingThread && !hasThreadModel ? threadId : undefined,
  );

  const remoteContext = useMemo<ThreadContextSettings | undefined>(() => {
    if (!isExistingThread) {
      return undefined;
    }
    if (hasThreadModel) {
      return { ...threadQuery.data };
    }
    const latestModelName = (runsQuery.data ?? [])
      .map((run) => (run as { model_name?: unknown }).model_name)
      .find((name): name is string => typeof name === "string" && name.length > 0);
    return latestModelName ? { model_name: latestModelName } : undefined;
  }, [hasThreadModel, isExistingThread, runsQuery.data, threadQuery.data]);

  // Until the thread record resolves, a brand-new chat keeps using its draft.
  // For existing threads the record (or run fallback) wins over localStorage.
  const effectiveThreadContext = isExistingThread ? remoteContext : draftContext;

  const settings = useMemo(
    () => applyThreadContextOverride(baseSettings, effectiveThreadContext),
    [baseSettings, effectiveThreadContext],
  );

  const setSettings = useCallback<LocalSettingsSetter>(
    (key, value) => {
      // Keep the local draft so a not-yet-created thread still has a value to
      // send along with its create request.
      updateThreadSettings(threadId, key, value);

      if (key !== "context" || !isExistingThread) {
        return;
      }

      const next = value as {
        model_name?: string;
        mode?: ThreadModelSettings["mode"];
        reasoning_effort?: ThreadModelSettings["reasoning_effort"];
      };
      const metadata: ThreadModelSettings = {};
      if (next.model_name) {
        metadata.model_name = next.model_name;
      }
      if (next.mode) {
        metadata.mode = next.mode;
      }
      // Mirror the per-mode default used when submitting a run so the stored
      // effort always matches what the next run will actually execute with.
      const effectiveEffort = next.mode
        ? next.mode === "flash"
          ? undefined
          : (next.reasoning_effort ??
            (next.mode === "ultra" ? "high" : "low"))
        : next.reasoning_effort;
      if (effectiveEffort) {
        metadata.reasoning_effort = effectiveEffort;
      }
      if (!Object.keys(metadata).length) {
        return;
      }

      queryClient.setQueryData<ThreadModelSettings | null>(
        ["thread-settings", threadId],
        (previous) => ({ ...(previous ?? {}), ...metadata }),
      );
      // Fire-and-forget: the UI is already updated optimistically.
      void updateThreadModelSettings(threadId, metadata).catch(
        () => undefined,
      );
    },
    [isExistingThread, queryClient, threadId],
  );

  return [settings, setSettings];
}
