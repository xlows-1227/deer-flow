"use client";

import type { BaseStream } from "@langchain/langgraph-sdk/react";
import {
  ArrowLeftIcon,
  CheckIcon,
  FileDiffIcon,
  HistoryIcon,
  Loader2Icon,
  RotateCcwIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThreadContext } from "@/components/workspace/messages/context";
import { upsertSkillAiCreateSession } from "@/components/workspace/skills/ai-create/skill-ai-create-sessions";
import { SkillConversationPanel } from "@/components/workspace/skills/ai-create/skill-conversation-panel";
import { SkillFileTree } from "@/components/workspace/skills/ai-create/skill-file-tree";
import type { OpenFileTab } from "@/components/workspace/skills/ai-create/skill-file-viewer";
import { SkillFileViewer } from "@/components/workspace/skills/ai-create/skill-file-viewer";
import {
  addLocalDirectory,
  addLocalFile,
  buildRenamedChildPath,
  buildTreeFromLocalDraft,
  convertWorkspaceDraftToServerDraft,
  countDirectoryEntries,
  createEmptyLocalDraft,
  draftPathExists,
  findWorkspaceSkillMdPath,
  getCurrentDirectory,
  readLocalFileContent,
  remapPathUnderPrefix,
  removeLocalDirectory,
  removeLocalFile,
  renameLocalDirectoryPath,
  renameLocalFilePath,
  reorganizeSkillsIntoNamedFolder,
  saveLocalDraft,
  serverPathToWorkspacePath,
  updateLocalFileContent,
  workspacePathToServerPath,
  type SkillLocalDraft,
} from "@/components/workspace/skills/ai-create/skill-local-draft";
import {
  loadCustomSkillFileSnapshot,
  loadCustomSkillVersionSnapshot,
  mergeCustomSkillSnapshotIntoDraft,
} from "@/components/workspace/skills/ai-create/skill-server-import";
import { importThreadOutputsIntoDraft } from "@/components/workspace/skills/ai-create/skill-thread-import";
import {
  extractSkillManageServerPath,
  getDefaultExpandedPaths,
} from "@/components/workspace/skills/ai-create/utils";
import { parseSkillMarkdown } from "@/components/workspace/skills/skill-create-utils";
import { useHighlightTimeout } from "@/components/workspace/skills/use-highlight-timeout";
import { useThreadSettings } from "@/core/settings";
import {
  createCustomSkillDirectory,
  createCustomSkillVersionSnapshot,
  deleteCustomSkillFile,
  updateCustomSkill,
  writeCustomSkillFile,
} from "@/core/skills/api";
import {
  useCustomSkillVersions,
  useRestoreCustomSkillVersion,
} from "@/core/skills/hooks";
import type { SkillVersion } from "@/core/skills/type";
import { useThreadStream } from "@/core/threads/hooks";
import type { ToolEndEvent } from "@/core/threads/hooks";
import type { AgentThreadState } from "@/core/threads/types";
import { THREAD_SOURCE_SKILL_SESSION } from "@/core/threads/utils";
import { uuid } from "@/core/utils/uuid";
import { cn } from "@/lib/utils";

import {
  applySkillDraftChangeToBaseline,
  buildSkillDraftChanges,
  type SkillDraftChange,
} from "./skill-editor-utils";

const TEXT_FILE_PATTERN =
  /\.(md|txt|json|ya?ml|sh|py|ts|js|css|html|csv|xml)$/i;

function isTextUploadPath(path: string, file: File) {
  return (
    TEXT_FILE_PATTERN.test(path) ||
    file.type.startsWith("text/") ||
    file.type === "application/json"
  );
}

function expandPathAncestors(paths: Iterable<string>) {
  const expanded = new Set<string>();
  for (const path of paths) {
    const parts = path.split("/");
    for (let index = 1; index < parts.length; index += 1) {
      expanded.add(parts.slice(0, index).join("/"));
    }
  }
  return expanded;
}

function applyOpenTabsToDraft(draft: SkillLocalDraft, tabs: OpenFileTab[]) {
  let next = draft;
  for (const tab of tabs) {
    next = updateLocalFileContent(next, tab.path, tab.content);
  }
  return next;
}

function getSkillNameFromDraft(draft: SkillLocalDraft, fallback: string) {
  const skillMdPath = findWorkspaceSkillMdPath(draft);
  const content = skillMdPath ? draft.files[skillMdPath] : "";
  const parsed = parseSkillMarkdown(content ?? "");
  return parsed.name?.trim() ?? draft.skillName ?? fallback;
}

function remapPathToSkillFolder(
  path: string,
  previousSkillName: string | null | undefined,
  nextSkillName: string,
) {
  const normalized = path.replace(/\\/g, "/");
  const nextPrefix = `skills/${nextSkillName}/`;
  if (normalized === "skills") return normalized;
  if (
    normalized === `skills/${nextSkillName}` ||
    normalized.startsWith(nextPrefix)
  ) {
    return normalized;
  }
  if (previousSkillName) {
    const previousPrefix = `skills/${previousSkillName}/`;
    if (normalized === `skills/${previousSkillName}`) {
      return `skills/${nextSkillName}`;
    }
    if (normalized.startsWith(previousPrefix)) {
      return nextPrefix + normalized.slice(previousPrefix.length);
    }
  }
  if (normalized.startsWith("skills/")) {
    return nextPrefix + normalized.slice("skills/".length);
  }
  return normalized;
}

function normalizeSkillEditorDraft(
  draft: SkillLocalDraft,
  fallbackSkillName: string,
) {
  return reorganizeSkillsIntoNamedFolder(
    draft,
    getSkillNameFromDraft(draft, fallbackSkillName),
  );
}

function getReviewDisplayPath(path: string, skillName: string) {
  const normalized = path.replace(/\\/g, "/");
  const namedPrefix = `skills/${skillName}/`;
  if (normalized.startsWith(namedPrefix)) {
    return normalized.slice(namedPrefix.length);
  }
  if (normalized.startsWith("skills/")) {
    return normalized.slice("skills/".length);
  }
  return normalized;
}

function changeLabel(type: SkillDraftChange["type"]) {
  if (type === "added") return "新增";
  if (type === "deleted") return "删除";
  return "修改";
}

function formatVersionLabel(version: SkillVersion) {
  const label = version.label ? ` (${version.label})` : "";
  const message = version.message ? ` — ${version.message}` : "";
  return `v${version.seq}${label} · ${version.action}${message}`;
}

function formatVersionTime(createdAt: string) {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return createdAt;
  return date.toLocaleString();
}

type DiffLineOperation =
  | {
      type: "equal";
      beforeText: string;
      beforeNumber: number;
      afterText: string;
      afterNumber: number;
    }
  | { type: "deleted"; beforeText: string; beforeNumber: number }
  | { type: "added"; afterText: string; afterNumber: number };

interface SideBySideDiffRow {
  key: string;
  type: "equal" | "added" | "deleted" | "modified";
  beforeNumber: number | null;
  afterNumber: number | null;
  beforeText: string;
  afterText: string;
}

function splitDiffText(text: string | null) {
  if (!text) return [];
  return text.split("\n");
}

