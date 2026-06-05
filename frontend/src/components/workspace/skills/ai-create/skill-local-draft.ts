import type { SkillFileEntry } from "@/core/skills/type";

import { buildSkillFileTree, joinSkillRelativePath } from "./utils";
import type { SkillFileNode } from "./utils";

export interface SkillLocalDraft {
  skillName: string | null;
  directories: string[];
  files: Record<string, string>;
  displayName?: string;
  descriptionZh?: string;
}

export interface SkillLocalBinaryFile {
  path: string;
  file: File;
}

const DRAFT_STORAGE_PREFIX = "skill-ai-create-draft:";

export const SKILL_WORKSPACE_DIR = "skills";
export const SKILL_MD_WORKSPACE_PATH = "skills/SKILL.md";

const SERVER_SUPPORT_ROOTS = new Set([
  "skills",
  "references",
  "templates",
  "scripts",
  "assets",
]);

const BINARY_SKILL_FILE_PATTERN =
  /\.(skill|zip|png|jpe?g|gif|webp|svg|pdf|docx?|xlsx?|pptx?)$/i;

export function isReadableSkillServerFile(path: string) {
  const fileName = path.replace(/\\/g, "/").split("/").pop() ?? path;
  return !BINARY_SKILL_FILE_PATTERN.test(fileName);
}

export function createEmptyLocalDraft(): SkillLocalDraft {
  return {
    skillName: null,
    directories: [],
    files: {},
  };
}

