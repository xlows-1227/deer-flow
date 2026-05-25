import {
  CheckCircle2Icon,
  CircleIcon,
  FileCode2Icon,
  FileIcon,
  FileImageIcon,
  FileSpreadsheetIcon,
  FolderIcon,
  ListTodoIcon,
  LoaderCircleIcon,
  PanelRightCloseIcon,
  RotateCwIcon,
} from "lucide-react";
import { useEffect, useMemo } from "react";

import { Button } from "@/components/ui/button";
import { useSandboxFiles, type SandboxFileInfo } from "@/core/sandbox";
import type { Todo } from "@/core/todos";
import { getFileName } from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { ArtifactFileDetail, useArtifacts } from "../artifacts";
import { useThread } from "../messages/context";

const SOURCE_LABELS: Record<string, string> = {
  workspace: "工作区",
  uploads: "上传",
  outputs: "输出",
  "user-data": "Sandbox",
};

const STATUS_LABELS: Record<NonNullable<Todo["status"]>, string> = {
  pending: "待执行",
  in_progress: "当前",
  completed: "已完成",
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

function StepStatusIcon({ status }: { status: Todo["status"] }) {
  if (status === "completed") {
    return <CheckCircle2Icon className="size-4 text-emerald-600" />;
  }
  if (status === "in_progress") {
    return <LoaderCircleIcon className="size-4 animate-spin text-blue-600" />;
  }
  return <CircleIcon className="size-4 text-slate-300" />;
}

function ExecutionSteps({ todos }: { todos: Todo[] }) {
  const completed = todos.filter((todo) => todo.status === "completed").length;
  const currentIndex = todos.findIndex((todo) => todo.status === "in_progress");

  return (
    <section className="min-h-0">
      <div className="mb-2 flex items-center justify-between px-1">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
          <ListTodoIcon className="size-4 text-slate-500" />
          执行步骤
        </div>
        {todos.length > 0 && (
          <span className="text-xs text-slate-500">
            {completed}/{todos.length}
          </span>
        )}
      </div>
      {todos.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-200 bg-white px-3 py-4 text-center text-xs text-slate-500">
          Pro/Ultra 模式开始规划后，这里会显示当前步骤。
        </div>
      ) : (
        <div className="space-y-1.5">
          {todos.map((todo, index) => {
            const status = todo.status ?? "pending";
            const isCurrent =
              status === "in_progress" ||
              (currentIndex === -1 &&
                status === "pending" &&
                index === completed);
            return (
              <div
                key={`${todo.content ?? "step"}-${index}`}
                className={cn(
                  "flex gap-2 rounded-md border px-2.5 py-2 text-sm",
                  isCurrent
                    ? "border-blue-200 bg-blue-50"
                    : "border-slate-200 bg-white",
                )}
              >
                <StepStatusIcon status={isCurrent ? "in_progress" : status} />
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 text-slate-800">
                    {todo.content ?? `步骤 ${index + 1}`}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {isCurrent ? "当前" : STATUS_LABELS[status]}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function SandboxFileList({
  files,
  selectedPath,
  onSelect,
}: {
  files: SandboxFileInfo[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="mb-2 flex items-center justify-between px-1">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
          <FolderIcon className="size-4 text-slate-500" />
          Sandbox 文件
        </div>
        <span className="text-xs text-slate-500">{files.length}</span>
      </div>
      {files.length === 0 ? (
        <div className="flex min-h-24 items-center justify-center rounded-md border border-dashed border-slate-200 bg-white px-3 text-center text-xs text-slate-500">
          当前对话还没有可预览文件。
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          <div className="space-y-1">
            {files.map((file) => {
              const Icon = fileIcon(file);
              const selected = selectedPath === file.path;
              return (
                <button
                  key={file.path}
                  type="button"
                  onClick={() => onSelect(file.path)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md border px-2 py-2 text-left transition-colors",
                    selected
                      ? "border-blue-200 bg-blue-50"
                      : "border-transparent bg-white hover:border-slate-200 hover:bg-slate-50",
                  )}
                >
                  <Icon className="size-4 shrink-0 text-slate-500" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-800">
                      {file.name}
                    </span>
                    <span className="block truncate text-xs text-slate-500">
                      {sourceLabel(file.source)} / {shortPath(file.path)}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">
                    {formatFileSize(file.size)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
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

export function ConversationWorkspacePanel({
  threadId,
  onClose,
}: {
  threadId: string;
  onClose: () => void;
}) {
  const { thread } = useThread();
  const { data, error, isFetching, refetch } = useSandboxFiles(threadId);
  const {
    selectedArtifact,
    select: selectArtifact,
    setArtifacts,
  } = useArtifacts();

  const files = useMemo(
    () =>
      mergeSandboxAndArtifacts(
        data?.files ?? [],
        thread.values.artifacts ?? [],
      ),
    [data?.files, thread.values.artifacts],
  );
  const filePaths = useMemo(() => files.map((file) => file.path), [files]);

  useEffect(() => {
    setArtifacts(filePaths);
  }, [filePaths, setArtifacts]);

  useEffect(() => {
    if (
      filePaths.length > 0 &&
      (!selectedArtifact ||
        (!selectedArtifact.startsWith("write-file:") &&
          !filePaths.includes(selectedArtifact)))
    ) {
      selectArtifact(filePaths[0]!, true);
    }
  }, [filePaths, selectArtifact, selectedArtifact]);

  const selectedFilePath = selectedArtifact?.startsWith("write-file:")
    ? null
    : selectedArtifact;

  return (
    <div className="flex size-full min-w-0 flex-col border-l border-slate-200 bg-slate-50">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-3">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-slate-900">
            对话工作区
          </h2>
          <p className="truncate text-xs text-slate-500">
            当前 sandbox 文件和执行进度
          </p>
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => void refetch()}
            disabled={isFetching}
          >
            <RotateCwIcon className={cn(isFetching && "animate-spin")} />
          </Button>
          <Button size="icon-sm" variant="ghost" onClick={onClose}>
            <PanelRightCloseIcon />
          </Button>
        </div>
      </header>
      <div className="flex min-h-0 flex-[0_0_42%] flex-col gap-4 overflow-hidden border-b border-slate-200 p-3">
        <div className="max-h-[45%] min-h-0 overflow-y-auto pr-1">
          <ExecutionSteps todos={thread.values.todos ?? []} />
        </div>
        <SandboxFileList
          files={files}
          selectedPath={selectedFilePath}
          onSelect={(path) => selectArtifact(path)}
        />
        {error && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            无法读取 sandbox 文件列表，仍会显示已记录的 artifacts。
          </div>
        )}
        {data?.truncated && (
          <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
            文件较多，当前只显示前 {files.length} 个。
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 bg-white p-3">
        {selectedFilePath ? (
          <ArtifactFileDetail
            className="size-full overflow-hidden rounded-md border border-slate-200"
            filepath={selectedFilePath}
            threadId={threadId}
          />
        ) : (
          <div className="flex size-full items-center justify-center rounded-md border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
            选择一个文件后在这里预览。
          </div>
        )}
      </div>
    </div>
  );
}
