"use client";

import {
  AlertCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FileCode2Icon,
  FileIcon,
  FileImageIcon,
  FileSpreadsheetIcon,
  FileTextIcon,
  FileXIcon,
  FolderIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  PanelRightCloseIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  buildSandboxFileTree,
  useSandboxFiles,
  type SandboxFileInfo,
  type SandboxFileTreeNode,
} from "@/core/sandbox";
import { getFileName } from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { ArtifactFileDetail, useArtifacts } from "../artifacts";
import { useThread } from "../messages/context";

import { WorkspaceToolExecutionPanel } from "./workspace-tool-execution-panel";

type WorkspaceTab = "files" | "tools";

const SOURCE_LABELS: Record<string, string> = {
  workspace: "工作区",
  uploads: "上传",
  outputs: "输出",
  "user-data": "Sandbox",
};

function formatFileSize(size: number) {
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const unitIndex = Math.min(
    Math.floor(Math.log(size) / Math.log(1024)),
    units.length - 1,
  );
  return `${(size / 1024 ** unitIndex).toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function fileIcon(file: SandboxFileInfo) {
  const ext = (file.extension ?? "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) {
    return FileImageIcon;
  }
  if (["csv", "xls", "xlsx"].includes(ext)) {
    return FileSpreadsheetIcon;
  }
  if (
    [
      "css",
      "html",
      "js",
      "json",
      "md",
      "py",
      "ts",
      "tsx",
      "xml",
      "yaml",
      "yml",
    ].includes(ext)
  ) {
    return FileCode2Icon;
  }
  return FileIcon;
}

function sourceLabel(source: string) {
  return SOURCE_LABELS[source] ?? source;
}

function shortPath(path: string) {
  return path.replace(/^\/mnt\/user-data\/?/, "");
}

function mergeSandboxAndArtifacts(
  sandboxFiles: SandboxFileInfo[],
  artifacts: string[],
) {
  const byPath = new Map<string, SandboxFileInfo>();
  for (const file of sandboxFiles) {
    byPath.set(file.path, file);
  }
  for (const path of artifacts) {
    if (path.startsWith("write-file:") || byPath.has(path)) continue;
    byPath.set(path, {
      path,
      name: getFileName(path),
      size: 0,
      modified_at: 0,
      source: path.includes("/uploads/") ? "uploads" : "outputs",
      extension: getFileName(path).split(".").pop() ?? "",
      mime_type: null,
    });
  }
  return [...byPath.values()].sort((a, b) => {
    const sourceOrder =
      ["workspace", "uploads", "outputs"].indexOf(a.source) -
      ["workspace", "uploads", "outputs"].indexOf(b.source);
    if (sourceOrder !== 0) return sourceOrder;
    return shortPath(a.path).localeCompare(shortPath(b.path));
  });
}

function WorkspaceFileTreeRow({
  node,
  depth,
  selectedPath,
  collapsedPaths,
  onToggleDirectory,
  onSelect,
}: {
  node: SandboxFileTreeNode;
  depth: number;
  selectedPath: string | null;
  collapsedPaths: Set<string>;
  onToggleDirectory: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  if (node.type === "directory") {
    const collapsed = collapsedPaths.has(node.path);
    return (
      <div>
        <button
          type="button"
          onClick={() => onToggleDirectory(node.path)}
          className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-slate-50"
          style={{ paddingLeft: `${8 + depth * 14}px` }}
        >
          {collapsed ? (
            <ChevronRightIcon className="size-3.5 shrink-0 text-slate-400" />
          ) : (
            <ChevronDownIcon className="size-3.5 shrink-0 text-slate-400" />
          )}
          <FolderIcon className="size-4 shrink-0 text-amber-500" />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-700">
            {node.name}
          </span>
          <span className="shrink-0 text-xs text-slate-400">
            {node.children.length}
          </span>
        </button>
        {!collapsed &&
          node.children.map((child) => (
            <WorkspaceFileTreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              collapsedPaths={collapsedPaths}
              onToggleDirectory={onToggleDirectory}
              onSelect={onSelect}
            />
          ))}
      </div>
    );
  }

  const file = node.file;
  if (!file) return null;

  const Icon = fileIcon(file);
  const selected = selectedPath === file.path;
  return (
    <button
      type="button"
      onClick={() => onSelect(file.path)}
      className={cn(
        "flex w-full items-center gap-2 rounded-md border px-2 py-2 text-left transition-colors",
        selected
          ? "border-indigo-200 bg-indigo-50"
          : "border-transparent hover:border-slate-200 hover:bg-slate-50",
      )}
      style={{ paddingLeft: `${22 + depth * 14}px` }}
    >
      <Icon className="size-4 shrink-0 text-slate-500" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-slate-800">
          {file.name}
        </span>
        <span className="block truncate text-xs text-slate-500">
          {sourceLabel(file.source)} · {shortPath(file.path)}
        </span>
      </span>
      <span className="shrink-0 text-xs text-slate-400">
        {formatFileSize(file.size)}
      </span>
    </button>
  );
}

function WorkspaceFileTree({
  files,
  selectedPath,
  isLoading,
  onSelect,
}: {
  files: SandboxFileInfo[];
  selectedPath: string | null;
  isLoading: boolean;
  onSelect: (path: string) => void;
}) {
  const tree = buildSandboxFileTree(files);
  const [collapsedPaths, setCollapsedPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const handleToggleDirectory = useCallback((path: string) => {
    setCollapsedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center p-6">
        <LoaderCircleIcon className="size-5 animate-spin text-blue-600" />
      </div>
    );
  }

  if (files.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="text-center text-slate-400">
          <FileTextIcon className="mx-auto mb-2 size-12" />
          <p className="text-sm">暂无文件</p>
          <p className="mt-1 text-xs">上传文件或让 Agent 在工作区中创建文件</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-0.5 p-1">
      {tree.map((node) => (
        <WorkspaceFileTreeRow
          key={node.path}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          collapsedPaths={collapsedPaths}
          onToggleDirectory={handleToggleDirectory}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function WorkspacePreviewEmpty() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center p-8 text-slate-400">
      <FileXIcon className="mb-2 size-12" />
      <p className="text-sm">选择文件以预览</p>
    </div>
  );
}

export function ConversationWorkspacePanel({
  threadId,
  onCollapse,
}: {
  threadId: string;
  onCollapse: () => void;
}) {
  const { thread } = useThread();
  const { data, error, isFetching, refetch } = useSandboxFiles(threadId);
  const {
    selectedArtifact,
    select: selectArtifact,
    setArtifacts,
  } = useArtifacts();

  const [activeTab, setActiveTab] = useState<WorkspaceTab>("files");
  const [filesSectionOpen, setFilesSectionOpen] = useState(true);
  const [previewSectionOpen, setPreviewSectionOpen] = useState(true);

  const files = useMemo(
    () =>
      mergeSandboxAndArtifacts(
        data?.files ?? [],
        thread.values.artifacts ?? [],
      ),
    [data?.files, thread.values.artifacts],
  );


  const filePaths = useMemo(() => files.map((file) => file.path), [files]);
  const hasAutoSelectedRef = useRef(false);

  // Reset the auto-select guard when the thread changes so that the auto-select
  // effect below runs after the guard is cleared.
  useEffect(() => {
    hasAutoSelectedRef.current = false;
  }, [threadId]);

  useEffect(() => {
    setArtifacts(filePaths);
  }, [filePaths, setArtifacts]);

  useEffect(() => {
    if (
      filePaths.length > 0 &&
      !hasAutoSelectedRef.current &&
      (!selectedArtifact ||
        (!selectedArtifact.startsWith("write-file:") &&
          !filePaths.includes(selectedArtifact)))
    ) {
      hasAutoSelectedRef.current = true;
      selectArtifact(filePaths[0]!, true);
    }
  }, [filePaths, selectArtifact, selectedArtifact]);

  const selectedFilePath = selectedArtifact?.startsWith("write-file:")
    ? null
    : selectedArtifact;

  const handleRefreshFiles = useCallback(() => {
    void refetch();
  }, [refetch]);

  const tabClass = (tab: WorkspaceTab) =>
    cn(
      "rounded-lg px-2.5 py-1.5 text-sm whitespace-nowrap transition-all duration-150",
      activeTab === tab
        ? "bg-white font-medium text-indigo-600 shadow-sm"
        : "text-slate-500 hover:bg-white/60 hover:text-slate-700",
    );

  return (
    <div className="bg-background flex size-full min-w-0 flex-col border-l border-slate-200">
      <header className="bg-background flex shrink-0 items-center justify-between border-b border-slate-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-800">工作空间</h2>
        <button
          type="button"
          onClick={onCollapse}
          className="rounded-lg p-1.5 transition-colors hover:bg-slate-100"
          title="收缩工作空间"
          aria-label="收缩工作空间"
        >
          <PanelRightCloseIcon className="size-4 text-slate-400" />
        </button>
      </header>

      <div className="flex shrink-0 gap-1 border-b border-slate-100 bg-slate-50/50 px-3 py-2">
        <button
          type="button"
          className={tabClass("files")}
          onClick={() => setActiveTab("files")}
        >
          文件管理
        </button>
        <button
          type="button"
          className={tabClass("tools")}
          onClick={() => setActiveTab("tools")}
        >
          执行记录
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {activeTab === "files" && (
          <>
            <div
              className={cn(
                "bg-background flex flex-col border-b border-slate-100",
                !previewSectionOpen && "min-h-0 flex-1",
              )}
            >
              <button
                type="button"
                onClick={() => setFilesSectionOpen((open) => !open)}
                className="flex w-full shrink-0 items-center justify-between border-b border-slate-100 bg-slate-50/50 px-4 py-2.5 text-left transition-colors hover:bg-slate-100/50"
              >
                <span className="flex items-center gap-1.5">
                  {filesSectionOpen ? (
                    <ChevronDownIcon className="size-3.5 shrink-0 text-slate-400" />
                  ) : (
                    <ChevronRightIcon className="size-3.5 shrink-0 text-slate-400" />
                  )}
                  <span className="text-xs font-semibold tracking-wider text-slate-600 uppercase">
                    文件管理
                    {files.length > 0 && ` (${files.length})`}
                  </span>
                </span>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(event) => {
                    event.stopPropagation();
                    handleRefreshFiles();
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      event.stopPropagation();
                      handleRefreshFiles();
                    }
                  }}
                  className={cn(
                    "rounded-md p-1 transition-colors hover:bg-slate-200",
                    isFetching && "cursor-not-allowed opacity-50",
                  )}
                  title="刷新文件"
                  aria-label="刷新文件"
                >
                  <RefreshCwIcon
                    className={cn(
                      "size-3.5 text-slate-400",
                      isFetching && "animate-spin",
                    )}
                  />
                </span>
              </button>

              {filesSectionOpen && (
                <div
                  className={cn(
                    previewSectionOpen
                      ? "max-h-64 min-h-0 overflow-y-auto"
                      : "flex min-h-0 flex-1 flex-col overflow-hidden",
                  )}
                >
                  {error && !isFetching && (
                    <div className="flex items-center gap-2 p-4 text-amber-800">
                      <AlertCircleIcon className="size-4 shrink-0" />
                      <p className="text-sm">
                        无法读取 sandbox 文件列表，仍会显示已记录的 artifacts。
                      </p>
                    </div>
                  )}
                  {data?.truncated && (
                    <div className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
                      文件较多，当前只显示前 {files.length} 个。
                    </div>
                  )}
                  <WorkspaceFileTree
                    files={files}
                    selectedPath={selectedFilePath}
                    isLoading={isFetching && files.length === 0}
                    onSelect={(path) => selectArtifact(path)}
                  />
                </div>
              )}
            </div>

            <div className="flex min-h-0 flex-1 flex-col overflow-hidden border-t border-slate-100">
              <button
                type="button"
                onClick={() => setPreviewSectionOpen((open) => !open)}
                className="flex w-full shrink-0 items-center gap-1.5 border-b border-slate-100 bg-slate-50/50 px-4 py-2.5 text-left transition-colors hover:bg-slate-100/50"
              >
                {previewSectionOpen ? (
                  <ChevronDownIcon className="size-3.5 shrink-0 text-slate-400" />
                ) : (
                  <ChevronRightIcon className="size-3.5 shrink-0 text-slate-400" />
                )}
                <span className="text-xs font-semibold tracking-wider text-slate-600 uppercase">
                  预览
                </span>
              </button>
              {previewSectionOpen && (
                <div className="bg-background min-h-0 flex-1 overflow-hidden">
                  {selectedFilePath ||
                  selectedArtifact?.startsWith("write-file:") ? (
                    <ArtifactFileDetail
                      className="size-full overflow-hidden"
                      filepath={selectedArtifact ?? selectedFilePath!}
                      threadId={threadId}
                    />
                  ) : (
                    <WorkspacePreviewEmpty />
                  )}
                </div>
              )}
            </div>
          </>
        )}

        {activeTab === "tools" && (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="border-b border-slate-100 bg-slate-50/50 px-4 py-2.5">
              <h3 className="text-xs font-semibold tracking-wider text-slate-600 uppercase">
                工具执行记录
              </h3>
            </div>
            <WorkspaceToolExecutionPanel />
          </div>
        )}
      </div>
    </div>
  );
}
