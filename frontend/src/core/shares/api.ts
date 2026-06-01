import { fetch as fetchWithAuth } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export interface CreateShareResponse {
  share_token: string;
  share_url: string;
  expires_at: string | null;
}

export interface SharedMessage {
  type: string;
  id: string | null;
  content: string;
  name?: string;
}

export interface SharedThreadResponse {
  thread_id: string;
  title: string | null;
  created_at: string | null;
  messages: SharedMessage[];
}

export async function createThreadShare(
  threadId: string,
): Promise<CreateShareResponse> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/share`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expires_in_days: 30 }),
    },
  );
  if (!response.ok) {
    throw new Error(`Failed to create share link: ${response.statusText}`);
  }
  return (await response.json()) as CreateShareResponse;
}

export async function fetchSharedThread(
  token: string,
): Promise<SharedThreadResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/share/${encodeURIComponent(token)}`,
  );
  if (!response.ok) {
    throw new Error(`Failed to load shared thread: ${response.statusText}`);
  }
  return (await response.json()) as SharedThreadResponse;
}