function buildDiffOperations(
  beforeLines: string[],
  afterLines: string[],
): DiffLineOperation[] {
  const matrix = Array.from({ length: beforeLines.length + 1 }, () =>
    Array(afterLines.length + 1).fill(0),
  );

  for (
    let beforeIndex = beforeLines.length - 1;
    beforeIndex >= 0;
    beforeIndex -= 1
  ) {
    for (
      let afterIndex = afterLines.length - 1;
      afterIndex >= 0;
      afterIndex -= 1
    ) {
      const currentRow = matrix[beforeIndex]!;
      const nextRow = matrix[beforeIndex + 1]!;
      currentRow[afterIndex] =
        beforeLines[beforeIndex] === afterLines[afterIndex]
          ? nextRow[afterIndex + 1]! + 1
          : Math.max(nextRow[afterIndex], currentRow[afterIndex + 1]);
    }
  }

  const operations: DiffLineOperation[] = [];
  let beforeIndex = 0;
  let afterIndex = 0;

  while (beforeIndex < beforeLines.length && afterIndex < afterLines.length) {
    if (beforeLines[beforeIndex] === afterLines[afterIndex]) {
      operations.push({
        type: "equal",
        beforeText: beforeLines[beforeIndex] ?? "",
        beforeNumber: beforeIndex + 1,
        afterText: afterLines[afterIndex] ?? "",
        afterNumber: afterIndex + 1,
      });
      beforeIndex += 1;
      afterIndex += 1;
    } else if (
      matrix[beforeIndex + 1]![afterIndex]! >=
      matrix[beforeIndex]![afterIndex + 1]!
    ) {
      operations.push({
        type: "deleted",
        beforeText: beforeLines[beforeIndex] ?? "",
        beforeNumber: beforeIndex + 1,
      });
      beforeIndex += 1;
    } else {
      operations.push({
        type: "added",
        afterText: afterLines[afterIndex] ?? "",
        afterNumber: afterIndex + 1,
      });
      afterIndex += 1;
    }
  }

  while (beforeIndex < beforeLines.length) {
    operations.push({
      type: "deleted",
      beforeText: beforeLines[beforeIndex] ?? "",
      beforeNumber: beforeIndex + 1,
    });
    beforeIndex += 1;
  }
  while (afterIndex < afterLines.length) {
    operations.push({
      type: "added",
      afterText: afterLines[afterIndex] ?? "",
      afterNumber: afterIndex + 1,
    });
    afterIndex += 1;
  }

  return operations;
}

function buildSideBySideDiffRows(change: SkillDraftChange | null) {
  if (!change) return [];
  const operations = buildDiffOperations(
    splitDiffText(change.before),
    splitDiffText(change.after),
  );
  const rows: SideBySideDiffRow[] = [];

  for (let index = 0; index < operations.length; index += 1) {
    const operation = operations[index];
    if (!operation) continue;

    if (operation.type === "equal") {
      rows.push({
        key: `equal-${operation.beforeNumber}-${operation.afterNumber}`,
        type: "equal",
        beforeNumber: operation.beforeNumber,
        afterNumber: operation.afterNumber,
        beforeText: operation.beforeText,
        afterText: operation.afterText,
      });
      continue;
    }

    if (operation.type === "deleted") {
      const nextOperation = operations[index + 1];
      if (nextOperation?.type === "added") {
        rows.push({
          key: `modified-${operation.beforeNumber}-${nextOperation.afterNumber}`,
          type: "modified",
          beforeNumber: operation.beforeNumber,
          afterNumber: nextOperation.afterNumber,
          beforeText: operation.beforeText,
          afterText: nextOperation.afterText,
        });
        index += 1;
      } else {
        rows.push({
          key: `deleted-${operation.beforeNumber}`,
          type: "deleted",
          beforeNumber: operation.beforeNumber,
          afterNumber: null,
          beforeText: operation.beforeText,
          afterText: "",
        });
      }
      continue;
    }

    rows.push({
      key: `added-${operation.afterNumber}`,
      type: "added",
      beforeNumber: null,
      afterNumber: operation.afterNumber,
      beforeText: "",
      afterText: operation.afterText,
    });
  }

  return rows;
}

async function loadCustomSkillDraft(skillName: string) {
  const { entries, contents } = await loadCustomSkillFileSnapshot(skillName);
  const { draft } = mergeCustomSkillSnapshotIntoDraft(
    { ...createEmptyLocalDraft(), skillName },
    skillName,
    entries,
    contents,
    { replaceExisting: true },
  );
  return { draft: normalizeSkillEditorDraft(draft, skillName), entries };
}

function applyImportedPathsToTree(
  paths: string[],
  setExpandedPaths: (
    value: Set<string> | ((current: Set<string>) => Set<string>),
  ) => void,
  setHighlightedPaths: (
    value: Set<string> | ((current: Set<string>) => Set<string>),
  ) => void,
  highlightPaths: (
    setter: (value: Set<string>) => void,
    paths: Set<string>,
    delay?: number,
  ) => void,
) {
  if (paths.length === 0) return;
  setExpandedPaths((current) => {
    const next = new Set(current);
    expandPathAncestors(paths).forEach((item) => next.add(item));
    return next;
  });
  highlightPaths(
    setHighlightedPaths as (value: Set<string>) => void,
    new Set(paths),
  );
}

