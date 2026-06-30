import { useCallback, useMemo, useSyncExternalStore } from "react";

import {
  DEFAULT_LOCAL_SETTINGS,
  applyThreadContextOverride,
  type LocalSettings,
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

export function useThreadSettings(
  threadId: string,
): [LocalSettings, LocalSettingsSetter] {
  const baseSettings = useSyncExternalStore(
    subscribe,
    getBaseSettingsSnapshot,
    () => DEFAULT_LOCAL_SETTINGS,
  );

  const threadContext = useSyncExternalStore(
    subscribe,
    () => getThreadContextSnapshot(threadId),
    () => undefined,
  );

  const settings = useMemo(
    () => applyThreadContextOverride(baseSettings, threadContext),
    [baseSettings, threadContext],
  );

  const setSettings = useCallback<LocalSettingsSetter>(
    (key, value) => {
      updateThreadSettings(threadId, key, value);
    },
    [threadId],
  );

  return [settings, setSettings];
}
