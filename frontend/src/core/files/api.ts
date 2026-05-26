import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { UserFileItem, UserFileListResponse } from "./types";

export interface ListUserFilesParams {
  folderPath?: string;
  source?: string;
  type?: string;
  q?: string;
}

async function readError(response: Response, fallback: string) {
  const data = await response.json().catch(() => null);
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  return fallback;
}

function apiPath(path: string) {
  return `${getBackendBaseURL()}${path}`;
}

function encodeFilePath(path: string) {
  return path.split("/").map(encodeURIComponent).join("/");
}

export function userFileUrl(path: string, download = false) {
  const suffix = download ? "?download=true" : "";
  return apiPath(`/api/files/${encodeFilePath(path)}${suffix}`);
}

export async function listUserFiles({
  folderPath = "",
  source = "all",
  type = "all",
  q = "",
}: ListUserFilesParams = {}): Promise<UserFileListResponse> {
  const params = new URLSearchParams({
    folder_path: folderPath,
    source,
    type,
    q,
  });
  const response = await fetch(apiPath(`/api/files?${params.toString()}`));
  if (!response.ok) {
    throw new Error(await readError(response, "加载文件失败"));
  }
  return response.json();
}

export async function createUserFolder(
  name: string,
  parentPath = "",
): Promise<UserFileItem> {
  const response = await fetch(apiPath("/api/files/folders"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, parent_path: parentPath }),
  });
  if (!response.ok) {
    throw new Error(await readError(response, "新建文件夹失败"));
  }
  return response.json();
}

export async function uploadUserFiles(
  files: File[],
  folderPath = "",
): Promise<UserFileListResponse> {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  formData.append("folder_path", folderPath);

  const response = await fetch(apiPath("/api/files/upload"), {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await readError(response, "上传文件失败"));
  }
  return response.json();
}

export async function deleteUserFile(path: string): Promise<void> {
  const response = await fetch(apiPath(`/api/files/${encodeFilePath(path)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await readError(response, "删除文件失败"));
  }
}

