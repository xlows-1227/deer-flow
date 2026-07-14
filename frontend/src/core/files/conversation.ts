import { getBackendBaseURL } from "@/core/config";

import type {
  ConversationFileSource,
  FileItem,
  SystemFileFolder,
} from "./type";

export const CONVERSATION_SYSTEM_FOLDER_NAMES: Record<
  ConversationFileSource,
  string
> = {
  uploaded: "对话上传",
  generated: "对话生成",
};

export const CONVERSATION_SYSTEM_FOLDERS: readonly FileItem[] = (
  Object.entries(CONVERSATION_SYSTEM_FOLDER_NAMES) as [
    ConversationFileSource,
    string,
  ][]
).map(([source, name]) => ({
  id: `system:conversation:${source}`,
  name,
  path: `@conversation/${source}`,
  kind: "folder",
  source,
  size: 0,
  mime_type: null,
  extension: "",
  modified_at: "1970-01-01T00:00:00.000Z",
  preview_url: null,
  download_url: null,
  system_folder: source,
}));

export const SHARED_SYSTEM_FOLDER: FileItem = {
  id: "system:shared",
  name: "他人分享",
  path: "@shared",
  kind: "folder",
  source: null,
  size: 0,
  mime_type: null,
  extension: "",
  modified_at: "1970-01-01T00:00:00.000Z",
  preview_url: null,
  download_url: null,
  system_folder: "shared",
};

export const SYSTEM_FOLDER_NAMES: Record<SystemFileFolder, string> = {
  ...CONVERSATION_SYSTEM_FOLDER_NAMES,
  shared: SHARED_SYSTEM_FOLDER.name,
};

export const SYSTEM_FOLDERS: readonly FileItem[] = [
  ...CONVERSATION_SYSTEM_FOLDERS,
  SHARED_SYSTEM_FOLDER,
];

const SYSTEM_FOLDER_PATH_ROOTS = new Set(["@conversation", "@shared"]);

/** Prevent writes to the synthetic paths used by locked system views. */
export function isReservedSystemFolderPath(path: string): boolean {
  const rootName = path.trim().replace(/\\/g, "/").split("/")[0];
  return SYSTEM_FOLDER_PATH_ROOTS.has(rootName ?? "");
}

/** @deprecated Use {@link isReservedSystemFolderPath}. */
export const isReservedConversationFolderPath = isReservedSystemFolderPath;

export function threadGeneratedFileUrl(
  threadId: string,
  artifactPath: string,
  download = false,
): string {
  const normalizedPath = artifactPath.startsWith("/")
    ? artifactPath
    : `/${artifactPath}`;
  const url = `${getBackendBaseURL()}/api/threads/${threadId}/artifacts${normalizedPath}`;
  return download ? `${url}?download=true` : url;
}

/** Normalize a generated thread artifact into the shared file-list shape. */
export function threadArtifactToFileItem(
  artifactPath: string,
  threadId: string,
  threadTitle?: string,
  modifiedAt?: string,
): FileItem {
  const normalizedPath = artifactPath.trim().replace(/\\/g, "/");
  const extractedName = normalizedPath.replace(/\/+$/, "").split("/").pop();
  const name = extractedName?.length ? extractedName : artifactPath;
  const dotIndex = name.lastIndexOf(".");
  const extension = dotIndex > 0 ? name.slice(dotIndex).toLowerCase() : "";
  const modifiedDate = modifiedAt ? new Date(modifiedAt) : new Date();
  const modifiedAtIso = Number.isNaN(modifiedDate.getTime())
    ? new Date().toISOString()
    : modifiedDate.toISOString();

  return {
    id: `thread:${threadId}:artifact:${normalizedPath}`,
    name,
    path: normalizedPath,
    kind: "file",
    source: "generated",
    size: 0,
    mime_type: null,
    extension,
    modified_at: modifiedAtIso,
    preview_url: threadGeneratedFileUrl(threadId, normalizedPath),
    download_url: threadGeneratedFileUrl(threadId, normalizedPath, true),
    source_thread_id: threadId,
    source_thread_title: threadTitle,
    conversation_source: "generated",
  };
}
