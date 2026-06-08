import type { Message } from "@langchain/langgraph-sdk";

import { loadArtifactContent } from "@/core/artifacts/loader";
import {
  buildWriteFileDraftContent,
  isWriteFileArtifact,
} from "@/core/artifacts/preview";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { extractPresentFilesFromMessage } from "@/core/messages/utils";
import { listSandboxFiles, type SandboxFileInfo } from "@/core/sandbox/api";
import { getFileName } from "@/core/utils/files";

import {
  addLocalFile,
  type SkillLocalBinaryFile,
  type SkillLocalDraft,
} from "./skill-local-draft";

const TEXT_FILE_PATTERN =
  /\.(md|txt|json|ya?ml|sh|py|ts|tsx|js|css|html|csv|xml)$/i;

const BINARY_FILE_PATTERN =
  /\.(skill|zip|png|jpe?g|gif|webp|svg|pdf|docx?|xlsx?|pptx?)$/i;

const IMPORT_SOURCE_PREFIXES = ["outputs/", "workspace/"] as const;

export function sandboxPathToWorkspacePath(sandboxPath: string): string {
  let path = sandboxPath.replace(/\\/g, "/").replace(/^\/+/, "");
  path = path.replace(/^mnt\/user-data\//, "");
  for (const prefix of IMPORT_SOURCE_PREFIXES) {
    if (path.startsWith(prefix)) {
      path = path.slice(prefix.length);
      break;
    }
  }
  if (path.startsWith("uploads/")) {
    return "";
  }
  if (!path) return "";
  if (path.startsWith("skills/")) return path;
  return `skills/${path}`;
}

function shouldImportSandboxFile(file: SandboxFileInfo) {
  if (file.source === "uploads") return false;
  if (file.source === "outputs" || file.source === "workspace") return true;
  const short = file.path.replace(/^\/?mnt\/user-data\//, "");
  return IMPORT_SOURCE_PREFIXES.some((prefix) => short.startsWith(prefix));
}

function normalizeSandboxPath(path: string) {
  const normalized = path.replace(/\\/g, "/");
  return normalized.startsWith("/") ? normalized : `/${normalized}`;
}

function parseSandboxPathFromWriteArtifact(artifact: string): string | null {
  if (!isWriteFileArtifact(artifact)) return null;
  try {
    const url = new URL(artifact);
    return normalizeSandboxPath(decodeURIComponent(url.pathname));
  } catch {
    return null;
  }
}

function isBinaryArtifactPath(path: string) {
  const name = getFileName(path).toLowerCase();
  return BINARY_FILE_PATTERN.test(name);
}

function isTextArtifactPath(path: string) {
  if (isBinaryArtifactPath(path)) return false;
  const name = getFileName(path);
  return TEXT_FILE_PATTERN.test(name);
}

export function extractFileToolPathsFromMessages(messages: Message[]) {
  const paths = new Set<string>();
  for (const message of messages) {
    if (message.type !== "ai") continue;
    for (const toolCall of message.tool_calls ?? []) {
      if (toolCall.name !== "write_file" && toolCall.name !== "str_replace") {
        continue;
      }
      const path = toolCall.args?.path;
      if (typeof path === "string" && path.trim()) {
        paths.add(path.trim());
      }
    }
  }
  return [...paths];
}

export function collectThreadOutputSandboxPaths({
  messages,
  artifacts,
  sandboxFiles,
}: {
  messages: Message[];
  artifacts: string[];
  sandboxFiles: SandboxFileInfo[];
}) {
  const paths = new Set<string>();

  for (const filepath of extractFileToolPathsFromMessages(messages)) {
    paths.add(filepath);
  }

  for (const message of messages) {
    for (const filepath of extractPresentFilesFromMessage(message)) {
      paths.add(filepath);
    }
  }

  for (const artifact of artifacts) {
    if (isWriteFileArtifact(artifact)) {
      const sandboxPath = parseSandboxPathFromWriteArtifact(artifact);
      if (sandboxPath) paths.add(sandboxPath);
      continue;
    }
    paths.add(artifact);
  }

  for (const file of sandboxFiles) {
    if (shouldImportSandboxFile(file)) {
      paths.add(file.path);
    }
  }

  return [...paths];
}

async function fetchArtifactBinaryFile(
  sandboxPath: string,
  threadId: string,
  workspacePath: string,
): Promise<SkillLocalBinaryFile | null> {
  try {
    const response = await fetch(
      urlOfArtifact({ filepath: sandboxPath, threadId, download: true }),
    );
    if (!response.ok) return null;
    const blob = await response.blob();
    const name = getFileName(workspacePath) || getFileName(sandboxPath);
    return {
      path: workspacePath,
      file: new File([blob], name, {
        type: blob.type || "application/octet-stream",
      }),
    };
  } catch {
    return null;
  }
}

async function resolveTextContent({
  sandboxPath,
  threadId,
  messages,
  artifacts,
}: {
  sandboxPath: string;
  threadId: string;
  messages: Message[];
  artifacts: string[];
}): Promise<string | null> {
  for (const artifact of artifacts) {
    if (!isWriteFileArtifact(artifact)) continue;
    const artifactPath = parseSandboxPathFromWriteArtifact(artifact);
    if (
      artifactPath !== normalizeSandboxPath(sandboxPath) &&
      artifactPath !== sandboxPath
    ) {
      continue;
    }
    const draft = buildWriteFileDraftContent({ filepath: artifact, messages });
    if (draft !== undefined) return draft;
  }

  try {
    const { content } = await loadArtifactContent({
      filepath: sandboxPath,
      threadId,
    });
    return content;
  } catch {
    return null;
  }
}

export async function importThreadOutputsIntoDraft({
  threadId,
  messages,
  artifacts,
  draft,
  binaries,
  options = {},
}: {
  threadId: string;
  messages: Message[];
  artifacts: string[];
  draft: SkillLocalDraft;
  binaries: SkillLocalBinaryFile[];
  options?: { replaceExisting?: boolean; preservePaths?: Set<string> };
}): Promise<{
  draft: SkillLocalDraft;
  binaries: SkillLocalBinaryFile[];
  importedPaths: string[];
}> {
  const replaceExisting = options.replaceExisting ?? false;
  const preservePaths = options.preservePaths ?? new Set<string>();
  const sandboxResponse = await listSandboxFiles(threadId);
  const sandboxPaths = collectThreadOutputSandboxPaths({
    messages,
    artifacts,
    sandboxFiles: sandboxResponse.files,
  });

  let nextDraft = draft;
  const nextBinaries = new Map(binaries.map((entry) => [entry.path, entry]));
  const importedPaths: string[] = [];

  for (const sandboxPath of sandboxPaths) {
    const workspacePath = sandboxPathToWorkspacePath(sandboxPath);
    if (!workspacePath) continue;

    if (preservePaths.has(workspacePath)) continue;

    if (isBinaryArtifactPath(sandboxPath)) {
      if (!replaceExisting && nextBinaries.has(workspacePath)) continue;
      const binary = await fetchArtifactBinaryFile(
        sandboxPath,
        threadId,
        workspacePath,
      );
      if (!binary) continue;
      nextBinaries.set(workspacePath, binary);
      importedPaths.push(workspacePath);
      continue;
    }

    if (!isTextArtifactPath(sandboxPath)) continue;
    if (!replaceExisting && workspacePath in nextDraft.files) continue;

    const content = await resolveTextContent({
      sandboxPath,
      threadId,
      messages,
      artifacts,
    });
    if (content === null) continue;

    nextDraft = addLocalFile(nextDraft, workspacePath, content);
    importedPaths.push(workspacePath);
  }

  return {
    draft: nextDraft,
    binaries: [...nextBinaries.values()],
    importedPaths,
  };
}
