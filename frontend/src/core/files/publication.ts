import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { FileItem } from "./type";

export interface FilePublicationRecord {
  id: string;
  name: string;
  thread_id: string;
  path: string;
  public_token: string;
  public_url: string;
  created_at: string;
}

interface FilePublicationListResponse {
  items: FilePublicationRecord[];
  total: number;
}

interface PublicFileMetadata {
  name: string;
  content_url: string;
}

export interface PublishedHtml {
  name: string;
  html: string;
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

function normalizedExtension(item: FileItem): string {
  const extension = item.extension.trim().toLowerCase();
  if (!extension) return "";
  return extension.startsWith(".") ? extension : `.${extension}`;
}

export function isPublishableGeneratedHtml(item: FileItem): boolean {
  return (
    item.kind === "file" &&
    item.conversation_source === "generated" &&
    !!item.source_thread_id &&
    [".html", ".htm"].includes(normalizedExtension(item))
  );
}

export async function publishGeneratedHtml(
  item: FileItem,
): Promise<FilePublicationRecord> {
  if (!isPublishableGeneratedHtml(item)) {
    throw new Error("只能发布对话生成的 HTML 文件");
  }
  const response = await fetch(apiUrl("/api/file-publications"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      thread_id: item.source_thread_id,
      path: item.path,
    }),
  });
  if (!response.ok) {
    throw await responseError(response, "发布外链失败");
  }
  return (await response.json()) as FilePublicationRecord;
}

export async function listFilePublications(): Promise<FilePublicationRecord[]> {
  const response = await fetch(apiUrl("/api/file-publications"), {
    method: "GET",
  });
  if (!response.ok) {
    throw await responseError(response, "加载发布记录失败");
  }
  const payload = (await response.json()) as FilePublicationListResponse;
  return payload.items;
}

export async function cancelFilePublication(
  publicationId: string,
): Promise<void> {
  const response = await fetch(
    apiUrl(`/api/file-publications/${encodeURIComponent(publicationId)}`),
    { method: "DELETE" },
  );
  if (!response.ok) {
    throw await responseError(response, "取消发布失败");
  }
}

export async function loadPublishedHtml(token: string): Promise<PublishedHtml> {
  const metadataResponse = await globalThis.fetch(
    apiUrl(`/api/public-files/${encodeURIComponent(token)}`),
    { cache: "no-store" },
  );
  if (!metadataResponse.ok) {
    throw new Error("发布页面不存在或已停止发布");
  }
  const metadata = (await metadataResponse.json()) as PublicFileMetadata;
  const contentResponse = await globalThis.fetch(apiUrl(metadata.content_url), {
    cache: "no-store",
  });
  if (!contentResponse.ok) {
    throw new Error("发布页面不存在或已停止发布");
  }
  return {
    name: metadata.name,
    html: await contentResponse.text(),
  };
}
