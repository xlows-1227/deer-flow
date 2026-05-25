import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";

export interface SandboxFileInfo {
  path: string;
  name: string;
  size: number;
  modified_at: number;
  source: "workspace" | "uploads" | "outputs" | "user-data" | string;
  extension?: string;
  mime_type?: string | null;
}

export interface SandboxFilesResponse {
  files: SandboxFileInfo[];
  count: number;
  truncated?: boolean;
}

async function readErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const error = await response.json().catch(() => ({ detail: fallback }));
  return error.detail ?? fallback;
}

export async function listSandboxFiles(
  threadId: string,
): Promise<SandboxFilesResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${threadId}/sandbox/files`,
  );

  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to list sandbox files"),
    );
  }

  return response.json();
}
