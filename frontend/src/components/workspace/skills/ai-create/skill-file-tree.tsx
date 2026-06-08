"use client";

import {
  ChevronRightIcon,
  FileCode2Icon,
  FileIcon,
  FilePlusIcon,
  FileQuestionIcon,
  FileTextIcon,
  FolderIcon,
  FolderOpenIcon,
  FolderPlusIcon,
  PencilIcon,
  RefreshCwIcon,
  SearchIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { createFileInDirectory } from "./skill-local-draft";
import {
  joinSkillRelativePath,
  resolveUploadRelativePath,
  type SkillFileNode,
} from "./utils";

interface SkillFileRefreshOptions {
  replaceExisting?: boolean;
}

interface SkillFileTreeProps {
  tree: SkillFileNode[];
  selectedPath: string | null;
  selectedType: "file" | "directory" | null;
  expandedPaths: Set<string>;
  highlightedPaths: Set<string>;
  currentDirectory: string;
  isEmpty: boolean;
  isRefreshing?: boolean;
  onSelectFile: (path: string) => void;
  onSelectDirectory: (path: string) => void;
  onToggleDirectory: (path: string) => void;
  onRefresh: (options?: SkillFileRefreshOptions) => void | Promise<void>;
  onCreateFile: (path: string) => Promise<void>;
  onCreateDirectory: (path: string) => Promise<void>;
  onUploadFiles: (entries: { path: string; file: File }[]) => Promise<void>;
  pathExists?: (path: string) => boolean;
  getDirectoryEntryCount: (path: string) => number;
  onDeleteFile: (path: string) => void;
  onDeleteDirectory: (path: string) => void;
  onRename: (
    path: string,
    nextName: string,
    type: "file" | "directory",
  ) => Promise<boolean>;
  isProtectedPath?: (path: string, type: "file" | "directory") => boolean;
}

function getFileIcon(name: string) {
  if (name.endsWith(".md")) return FileTextIcon;
  if (name.endsWith(".sh") || name.endsWith(".py") || name.endsWith(".ts")) {
    return FileCode2Icon;
  }
  return FileIcon;
}

function TreeNode({
  node,
  depth,
  selectedPath,
  selectedType,
  expandedPaths,
  highlightedPaths,
  onSelectFile,
  onSelectDirectory,
  onToggleDirectory,
  onRequestDelete,
  onRequestRename,
  isProtectedPath,
}: {
  node: SkillFileNode;
  depth: number;
  selectedPath: string | null;
  selectedType: "file" | "directory" | null;
  expandedPaths: Set<string>;
  highlightedPaths: Set<string>;
  onSelectFile: (path: string) => void;
  onSelectDirectory: (path: string) => void;
  onToggleDirectory: (path: string) => void;
  onRequestDelete: (node: SkillFileNode) => void;
  onRequestRename: (node: SkillFileNode) => void;
  isProtectedPath?: (path: string, type: "file" | "directory") => boolean;
}) {
  const isDirectory = node.type === "directory";
  const isProtected = isProtectedPath?.(node.path, node.type) ?? false;
  const isExpanded = isDirectory && expandedPaths.has(node.path);
  const isSelected =
    selectedPath === node.path &&
    (isDirectory ? selectedType === "directory" : selectedType === "file");
  const isHighlighted = highlightedPaths.has(node.path);
  const Icon = isDirectory
    ? isExpanded
      ? FolderOpenIcon
      : FolderIcon
    : getFileIcon(node.name);

  return (
    <>
      <div
        className={cn(
          "group flex h-8 w-full items-center gap-0.5 rounded-md pr-0.5 transition-colors",
          isSelected
            ? "bg-sky-50 text-sky-700"
            : "text-gray-700 hover:bg-gray-100",
          isHighlighted && !isSelected && "bg-amber-50/80",
        )}
      >
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1 rounded-md px-2 text-left text-sm"
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
          onClick={() => {
            if (isDirectory) {
              onSelectDirectory(node.path);
              onToggleDirectory(node.path);
              return;
            }
            onSelectFile(node.path);
          }}
        >
          {isDirectory ? (
            <ChevronRightIcon
              className={cn(
                "size-3.5 shrink-0 text-gray-400 transition-transform duration-200",
                isExpanded && "rotate-90",
              )}
            />
          ) : (
            <span className="size-3.5 shrink-0" />
          )}
          <Icon
            className={cn(
              "size-4 shrink-0",
              isSelected ? "text-sky-600" : "text-gray-400",
            )}
          />
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
          {isHighlighted ? (
            <span className="rounded bg-amber-100 px-1.5 text-[10px] text-amber-600">
              新
            </span>
          ) : null}
        </button>
        {!isProtected ? (
          <div className="flex shrink-0 items-center opacity-0 transition-opacity group-hover:opacity-100">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="size-7 text-gray-400 hover:text-gray-700"
              aria-label={
                isDirectory
                  ? `重命名文件夹 ${node.name}`
                  : `重命名文件 ${node.name}`
              }
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onRequestRename(node);
              }}
            >
              <PencilIcon className="size-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="size-7 text-gray-400 hover:text-red-600"
              aria-label={
                isDirectory
                  ? `删除文件夹 ${node.name}`
                  : `删除文件 ${node.name}`
              }
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onRequestDelete(node);
              }}
            >
              <Trash2Icon className="size-3.5" />
            </Button>
          </div>
        ) : null}
      </div>
      {isDirectory && isExpanded
        ? node.children?.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              selectedType={selectedType}
              expandedPaths={expandedPaths}
              highlightedPaths={highlightedPaths}
              onSelectFile={onSelectFile}
              onSelectDirectory={onSelectDirectory}
              onToggleDirectory={onToggleDirectory}
              onRequestDelete={onRequestDelete}
              onRequestRename={onRequestRename}
              isProtectedPath={isProtectedPath}
            />
          ))
        : null}
    </>
  );
}