export function loadLocalDraft(threadId: string): SkillLocalDraft | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(`${DRAFT_STORAGE_PREFIX}${threadId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as SkillLocalDraft;
    return {
      skillName: parsed.skillName ?? null,
      directories: Array.isArray(parsed.directories) ? parsed.directories : [],
      files: parsed.files ?? {},
      displayName: parsed.displayName ?? "",
      descriptionZh: parsed.descriptionZh ?? "",
    };
  } catch {
    return null;
  }
}

export function saveLocalDraft(threadId: string, draft: SkillLocalDraft) {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(
    `${DRAFT_STORAGE_PREFIX}${threadId}`,
    JSON.stringify(draft),
  );
}

export function deleteLocalDraft(threadId: string) {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(`${DRAFT_STORAGE_PREFIX}${threadId}`);
}

export function isLocalDraftEmpty(draft: SkillLocalDraft) {
  return (
    draft.directories.length === 0 && Object.keys(draft.files).length === 0
  );
}

function normalizeDraftPath(path: string) {
  return path.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
}

function isPathUnderPrefix(path: string, prefix: string) {
  const normalized = normalizeDraftPath(path);
  const normalizedPrefix = normalizeDraftPath(prefix);
  if (!normalizedPrefix) return false;
  return (
    normalized === normalizedPrefix ||
    normalized.startsWith(`${normalizedPrefix}/`)
  );
}

export function collectPathsUnderPrefix(
  draft: SkillLocalDraft,
  directoryPath: string,
  binaryPaths: string[] = [],
) {
  const prefix = normalizeDraftPath(directoryPath);
  const files = Object.keys(draft.files).filter((path) =>
    isPathUnderPrefix(path, prefix),
  );
  const directories = draft.directories.filter(
    (path) => path !== prefix && isPathUnderPrefix(path, prefix),
  );
  const binaries = binaryPaths.filter((path) =>
    isPathUnderPrefix(path, prefix),
  );
  return { files, directories, binaries };
}

export function countDirectoryEntries(
  draft: SkillLocalDraft,
  directoryPath: string,
  binaryPaths: string[] = [],
) {
  const { files, directories, binaries } = collectPathsUnderPrefix(
    draft,
    directoryPath,
    binaryPaths,
  );
  return files.length + directories.length + binaries.length;
}

export function removeLocalFile(
  draft: SkillLocalDraft,
  path: string,
): SkillLocalDraft {
  const normalized = normalizeDraftPath(path);
  if (!(normalized in draft.files)) {
    return draft;
  }
  const files = { ...draft.files };
  delete files[normalized];
  return { ...draft, files };
}

export function removeLocalDirectory(
  draft: SkillLocalDraft,
  directoryPath: string,
): SkillLocalDraft {
  const prefix = normalizeDraftPath(directoryPath);
  const files = Object.fromEntries(
    Object.entries(draft.files).filter(
      ([path]) => !isPathUnderPrefix(path, prefix),
    ),
  );
  const directories = draft.directories.filter(
    (path) => !isPathUnderPrefix(path, prefix),
  );
  return { ...draft, files, directories };
}

export function buildRenamedChildPath(currentPath: string, nextName: string) {
  const trimmed = nextName.trim().replace(/\\/g, "").replace(/\//g, "");
  if (!trimmed) return null;
  const normalized = normalizeDraftPath(currentPath);
  if (!normalized) return null;
  const parts = normalized.split("/");
  parts[parts.length - 1] = trimmed;
  return parts.join("/");
}

export function remapPathUnderPrefix(
  path: string,
  oldPrefix: string,
  newPrefix: string,
) {
  const normalized = normalizeDraftPath(path);
  const oldPath = normalizeDraftPath(oldPrefix);
  const newPath = normalizeDraftPath(newPrefix);
  if (normalized === oldPath) return newPath;
  if (isPathUnderPrefix(normalized, oldPath) && normalized !== oldPath) {
    return `${newPath}${normalized.slice(oldPath.length)}`;
  }
  return normalized;
}

export function draftPathExists(
  draft: SkillLocalDraft,
  path: string,
  binaryPaths: string[] = [],
) {
  const normalized = normalizeDraftPath(path);
  if (normalized in draft.files) return true;
  if (draft.directories.includes(normalized)) return true;
  return binaryPaths.includes(normalized);
}

export function renameLocalFilePath(
  draft: SkillLocalDraft,
  oldPath: string,
  newPath: string,
): SkillLocalDraft | null {
  const oldNormalized = normalizeDraftPath(oldPath);
  const newNormalized = normalizeDraftPath(newPath);
  if (oldNormalized === newNormalized) return draft;
  if (!(oldNormalized in draft.files) || newNormalized in draft.files) {
    return null;
  }

  const files = { ...draft.files };
  files[newNormalized] = files[oldNormalized] ?? "";
  delete files[oldNormalized];

  const next: SkillLocalDraft = { ...draft, files };
  ensureAncestorDirectories(next, newNormalized);
  return next;
}

export function renameLocalDirectoryPath(
  draft: SkillLocalDraft,
  oldPath: string,
  newPath: string,
): SkillLocalDraft | null {
  const oldNormalized = normalizeDraftPath(oldPath);
  const newNormalized = normalizeDraftPath(newPath);
  if (oldNormalized === newNormalized) return draft;
  if (!draft.directories.includes(oldNormalized)) return null;
  if (
    draft.directories.includes(newNormalized) ||
    newNormalized in draft.files
  ) {
    return null;
  }

  const files = Object.fromEntries(
    Object.entries(draft.files).map(([path, content]) => [
      remapPathUnderPrefix(path, oldNormalized, newNormalized),
      content,
    ]),
  );
  const directories = [
    ...new Set(
      draft.directories.map((path) =>
        remapPathUnderPrefix(path, oldNormalized, newNormalized),
      ),
    ),
  ];

  return { ...draft, files, directories };
}

function ensureAncestorDirectories(draft: SkillLocalDraft, path: string) {
  const parts = path.split("/");
  if (parts.length <= 1) return;
  for (let index = 1; index < parts.length; index += 1) {
    const dirPath = parts.slice(0, index).join("/");
    if (!draft.directories.includes(dirPath)) {
      draft.directories.push(dirPath);
    }
  }
}

export function addLocalFile(
  draft: SkillLocalDraft,
  path: string,
  content = "",
): SkillLocalDraft {
  const normalized = path.replace(/\\/g, "/").replace(/^\/+/, "");
  if (!normalized) return draft;
  ensureAncestorDirectories(draft, normalized);
  return {
    ...draft,
    files: { ...draft.files, [normalized]: content },
  };
}

export function addLocalDirectory(
  draft: SkillLocalDraft,
  path: string,
): SkillLocalDraft {
  const normalized = path.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!normalized) return draft;
  ensureAncestorDirectories(draft, normalized);
  const directories = draft.directories.includes(normalized)
    ? draft.directories
    : [...draft.directories, normalized];
  return { ...draft, directories };
}

export function updateLocalFileContent(
  draft: SkillLocalDraft,
  path: string,
  content: string,
): SkillLocalDraft {
  const normalized = path.replace(/\\/g, "/").replace(/^\/+/, "");
  return {
    ...draft,
    files: { ...draft.files, [normalized]: content },
  };
}

export function readLocalFileContent(draft: SkillLocalDraft, path: string) {
  const normalized = path.replace(/\\/g, "/").replace(/^\/+/, "");
  return draft.files[normalized] ?? "";
}

export function draftToFileEntries(
  draft: SkillLocalDraft,
  binaries: SkillLocalBinaryFile[] = [],
): SkillFileEntry[] {
  const entries: SkillFileEntry[] = [];
  const seen = new Set<string>();

  for (const directory of [...draft.directories].sort()) {
    if (seen.has(directory)) continue;
    seen.add(directory);
    entries.push({ path: directory, type: "directory", size: null });
  }

  for (const [path, content] of Object.entries(draft.files).sort(([a], [b]) =>
    a.localeCompare(b),
  )) {
    if (seen.has(path)) continue;
    seen.add(path);
    entries.push({ path, type: "file", size: content.length });
  }

  for (const binary of binaries) {
    if (seen.has(binary.path)) continue;
    seen.add(binary.path);
    entries.push({
      path: binary.path,
      type: "file",
      size: binary.file.size,
    });
  }

  return entries;
}

export function buildTreeFromLocalDraft(
  draft: SkillLocalDraft,
  binaries: SkillLocalBinaryFile[] = [],
): SkillFileNode[] {
  return buildSkillFileTree(draftToFileEntries(draft, binaries));
}

export function getCurrentDirectory(
  selectedPath: string | null,
  selectedType: "file" | "directory" | null,
) {
  if (!selectedPath) {
    return "skills";
  }
  if (selectedType === "directory") {
    return selectedPath;
  }
  if (!selectedPath.includes("/")) {
    return "skills";
  }
  return selectedPath.split("/").slice(0, -1).join("/");
}

export function createFileInDirectory(directory: string, fileName: string) {
  return joinSkillRelativePath(directory, fileName);
}

export function findWorkspaceSkillMdPath(
  draft: SkillLocalDraft,
): string | null {
  let nestedSkillMdPath: string | null = null;
  for (const path of Object.keys(draft.files)) {
    const normalized = path.replace(/\\/g, "/").toLowerCase();
    if (normalized === SKILL_MD_WORKSPACE_PATH.toLowerCase()) {
      return path;
    }
    if (
      normalized.startsWith(`${SKILL_WORKSPACE_DIR}/`) &&
      normalized.endsWith("/skill.md")
    ) {
      nestedSkillMdPath = path;
    }
  }
  if ("SKILL.md" in draft.files) {
    return "SKILL.md";
  }
  return nestedSkillMdPath;
}

export function serverPathToWorkspacePath(serverPath: string): string {
  const normalized = serverPath.replace(/\\/g, "/").replace(/^\/+/, "");
  if (normalized === "SKILL.md") {
    return SKILL_MD_WORKSPACE_PATH;
  }
  if (normalized.startsWith(`${SKILL_WORKSPACE_DIR}/`)) {
    return normalized;
  }
  return `${SKILL_WORKSPACE_DIR}/${normalized}`;
}

function resolveServerSupportRest(rest: string): string {
  if (!rest || rest.toLowerCase() === "skill.md") {
    return "SKILL.md";
  }
  const topLevel = rest.split("/")[0] ?? "";
  if (SERVER_SUPPORT_ROOTS.has(topLevel)) {
    return rest;
  }
  return `${SKILL_WORKSPACE_DIR}/${rest}`;
}

export function isValidServerSupportDirectory(path: string) {
  const normalized = path.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!normalized) return false;
  const topLevel = normalized.split("/")[0] ?? "";
  return SERVER_SUPPORT_ROOTS.has(topLevel);
}

export function workspaceDirectoryToServerPath(
  workspacePath: string,
  skillName: string,
): string | null {
  const normalized = workspacePath
    .replace(/\\/g, "/")
    .replace(/^\/+|\/+$/g, "");
  if (
    !normalized ||
    normalized === SKILL_WORKSPACE_DIR ||
    normalized === `${SKILL_WORKSPACE_DIR}/${skillName}`
  ) {
    return null;
  }
  const serverPath = workspacePathToServerPath(normalized, skillName);
  if (serverPath === "SKILL.md" || !isValidServerSupportDirectory(serverPath)) {
    return null;
  }
  return serverPath;
}

export function workspacePathToServerPath(
  workspacePath: string,
  skillName: string,
): string {
  const normalized = workspacePath.replace(/\\/g, "/");
  if (normalized === "SKILL.md") {
    return "SKILL.md";
  }

  const namedPrefix = `${SKILL_WORKSPACE_DIR}/${skillName}/`;
  let rest = normalized;
  if (rest.startsWith(namedPrefix)) {
    rest = rest.slice(namedPrefix.length);
    return resolveServerSupportRest(rest);
  }
  if (rest.startsWith(`${SKILL_WORKSPACE_DIR}/`)) {
    rest = rest.slice(`${SKILL_WORKSPACE_DIR}/`.length);
    if (rest === skillName) {
      rest = "";
    } else if (rest.startsWith(`${skillName}/`)) {
      rest = rest.slice(skillName.length + 1);
    }
  } else {
    return normalized;
  }

  return resolveServerSupportRest(rest);
}

export function reorganizeSkillsIntoNamedFolder(
  draft: SkillLocalDraft,
  skillName: string,
): SkillLocalDraft {
  const prefix = `${SKILL_WORKSPACE_DIR}/`;
  const targetPrefix = `${SKILL_WORKSPACE_DIR}/${skillName}/`;
  const previousSkillName = draft.skillName?.trim();
  const previousPrefix =
    previousSkillName && previousSkillName !== skillName
      ? `${SKILL_WORKSPACE_DIR}/${previousSkillName}/`
      : null;

  const movePath = (path: string) => {
    const normalized = path.replace(/\\/g, "/");
    if (!normalized.startsWith(prefix)) {
      return normalized;
    }
    if (normalized.startsWith(targetPrefix)) {
      return normalized;
    }
    if (previousPrefix && normalized.startsWith(previousPrefix)) {
      return targetPrefix + normalized.slice(previousPrefix.length);
    }
    const rest = normalized.slice(prefix.length);
    if (rest === skillName || rest.startsWith(`${skillName}/`)) {
      return normalized;
    }
    return targetPrefix + rest;
  };

  let next: SkillLocalDraft = {
    ...draft,
    skillName,
    directories: [],
    files: {},
  };

  for (const directory of draft.directories) {
    next = addLocalDirectory(next, movePath(directory));
  }
  next = addLocalDirectory(next, SKILL_WORKSPACE_DIR);
  next = addLocalDirectory(next, `${SKILL_WORKSPACE_DIR}/${skillName}`);

  for (const [path, content] of Object.entries(draft.files)) {
    next = addLocalFile(next, movePath(path), content);
  }

  return next;
}

export function convertWorkspaceDraftToServerDraft(
  draft: SkillLocalDraft,
  skillName: string,
): SkillLocalDraft {
  const files: Record<string, string> = {};
  const directories = new Set<string>();

  const trackDirectory = (serverPath: string) => {
    const parts = serverPath.split("/");
    if (parts.length <= 1) {
      return;
    }
    for (let index = 1; index < parts.length; index += 1) {
      const directory = parts.slice(0, index).join("/");
      if (isValidServerSupportDirectory(directory)) {
        directories.add(directory);
      }
    }
  };

  for (const directory of draft.directories) {
    const serverDirectory = workspaceDirectoryToServerPath(
      directory,
      skillName,
    );
    if (serverDirectory) {
      directories.add(serverDirectory);
    }
  }

  for (const [path, content] of Object.entries(draft.files)) {
    const normalized = path.replace(/\\/g, "/");
    if (
      !normalized.startsWith(`${SKILL_WORKSPACE_DIR}/`) &&
      normalized !== "SKILL.md"
    ) {
      continue;
    }
    const serverPath = workspacePathToServerPath(normalized, skillName);
    files[serverPath] = content;
    trackDirectory(serverPath);
  }

  return {
    skillName,
    directories: [...directories].sort(
      (a, b) => a.split("/").length - b.split("/").length,
    ),
    files,
    displayName: draft.displayName,
    descriptionZh: draft.descriptionZh,
  };
}

export function convertWorkspaceBinariesToServer(
  binaries: SkillLocalBinaryFile[],
  skillName: string,
): SkillLocalBinaryFile[] {
  return binaries
    .map((entry) => ({
      ...entry,
      path: workspacePathToServerPath(entry.path, skillName),
    }))
    .filter((entry) => entry.path !== "SKILL.md");
}

export async function publishLocalDraft(
  skillName: string,
  draft: SkillLocalDraft,
  binaries: SkillLocalBinaryFile[],
  writers: {
    writeFile: (path: string, content: string) => Promise<void>;
    writeSkillMd: (content: string) => Promise<void>;
    createDirectory: (path: string) => Promise<void>;
    uploadFiles: (entries: { path: string; file: File }[]) => Promise<void>;
  },
) {
  const directories = [...draft.directories].sort(
    (a, b) => a.split("/").length - b.split("/").length,
  );
  for (const directory of directories) {
    try {
      await writers.createDirectory(directory);
    } catch {
      // Directory may already exist after agent creation.
    }
  }

  for (const [path, content] of Object.entries(draft.files)) {
    const normalized = path.replace(/\\/g, "/");
    if (
      normalized === "SKILL.md" ||
      normalized.toLowerCase().endsWith("/skill.md")
    ) {
      await writers.writeSkillMd(content);
      continue;
    }
    await writers.writeFile(path, content);
  }

  if (binaries.length > 0) {
    await writers.uploadFiles(
      binaries.map((entry) => ({ path: entry.path, file: entry.file })),
    );
  }
}

export function mergeServerEntriesIntoDraft(
  draft: SkillLocalDraft,
  entries: SkillFileEntry[],
  contents: Record<string, string>,
  options: { replaceExisting?: boolean } = {},
): SkillLocalDraft {
  let next = {
    ...draft,
    directories: [...draft.directories],
    files: { ...draft.files },
  };

  for (const entry of entries) {
    const workspacePath = serverPathToWorkspacePath(entry.path);
    if (entry.type === "directory") {
      next = addLocalDirectory(next, workspacePath);
      continue;
    }
    if (!options.replaceExisting && workspacePath in next.files) continue;
    const content = contents[entry.path];
    if (content !== undefined) {
      next = addLocalFile(next, workspacePath, content);
    }
  }

  return next;
}
