// ----------------------------------------------------------------------------
// File-library API
// ----------------------------------------------------------------------------
//
// Thin client over the backend `/api/files` router (see
// `backend/app/gateway/routers/files.py`). The library is the user-scoped
// document store used by the @-mention picker in the chat input and by the
// standalone `/workspace/files` management page.

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import type { UploadedFileInfo } from "@/core/uploads/api";

import type {
  FileItem,
  FileListResponse,
  UserFileItem,
  UserFileTypeFilter,
} from "./type";

export interface ListFilesParams {
  /** Folder path relative to the library root. `""` means the root. */
  folder_path?: string;
  /** `"uploaded" | "generated" | "all"` — mirrors the backend query param. */
  source?: "uploaded" | "generated" | "all";
  /** Substring filter on the file name (case-insensitive). */
  q?: string;
  /** Hard cap on returned items; the picker only needs the top matches. */
  limit?: number;
}

export async function listFiles(
  params: ListFilesParams = {},
): Promise<FileItem[]> {
  const { items } = await listUserFiles({
    folderPath: params.folder_path,
    source: params.source,
    q: params.q,
  });
  return items;
}

export interface ListUserFilesParams {
  folderPath?: string;
  /**
   * The standalone files page stores `source` as a raw `string` (it never
   * constrains the Select value beyond `""` defaults). We accept any string
   * and forward non-`"all"` values to the backend, which validates them.
   */
  source?: string;
  type?: UserFileTypeFilter;
  q?: string;
}

/**
 * Standalone-files-page variant of {@link listFiles}. Returns the raw
 * `FileListResponse` (items + total + folder_path) so the management UI
 * can show counts. Also includes `folder` items in the result so the
 * page can render folder rows.
 */
export async function listUserFiles(
  params: ListUserFilesParams = {},
): Promise<FileListResponse> {
  const search = new URLSearchParams();
  if (params.folderPath) {
    search.set("folder_path", params.folderPath);
  }
  if (params.source && params.source !== "all") {
    search.set("source", params.source);
  }
  if (params.type && params.type !== "all") {
    search.set("type", params.type);
  }
  if (params.q) {
    search.set("q", params.q);
  }

  const url = `${getBackendBaseURL()}/api/files${
    search.size > 0 ? `?${search.toString()}` : ""
  }`;
  const response = await fetch(url, { method: "GET" });

  if (!response.ok) {
    throw new Error(
      `Failed to list files: ${response.status} ${response.statusText}`,
    );
  }

  return (await response.json()) as FileListResponse;
}

export async function createUserFolder(
  name: string,
  parentPath = "",
): Promise<UserFileItem> {
  const response = await fetch(`${getBackendBaseURL()}/api/files/folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, parent_path: parentPath }),
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(
      detail.detail ?? `Failed to create folder: ${response.statusText}`,
    );
  }
  return (await response.json()) as UserFileItem;
}

export interface UserFileUploadConfig {
  max_upload_bytes: number;
  max_upload_label: string;
}

export async function listUserFolders(): Promise<string[]> {
  const response = await fetch(`${getBackendBaseURL()}/api/files/folders`, {
    method: "GET",
  });
  if (!response.ok) {
    throw new Error(
      `Failed to list folders: ${response.status} ${response.statusText}`,
    );
  }
  const data = (await response.json()) as { folders: string[] };
  return data.folders;
}

export async function getUserFileUploadConfig(): Promise<UserFileUploadConfig> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/files/upload-config`,
    { method: "GET" },
  );
  if (!response.ok) {
    throw new Error(
      `Failed to load upload configuration: ${response.status} ${response.statusText}`,
    );
  }
  return (await response.json()) as UserFileUploadConfig;
}

export async function deleteUserFile(path: string): Promise<void> {
  const url = `${getBackendBaseURL()}/api/files/${encodeURI(path).replace(
    /%2F/g,
    "/",
  )}`;
  const response = await fetch(url, { method: "DELETE" });
  if (!response.ok) {
    const detail = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(
      detail.detail ?? `Failed to delete file: ${response.statusText}`,
    );
  }
}

export async function uploadUserFiles(
  files: File[],
  folderPath = "",
): Promise<UserFileItem[]> {
  if (files.length === 0) {
    return [];
  }
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  formData.append("folder_path", folderPath);

  const response = await fetch(`${getBackendBaseURL()}/api/files/upload`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(
      detail.detail ?? `Failed to upload files: ${response.statusText}`,
    );
  }
  const data = (await response.json()) as FileListResponse;
  return data.items;
}

/**
 * Build a URL that points at a file in the user's library. Use this for
 * `<img src>` and `<a href>` so the path is encoded the same way the
 * backend expects.
 */
export function userFileUrl(path: string, download = false): string {
  const base = `${getBackendBaseURL()}/api/files/${encodeURI(path).replace(
    /%2F/g,
    "/",
  )}`;
  return download ? `${base}?download=true` : base;
}

/**
 * Build a download URL for a thread-uploaded file. Thread uploads live
 * under the per-thread sandbox uploads dir; the gateway doesn't expose
 * a public download route for them, so we point the file-management
 * page at the langgraph artifact URL (which is what the chat input
 * itself uses for inline previews).
 */
export function threadUploadDownloadUrl(
  threadId: string,
  filename: string,
): string {
  return `${getBackendBaseURL()}/api/threads/${threadId}/uploads/${encodeURIComponent(
    filename,
  )}?download=true`;
}

/**
 * Normalize a thread-upload record (returned by
 * `listUploadedFiles(threadId)`) into the canonical {@link FileItem}
 * shape so the file-management page can render library + thread uploads
 * side-by-side.
 *
 * The original `UploadedFileInfo` shape is documented in
 * `core/uploads/api.ts`; the bits we care about are `filename`,
 * `size` (string-encoded bytes), `path` (the sandbox virtual path),
 * `extension`, `modified` (epoch seconds), `artifact_url`.
 */
export function threadUploadToFileItem(
  file: UploadedFileInfo,
  threadId: string,
  threadTitle?: string,
): FileItem {
  const sizeNum =
    typeof file.size === "string" ? parseInt(file.size, 10) : file.size;
  const modifiedIso =
    typeof file.modified === "number"
      ? new Date(file.modified * 1000).toISOString()
      : new Date().toISOString();
  // Match the library convention: `extension` always carries a leading
  // dot (e.g. ".pdf"). When the upload record omits the extension, fall
  // back to the last dotted segment of the filename.
  const fallbackExt = file.filename.includes(".")
    ? `.${file.filename.split(".").pop() ?? ""}`
    : "";
  const ext = (file.extension ?? fallbackExt).toLowerCase();
  // The library uses POSIX-style relative paths; for a thread upload the
  // path is just the filename (each thread is its own scope).
  return {
    id: `thread:${threadId}:${file.filename}`,
    name: file.filename,
    path: file.filename,
    kind: "file",
    source: "uploaded",
    size: Number.isFinite(sizeNum) ? sizeNum : 0,
    mime_type: null,
    extension: ext,
    modified_at: modifiedIso,
    preview_url: file.artifact_url ?? null,
    download_url: file.artifact_url ?? null,
    source_thread_id: threadId,
    source_thread_title: threadTitle,
  };
}
