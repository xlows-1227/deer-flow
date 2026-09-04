import { fetch } from "../../api/fetcher";
import { getBackendBaseURL } from "../../config";
import type { RunMessage } from "../types";

export const RUN_MESSAGES_PAGE_SIZE = 200;

export type RunMessagesResponse = {
  data?: RunMessage[];
  has_more?: boolean;
  hasMore?: boolean;
};

/**
 * Fetch all messages for a specific run, paginating backwards through
 * `before_seq` until all pages are exhausted.
 *
 * Pages are collected in reverse order (newest→oldest) then `unshift`ed
 * so the final array is oldest→newest, matching run-internal seq order.
 */
export async function fetchRunMessages(
  threadId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<RunMessage[]> {
  const pages: RunMessage[][] = [];
  let beforeSeq: number | null = null;

  for (let page = 0; page < 1000; page += 1) {
    const params = new URLSearchParams({
      limit: String(RUN_MESSAGES_PAGE_SIZE),
    });
    if (beforeSeq !== null) {
      params.set("before_seq", String(beforeSeq));
    }

    const response = await fetch(
      `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages?${params.toString()}`,
      {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        signal,
      },
    );

    if (!response.ok) {
      throw new Error("Failed to load run messages.");
    }

    const result = (await response.json()) as RunMessagesResponse;
    const data = Array.isArray(result.data) ? result.data : [];
    if (data.length === 0) {
      break;
    }

    pages.unshift(data);

    const hasMore = result.has_more ?? result.hasMore ?? false;
    const firstSeq = data[0]?.seq;
    if (!hasMore || typeof firstSeq !== "number" || firstSeq === beforeSeq) {
      break;
    }
    beforeSeq = firstSeq;
  }

  return pages.flat();
}
