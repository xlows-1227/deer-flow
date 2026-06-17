import { getAPIClient } from "@/core/api";
import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { ThreadTokenUsageResponse } from "./types";
import { THREAD_SOURCE_SKILL_SESSION } from "./utils";

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
