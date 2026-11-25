import { getAPIClient } from "@/core/api";
import { fetch as fetchWithAuth, getCsrfHeaders } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { ThreadTokenUsageResponse } from "./types";
import { THREAD_SOURCE_SKILL_SESSION } from "./utils";

/** Model selection persisted on the thread record (source of truth, not localStorage). */
export interface ThreadModelSettings {
  model_name?: string;
  mode?: "flash" | "thinking" | "pro" | "ultra";
  reasoning_effort?: "minimal" | "low" | "medium" | "high";
}

function pickThreadModelSettings(
  metadata: Record<string, unknown>,
): ThreadModelSettings {
  const settings: ThreadModelSettings = {};
  if (typeof metadata.model_name === "string") {
    settings.model_name = metadata.model_name;
  }
  if (
    metadata.mode === "flash" ||
    metadata.mode === "thinking" ||
    metadata.mode === "pro" ||
    metadata.mode === "ultra"
  ) {
    settings.mode = metadata.mode;
  }
  if (
    metadata.reasoning_effort === "minimal" ||
    metadata.reasoning_effort === "low" ||
    metadata.reasoning_effort === "medium" ||
    metadata.reasoning_effort === "high"
  ) {
    settings.reasoning_effort = metadata.reasoning_effort;
  }
  return settings;
}

/**
 * Read the model selection stored on a thread.
 * Returns ``null`` when the thread does not exist (e.g. a brand-new chat).
 */
export async function fetchThreadModelSettings(
  threadId: string,
): Promise<ThreadModelSettings | null> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
    { method: "GET" },
  );

  if (!response.ok) {
    if (response.status === 403 || response.status === 404) {
      return null;
    }
    throw new Error("Failed to load thread settings.");
  }

  const json = (await response.json()) as { metadata?: Record<string, unknown> };
  return pickThreadModelSettings(json.metadata ?? {});
}

/** Persist the user's model selection on the thread record. */
export async function updateThreadModelSettings(
  threadId: string,
  settings: ThreadModelSettings,
): Promise<void> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...getCsrfHeaders(),
      },
      body: JSON.stringify({ metadata: settings }),
    },
  );

  if (!response.ok) {
    throw new Error("Failed to update thread settings.");
  }
}

export async function ensureSkillSessionThreadMetadata(threadId: string) {
  try {
    await getAPIClient().threads.update(threadId, {
      metadata: { source: THREAD_SOURCE_SKILL_SESSION },
    });
  } catch {
    // Non-fatal: thread may not exist yet.
  }
}

export async function fetchThreadTokenUsage(
  threadId: string,
): Promise<ThreadTokenUsageResponse | null> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/token-usage`,
    {
      method: "GET",
    },
  );

  if (!response.ok) {
    if (response.status === 403 || response.status === 404) {
      return null;
    }
    throw new Error("Failed to load thread token usage.");
  }

  return (await response.json()) as ThreadTokenUsageResponse;
}
