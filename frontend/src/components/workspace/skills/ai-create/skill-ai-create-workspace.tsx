"use client";

import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, Loader2Icon, SparklesIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Textarea } from "@/components/ui/textarea";
import { ThreadContext } from "@/components/workspace/messages/context";
import {
  parseSkillMarkdown,
  resolveSkillDisplayName,
  syncSkillDisplayFrontmatter,
  formatSkillValidationError,
  validateSkillMarkdownContent,
} from "@/components/workspace/skills/skill-create-utils";
import { useHighlightTimeout } from "@/components/workspace/skills/use-highlight-timeout";
import { useI18n } from "@/core/i18n/hooks";
import { useThreadSettings } from "@/core/settings";
import {
  createCustomSkill,
  createCustomSkillDirectory,
  listCustomSkillFiles,
  readCustomSkillFile,
  uploadCustomSkillFiles,
  writeCustomSkillFile,
} from "@/core/skills/api";
import {
  useCustomSkill,
  useCustomSkills,
  useEnableSkill,
  useUpdateCustomSkill,
} from "@/core/skills/hooks";
import { ensureSkillSessionThreadMetadata } from "@/core/threads/api";
import { useThreadStream } from "@/core/threads/hooks";
import type { AgentThreadState } from "@/core/threads/types";
import { THREAD_SOURCE_SKILL_SESSION } from "@/core/threads/utils";
import { uuid } from "@/core/utils/uuid";

import { SkillAiCreateSessionHistory } from "./skill-ai-create-session-history";
import {
  getSkillAiCreateSession,
  markSkillAiCreateSessionPublished,
  upsertSkillAiCreateSession,
} from "./skill-ai-create-sessions";
import { SkillConversationPanel } from "./skill-conversation-panel";
import { SkillFileTree } from "./skill-file-tree";
import type { OpenFileTab } from "./skill-file-viewer";
import { SkillFileViewer } from "./skill-file-viewer";
import {
  addLocalDirectory,
  addLocalFile,
  buildTreeFromLocalDraft,
  convertWorkspaceBinariesToServer,
  convertWorkspaceDraftToServerDraft,
  isReadableSkillServerFile,
  buildRenamedChildPath,
  countDirectoryEntries,
  createEmptyLocalDraft,
  draftPathExists,
  findWorkspaceSkillMdPath,
  getCurrentDirectory,
  isLocalDraftEmpty,
  loadLocalDraft,
  mergeServerEntriesIntoDraft,
  publishLocalDraft,
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
  type SkillLocalBinaryFile,
  type SkillLocalDraft,
} from "./skill-local-draft";
import { SkillQuickActions } from "./skill-quick-actions";
import { importThreadOutputsIntoDraft } from "./skill-thread-import";
import {
  extractSkillManageName,
  extractSkillManageServerPath,
  resolveSkillConversationTopic,
} from "./utils";

const TEXT_FILE_PATTERN =
  /\.(md|txt|json|ya?ml|sh|py|ts|js|css|html|csv|xml)$/i;

function isTextUploadPath(path: string, file: File) {
  return (
    TEXT_FILE_PATTERN.test(path) ||
    file.type.startsWith("text/") ||
    file.type === "application/json"
  );
}

function isPathUnderDirectory(path: string, directoryPath: string) {
  const normalized = path.replace(/\\/g, "/");
  const prefix = directoryPath.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!prefix) return false;
  return normalized === prefix || normalized.startsWith(`${prefix}/`);
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