function EmptyState({
  onCreateFile,
  onUploadFiles,
}: {
  onCreateFile: () => void;
  onUploadFiles: () => void;
}) {
  return (
    <div className="mx-3 mb-3 flex flex-1 flex-col items-center justify-center rounded-lg border border-dashed border-gray-200 bg-gray-50/40 px-4 py-8 text-center">
      <FileQuestionIcon className="mb-3 size-10 text-gray-300" />
      <p className="text-sm font-medium text-gray-700">暂无项目文件</p>
      <p className="mt-1 text-xs leading-5 text-gray-400">
        您可以通过智能体创建、手动创建或上传本地文件
      </p>
      <div className="mt-5 flex flex-col items-center gap-2">
        <Button className="h-9 min-w-28 px-5" onClick={onCreateFile}>
          新建文件
        </Button>
        <Button
          variant="outline"
          className="h-9 min-w-28 bg-white px-5"
          onClick={onUploadFiles}
        >
          上传文件
        </Button>
      </div>
    </div>
  );
}

export function SkillFileTree({
  tree,
  selectedPath,
  selectedType,
  expandedPaths,
  highlightedPaths,
  currentDirectory,
  isEmpty,
  isRefreshing,
  onSelectFile,
  onSelectDirectory,
  onToggleDirectory,
  onRefresh,
  onCreateFile,
  onCreateDirectory,
  onUploadFiles,
  pathExists,
  getDirectoryEntryCount,
  onDeleteFile,
  onDeleteDirectory,
  onRename,
  isProtectedPath,
}: SkillFileTreeProps) {
  const [query, setQuery] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  const [newFileOpen, setNewFileOpen] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [deleteFolderTarget, setDeleteFolderTarget] = useState<{
    path: string;
    entryCount: number;
  } | null>(null);
  const [renameTarget, setRenameTarget] = useState<{
    path: string;
    type: "file" | "directory";
    name: string;
  } | null>(null);
  const [pendingUploadEntries, setPendingUploadEntries] = useState<
    { path: string; file: File }[] | null
  >(null);
  const [uploadConflictPaths, setUploadConflictPaths] = useState<string[]>([]);
  const [newFileName, setNewFileName] = useState("");
  const [newFolderName, setNewFolderName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const directoryFileInputRef = useRef<HTMLInputElement | null>(null);

  const filteredTree = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return tree;

    function filterNodes(nodes: SkillFileNode[]): SkillFileNode[] {
      return nodes.flatMap((node) => {
        if (node.type === "file") {
          return node.name.toLowerCase().includes(normalized) ||
            node.path.toLowerCase().includes(normalized)
            ? [node]
            : [];
        }
        const children = filterNodes(node.children ?? []);
        if (
          node.name.toLowerCase().includes(normalized) ||
          children.length > 0
        ) {
          return [{ ...node, children }];
        }
        return [];
      });
    }

    return filterNodes(tree);
  }, [query, tree]);

  function openNewFileDialog() {
    setNewFileName("");
    setNewFileOpen(true);
  }

  function openNewFolderDialog() {
    setNewFolderName("");
    setNewFolderOpen(true);
  }

  async function submitNewFile() {
    const path = createFileInDirectory(currentDirectory, newFileName);
    if (!path) return;
    setIsSubmitting(true);
    try {
      await onCreateFile(path);
      setNewFileOpen(false);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitNewFolder() {
    const path = joinSkillRelativePath(currentDirectory, newFolderName);
    if (!path) return;
    setIsSubmitting(true);
    try {
      await onCreateDirectory(path);
      setNewFolderOpen(false);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitUploadEntries(entries: { path: string; file: File }[]) {
    setIsSubmitting(true);
    try {
      await onUploadFiles(entries);
      await onRefresh({ replaceExisting: false });
    } finally {
      setIsSubmitting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (folderInputRef.current) folderInputRef.current.value = "";
      if (directoryFileInputRef.current) {
        directoryFileInputRef.current.value = "";
      }
    }
  }

  async function handleSelectedFiles(
    fileList: FileList | null,
    targetDirectory = currentDirectory,
  ) {
    if (!fileList?.length) return;
    const entries = [...fileList].map((file) => ({
      file,
      path: resolveUploadRelativePath(file, targetDirectory),
    }));
    const conflictPaths = [
      ...new Set(
        entries.map((entry) => entry.path).filter((path) => pathExists?.(path)),
      ),
    ];
    if (conflictPaths.length > 0) {
      setPendingUploadEntries(entries);
      setUploadConflictPaths(conflictPaths);
      return;
    }
    await submitUploadEntries(entries);
  }

  const toolbarDisabled = isSubmitting;
  const uploadTargetDirectory =
    selectedType === "directory" && selectedPath
      ? selectedPath
      : currentDirectory;

  function openRename(node: SkillFileNode) {
    if (isProtectedPath?.(node.path, node.type)) return;
    setRenameTarget({
      path: node.path,
      type: node.type,
      name: node.name,
    });
  }

  async function submitRename() {
    if (!renameTarget) return;
    setIsSubmitting(true);
    try {
      const success = await onRename(
        renameTarget.path,
        renameTarget.name,
        renameTarget.type,
      );
      if (success) {
        setRenameTarget(null);
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  function requestDelete(node: SkillFileNode) {
    if (isProtectedPath?.(node.path, node.type)) return;
    if (node.type === "file") {
      onDeleteFile(node.path);
      return;
    }
    const entryCount = getDirectoryEntryCount(node.path);
    if (entryCount > 0) {
      setDeleteFolderTarget({ path: node.path, entryCount });
      return;
    }
    onDeleteDirectory(node.path);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => void handleSelectedFiles(event.target.files)}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => void handleSelectedFiles(event.target.files)}
        // @ts-expect-error non-standard attributes for directory upload
        webkitdirectory=""
        directory=""
      />
      <input
        ref={directoryFileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) =>
          void handleSelectedFiles(event.target.files, uploadTargetDirectory)
        }
      />

      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <span className="text-sm font-medium text-gray-900">文件</span>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-7 text-gray-500"
            onClick={() => setShowSearch((value) => !value)}
            aria-label="搜索文件"
            disabled={isEmpty}
          >
            <SearchIcon className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-7 text-gray-500"
            disabled={toolbarDisabled}
            onClick={openNewFileDialog}
            aria-label="新建文件"
          >
            <FilePlusIcon className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-7 text-gray-500"
            disabled={toolbarDisabled}
            onClick={openNewFolderDialog}
            aria-label="新建文件夹"
          >
            <FolderPlusIcon className="size-3.5" />
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className="size-7 text-gray-500"
                disabled={toolbarDisabled}
                aria-label="上传"
              >
                <UploadIcon className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
                上传文件
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => folderInputRef.current?.click()}>
                上传文件夹
              </DropdownMenuItem>
              {selectedType === "directory" && selectedPath ? (
                <DropdownMenuItem
                  onClick={() => directoryFileInputRef.current?.click()}
                >
                  向「{selectedPath}」添加文件
                </DropdownMenuItem>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-7 text-gray-500"
            onClick={() => void onRefresh()}
            aria-label="刷新文件"
            disabled={isRefreshing ? true : isEmpty}
          >
            <RefreshCwIcon
              className={cn("size-3.5", isRefreshing && "animate-spin")}
            />
          </Button>
        </div>
      </div>

      {showSearch && !isEmpty ? (
        <div className="px-3 pb-2">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索文件名"
            className="h-8 bg-white text-xs"
          />
        </div>
      ) : null}

      {isEmpty ? (
        <EmptyState
          onCreateFile={openNewFileDialog}
          onUploadFiles={() => fileInputRef.current?.click()}
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
          {filteredTree.length === 0 ? (
            <div className="flex h-full items-center justify-center px-4 py-8 text-center text-xs text-gray-400">
              没有匹配的文件
            </div>
          ) : (
            filteredTree.map((node) => (
              <TreeNode
                key={node.path}
                node={node}
                depth={0}
                selectedPath={selectedPath}
                selectedType={selectedType}
                expandedPaths={expandedPaths}
                highlightedPaths={highlightedPaths}
                onSelectFile={onSelectFile}
                onSelectDirectory={onSelectDirectory}
                onToggleDirectory={onToggleDirectory}
                onRequestDelete={requestDelete}
                onRequestRename={openRename}
                isProtectedPath={isProtectedPath}
              />
            ))
          )}
        </div>
      )}

      <Dialog
        open={deleteFolderTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteFolderTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>删除文件夹</DialogTitle>
            <DialogDescription>
              确定删除「{deleteFolderTarget?.path}」吗？将同时删除其中的{" "}
              {deleteFolderTarget?.entryCount} 项内容，此操作无法撤销。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteFolderTarget(null)}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (!deleteFolderTarget) return;
                onDeleteDirectory(deleteFolderTarget.path);
                setDeleteFolderTarget(null);
              }}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={pendingUploadEntries !== null && uploadConflictPaths.length > 0}
        onOpenChange={(open) => {
          if (!open) {
            setPendingUploadEntries(null);
            setUploadConflictPaths([]);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>确认覆盖文件</DialogTitle>
            <DialogDescription>
              以下文件已存在，继续上传将覆盖当前草稿中的内容。
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-48 overflow-y-auto rounded-md border border-gray-100 bg-gray-50 p-2">
            {uploadConflictPaths.map((path) => (
              <div
                key={path}
                className="truncate rounded px-2 py-1 font-mono text-xs text-gray-700"
                title={path}
              >
                {path}
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setPendingUploadEntries(null);
                setUploadConflictPaths([]);
              }}
              disabled={isSubmitting}
            >
              取消
            </Button>
            <Button
              onClick={async () => {
                if (!pendingUploadEntries) return;
                const entries = pendingUploadEntries;
                setPendingUploadEntries(null);
                setUploadConflictPaths([]);
                await submitUploadEntries(entries);
              }}
              disabled={isSubmitting}
            >
              覆盖上传
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={renameTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRenameTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {renameTarget?.type === "directory"
                ? "重命名文件夹"
                : "重命名文件"}
            </DialogTitle>
          </DialogHeader>
          <label className="block space-y-2">
            <span className="text-xs font-medium text-gray-600">新名称</span>
            <Input
              value={renameTarget?.name ?? ""}
              onChange={(event) =>
                setRenameTarget((current) =>
                  current ? { ...current, name: event.target.value } : current,
                )
              }
              placeholder={
                renameTarget?.type === "directory"
                  ? "例如 docs"
                  : "例如 notes.md"
              }
              autoFocus
              onKeyDown={(event) => {
                if (event.key === "Enter") void submitRename();
              }}
            />
          </label>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRenameTarget(null)}
              disabled={isSubmitting}
            >
              取消
            </Button>
            <Button
              onClick={() => void submitRename()}
              disabled={!renameTarget?.name.trim() || isSubmitting}
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={newFileOpen} onOpenChange={setNewFileOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>在「{currentDirectory}」下新建文件</DialogTitle>
          </DialogHeader>
          <label className="block space-y-2">
            <span className="text-xs font-medium text-gray-600">文件名</span>
            <Input
              value={newFileName}
              onChange={(event) => setNewFileName(event.target.value)}
              placeholder="例如 notes.md"
              autoFocus
              onKeyDown={(event) => {
                if (event.key === "Enter") void submitNewFile();
              }}
            />
          </label>
          <DialogFooter>
            <Button
              onClick={() => void submitNewFile()}
              disabled={!newFileName.trim() || isSubmitting}
            >
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={newFolderOpen} onOpenChange={setNewFolderOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>在「{currentDirectory}」下新建文件夹</DialogTitle>
          </DialogHeader>
          <label className="block space-y-2">
            <span className="text-xs font-medium text-gray-600">文件夹名</span>
            <Input
              value={newFolderName}
              onChange={(event) => setNewFolderName(event.target.value)}
              placeholder="例如 docs"
              autoFocus
              onKeyDown={(event) => {
                if (event.key === "Enter") void submitNewFolder();
              }}
            />
          </label>
          <DialogFooter>
            <Button
              onClick={() => void submitNewFolder()}
              disabled={!newFolderName.trim() || isSubmitting}
            >
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
