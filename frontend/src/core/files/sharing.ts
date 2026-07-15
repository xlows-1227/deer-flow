import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { FileItem } from "./type";

// Scripts and forms make self-contained generated HTML interactive. Omitting
// `allow-same-origin` keeps shared content in an opaque origin, preventing it
// from reading the app's cookies, storage, or parent DOM.
export const SHARED_HTML_IFRAME_SANDBOX = "allow-scripts allow-forms";

export type FileShareSourceType =
  | "library"
  | "conversation_upload"
  | "conversation_generated";

export interface SharedFileRecord {
  id: string;
  name: string;
  size: number;
  mime_type: string | null;
  extension: string;
  modified_at: string;
  shared_at: string;
  owner_email: string;
  source_type: FileShareSourceType;
  preview_url: string;
  download_url: string;
}

interface SharedFileListResponse {
  items: SharedFileRecord[];
  total: number;
}

function apiUrl(path: string): string {
  return `${getBackendBaseURL()}${path}`;
}

async function responseError(response: Response, fallback: string) {
  const payload = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return new Error(payload?.detail ?? fallback);
}

export function sharedFileToFileItem(record: SharedFileRecord): FileItem {
  return {
    id: `share:${record.id}`,
    name: record.name,
    path: `@shared/${record.id}`,
    kind: "file",
    source:
      record.source_type === "conversation_generated"
        ? "generated"
        : "uploaded",
    size: record.size,
    mime_type: record.mime_type,
    extension: record.extension,
    modified_at: record.modified_at,
    preview_url: record.preview_url,
    download_url: record.download_url,
    shared_file_id: record.id,
    shared_by_email: record.owner_email,
    shared_at: record.shared_at,
  };
}

export async function listReceivedFileShares(): Promise<FileItem[]> {
  const response = await fetch(apiUrl("/api/file-shares"), { method: "GET" });
  if (!response.ok) {
    throw await responseError(response, "加载他人分享的文件失败");
  }
  const payload = (await response.json()) as SharedFileListResponse;
  return payload.items.map(sharedFileToFileItem);
}

export async function shareFileWithUser(
  item: FileItem,
  recipientEmail: string,
): Promise<SharedFileRecord> {
  if (item.kind !== "file" || item.shared_file_id || item.system_folder) {
    throw new Error("只能分享自己的文件");
  }
  const sourceType: FileShareSourceType =
    item.conversation_source === "uploaded"
      ? "conversation_upload"
      : item.conversation_source === "generated"
        ? "conversation_generated"
        : "library";
  const response = await fetch(apiUrl("/api/file-shares"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      recipient_email: recipientEmail.trim(),
      source_type: sourceType,
      path: item.path,
      thread_id: item.source_thread_id ?? null,
    }),
  });
  if (!response.ok) {
    throw await responseError(response, "分享文件失败");
  }
  return (await response.json()) as SharedFileRecord;
}

export async function loadSharedFileText(shareId: string): Promise<string> {
  const response = await fetch(
    apiUrl(`/api/file-shares/${encodeURIComponent(shareId)}/content`),
    { method: "GET" },
  );
  if (!response.ok) {
    throw await responseError(response, "加载分享文件失败");
  }
  return response.text();
}
