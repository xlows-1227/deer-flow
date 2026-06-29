import { getFileName } from "@/core/utils/files";

import { urlOfArtifact } from "./utils";

function parseFilenameFromContentDisposition(
  header: string | null,
): string | null {
  if (!header) return null;

  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }

  const quotedMatch = /filename="([^"]+)"/i.exec(header);
  if (quotedMatch?.[1]) return quotedMatch[1];

  const simpleMatch = /filename=([^;]+)/i.exec(header);
  if (simpleMatch?.[1]) return simpleMatch[1].trim();

  return null;
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(objectUrl);
}

export async function downloadArtifactFile({
  filepath,
  threadId,
  isMock,
}: {
  filepath: string;
  threadId: string;
  isMock?: boolean;
}): Promise<void> {
  const url = urlOfArtifact({ filepath, threadId, download: true, isMock });
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) {
    throw new Error(`Download failed (${response.status})`);
  }

  const blob = await response.blob();
  const filename =
    parseFilenameFromContentDisposition(
      response.headers.get("Content-Disposition"),
    ) ?? getFileName(filepath);
  triggerBlobDownload(blob, filename);
}