export function SkillEditorWorkspace({ skillName }: { skillName: string }) {
  const [baselineDraft, setBaselineDraft] = useState<SkillLocalDraft | null>(
    null,
  );
  const [draft, setDraft] = useState<SkillLocalDraft>(() =>
    createEmptyLocalDraft(),
  );
  const [threadId, setThreadId] = useState(() => uuid());
  const [isNewThread, setIsNewThread] = useState(true);
  const [isWelcomeMode, setIsWelcomeMode] = useState(true);
  const [openTabs, setOpenTabs] = useState<OpenFileTab[]>([]);
  const [activeTabPath, setActiveTabPath] = useState<string | null>(null);
  const [selectedTreePath, setSelectedTreePath] = useState<string | null>(null);
  const [selectedTreeType, setSelectedTreeType] = useState<
    "file" | "directory" | null
  >(null);
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const [highlightedPaths, setHighlightedPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const highlightPaths = useHighlightTimeout();
  const [selectedChangePath, setSelectedChangePath] = useState<string | null>(
    null,
  );
  const [reviewOpen, setReviewOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [selectedVersionSeq, setSelectedVersionSeq] = useState<number | null>(
    null,
  );
  const [versionDraft, setVersionDraft] = useState<SkillLocalDraft | null>(
    null,
  );
  const [selectedVersionChangePath, setSelectedVersionChangePath] = useState<
    string | null
  >(null);
  const [isLoadingVersionDraft, setIsLoadingVersionDraft] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isApplying, setIsApplying] = useState(false);
  const draftRef = useRef(draft);
  const openTabsRef = useRef(openTabs);
  const threadRef = useRef<BaseStream<AgentThreadState> | null>(null);
  const isNewThreadRef = useRef(isNewThread);
  const isLoadingRef = useRef(isLoading);
  const runThreadFileImportRef = useRef<
    (options?: { replaceExisting?: boolean }) => Promise<string[]>
  >(async () => []);
  const importServerSkillFilesRef = useRef<
    (options?: {
      replaceExisting?: boolean;
      highlightServerPath?: string | null;
    }) => Promise<string[]>
  >(async () => []);

  draftRef.current = draft;
  openTabsRef.current = openTabs;
  isNewThreadRef.current = isNewThread;
  isLoadingRef.current = isLoading;

  const effectiveDraft = useMemo(
    () => applyOpenTabsToDraft(draft, openTabs),
    [draft, openTabs],
  );
  const fileTree = useMemo(() => buildTreeFromLocalDraft(draft), [draft]);
  const skillFolderPath = draft.skillName
    ? `skills/${draft.skillName}`
    : "skills";
  const currentDirectory = useMemo(() => {
    const directory = getCurrentDirectory(selectedTreePath, selectedTreeType);
    return directory === "skills" ? skillFolderPath : directory;
  }, [selectedTreePath, selectedTreeType, skillFolderPath]);
  const changes = useMemo(
    () =>
      baselineDraft
        ? buildSkillDraftChanges(baselineDraft, effectiveDraft)
        : [],
    [baselineDraft, effectiveDraft],
  );
  const selectedChange =
    changes.find((change) => change.path === selectedChangePath) ??
    changes[0] ??
    null;
  const selectedChangeDiffRows = useMemo(
    () => buildSideBySideDiffRows(selectedChange),
    [selectedChange],
  );
  const {
    versions,
    isLoading: isVersionsLoading,
    refetch: refetchVersions,
  } = useCustomSkillVersions(versionsOpen ? skillName : null);
  const restoreVersionMutation = useRestoreCustomSkillVersion();
  const versionChanges = useMemo(
    () =>
      versionDraft && baselineDraft
        ? buildSkillDraftChanges(versionDraft, baselineDraft)
        : [],
    [baselineDraft, versionDraft],
  );
  const selectedVersionChange =
    versionChanges.find(
      (change) => change.path === selectedVersionChangePath,
    ) ??
    versionChanges[0] ??
    null;
  const selectedVersionDiffRows = useMemo(
    () => buildSideBySideDiffRows(selectedVersionChange),
    [selectedVersionChange],
  );
  const parsedSkill = useMemo(() => {
    const skillMdPath = findWorkspaceSkillMdPath(effectiveDraft);
    const content = skillMdPath
      ? (effectiveDraft.files[skillMdPath] ?? "")
      : "";
    return parseSkillMarkdown(content);
  }, [effectiveDraft]);
  const hasDirtyTabs = openTabs.some((tab) => tab.dirty);
  const displaySkillName =
    parsedSkill.displayName || parsedSkill.name || skillName;
  const pathSkillName =
    parsedSkill.name?.trim() ?? draft.skillName ?? skillName;
  const protectedDirectoryPaths = useMemo(
    () => new Set(["skills", `skills/${pathSkillName}`]),
    [pathSkillName],
  );
  const showEditorPanel = openTabs.length > 0;
  const [settings] = useThreadSettings(threadId);
  const skillContext = useMemo(
    () => ({
      ...settings.context,
      mode: settings.context.mode ?? "pro",
      skill_name: "skill-creator",
    }),
    [settings.context],
  );

  const hydrateDraft = useCallback(
    (nextDraft: SkillLocalDraft, nextThreadId: string) => {
      setDraft(nextDraft);
      setThreadId(nextThreadId);
      setIsNewThread(true);
      setIsWelcomeMode(true);
      const skillMdPath = findWorkspaceSkillMdPath(nextDraft);
      setOpenTabs([]);
      setActiveTabPath(null);
      setSelectedTreePath(skillMdPath);
      setSelectedTreeType(skillMdPath ? "file" : null);
      setExpandedPaths(
        expandPathAncestors([
          ...nextDraft.directories,
          ...Object.keys(nextDraft.files),
        ]),
      );
      setHighlightedPaths(new Set());
    },
    [],
  );

  const loadBaseline = useCallback(async () => {
    setIsLoading(true);
    try {
      const { draft: serverDraft } = await loadCustomSkillDraft(skillName);
      setBaselineDraft(serverDraft);
      hydrateDraft(serverDraft, uuid());
      setExpandedPaths(
        getDefaultExpandedPaths([
          ...serverDraft.directories.map((path) => ({
            path,
            type: "directory" as const,
            size: null,
          })),
          ...Object.keys(serverDraft.files).map((path) => ({
            path,
            type: "file" as const,
            size: serverDraft.files[path]?.length ?? null,
          })),
        ]),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载 Skill 失败");
    } finally {
      setIsLoading(false);
    }
  }, [hydrateDraft, skillName]);

  useEffect(() => {
    void loadBaseline();
  }, [loadBaseline]);

  useEffect(() => {
    if (!versionsOpen) return;
    void refetchVersions();
  }, [refetchVersions, versionsOpen]);

  useEffect(() => {
    if (!versionsOpen || selectedVersionSeq === null) {
      setVersionDraft(null);
      return;
    }

    let cancelled = false;
    setIsLoadingVersionDraft(true);
    void loadCustomSkillVersionSnapshot(skillName, selectedVersionSeq)
      .then(({ entries, contents }) => {
        if (cancelled) return;
        const { draft: nextDraft } = mergeCustomSkillSnapshotIntoDraft(
          { ...createEmptyLocalDraft(), skillName },
          skillName,
          entries,
          contents,
          { replaceExisting: true },
        );
        const organizedDraft = normalizeSkillEditorDraft(nextDraft, skillName);
        setVersionDraft(organizedDraft);
        setSelectedVersionChangePath(null);
      })
      .catch((error) => {
        if (cancelled) return;
        toast.error(
          error instanceof Error ? error.message : "加载版本快照失败",
        );
        setVersionDraft(null);
      })
      .finally(() => {
        if (!cancelled) setIsLoadingVersionDraft(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedVersionSeq, skillName, versionsOpen]);

  useEffect(() => {
    const nextSkillName = parsedSkill.name?.trim();
    if (!nextSkillName || draft.skillName === nextSkillName) return;

    const previousSkillName = draft.skillName;
    const draftWithTabs = applyOpenTabsToDraft(draft, openTabs);
    const nextDraft = reorganizeSkillsIntoNamedFolder(
      draftWithTabs,
      nextSkillName,
    );

    setDraft(nextDraft);
    setBaselineDraft((current) =>
      current
        ? reorganizeSkillsIntoNamedFolder(current, nextSkillName)
        : current,
    );
    setOpenTabs((current) =>
      current.map((tab) => {
        const nextPath = remapPathToSkillFolder(
          tab.path,
          previousSkillName,
          nextSkillName,
        );
        return {
          ...tab,
          path: nextPath,
          content: nextDraft.files[nextPath] ?? tab.content,
        };
      }),
    );
    setActiveTabPath((current) =>
      current
        ? remapPathToSkillFolder(current, previousSkillName, nextSkillName)
        : current,
    );
    setSelectedTreePath((current) =>
      current
        ? remapPathToSkillFolder(current, previousSkillName, nextSkillName)
        : current,
    );
    setExpandedPaths((current) => {
      const next = new Set<string>();
      current.forEach((path) =>
        next.add(
          remapPathToSkillFolder(path, previousSkillName, nextSkillName),
        ),
      );
      expandPathAncestors([
        ...nextDraft.directories,
        ...Object.keys(nextDraft.files),
      ]).forEach((path) => next.add(path));
      return next;
    });
    setHighlightedPaths((current) => {
      const next = new Set<string>();
      current.forEach((path) =>
        next.add(
          remapPathToSkillFolder(path, previousSkillName, nextSkillName),
        ),
      );
      return next;
    });
  }, [draft, openTabs, parsedSkill.name]);

  useEffect(() => {
    if (!baselineDraft || isLoading) return;
    saveLocalDraft(threadId, effectiveDraft);
    if (changes.length === 0 && !hasDirtyTabs) return;
    upsertSkillAiCreateSession({
      threadId,
      title: `编辑 ${displaySkillName}`,
      skillName,
      published: false,
    });
  }, [
    baselineDraft,
    changes.length,
    displaySkillName,
    effectiveDraft,
    hasDirtyTabs,
    isLoading,
    skillName,
    threadId,
  ]);

  const {
    thread,
    sendMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    context: skillContext,
    threadMetadata: { source: THREAD_SOURCE_SKILL_SESSION },
    onSend: () => setIsWelcomeMode(false),
    onStart: (createdThreadId) => {
      saveLocalDraft(createdThreadId, effectiveDraft);
      setThreadId(createdThreadId);
      setIsNewThread(false);
    },
    onToolEnd: (event: ToolEndEvent) => {
      if (
        event.name === "write_file" ||
        event.name === "present_files" ||
        event.name === "str_replace"
      ) {
        void runThreadFileImportRef.current({ replaceExisting: true });
      }
      if (event.name === "skill_manage") {
        void importServerSkillFilesRef.current({
          replaceExisting: true,
          highlightServerPath: extractSkillManageServerPath(event),
        });
        void runThreadFileImportRef.current({ replaceExisting: true });
      }
    },
    onFinish: () => {
      void runThreadFileImportRef.current({ replaceExisting: true });
      void importServerSkillFilesRef.current({ replaceExisting: true });
    },
  });

  threadRef.current = thread;

  const importServerSkillFiles = useCallback(
    async (
      options: {
        replaceExisting?: boolean;
        highlightServerPath?: string | null;
      } = {},
    ) => {
      if (isLoadingRef.current) return [];
      try {
        const { entries, contents } =
          await loadCustomSkillFileSnapshot(skillName);
        const { draft: nextDraft, workspacePaths } =
          mergeCustomSkillSnapshotIntoDraft(
            draftRef.current,
            skillName,
            entries,
            contents,
            { replaceExisting: options.replaceExisting ?? true },
          );
        const organizedDraft = normalizeSkillEditorDraft(nextDraft, skillName);
        const organizedPaths = workspacePaths.map((path) =>
          remapPathToSkillFolder(
            path,
            nextDraft.skillName,
            organizedDraft.skillName ?? skillName,
          ),
        );
        setDraft(organizedDraft);
        applyImportedPathsToTree(
          organizedPaths,
          setExpandedPaths,
          setHighlightedPaths,
          highlightPaths,
        );
        const highlightPath = options.highlightServerPath
          ? remapPathToSkillFolder(
              serverPathToWorkspacePath(options.highlightServerPath),
              nextDraft.skillName,
              organizedDraft.skillName ?? skillName,
            )
          : null;
        if (highlightPath) {
          highlightPaths(setHighlightedPaths, new Set([highlightPath]));
        }
        return organizedPaths;
      } catch {
        return [];
      }
    },
    [highlightPaths, skillName],
  );

  importServerSkillFilesRef.current = importServerSkillFiles;

  const runThreadFileImport = useCallback(
    async (options: { replaceExisting?: boolean } = {}) => {
      const currentThread = threadRef.current;
      if (isNewThreadRef.current || isLoadingRef.current || !currentThread) {
        return [];
      }
      try {
        const preservePaths = new Set(
          openTabsRef.current.filter((tab) => tab.dirty).map((tab) => tab.path),
        );
        const result = await importThreadOutputsIntoDraft({
          threadId,
          messages: currentThread.messages,
          artifacts: currentThread.values?.artifacts ?? [],
          draft: draftRef.current,
          binaries: [],
          options: {
            replaceExisting: options.replaceExisting,
            preservePaths,
          },
        });
        if (result.importedPaths.length === 0) return [];
        const organizedDraft = normalizeSkillEditorDraft(
          result.draft,
          skillName,
        );
        const organizedPaths = result.importedPaths.map((path) =>
          remapPathToSkillFolder(
            path,
            result.draft.skillName,
            organizedDraft.skillName ?? skillName,
          ),
        );
        setDraft(organizedDraft);
        applyImportedPathsToTree(
          organizedPaths,
          setExpandedPaths,
          setHighlightedPaths,
          highlightPaths,
        );
        setOpenTabs((current) =>
          current.map((tab) => {
            if (tab.dirty || preservePaths.has(tab.path)) return tab;
            const nextPath = remapPathToSkillFolder(
              tab.path,
              result.draft.skillName,
              organizedDraft.skillName ?? skillName,
            );
            const content = organizedDraft.files[nextPath];
            if (content === undefined) return tab;
            return { ...tab, path: nextPath, content, dirty: false };
          }),
        );
        return organizedPaths;
      } catch {
        return [];
      }
    },
    [highlightPaths, skillName, threadId],
  );

  runThreadFileImportRef.current = runThreadFileImport;

  const threadSyncKey = useMemo(
    () =>
      `${thread.messages.length}:${(thread.values?.artifacts ?? []).join("\u0000")}`,
    [thread.messages.length, thread.values?.artifacts],
  );

  useEffect(() => {
    if (isNewThread || isLoading) return;
    void runThreadFileImport();
  }, [isLoading, isNewThread, runThreadFileImport, threadSyncKey]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      const submitPromise = sendMessage(threadId, message);
      if (message.files.length > 0) {
        return submitPromise;
      }
      void submitPromise;
    },
    [sendMessage, threadId],
  );

  function openFile(path: string) {
    const existing = openTabs.find((tab) => tab.path === path);
    if (existing) {
      setActiveTabPath(path);
      setSelectedTreePath(path);
      setSelectedTreeType("file");
      return;
    }
    setOpenTabs((current) => [
      ...current,
      { path, content: readLocalFileContent(draft, path), dirty: false },
    ]);
    setActiveTabPath(path);
    setSelectedTreePath(path);
    setSelectedTreeType("file");
  }

  function handleCloseTab(path: string) {
    setOpenTabs((current) => current.filter((tab) => tab.path !== path));
    if (activeTabPath === path) {
      const remaining = openTabs.filter((tab) => tab.path !== path);
      setActiveTabPath(remaining.at(-1)?.path ?? null);
    }
  }

  function handleSaveFile(path: string) {
    const tab = openTabs.find((item) => item.path === path);
    if (!tab) return;
    setDraft((current) => updateLocalFileContent(current, path, tab.content));
    setOpenTabs((current) =>
      current.map((item) =>
        item.path === path ? { ...item, dirty: false } : item,
      ),
    );
  }

  function handleRefreshFile(path: string) {
    const content = readLocalFileContent(draft, path);
    setOpenTabs((current) =>
      current.map((tab) =>
        tab.path === path ? { ...tab, content, dirty: false } : tab,
      ),
    );
  }

  async function handleCreateFile(path: string) {
    setDraft((current) => addLocalFile(current, path, ""));
    setExpandedPaths((current) => {
      const next = new Set(current);
      expandPathAncestors([path]).forEach((item) => next.add(item));
      return next;
    });
    setHighlightedPaths(new Set([path]));
    openFile(path);
  }

  async function handleCreateDirectory(path: string) {
    setDraft((current) => addLocalDirectory(current, path));
    setExpandedPaths((current) => {
      const next = new Set(current);
      expandPathAncestors([path]).forEach((item) => next.add(item));
      next.add(path);
      return next;
    });
    setSelectedTreePath(path);
    setSelectedTreeType("directory");
  }

  async function handleUploadFiles(entries: { path: string; file: File }[]) {
    const unsupported = entries.filter(
      (entry) => !isTextUploadPath(entry.path, entry.file),
    );
    if (unsupported.length > 0) {
      toast.error("Skill 编辑器暂只支持文本文件上传");
      return;
    }

    const textEntries = await Promise.all(
      entries.map(async (entry) => ({
        path: entry.path,
        content: await entry.file.text(),
      })),
    );
    setDraft((current) => {
      let next = current;
      for (const entry of textEntries) {
        next = addLocalFile(next, entry.path, entry.content);
      }
      return next;
    });
    const paths = textEntries.map((entry) => entry.path);
    setExpandedPaths((current) => {
      const next = new Set(current);
      expandPathAncestors(paths).forEach((item) => next.add(item));
      return next;
    });
    setHighlightedPaths(new Set(paths));
  }

  function handleDeleteFile(path: string) {
    if (protectedDirectoryPaths.has(path)) return;
    setDraft((current) => removeLocalFile(current, path));
    setOpenTabs((current) => current.filter((tab) => tab.path !== path));
    if (activeTabPath === path) setActiveTabPath(null);
  }

  function handleDeleteDirectory(path: string) {
    if (protectedDirectoryPaths.has(path)) return;
    setDraft((current) => removeLocalDirectory(current, path));
    setOpenTabs((current) =>
      current.filter((tab) => !tab.path.startsWith(`${path}/`)),
    );
    if (selectedTreePath?.startsWith(path)) {
      setSelectedTreePath(null);
      setSelectedTreeType(null);
    }
  }

  async function handleRename(
    path: string,
    nextName: string,
    type: "file" | "directory",
  ) {
    if (type === "directory" && protectedDirectoryPaths.has(path)) {
      toast.error("根目录和 Skill 目录不能手动重命名");
      return false;
    }
    const nextPath = buildRenamedChildPath(path, nextName);
    if (!nextPath) return false;
    if (draftPathExists(effectiveDraft, nextPath)) {
      toast.error("目标路径已存在");
      return false;
    }
    const renamed =
      type === "file"
        ? renameLocalFilePath(effectiveDraft, path, nextPath)
        : renameLocalDirectoryPath(effectiveDraft, path, nextPath);
    if (!renamed) {
      toast.error("重命名失败");
      return false;
    }
    setDraft(renamed);
    setOpenTabs((current) =>
      current.map((tab) =>
        tab.path === path || tab.path.startsWith(`${path}/`)
          ? { ...tab, path: remapPathUnderPrefix(tab.path, path, nextPath) }
          : tab,
      ),
    );
    setSelectedTreePath((current) =>
      current ? remapPathUnderPrefix(current, path, nextPath) : current,
    );
    setActiveTabPath((current) =>
      current ? remapPathUnderPrefix(current, path, nextPath) : current,
    );
    return true;
  }

  async function applyChanges(targetChange?: SkillDraftChange) {
    const targetChanges = targetChange ? [targetChange] : changes;
    if (!baselineDraft || targetChanges.length === 0) return;
    if (hasDirtyTabs) {
      setDraft(effectiveDraft);
      setOpenTabs((current) =>
        current.map((tab) => ({ ...tab, dirty: false })),
      );
    }

    const skillMdDelete = targetChanges.find(
      (change) =>
        change.type === "deleted" &&
        workspacePathToServerPath(change.path, pathSkillName) === "SKILL.md",
    );
    if (skillMdDelete) {
      toast.error("不能删除 SKILL.md");
      return;
    }

    setIsApplying(true);
    try {
      const serverDraft = convertWorkspaceDraftToServerDraft(
        effectiveDraft,
        pathSkillName,
      );
      const skillMdWorkspacePath = findWorkspaceSkillMdPath(effectiveDraft);
      const skillMdChange = targetChanges.find(
        (change) =>
          workspacePathToServerPath(change.path, pathSkillName) === "SKILL.md",
      );
      const skillMdContent =
        skillMdChange?.after ??
        (skillMdWorkspacePath
          ? effectiveDraft.files[skillMdWorkspacePath]
          : undefined);
      if (skillMdContent !== undefined) {
        await updateCustomSkill(skillName, skillMdContent);
      }

      for (const directory of serverDraft.directories) {
        try {
          await createCustomSkillDirectory(skillName, directory);
        } catch {
          // Directory may already exist after agent creation.
        }
      }

      for (const change of targetChanges) {
        const serverPath = workspacePathToServerPath(
          change.path,
          pathSkillName,
        );
        if (change.type === "deleted") {
          await deleteCustomSkillFile(skillName, serverPath);
        } else if (serverPath === "SKILL.md") {
          continue;
        } else {
          await writeCustomSkillFile(skillName, serverPath, change.after ?? "");
        }
      }

      const appliedDraft = effectiveDraft;
      setBaselineDraft((current) => {
        if (!current) return current;
        if (!targetChange) return appliedDraft;
        return applySkillDraftChangeToBaseline(
          current,
          appliedDraft,
          targetChange,
        );
      });
      setDraft(appliedDraft);
      setOpenTabs((current) =>
        current.map((tab) => ({ ...tab, dirty: false })),
      );
      upsertSkillAiCreateSession({
        threadId,
        title: `已应用 ${displaySkillName}`,
        skillName,
        published: true,
      });
      try {
        await createCustomSkillVersionSnapshot(skillName, {
          action: "edit",
          message: targetChange
            ? `applied ${targetChange.path}`
            : `applied ${targetChanges.length} file(s)`,
        });
        if (versionsOpen) void refetchVersions();
      } catch (error) {
        toast.warning(
          error instanceof Error
            ? `修改已应用，但版本快照失败：${error.message}`
            : "修改已应用，但版本快照失败",
        );
      }
      if (targetChange) {
        const remainingChanges = changes.filter(
          (change) => change.path !== targetChange.path,
        );
        setSelectedChangePath(remainingChanges[0]?.path ?? null);
        if (remainingChanges.length === 0) setReviewOpen(false);
        toast.success(`已应用 ${targetChange.path}`);
      } else {
        setReviewOpen(false);
        toast.success("修改已应用");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "应用修改失败");
    } finally {
      setIsApplying(false);
    }
  }

  async function handleRestoreVersion(seq: number) {
    if (
      !window.confirm(
        `确定将 Skill 恢复到 v${seq} 吗？当前状态会先自动保存为一个版本快照。`,
      )
    ) {
      return;
    }

    try {
      await restoreVersionMutation.mutateAsync({ skillName, seq });
      setVersionsOpen(false);
      setSelectedVersionSeq(null);
      setVersionDraft(null);
      await loadBaseline();
      toast.success(`已恢复到 v${seq}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "恢复版本失败");
    }
  }

  return (
    <ThreadContext.Provider value={{ thread }}>
      <div className="flex size-full flex-col bg-[#fafafa]">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-gray-200 bg-white px-6 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="ghost" size="icon-sm" asChild>
              <Link href="/workspace/skills" aria-label="返回 Skill 管理">
                <ArrowLeftIcon className="size-4" />
              </Link>
            </Button>
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold text-gray-900">
                {displaySkillName}
              </h1>
              <p className="mt-1 truncate text-sm text-gray-500">
                独立 Skill 编辑器，先审查变动再应用。
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={changes.length > 0 ? "secondary" : "outline"}>
              {changes.length} 个变动文件
            </Badge>
            <Button
              variant="outline"
              disabled={isLoading || isApplying}
              onClick={() => {
                setSelectedVersionSeq(null);
                setVersionsOpen(true);
              }}
            >
              <HistoryIcon className="size-4" />
              版本历史
            </Button>
            <Button
              variant="outline"
              disabled={isLoading || isApplying}
              onClick={() => void loadBaseline()}
            >
              <RotateCcwIcon className="size-4" />
              重新加载
            </Button>
            <Button
              disabled={isLoading || changes.length === 0 || isApplying}
              onClick={() => {
                setSelectedChangePath(changes[0]?.path ?? null);
                setReviewOpen(true);
              }}
            >
              <FileDiffIcon className="size-4" />
              审查并应用
            </Button>
          </div>
        </header>

        {isLoading ? (
          <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            加载 Skill 文件中...
          </div>
        ) : (
          <ResizablePanelGroup
            key={showEditorPanel ? "skill-editor-3" : "skill-editor-2"}
            id="skill-editor-panels"
            orientation="horizontal"
            className="min-h-0 flex-1"
            defaultLayout={
              showEditorPanel
                ? { files: 22, editor: 43, chat: 35 }
                : { files: 32, chat: 68 }
            }
          >
            <ResizablePanel id="files" defaultSize="22%" minSize="200px">
              <aside className="flex h-full min-h-0 flex-col gap-3 p-3">
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-gray-200 bg-white">
                  <SkillFileTree
                    tree={fileTree}
                    selectedPath={selectedTreePath ?? activeTabPath}
                    selectedType={selectedTreeType}
                    expandedPaths={expandedPaths}
                    highlightedPaths={highlightedPaths}
                    currentDirectory={currentDirectory}
                    isEmpty={Object.keys(draft.files).length === 0}
                    onSelectFile={openFile}
                    onSelectDirectory={(path) => {
                      setSelectedTreePath(path);
                      setSelectedTreeType("directory");
                    }}
                    onToggleDirectory={(path) =>
                      setExpandedPaths((current) => {
                        const next = new Set(current);
                        if (next.has(path)) next.delete(path);
                        else next.add(path);
                        return next;
                      })
                    }
                    onRefresh={async (options) => {
                      if (options?.replaceExisting === false) return;
                      await importServerSkillFiles({ replaceExisting: true });
                      await runThreadFileImport({ replaceExisting: true });
                    }}
                    onCreateFile={handleCreateFile}
                    onCreateDirectory={handleCreateDirectory}
                    onUploadFiles={handleUploadFiles}
                    pathExists={(path) => draftPathExists(effectiveDraft, path)}
                    getDirectoryEntryCount={(path) =>
                      countDirectoryEntries(draft, path)
                    }
                    onDeleteFile={handleDeleteFile}
                    onDeleteDirectory={handleDeleteDirectory}
                    onRename={handleRename}
                    isProtectedPath={(path, type) =>
                      type === "directory" && protectedDirectoryPaths.has(path)
                    }
                  />
                </div>
              </aside>
            </ResizablePanel>

            {showEditorPanel ? (
              <>
                <ResizableHandle withHandle />

                <ResizablePanel id="editor" defaultSize="43%" minSize="30%">
                  <section className="h-full min-h-0 p-3 pl-0">
                    <div className="h-full overflow-hidden rounded-lg border border-gray-200 bg-white">
                      <SkillFileViewer
                        tabs={openTabs}
                        activePath={activeTabPath}
                        onSelectTab={setActiveTabPath}
                        onCloseTab={handleCloseTab}
                        onChangeContent={(path, content) =>
                          setOpenTabs((current) =>
                            current.map((tab) =>
                              tab.path === path
                                ? { ...tab, content, dirty: true }
                                : tab,
                            ),
                          )
                        }
                        onSave={handleSaveFile}
                        onRefresh={handleRefreshFile}
                      />
                    </div>
                  </section>
                </ResizablePanel>
              </>
            ) : null}

            <ResizableHandle withHandle />

            <ResizablePanel id="chat" defaultSize="35%" minSize="28%">
              <section className="h-full min-h-0 p-3 pl-0">
                <div className="h-full overflow-hidden rounded-lg border border-gray-200 bg-white">
                  <SkillConversationPanel
                    threadId={threadId}
                    thread={thread}
                    isWelcomeMode={isWelcomeMode}
                    isHistoryLoading={isHistoryLoading}
                    hasMoreHistory={hasMoreHistory}
                    loadMoreHistory={loadMoreHistory}
                    initialPrompt={`你需要对 ${displaySkillName} skill 进行哪些调整？`}
                    disabled={isUploading}
                    showWelcomeSuggestions={false}
                    onSubmit={handleSubmit}
                    onStop={() => void thread.stop()}
                  />
                </div>
              </section>
            </ResizablePanel>
          </ResizablePanelGroup>
        )}

        <Dialog open={versionsOpen} onOpenChange={setVersionsOpen}>
          <DialogContent className="flex h-[88vh] max-w-[84vw] flex-col overflow-hidden p-0 sm:max-w-[84vw]">
            <DialogHeader className="shrink-0 border-b border-gray-100 px-5 py-4">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <DialogTitle>版本历史</DialogTitle>
                  <p className="mt-1 text-sm text-gray-500">
                    浏览历史版本快照，对比差异，或恢复到指定版本。
                  </p>
                </div>
                <Badge
                  variant="outline"
                  className="mr-6 rounded-full bg-gray-50 px-3 py-1 text-xs font-medium text-gray-600"
                >
                  {versions.length} 个版本
                </Badge>
              </div>
            </DialogHeader>
            <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)] bg-white">
              <aside className="flex min-h-0 flex-col border-r border-gray-100">
                <div className="flex h-12 shrink-0 items-center justify-between border-b border-gray-100 px-4">
                  <span className="text-sm font-medium text-gray-900">
                    版本列表
                  </span>
                  <span className="text-xs text-gray-400">
                    {versions.length} versions
                  </span>
                </div>
                <ScrollArea className="h-full">
                  <div className="space-y-1.5 p-3">
                    {isVersionsLoading ? (
                      <div className="flex items-center gap-2 px-2 py-3 text-sm text-gray-500">
                        <Loader2Icon className="size-4 animate-spin" />
                        加载版本...
                      </div>
                    ) : versions.length === 0 ? (
                      <div className="px-2 py-3 text-sm text-gray-400">
                        暂无版本快照
                      </div>
                    ) : (
                      versions.map((version) => (
                        <button
                          key={version.seq}
                          type="button"
                          className={cn(
                            "w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
                            selectedVersionSeq === version.seq
                              ? "border-sky-200 bg-sky-50 shadow-xs"
                              : "border-transparent hover:border-gray-100 hover:bg-gray-50",
                          )}
                          onClick={() => setSelectedVersionSeq(version.seq)}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <span className="min-w-0 text-sm leading-5 font-medium break-words text-gray-800">
                              {formatVersionLabel(version)}
                            </span>
                            <Badge
                              variant="outline"
                              className="shrink-0 rounded-full text-[10px]"
                            >
                              v{version.seq}
                            </Badge>
                          </div>
                          <div className="mt-1 text-xs text-gray-400">
                            {formatVersionTime(version.created_at)}
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                </ScrollArea>
              </aside>

              <section className="flex min-h-0 min-w-0 flex-col">
                <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-gray-100 px-4">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-gray-900">
                      {selectedVersionSeq
                        ? `v${selectedVersionSeq} 与当前版本对比`
                        : "请选择一个版本"}
                    </div>
                    <div className="mt-0.5 text-xs text-gray-400">
                      左侧为历史版本，右侧为当前版本
                    </div>
                  </div>
                  {selectedVersionSeq !== null ? (
                    <Badge variant="outline" className="rounded-full">
                      v{selectedVersionSeq}
                    </Badge>
                  ) : null}
                </div>

                {selectedVersionSeq === null ? (
                  <div className="flex flex-1 items-center justify-center text-sm text-gray-400">
                    从左侧选择一个版本查看差异
                  </div>
                ) : isLoadingVersionDraft ? (
                  <div className="flex flex-1 items-center justify-center text-sm text-gray-500">
                    <Loader2Icon className="mr-2 size-4 animate-spin" />
                    加载版本文件...
                  </div>
                ) : (
                  <div className="flex min-h-0 flex-1 flex-col">
                    <div className="shrink-0 border-b border-gray-100 bg-white">
                      <div className="flex h-10 items-center justify-between px-4">
                        <span className="text-xs font-medium text-gray-700">
                          变动文件
                        </span>
                        <span className="text-xs text-gray-400">
                          {versionChanges.length}
                        </span>
                      </div>
                      <div className="overflow-x-auto px-4 pb-3 [scrollbar-width:thin]">
                        {versionChanges.length === 0 ? (
                          <div className="text-xs text-gray-400">
                            与当前版本无差异
                          </div>
                        ) : (
                          <div className="flex min-w-max gap-2">
                            {versionChanges.map((change) => {
                              const displayPath = getReviewDisplayPath(
                                change.path,
                                pathSkillName,
                              );
                              return (
                                <button
                                  key={change.path}
                                  type="button"
                                  title={change.path}
                                  className={cn(
                                    "inline-grid max-w-72 grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-full border px-3 py-1.5 text-left transition-colors",
                                    selectedVersionChange?.path === change.path
                                      ? "border-sky-200 bg-sky-50 text-sky-700 shadow-xs"
                                      : "border-gray-200 bg-white text-gray-700 hover:bg-gray-50",
                                  )}
                                  onClick={() =>
                                    setSelectedVersionChangePath(change.path)
                                  }
                                >
                                  <span className="min-w-0 truncate font-mono text-xs">
                                    {displayPath}
                                  </span>
                                  <span className="shrink-0 text-[10px] text-gray-400">
                                    {changeLabel(change.type)}
                                  </span>
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </div>
                    <ScrollArea className="min-h-0 flex-1 bg-[#fbfbfb]">
                      <div className="min-w-[980px]">
                        <div className="grid grid-cols-[56px_minmax(0,1fr)_56px_minmax(0,1fr)] border-b border-gray-200 bg-gray-50 font-mono text-xs font-medium text-gray-500">
                          <div className="border-r border-gray-200 px-2 py-2 text-right">
                            旧
                          </div>
                          <div className="border-r border-gray-200 px-3 py-2">
                            历史版本
                          </div>
                          <div className="border-r border-gray-200 px-2 py-2 text-right">
                            新
                          </div>
                          <div className="px-3 py-2">当前版本</div>
                        </div>
                        {selectedVersionDiffRows.length === 0 ? (
                          <div className="p-10 text-center text-sm text-gray-400">
                            {versionChanges.length === 0
                              ? "该版本与当前内容一致"
                              : "暂无可展示的文本差异"}
                          </div>
                        ) : (
                          <div className="grid grid-cols-[56px_minmax(0,1fr)_56px_minmax(0,1fr)] font-mono text-xs leading-5">
                            {selectedVersionDiffRows.map((row) => (
                              <div key={row.key} className="contents">
                                <div
                                  className={cn(
                                    "border-r border-gray-100 px-2 py-0.5 text-right text-gray-400 select-none",
                                    (row.type === "deleted" ||
                                      row.type === "modified") &&
                                      "bg-red-50 text-red-500",
                                  )}
                                >
                                  {row.beforeNumber ?? ""}
                                </div>
                                <pre
                                  className={cn(
                                    "min-h-6 overflow-x-auto border-r border-gray-100 px-3 py-0.5 whitespace-pre-wrap text-gray-700",
                                    row.type === "deleted" &&
                                      "bg-red-50 text-red-700",
                                    row.type === "modified" &&
                                      "bg-red-50 text-red-700",
                                    row.type === "added" && "bg-gray-50",
                                  )}
                                >
                                  {row.beforeText || " "}
                                </pre>
                                <div
                                  className={cn(
                                    "border-r border-gray-100 px-2 py-0.5 text-right text-gray-400 select-none",
                                    (row.type === "added" ||
                                      row.type === "modified") &&
                                      "bg-emerald-50 text-emerald-600",
                                  )}
                                >
                                  {row.afterNumber ?? ""}
                                </div>
                                <pre
                                  className={cn(
                                    "min-h-6 overflow-x-auto px-3 py-0.5 whitespace-pre-wrap text-gray-700",
                                    row.type === "added" &&
                                      "bg-emerald-50 text-emerald-700",
                                    row.type === "modified" &&
                                      "bg-emerald-50 text-emerald-700",
                                    row.type === "deleted" && "bg-gray-50",
                                  )}
                                >
                                  {row.afterText || " "}
                                </pre>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  </div>
                )}
              </section>
            </div>
            <DialogFooter className="shrink-0 border-t border-gray-100 bg-white px-5 py-3">
              <div className="flex w-full items-center justify-between gap-3">
                <div className="min-w-0 truncate text-xs text-gray-500">
                  {selectedVersionSeq !== null ? (
                    <>
                      当前选择：
                      <span className="font-medium text-gray-700">
                        v{selectedVersionSeq}
                      </span>
                      <span className="mx-2 text-gray-300">/</span>
                      {versionChanges.length} 个变动文件
                    </>
                  ) : (
                    "从左侧选择版本后可恢复"
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    variant="ghost"
                    className="text-gray-500"
                    onClick={() => setVersionsOpen(false)}
                  >
                    关闭
                  </Button>
                  <Button
                    disabled={
                      selectedVersionSeq === null ||
                      restoreVersionMutation.isPending
                    }
                    onClick={() => {
                      if (selectedVersionSeq === null) return;
                      void handleRestoreVersion(selectedVersionSeq);
                    }}
                    className="min-w-32"
                  >
                    {restoreVersionMutation.isPending ? (
                      <Loader2Icon className="size-4 animate-spin" />
                    ) : (
                      <RotateCcwIcon className="size-4" />
                    )}
                    恢复此版本
                  </Button>
                </div>
              </div>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={reviewOpen} onOpenChange={setReviewOpen}>
          <DialogContent className="flex h-[88vh] max-w-none flex-col overflow-hidden p-0 sm:max-w-7xl">
            <DialogHeader className="shrink-0 border-b border-gray-100 px-5 py-4">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <DialogTitle>审查变动文件</DialogTitle>
                  <p className="mt-1 text-sm text-gray-500">
                    选择单个文件确认，或一次性确认全部变动。
                  </p>
                </div>
                <Badge
                  variant="outline"
                  className="mr-6 rounded-full bg-gray-50 px-3 py-1 text-xs font-medium text-gray-600"
                >
                  {changes.length} 个待确认
                </Badge>
              </div>
            </DialogHeader>
            <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)] bg-white">
              <aside className="flex min-h-0 flex-col border-r border-gray-100">
                <div className="flex h-12 shrink-0 items-center justify-between border-b border-gray-100 px-4">
                  <span className="text-sm font-medium text-gray-900">
                    文件列表
                  </span>
                  <span className="text-xs text-gray-400">
                    {changes.length} files
                  </span>
                </div>
                <ScrollArea className="h-full">
                  <div className="space-y-1.5 p-3">
                    {changes.map((change) => {
                      const displayPath = getReviewDisplayPath(
                        change.path,
                        pathSkillName,
                      );
                      return (
                        <button
                          key={change.path}
                          type="button"
                          title={change.path}
                          className={cn(
                            "grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors",
                            selectedChange?.path === change.path
                              ? "border-sky-200 bg-sky-50 shadow-xs"
                              : "border-transparent hover:border-gray-100 hover:bg-gray-50",
                          )}
                          onClick={() => setSelectedChangePath(change.path)}
                        >
                          <span className="min-w-0 overflow-x-auto font-mono text-xs whitespace-nowrap text-gray-800 [scrollbar-width:thin]">
                            {displayPath}
                          </span>
                          <Badge
                            variant="outline"
                            className={cn(
                              "shrink-0",
                              change.type === "added" &&
                                "border-emerald-200 text-emerald-700",
                              change.type === "deleted" &&
                                "border-red-200 text-red-700",
                              change.type === "modified" &&
                                "border-amber-200 text-amber-700",
                            )}
                          >
                            {changeLabel(change.type)}
                          </Badge>
                        </button>
                      );
                    })}
                  </div>
                </ScrollArea>
              </aside>

              <section className="flex min-h-0 min-w-0 flex-col">
                <div className="flex h-14 shrink-0 items-center gap-3 border-b border-gray-100 px-4">
                  <div className="min-w-0">
                    <div
                      className="truncate text-sm font-medium text-gray-900"
                      title={selectedChange?.path}
                    >
                      {selectedChange
                        ? getReviewDisplayPath(
                            selectedChange.path,
                            pathSkillName,
                          )
                        : "未选择文件"}
                    </div>
                    <div className="mt-0.5 text-xs text-gray-400">
                      左侧为应用前，右侧为应用后
                    </div>
                  </div>
                </div>
                <ScrollArea className="min-h-0 flex-1 bg-[#fbfbfb]">
                  <div className="min-w-[920px]">
                    <div className="grid grid-cols-[56px_minmax(0,1fr)_56px_minmax(0,1fr)] border-b border-gray-200 bg-gray-50 font-mono text-xs font-medium text-gray-500">
                      <div className="border-r border-gray-200 px-2 py-2 text-right">
                        旧
                      </div>
                      <div className="border-r border-gray-200 px-3 py-2">
                        应用前
                      </div>
                      <div className="border-r border-gray-200 px-2 py-2 text-right">
                        新
                      </div>
                      <div className="px-3 py-2">应用后</div>
                    </div>
                    {selectedChangeDiffRows.length === 0 ? (
                      <div className="p-10 text-center text-sm text-gray-400">
                        暂无可展示的文本差异
                      </div>
                    ) : (
                      <div className="grid grid-cols-[56px_minmax(0,1fr)_56px_minmax(0,1fr)] font-mono text-xs leading-5">
                        {selectedChangeDiffRows.map((row) => (
                          <div key={row.key} className="contents">
                            <div
                              className={cn(
                                "border-r border-gray-100 px-2 py-0.5 text-right text-gray-400 select-none",
                                (row.type === "deleted" ||
                                  row.type === "modified") &&
                                  "bg-red-50 text-red-500",
                              )}
                            >
                              {row.beforeNumber ?? ""}
                            </div>
                            <pre
                              className={cn(
                                "min-h-6 overflow-x-auto border-r border-gray-100 px-3 py-0.5 whitespace-pre-wrap text-gray-700",
                                row.type === "deleted" &&
                                  "bg-red-50 text-red-700",
                                row.type === "modified" &&
                                  "bg-red-50 text-red-700",
                                row.type === "added" && "bg-gray-50",
                              )}
                            >
                              {row.beforeText || " "}
                            </pre>
                            <div
                              className={cn(
                                "border-r border-gray-100 px-2 py-0.5 text-right text-gray-400 select-none",
                                (row.type === "added" ||
                                  row.type === "modified") &&
                                  "bg-emerald-50 text-emerald-600",
                              )}
                            >
                              {row.afterNumber ?? ""}
                            </div>
                            <pre
                              className={cn(
                                "min-h-6 overflow-x-auto px-3 py-0.5 whitespace-pre-wrap text-gray-700",
                                row.type === "added" &&
                                  "bg-emerald-50 text-emerald-700",
                                row.type === "modified" &&
                                  "bg-emerald-50 text-emerald-700",
                                row.type === "deleted" && "bg-gray-50",
                              )}
                            >
                              {row.afterText || " "}
                            </pre>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </ScrollArea>
              </section>
            </div>
            <DialogFooter className="shrink-0 border-t border-gray-100 bg-white px-5 py-3">
              <div className="flex w-full items-center justify-between gap-3">
                <div className="min-w-0 text-xs text-gray-500">
                  {selectedChange ? (
                    <>
                      当前：
                      <span className="font-medium text-gray-700">
                        {getReviewDisplayPath(
                          selectedChange.path,
                          pathSkillName,
                        )}
                      </span>
                    </>
                  ) : (
                    "请选择一个文件查看差异"
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    variant="ghost"
                    className="text-gray-500"
                    onClick={() => setReviewOpen(false)}
                  >
                    取消
                  </Button>
                  <Button
                    variant="outline"
                    disabled={isApplying || !selectedChange}
                    onClick={() => {
                      if (!selectedChange) return;
                      void applyChanges(selectedChange);
                    }}
                  >
                    {isApplying ? (
                      <Loader2Icon className="size-4 animate-spin" />
                    ) : (
                      <CheckIcon className="size-4" />
                    )}
                    确认当前文件
                  </Button>
                  <Button
                    disabled={isApplying}
                    onClick={() => void applyChanges()}
                    className="min-w-28"
                  >
                    {isApplying ? (
                      <Loader2Icon className="size-4 animate-spin" />
                    ) : (
                      <CheckIcon className="size-4" />
                    )}
                    全部确认
                  </Button>
                </div>
              </div>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </ThreadContext.Provider>
  );
}