export function SkillAiCreateWorkspace({
  initialThreadId,
  initialIsNewThread = true,
}: {
  initialThreadId?: string;
  initialIsNewThread?: boolean;
} = {}) {
  const { t } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [threadId, setThreadId] = useState(() => initialThreadId ?? uuid());
  const [isNewThread, setIsNewThread] = useState(initialIsNewThread);
  const [isWelcomeMode, setIsWelcomeMode] = useState(initialIsNewThread);
  const [activeSkillName, setActiveSkillName] = useState<string | null>(null);
  const [openTabs, setOpenTabs] = useState<OpenFileTab[]>([]);
  const [activeTabPath, setActiveTabPath] = useState<string | null>(null);
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const [highlightedPaths, setHighlightedPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const highlightPaths = useHighlightTimeout();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isCompleting, setIsCompleting] = useState(false);
  const [isRefreshingFiles, setIsRefreshingFiles] = useState(false);
  const [isWorkspaceHydrated, setIsWorkspaceHydrated] =
    useState(initialIsNewThread);
  const [settingsName, setSettingsName] = useState("");
  const [settingsDescription, setSettingsDescription] = useState("");
  const [localDraft, setLocalDraft] = useState<SkillLocalDraft>(() =>
    createEmptyLocalDraft(),
  );
  const [binaryFiles, setBinaryFiles] = useState<SkillLocalBinaryFile[]>([]);
  const [selectedTreePath, setSelectedTreePath] = useState<string | null>(null);
  const [selectedTreeType, setSelectedTreeType] = useState<
    "file" | "directory" | null
  >(null);
  const [hasPublished, setHasPublished] = useState(false);
  const importedSkillRef = useRef<string | null>(null);
  const activeSkillNameRef = useRef<string | null>(null);
  const hydratedThreadRef = useRef<string | null>(null);
  const localDraftRef = useRef(localDraft);
  const binaryFilesRef = useRef(binaryFiles);
  const openTabsRef = useRef(openTabs);
  const threadRef = useRef<BaseStream<AgentThreadState> | null>(null);
  const isWorkspaceHydratedRef = useRef(isWorkspaceHydrated);
  const isNewThreadRef = useRef(isNewThread);
  const runThreadFileImportRef = useRef<
    (options?: { replaceExisting?: boolean }) => Promise<string[]>
  >(async () => []);
  const [settings] = useThreadSettings(threadId);

  isWorkspaceHydratedRef.current = isWorkspaceHydrated;
  isNewThreadRef.current = isNewThread;

  localDraftRef.current = localDraft;
  binaryFilesRef.current = binaryFiles;
  openTabsRef.current = openTabs;

  useEffect(() => {
    activeSkillNameRef.current = activeSkillName;
  }, [activeSkillName]);

  useEffect(() => {
    if (!isWorkspaceHydrated) return;
    saveLocalDraft(threadId, localDraft);
  }, [isWorkspaceHydrated, localDraft, threadId]);

  const skillContext = useMemo(
    () => ({
      ...settings.context,
      mode: settings.context.mode ?? "flash",
      skill_name: "skill-creator",
    }),
    [settings.context],
  );

  const { skills: customSkills, refetch: refetchCustomSkills } =
    useCustomSkills({
      refetchInterval: false,
    });

  const { skill, refetch: refetchSkill } = useCustomSkill(activeSkillName);
  const { mutateAsync: updateSkill, isPending: isSavingSkill } =
    useUpdateCustomSkill();
  const { mutateAsync: enableSkillMutation } = useEnableSkill();

  const fileTree = useMemo(
    () => buildTreeFromLocalDraft(localDraft, binaryFiles),
    [binaryFiles, localDraft],
  );
  const draftIsEmpty =
    isLocalDraftEmpty(localDraft) && binaryFiles.length === 0;
  const currentDirectory = useMemo(
    () => getCurrentDirectory(selectedTreePath, selectedTreeType),
    [selectedTreePath, selectedTreeType],
  );

  const importServerSkillIntoDraft = useCallback(
    async (
      skillName: string,
      options: { force?: boolean; replaceExisting?: boolean } = {},
    ) => {
      if (!options.force && importedSkillRef.current === skillName) {
        return null;
      }
      try {
        const entries = await listCustomSkillFiles(skillName);
        const textEntries = entries.filter(
          (entry) =>
            entry.type === "file" && isReadableSkillServerFile(entry.path),
        );
        const contents: Record<string, string> = {};
        await Promise.all(
          textEntries.map(async (entry) => {
            try {
              const response = await readCustomSkillFile(skillName, entry.path);
              contents[entry.path] = response.content;
            } catch {
              // Ignore unreadable files during import.
            }
          }),
        );
        setLocalDraft((current) =>
          mergeServerEntriesIntoDraft(
            { ...current, skillName },
            entries,
            contents,
            { replaceExisting: options.replaceExisting },
          ),
        );
        importedSkillRef.current = skillName;
        const workspaceContents = Object.fromEntries(
          Object.entries(contents).map(([serverPath, content]) => [
            serverPathToWorkspacePath(serverPath),
            content,
          ]),
        );
        return { entries, contents: workspaceContents };
      } catch {
        setLocalDraft((current) => ({ ...current, skillName }));
        return null;
      }
    },
    [],
  );

  const restoreWorkspaceFromThread = useCallback(
    (nextThreadId: string) => {
      const draft = loadLocalDraft(nextThreadId) ?? createEmptyLocalDraft();
      const session = getSkillAiCreateSession(nextThreadId);
      const skillMdPath = findWorkspaceSkillMdPath(draft);

      setThreadId(nextThreadId);
      setIsNewThread(false);
      setIsWelcomeMode(false);
      setLocalDraft(draft);
      setBinaryFiles([]);
      setHasPublished(session?.published ?? false);
      setActiveSkillName(session?.skillName ?? draft.skillName ?? null);
      importedSkillRef.current = null;

      if (skillMdPath) {
        const content = draft.files[skillMdPath] ?? "";
        setOpenTabs([{ path: skillMdPath, content, dirty: false }]);
        setActiveTabPath(skillMdPath);
        setSelectedTreePath(skillMdPath);
        setSelectedTreeType("file");
      } else {
        setOpenTabs([]);
        setActiveTabPath(null);
        setSelectedTreePath(null);
        setSelectedTreeType(null);
      }

      setExpandedPaths(
        expandPathAncestors([
          ...draft.directories,
          ...Object.keys(draft.files),
        ]),
      );
      setHighlightedPaths(new Set());
      setIsWorkspaceHydrated(true);

      const skillName = session?.skillName ?? draft.skillName;
      if (skillName && isLocalDraftEmpty(draft)) {
        void importServerSkillIntoDraft(skillName);
      }

      hydratedThreadRef.current = nextThreadId;
    },
    [importServerSkillIntoDraft],
  );

  useEffect(() => {
    if (!initialThreadId) return;
    if (hydratedThreadRef.current === initialThreadId) return;

    if (threadId !== initialThreadId) {
      if (isWorkspaceHydrated) {
        saveLocalDraft(threadId, localDraft);
      }
    }
    restoreWorkspaceFromThread(initialThreadId);
  }, [
    initialThreadId,
    isWorkspaceHydrated,
    localDraft,
    restoreWorkspaceFromThread,
    threadId,
  ]);

  useEffect(() => {
    if (isNewThread) return;
    void ensureSkillSessionThreadMetadata(threadId).then(() => {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    });
  }, [isNewThread, queryClient, threadId]);

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
      setLocalDraft((current) => {
        saveLocalDraft(createdThreadId, current);
        return current;
      });
      setThreadId(createdThreadId);
      setIsNewThread(false);
      history.replaceState(
        null,
        "",
        `/workspace/skills/ai-create/${createdThreadId}`,
      );
    },
    onToolEnd: (event) => {
      if (
        event.name === "write_file" ||
        event.name === "present_files" ||
        event.name === "str_replace"
      ) {
        void runThreadFileImportRef.current({ replaceExisting: true });
      }

      if (event.name !== "skill_manage") return;
      const skillName = extractSkillManageName(event);
      if (!skillName) return;

      setActiveSkillName(skillName);
      importedSkillRef.current = null;
      const highlightServerPath = extractSkillManageServerPath(event);

      void (async () => {
        const snapshot = await importServerSkillIntoDraft(skillName, {
          force: true,
          replaceExisting: true,
        });
        if (!snapshot) return;

        const workspacePaths = snapshot.entries.map((entry) =>
          serverPathToWorkspacePath(entry.path),
        );
        setExpandedPaths((current) => {
          const next = new Set(current);
          expandPathAncestors(workspacePaths).forEach((item) => next.add(item));
          return next;
        });

        const highlightPath = highlightServerPath
          ? serverPathToWorkspacePath(highlightServerPath)
          : findWorkspaceSkillMdPath({
              skillName,
              directories: [],
              files: snapshot.contents,
            });
        if (highlightPath) {
          highlightPaths(setHighlightedPaths, new Set([highlightPath]));
        }
        void runThreadFileImportRef.current({ replaceExisting: true });
      })();
    },
    onFinish: () => {
      void runThreadFileImportRef.current({ replaceExisting: true });
      const skillName = activeSkillNameRef.current;
      if (!skillName) return;
      importedSkillRef.current = null;
      void importServerSkillIntoDraft(skillName, {
        force: true,
        replaceExisting: true,
      });
    },
  });

  threadRef.current = thread;

  const runThreadFileImport = useCallback(
    async (options: { replaceExisting?: boolean } = {}) => {
      const currentThread = threadRef.current;
      if (
        !isWorkspaceHydratedRef.current ||
        isNewThreadRef.current ||
        !currentThread
      ) {
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
          draft: localDraftRef.current,
          binaries: binaryFilesRef.current,
          options: {
            replaceExisting: options.replaceExisting,
            preservePaths,
          },
        });
        if (result.importedPaths.length === 0) {
          return [];
        }

        setLocalDraft(result.draft);
        setBinaryFiles(result.binaries);
        setOpenTabs((current) =>
          current.map((tab) => {
            if (tab.dirty || preservePaths.has(tab.path)) return tab;
            const content = result.draft.files[tab.path];
            if (content === undefined) return tab;
            return { ...tab, content, dirty: false };
          }),
        );
        setExpandedPaths((current) => {
          const next = new Set(current);
          expandPathAncestors(result.importedPaths).forEach((item) =>
            next.add(item),
          );
          return next;
        });
        highlightPaths(setHighlightedPaths, new Set(result.importedPaths));
        return result.importedPaths;
      } catch {
        return [];
      }
    },
    [threadId],
  );

  runThreadFileImportRef.current = runThreadFileImport;

  const threadSyncKey = useMemo(
    () =>
      `${thread.messages.length}:${(thread.values?.artifacts ?? []).join("\u0000")}`,
    [thread.messages.length, thread.values?.artifacts],
  );

  useEffect(() => {
    if (!isWorkspaceHydrated || isNewThread) return;
    void runThreadFileImport();
  }, [isNewThread, isWorkspaceHydrated, runThreadFileImport, threadSyncKey]);

  const openFile = useCallback(
    (path: string, contentOverride?: string) => {
      const existing = openTabs.find((tab) => tab.path === path);
      if (existing) {
        if (contentOverride !== undefined) {
          setOpenTabs((current) =>
            current.map((tab) =>
              tab.path === path
                ? { ...tab, content: contentOverride, dirty: false }
                : tab,
            ),
          );
        }
        setActiveTabPath(path);
        setSelectedTreePath(path);
        setSelectedTreeType("file");
        return;
      }

      if (!(path in localDraft.files)) {
        const isBinary = binaryFiles.some((entry) => entry.path === path);
        if (isBinary) {
          toast.info("二进制文件将在发布后写入 Skill 目录");
          return;
        }
      }

      const content = contentOverride ?? readLocalFileContent(localDraft, path);
      setOpenTabs((current) => [...current, { path, content, dirty: false }]);
      setActiveTabPath(path);
      setSelectedTreePath(path);
      setSelectedTreeType("file");
    },
    [binaryFiles, localDraft, openTabs],
  );

  const workspaceSkillMdPath = useMemo(
    () => findWorkspaceSkillMdPath(localDraft),
    [localDraft],
  );

  useEffect(() => {
    const skillMd = workspaceSkillMdPath
      ? localDraft.files[workspaceSkillMdPath]
      : undefined;
    if (skillMd) {
      const parsed = parseSkillMarkdown(skillMd);
      setSettingsName(parsed.displayName);
      setSettingsDescription(parsed.descriptionZh);
      return;
    }
    setSettingsName(localDraft.displayName ?? "");
    setSettingsDescription(localDraft.descriptionZh ?? "");
  }, [
    localDraft.descriptionZh,
    localDraft.displayName,
    localDraft.files,
    workspaceSkillMdPath,
  ]);

  useEffect(() => {
    if (initialIsNewThread || thread.messages.length === 0) return;
    setIsWelcomeMode(false);
  }, [initialIsNewThread, thread.messages.length]);

  const handleSelectFile = useCallback(
    (path: string) => {
      openFile(path);
    },
    [openFile],
  );

  const handleSelectDirectory = useCallback((path: string) => {
    setSelectedTreePath(path);
    setSelectedTreeType("directory");
  }, []);

  const handleCloseTab = useCallback(
    (path: string) => {
      setOpenTabs((current) => current.filter((tab) => tab.path !== path));
      if (activeTabPath === path) {
        const remaining = openTabs.filter((tab) => tab.path !== path);
        setActiveTabPath(remaining.at(-1)?.path ?? null);
      }
    },
    [activeTabPath, openTabs],
  );

  const handleChangeContent = useCallback((path: string, content: string) => {
    setOpenTabs((current) =>
      current.map((tab) =>
        tab.path === path ? { ...tab, content, dirty: true } : tab,
      ),
    );
  }, []);

  const handleSaveFile = useCallback(
    (path: string) => {
      const tab = openTabs.find((item) => item.path === path);
      if (!tab) return;

      setLocalDraft((current) =>
        updateLocalFileContent(current, path, tab.content),
      );
      setOpenTabs((current) =>
        current.map((item) =>
          item.path === path ? { ...item, dirty: false } : item,
        ),
      );
      toast.success("已保存到对话草稿");
    },
    [openTabs],
  );

  const handleRefreshFile = useCallback(
    (path: string) => {
      const content = readLocalFileContent(localDraft, path);
      setOpenTabs((current) =>
        current.map((tab) =>
          tab.path === path ? { ...tab, content, dirty: false } : tab,
        ),
      );
    },
    [localDraft],
  );

  const handleRefreshWorkspace = useCallback(
    async (options: { replaceExisting?: boolean } = {}) => {
      const replaceExisting = options.replaceExisting ?? true;

      setIsRefreshingFiles(true);
      try {
        await runThreadFileImport({ replaceExisting });

        if (!activeSkillName) {
          for (const tab of openTabs) {
            if (tab.dirty) handleRefreshFile(tab.path);
          }
          return;
        }

        const snapshot = await importServerSkillIntoDraft(activeSkillName, {
          force: true,
          replaceExisting,
        });
        if (!snapshot) return;

        setExpandedPaths((current) => {
          const next = new Set(current);
          expandPathAncestors(
            snapshot.entries.map((entry) =>
              serverPathToWorkspacePath(entry.path),
            ),
          ).forEach((item) => next.add(item));
          return next;
        });

        if (replaceExisting) {
          setOpenTabs((current) =>
            current.map((tab) => {
              if (
                Object.prototype.hasOwnProperty.call(
                  snapshot.contents,
                  tab.path,
                )
              ) {
                return {
                  ...tab,
                  content: snapshot.contents[tab.path] ?? "",
                  dirty: false,
                };
              }
              if (tab.dirty) {
                return {
                  ...tab,
                  content: readLocalFileContent(localDraft, tab.path),
                  dirty: false,
                };
              }
              return tab;
            }),
          );
        }
      } finally {
        setIsRefreshingFiles(false);
      }
    },
    [
      activeSkillName,
      handleRefreshFile,
      importServerSkillIntoDraft,
      localDraft,
      openTabs,
      runThreadFileImport,
    ],
  );

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      const sendPromise = sendMessage(threadId, message);
      if (message.files.length > 0) {
        return sendPromise;
      }
      void sendPromise;
    },
    [sendMessage, threadId],
  );

  const parsedSkillMd = useMemo(
    () =>
      parseSkillMarkdown(
        workspaceSkillMdPath
          ? (localDraft.files[workspaceSkillMdPath] ?? "")
          : "",
      ),
    [localDraft.files, workspaceSkillMdPath],
  );

  const displayName = useMemo(
    () =>
      resolveSkillDisplayName(
        parsedSkillMd,
        settingsName,
        localDraft.displayName,
      ),
    [localDraft.displayName, parsedSkillMd, settingsName],
  );

  const sessionTitle = useMemo(() => {
    const topic = resolveSkillConversationTopic(thread);
    if (topic !== "未命名话题") return topic;
    if (displayName !== "未命名技能") return displayName;
    return topic;
  }, [displayName, thread]);

  const handleComplete = useCallback(async () => {
    const skillMdPath = findWorkspaceSkillMdPath(localDraft);
    if (!skillMdPath) {
      toast.error("请在 skills 目录下创建 SKILL.md");
      return;
    }

    const dirtyTab = openTabs.find((tab) => tab.dirty);
    if (dirtyTab) {
      toast.error("请先保存未提交的编辑内容");
      return;
    }

    setIsCompleting(true);
    try {
      let draftForPublish: SkillLocalDraft = {
        ...localDraft,
        displayName: settingsName.trim(),
        descriptionZh: settingsDescription.trim(),
      };

      const skillMdContent =
        openTabs.find((tab) => tab.path === skillMdPath)?.content ??
        localDraft.files[skillMdPath] ??
        "";
      const syncedContent = syncSkillDisplayFrontmatter({
        content: skillMdContent,
        displayName: settingsName,
        descriptionZh: settingsDescription,
      });
      draftForPublish = updateLocalFileContent(
        draftForPublish,
        skillMdPath,
        syncedContent,
      );

      const parsed = parseSkillMarkdown(syncedContent);
      const skillName = parsed.name.trim();
      if (!skillName) {
        toast.error("SKILL.md 中缺少有效的 name");
        return;
      }

      if (customSkills.some((skill) => skill.name === skillName)) {
        toast.error(`技能「${skillName}」已存在，请修改 name 后重试`);
        return;
      }

      draftForPublish = reorganizeSkillsIntoNamedFolder(
        draftForPublish,
        skillName,
      );
      setLocalDraft(draftForPublish);

      const serverDraft = convertWorkspaceDraftToServerDraft(
        draftForPublish,
        skillName,
      );
      const serverBinaries = convertWorkspaceBinariesToServer(
        binaryFiles,
        skillName,
      );
      const skillMdForServer = serverDraft.files["SKILL.md"];
      if (!skillMdForServer) {
        toast.error("无法解析 skills 目录下的 SKILL.md");
        return;
      }

      const validation = validateSkillMarkdownContent(
        skillMdForServer,
        skillName,
      );
      if (!validation.valid) {
        toast.error(validation.message);
        return;
      }

      await createCustomSkill({
        name: skillName,
        description:
          parsed.description.trim() ||
          parsed.descriptionZh.trim() ||
          "Custom DeerFlow skill",
        content: skillMdForServer,
      });

      await publishLocalDraft(skillName, serverDraft, serverBinaries, {
        writeFile: (path, content) =>
          writeCustomSkillFile(skillName, path, content).then(() => undefined),
        writeSkillMd: (content) =>
          updateSkill({ skillName, content }).then(() => undefined),
        createDirectory: (path) =>
          createCustomSkillDirectory(skillName, path).then(() => undefined),
        uploadFiles: (entries) =>
          uploadCustomSkillFiles(skillName, entries).then(() => undefined),
      });
      await enableSkillMutation({
        skillName,
        enabled: true,
      });
      await refetchCustomSkills();
      setHasPublished(true);
      markSkillAiCreateSessionPublished(threadId);
      upsertSkillAiCreateSession({
        threadId,
        title: sessionTitle,
        skillName,
        published: true,
      });
      toast.success("Skill 已发布并启用");
      router.push("/workspace/skills");
    } catch (error) {
      toast.error(
        error instanceof Error
          ? formatSkillValidationError(error.message)
          : "发布失败",
      );
    } finally {
      setIsCompleting(false);
    }
  }, [
    binaryFiles,
    customSkills,
    enableSkillMutation,
    localDraft,
    openTabs,
    refetchCustomSkills,
    router,
    sessionTitle,
    threadId,
    updateSkill,
    settingsDescription,
    settingsName,
  ]);

  const handleSaveSettings = useCallback(() => {
    const skillMdPath = findWorkspaceSkillMdPath(localDraft);
    const skillMdContent = skillMdPath
      ? localDraft.files[skillMdPath]
      : undefined;
    const openSkillTab = skillMdPath
      ? openTabs.find((tab) => tab.path === skillMdPath)
      : undefined;

    setLocalDraft((current) => {
      let next: SkillLocalDraft = {
        ...current,
        displayName: settingsName.trim(),
        descriptionZh: settingsDescription.trim(),
      };
      if (skillMdPath && skillMdContent) {
        const nextContent = syncSkillDisplayFrontmatter({
          content: openSkillTab?.content ?? skillMdContent,
          displayName: settingsName,
          descriptionZh: settingsDescription,
        });
        next = updateLocalFileContent(next, skillMdPath, nextContent);
      }
      return next;
    });

    if (skillMdPath && skillMdContent) {
      const nextContent = syncSkillDisplayFrontmatter({
        content: openSkillTab?.content ?? skillMdContent,
        displayName: settingsName,
        descriptionZh: settingsDescription,
      });
      setOpenTabs((current) =>
        current.map((tab) =>
          tab.path === skillMdPath
            ? { ...tab, content: nextContent, dirty: false }
            : tab,
        ),
      );
    }

    toast.success("设置已保存到对话草稿");
    setSettingsOpen(false);
  }, [localDraft, openTabs, settingsDescription, settingsName]);

  useEffect(() => {
    if (!isWorkspaceHydrated) return;
    if (hasPublished) return;
    upsertSkillAiCreateSession({
      threadId,
      title: sessionTitle,
      skillName: activeSkillName ?? localDraft.skillName,
      published: false,
    });
  }, [
    activeSkillName,
    hasPublished,
    isWorkspaceHydrated,
    localDraft.skillName,
    sessionTitle,
    threadId,
  ]);

  const hasDirtyTabs = openTabs.some((tab) => tab.dirty);
  const showDraftBadge =
    !hasPublished && (!draftIsEmpty || hasDirtyTabs || binaryFiles.length > 0);
  const showEditorPanel = openTabs.length > 0;
  const canComplete = !hasPublished && workspaceSkillMdPath !== null;

  const handleCreateFile = useCallback(
    async (path: string) => {
      setLocalDraft((current) => addLocalFile(current, path, ""));
      setExpandedPaths((current) => {
        const next = new Set(current);
        expandPathAncestors([path]).forEach((item) => next.add(item));
        return next;
      });
      highlightPaths(setHighlightedPaths, new Set([path]));
      openFile(path);
    },
    [openFile],
  );

  const handleCreateDirectory = useCallback(
    async (path: string) => {
      setLocalDraft((current) => addLocalDirectory(current, path));
      setExpandedPaths((current) => {
        const next = new Set(current);
        expandPathAncestors([path]).forEach((item) => next.add(item));
        next.add(path);
        return next;
      });
      setSelectedTreePath(path);
      setSelectedTreeType("directory");
      highlightPaths(setHighlightedPaths, new Set([path]));
    },
    [highlightPaths],
  );

  const getDirectoryEntryCount = useCallback(
    (path: string) =>
      countDirectoryEntries(
        localDraft,
        path,
        binaryFiles.map((entry) => entry.path),
      ),
    [binaryFiles, localDraft],
  );

  const handleDeleteFile = useCallback(
    (path: string) => {
      setLocalDraft((current) => removeLocalFile(current, path));
      setBinaryFiles((current) =>
        current.filter((entry) => entry.path !== path),
      );
      setOpenTabs((current) => {
        const remaining = current.filter((tab) => tab.path !== path);
        setActiveTabPath((active) =>
          active === path ? (remaining.at(-1)?.path ?? null) : active,
        );
        return remaining;
      });
      if (selectedTreePath === path && selectedTreeType === "file") {
        setSelectedTreePath(null);
        setSelectedTreeType(null);
      }
      toast.success("已删除文件");
    },
    [selectedTreePath, selectedTreeType],
  );

  const applyRenamedPaths = useCallback(
    (oldPath: string, newPath: string, type: "file" | "directory") => {
      const remap = (path: string) =>
        type === "directory"
          ? remapPathUnderPrefix(path, oldPath, newPath)
          : path === oldPath
            ? newPath
            : path;

      setBinaryFiles((current) =>
        current.map((entry) => ({ ...entry, path: remap(entry.path) })),
      );
      setOpenTabs((current) =>
        current.map((tab) => ({ ...tab, path: remap(tab.path) })),
      );
      setActiveTabPath((current) => (current ? remap(current) : current));
      setSelectedTreePath((current) => (current ? remap(current) : current));
      setExpandedPaths((current) => {
        const next = new Set<string>();
        for (const item of current) {
          next.add(remap(item));
        }
        return next;
      });
      setHighlightedPaths((current) => {
        const next = new Set<string>();
        for (const item of current) {
          next.add(remap(item));
        }
        return next;
      });
    },
    [],
  );

  const handleRename = useCallback(
    async (
      oldPath: string,
      nextName: string,
      type: "file" | "directory",
    ): Promise<boolean> => {
      const newPath = buildRenamedChildPath(oldPath, nextName);
      if (!newPath) {
        toast.error("名称无效，不能包含 /");
        return false;
      }

      const oldNormalized = oldPath
        .replace(/\\/g, "/")
        .replace(/^\/+|\/+$/g, "");
      if (newPath === oldNormalized) {
        return true;
      }

      const otherBinaryPaths = binaryFiles
        .filter((entry) => entry.path !== oldPath)
        .map((entry) => entry.path);
      if (draftPathExists(localDraft, newPath, otherBinaryPaths)) {
        toast.error("该名称已被占用");
        return false;
      }

      if (type === "file") {
        if (oldNormalized in localDraft.files) {
          const nextDraft = renameLocalFilePath(localDraft, oldPath, newPath);
          if (!nextDraft) {
            toast.error("重命名失败");
            return false;
          }
          setLocalDraft(nextDraft);
        }
        applyRenamedPaths(oldPath, newPath, "file");
        toast.success("已重命名文件");
        return true;
      }

      const nextDraft = renameLocalDirectoryPath(localDraft, oldPath, newPath);
      if (!nextDraft) {
        toast.error("重命名失败");
        return false;
      }
      setLocalDraft(nextDraft);
      applyRenamedPaths(oldPath, newPath, "directory");
      toast.success("已重命名文件夹");
      return true;
    },
    [applyRenamedPaths, binaryFiles, localDraft],
  );

  const handleDeleteDirectory = useCallback(
    (path: string) => {
      setLocalDraft((current) => removeLocalDirectory(current, path));
      setBinaryFiles((current) =>
        current.filter((entry) => !isPathUnderDirectory(entry.path, path)),
      );
      setOpenTabs((current) => {
        const remaining = current.filter(
          (tab) => !isPathUnderDirectory(tab.path, path),
        );
        setActiveTabPath((active) =>
          active && isPathUnderDirectory(active, path)
            ? (remaining.at(-1)?.path ?? null)
            : active,
        );
        return remaining;
      });
      if (selectedTreePath && isPathUnderDirectory(selectedTreePath, path)) {
        setSelectedTreePath(null);
        setSelectedTreeType(null);
      }
      setExpandedPaths((current) => {
        const next = new Set<string>();
        for (const item of current) {
          if (!isPathUnderDirectory(item, path)) {
            next.add(item);
          }
        }
        return next;
      });
      toast.success("已删除文件夹");
    },
    [selectedTreePath, selectedTreeType],
  );

  const handleUploadFiles = useCallback(
    async (entries: { path: string; file: File }[]) => {
      if (entries.length === 0) return;

      const textEntries: { path: string; content: string }[] = [];
      const nextBinaries: SkillLocalBinaryFile[] = [];

      await Promise.all(
        entries.map(async (entry) => {
          if (isTextUploadPath(entry.path, entry.file)) {
            textEntries.push({
              path: entry.path,
              content: await entry.file.text(),
            });
            return;
          }
          nextBinaries.push(entry);
        }),
      );

      let nextDraft: SkillLocalDraft = {
        ...localDraftRef.current,
        directories: [...localDraftRef.current.directories],
        files: { ...localDraftRef.current.files },
      };
      for (const entry of textEntries) {
        nextDraft = addLocalFile(nextDraft, entry.path, entry.content);
      }
      localDraftRef.current = nextDraft;
      setLocalDraft(nextDraft);

      if (nextBinaries.length > 0) {
        const merged = new Map(
          binaryFilesRef.current.map((item) => [item.path, item]),
        );
        for (const entry of nextBinaries) {
          merged.set(entry.path, entry);
        }
        const nextBinaryFiles = [...merged.values()];
        binaryFilesRef.current = nextBinaryFiles;
        setBinaryFiles(nextBinaryFiles);
      }

      const uploadedPaths = entries.map((entry) => entry.path);
      setExpandedPaths((current) => {
        const next = new Set(current);
        expandPathAncestors(uploadedPaths).forEach((item) => next.add(item));
        return next;
      });
      highlightPaths(setHighlightedPaths, new Set(uploadedPaths));

      const firstTextPath = textEntries[0]?.path;
      if (firstTextPath) {
        const firstTextEntry = textEntries.find(
          (entry) => entry.path === firstTextPath,
        );
        openFile(firstTextPath, firstTextEntry?.content);
      }
    },
    [openFile],
  );

  return (
    <ThreadContext.Provider value={{ thread }}>
      <div className="flex size-full flex-col bg-[#fafafa]">
        <ResizablePanelGroup
          key={showEditorPanel ? "skill-ai-create-3" : "skill-ai-create-2"}
          id={
            showEditorPanel
              ? "skill-ai-create-panels"
              : "skill-ai-create-panels-2"
          }
          orientation="horizontal"
          className="min-h-0 flex-1"
          defaultLayout={
            showEditorPanel
              ? { files: 22, editor: 43, chat: 35 }
              : { files: 32, chat: 68 }
          }
        >
          <ResizablePanel
            id="files"
            defaultSize="22%"
            minSize="200px"
            maxSize="35%"
            collapsible={false}
            className="min-w-0"
          >
            <aside className="flex h-full min-h-0 flex-col gap-3 p-3">
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="icon-sm"
                  className="size-9 rounded-lg bg-white shadow-xs"
                  asChild
                >
                  <Link href="/workspace/skills" aria-label="返回 Skill 管理">
                    <ArrowLeftIcon className="size-4" />
                  </Link>
                </Button>
                <button
                  type="button"
                  className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 text-left shadow-xs transition-colors hover:bg-gray-50"
                  onClick={() => setSettingsOpen(true)}
                >
                  <div className="flex size-6 shrink-0 items-center justify-center rounded-md bg-violet-50 text-violet-600">
                    <SparklesIcon className="size-3.5" />
                  </div>
                  <span className="truncate text-sm font-medium text-gray-900">
                    {displayName}
                  </span>
                </button>
                {!hasPublished ? (
                  <SkillAiCreateSessionHistory currentThreadId={threadId} />
                ) : null}
              </div>

              <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-gray-200 bg-white">
                <SkillFileTree
                  tree={fileTree}
                  selectedPath={selectedTreePath ?? activeTabPath}
                  selectedType={selectedTreeType}
                  expandedPaths={expandedPaths}
                  highlightedPaths={highlightedPaths}
                  currentDirectory={currentDirectory}
                  isEmpty={draftIsEmpty}
                  isRefreshing={isRefreshingFiles}
                  onSelectFile={handleSelectFile}
                  onSelectDirectory={handleSelectDirectory}
                  onToggleDirectory={(path) =>
                    setExpandedPaths((current) => {
                      const next = new Set(current);
                      if (next.has(path)) next.delete(path);
                      else next.add(path);
                      return next;
                    })
                  }
                  onRefresh={handleRefreshWorkspace}
                  onCreateFile={handleCreateFile}
                  onCreateDirectory={handleCreateDirectory}
                  onUploadFiles={handleUploadFiles}
                  pathExists={(path) =>
                    draftPathExists(
                      localDraft,
                      path,
                      binaryFiles.map((entry) => entry.path),
                    )
                  }
                  getDirectoryEntryCount={getDirectoryEntryCount}
                  onDeleteFile={handleDeleteFile}
                  onDeleteDirectory={handleDeleteDirectory}
                  onRename={handleRename}
                />
              </div>

              <SkillQuickActions
                showDraftBadge={showDraftBadge}
                isCompleting={isCompleting}
                canComplete={canComplete}
                onSettingsClick={() => setSettingsOpen(true)}
                onCompleteClick={() => void handleComplete()}
              />
            </aside>
          </ResizablePanel>

          {showEditorPanel ? (
            <>
              <ResizableHandle withHandle />
              <ResizablePanel
                id="editor"
                defaultSize="43%"
                minSize="30%"
                className="min-w-0"
              >
                <section className="h-full min-h-0 p-3 pl-0">
                  <div className="h-full overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xs">
                    <SkillFileViewer
                      tabs={openTabs}
                      activePath={activeTabPath}
                      isSaving={isSavingSkill}
                      onSelectTab={setActiveTabPath}
                      onCloseTab={handleCloseTab}
                      onChangeContent={handleChangeContent}
                      onSave={(path) => void handleSaveFile(path)}
                      onRefresh={(path) => void handleRefreshFile(path)}
                    />
                  </div>
                </section>
              </ResizablePanel>
            </>
          ) : null}

          <ResizableHandle withHandle />

          <ResizablePanel
            id="chat"
            defaultSize="35%"
            minSize="28%"
            className="min-w-0"
          >
            <section className="h-full min-h-0 p-3 pl-0">
              <div className="h-full overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xs">
                <SkillConversationPanel
                  threadId={threadId}
                  thread={thread}
                  isWelcomeMode={isWelcomeMode}
                  isHistoryLoading={isHistoryLoading}
                  hasMoreHistory={hasMoreHistory}
                  loadMoreHistory={loadMoreHistory}
                  initialPrompt={t.inputBox.createSkillPrompt}
                  disabled={isUploading}
                  showWelcomeSuggestions={false}
                  onSubmit={handleSubmit}
                  onStop={() => void thread.stop()}
                />
              </div>
            </section>
          </ResizablePanel>
        </ResizablePanelGroup>

        <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Skill 设置</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <label className="block space-y-2">
                <span className="text-xs font-medium text-gray-600">
                  显示名称（display_name）
                </span>
                <Input
                  value={settingsName}
                  onChange={(event) => setSettingsName(event.target.value)}
                  placeholder={
                    parsedSkillMd.name
                      ? `未设置时使用：${parsedSkillMd.name}`
                      : "例如：幻灯片生成"
                  }
                />
              </label>
              <label className="block space-y-2">
                <span className="text-xs font-medium text-gray-600">
                  描述（description_zh）
                </span>
                <Textarea
                  value={settingsDescription}
                  onChange={(event) =>
                    setSettingsDescription(event.target.value)
                  }
                  className="min-h-24 resize-none"
                  placeholder={
                    parsedSkillMd.description
                      ? `未设置时使用：${parsedSkillMd.description}`
                      : "简要说明这个 Skill 的用途"
                  }
                />
              </label>
              <p className="text-xs text-gray-500">
                仅保存 display_name 与 description_zh，不会修改 SKILL.md 中的
                name 与 description。
              </p>
              {parsedSkillMd.name ? (
                <p className="text-xs text-gray-500">
                  Skill ID：<code>{parsedSkillMd.name}</code>
                </p>
              ) : null}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setSettingsOpen(false)}>
                取消
              </Button>
              <Button onClick={handleSaveSettings}>保存</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </ThreadContext.Provider>
  );
}
