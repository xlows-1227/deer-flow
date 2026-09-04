import type { Run } from "@langchain/langgraph-sdk";
import { useQuery } from "@tanstack/react-query";

import { fetch } from "../../api/fetcher";
import { getBackendBaseURL } from "../../config";

export const THREAD_RUNS_PAGE_SIZE = 100;

/**
 * Fetch the list of runs for a thread, paginating through all pages.
 *
 * Runs are returned newest-first by the backend (created_at DESC).
 */
export function useThreadRuns(threadId?: string) {
  return useQuery<Run[]>({
    queryKey: ["thread", threadId],
    queryFn: async () => {
      if (!threadId) {
        return [];
      }
      const runs: Run[] = [];
      let offset = 0;

      while (true) {
        const params = new URLSearchParams({
          limit: String(THREAD_RUNS_PAGE_SIZE),
          offset: String(offset),
        });
        const response = await fetch(
          `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/runs?${params.toString()}`,
          {
            method: "GET",
            headers: {
              "Content-Type": "application/json",
            },
            credentials: "include",
          },
        );

        if (!response.ok) {
          // Test / mixed deployments may not expose runs for all threads; avoid
          // noisy retries + console errors for missing resources.
          if (response.status === 403 || response.status === 404) {
            return [];
          }
          throw new Error("Failed to load thread runs.");
        }

        const page = (await response.json()) as Run[];
        runs.push(...page);

        if (page.length < THREAD_RUNS_PAGE_SIZE) {
          break;
        }
        offset += page.length;
      }

      return runs;
    },
    refetchOnWindowFocus: false,
    retry: false,
  });
}
