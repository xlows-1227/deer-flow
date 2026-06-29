"use client";

import {
  ChevronRightIcon,
  DownloadIcon,
  FileArchiveIcon,
  FileAudioIcon,
  FileImageIcon,
  FileTextIcon,
  FolderIcon,
  FolderPlusIcon,
  Grid2X2Icon,
  ListIcon,
  LoaderCircleIcon,
  MessageSquareIcon,
  MoreHorizontalIcon,
  SearchIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getBackendBaseURL } from "@/core/config";
import {
  createUserFolder,
  deleteUserFile,
  threadUploadDownloadUrl,
  uploadUserFiles,
  useAllUserFiles,
  useUserFileUploadConfig,
  useUserFolders,
  userFileUrl,
} from "@/core/files";
import type { UserFileItem, UserFileTypeFilter } from "@/core/files";
import { deleteUploadedFile } from "@/core/uploads/api";
import { cn } from "@/lib/utils";

type ViewMode = "list" | "grid";

/** Filter for the existing "来源" dropdown (uploaded vs generated). */
type SourceFilter = "all" | "uploaded" | "generated";

const sourceLabels: Record<SourceFilter, string> = {
  all: "所有来源",
  uploaded: "已上传",
  generated: "已生成",
};

/**
 * Filter for the new "位置" dropdown. Splits items between the user's
 * document library (`/api/files`) and per-thread chat uploads
 * (`/api/threads/{id}/uploads`). The two stores are stitched together
 * by {@link useAllUserFiles} so the page can render a unified list.
 */
type SourceKindFilter = "all" | "library" | "thread";

const sourceKindLabels: Record<SourceKindFilter, string> = {
  all: "全部位置",
  library: "资料库",
  thread: "聊天对话",
};

const typeLabels: Record<UserFileTypeFilter, string> = {
  all: "全部",
  folder: "文件夹",
  document: "文档",
  image: "图片",
  audio: "音频",
  other: "其他",
};

const IMAGE_EXTENSIONS = new Set([
  ".avif",
  ".bmp",
  ".gif",
  ".heic",
  ".ico",
  ".jpeg",
  ".jpg",
  ".png",
  ".svg",
  ".tiff",
  ".webp",
]);

const AUDIO_EXTENSIONS = new Set([
  ".aac",
  ".aiff",
  ".ape",
  ".flac",
  ".m4a",
  ".mp3",
  ".ogg",
  ".wav",
  ".wma",
]);

const DOCUMENT_EXTENSIONS = new Set([
  ".csv",
  ".doc",
  ".docx",
  ".md",
  ".pdf",
  ".txt",
  ".xls",
  ".xlsx",
]);

function formatSize(size: number) {
  if (!size) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function normalizeExtension(extension: string) {
  const normalized = extension.trim().toLowerCase();
  if (!normalized) return "";
  return normalized.startsWith(".") ? normalized : `.${normalized}`;
}

function fileType(item: UserFileItem): UserFileTypeFilter {
  if (item.kind === "folder") return "folder";
  const extension = normalizeExtension(item.extension);
  if (item.mime_type?.startsWith("image/")) return "image";
  if (IMAGE_EXTENSIONS.has(extension)) return "image";
  if (item.mime_type?.startsWith("audio/")) return "audio";
  if (AUDIO_EXTENSIONS.has(extension)) return "audio";
  if (DOCUMENT_EXTENSIONS.has(extension)) return "document";
  return "other";
}

function isImageFile(item: UserFileItem) {
  if (item.mime_type?.startsWith("image/")) return true;
  return IMAGE_EXTENSIONS.has(normalizeExtension(item.extension));
}

function resolveBackendUrl(url: string) {
  if (/^(?:[a-z][a-z\d+\-.]*:)?\/\//i.test(url)) return url;
  const backendBaseUrl = getBackendBaseURL();
  if (!backendBaseUrl || !url.startsWith("/")) return url;
  return `${backendBaseUrl}${url}`;
}

function previewImageSrc(item: UserFileItem) {
  if (!isImageFile(item)) return null;
  if (item.preview_url) return resolveBackendUrl(item.preview_url);
  if (!item.source_thread_id) return userFileUrl(item.path);
  return null;
}

function FileGlyph({ item }: { item: UserFileItem }) {
  const type = fileType(item);
  const iconClass = "size-5";
  const imageSrc = previewImageSrc(item);
  if (imageSrc) {
    return (
      <img src={imageSrc} alt="" className="size-10 rounded-md object-cover" />
    );
  }
  const Icon =
    type === "folder"
      ? FolderIcon
      : type === "image"
        ? FileImageIcon
        : type === "audio"
          ? FileAudioIcon
          : type === "document"
            ? FileTextIcon
            : FileArchiveIcon;
  return (
    <div
      className={cn(
        "flex size-10 items-center justify-center rounded-md bg-white text-gray-500",
        type === "folder" && "bg-amber-50 text-amber-600",
      )}
    >
      <Icon className={iconClass} />
    </div>
  );
}

function FolderBreadcrumb({
  folderPath,
  onOpen,
}: {
  folderPath: string;
  onOpen: (path: string) => void;
}) {
  const parts = folderPath ? folderPath.split("/") : [];
  return (
    <div className="flex min-h-6 flex-wrap items-center gap-1 text-sm text-gray-500">
      <button
        type="button"
        className={cn(!folderPath && "font-medium text-gray-950")}
        onClick={() => onOpen("")}
      >
        全部文件
      </button>
      {parts.map((part, index) => {
        const path = parts.slice(0, index + 1).join("/");
        return (
          <span key={path} className="flex items-center gap-1">
            <ChevronRightIcon className="size-3.5" />
            <button
              type="button"
              className={cn(
                index === parts.length - 1 && "font-medium text-gray-950",
              )}
              onClick={() => onOpen(path)}
            >
              {part}
            </button>
          </span>
        );
      })}
    </div>
  );
}

export default function WorkspaceFilesPage() {
  const [folderPath, setFolderPath] = useState("");
  const [source, setSource] = useState<SourceFilter>("all");
  const [sourceKind, setSourceKind] = useState<SourceKindFilter>("all");
  const [type, setType] = useState<UserFileTypeFilter>("all");
  const [query, setQuery] = useState("");
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [folderDialogOpen, setFolderDialogOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [savingFolder, setSavingFolder] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [uploadFolderPath, setUploadFolderPath] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { folders, refetch: refetchFolders } = useUserFolders();
  const { config: uploadConfig } = useUserFileUploadConfig();

  // Library filters (folder/source/q) drive the backend request for
  // the library half. The `type` switch is library-only and we apply
  // it client-side on the merged list below; thread uploads are filtered
  // client-side too because they live across many threads and we'd
  // rather avoid per-thread re-requests when the user toggles a filter.
  const {
    files: rawItems,
    isLoading,
    refetch,
  } = useAllUserFiles({
    folder_path: folderPath,
    source: source === "all" ? "all" : source,
    q: query,
  });

  // Apply the `sourceKind` switch, the `type` filter, and the
  // case-insensitive `q` search to the merged list. The library
  // backend already filters by `q` and `source`, but the thread half
  // is matched client-side, so we re-apply `q` to both for a uniform
  // experience. `type` and `sourceKind` are applied purely on the
  // client because the hook only forwards library-shape filters to
  // the backend.
  const items = useMemo<UserFileItem[]>(() => {
    const q = query.trim().toLowerCase();
    return rawItems.filter((item) => {
      if (sourceKind === "library" && item.source_thread_id) {
        return false;
      }
      if (sourceKind === "thread" && !item.source_thread_id) {
        return false;
      }
      if (q && !item.name.toLowerCase().includes(q)) {
        return false;
      }
      if (item.kind === "file" && source !== "all" && item.source !== source) {
        return false;
      }
      if (type !== "all" && fileType(item) !== type) {
        return false;
      }
      return true;
    });
  }, [rawItems, query, source, sourceKind, type]);

  const stats = useMemo(() => {
    const files = items.filter((item) => item.kind === "file").length;
    const folders = items.length - files;
    const fromThreads = rawItems.filter((item) => item.source_thread_id).length;
    return { files, folders, fromThreads };
  }, [items, rawItems]);

  const handleFileSelection = (fileList: FileList | null) => {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;
    if (fileInputRef.current) fileInputRef.current.value = "";

    const oversized = uploadConfig
      ? files.find((file) => file.size > uploadConfig.max_upload_bytes)
      : null;
    if (oversized && uploadConfig) {
      toast.error(
        `「${oversized.name}」超过单文件 ${uploadConfig.max_upload_label} 的上传限制`,
      );
      return;
    }

    setPendingUploadFiles(files);
    setUploadDialogOpen(true);
  };

  const handleUpload = async () => {
    if (pendingUploadFiles.length === 0) return;
    setUploading(true);
    try {
      await uploadUserFiles(pendingUploadFiles, uploadFolderPath);
      toast.success(`已上传 ${pendingUploadFiles.length} 个文件`);
      setUploadDialogOpen(false);
      setPendingUploadFiles([]);
      setFolderPath(uploadFolderPath);
      setSourceKind("library");
      setSource("uploaded");
      setType("all");
      setQuery("");
      void refetch();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "上传文件失败");
    } finally {
      setUploading(false);
    }
  };

  const handleCreateFolder = async () => {
    if (!folderName.trim()) return;
    setSavingFolder(true);
    try {
      await createUserFolder(folderName.trim(), folderPath);
      toast.success("文件夹已创建");
      setFolderDialogOpen(false);
      setFolderName("");
      void refetch();
      void refetchFolders();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "新建文件夹失败");
    } finally {
      setSavingFolder(false);
    }
  };

  const handleDelete = async (item: UserFileItem) => {
    const label = item.source_thread_id
      ? `对话「${item.source_thread_title ?? item.source_thread_id.slice(0, 8)}」中的「${item.name}」`
      : `「${item.name}」`;
    if (!window.confirm(`确定删除${label}吗？`)) return;
    try {
      if (item.source_thread_id) {
        await deleteUploadedFile(item.source_thread_id, item.path);
      } else {
        await deleteUserFile(item.path);
      }
      toast.success("已删除");
      void refetch();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    }
  };

  const openItem = (item: UserFileItem) => {
    if (item.kind === "folder") {
      setFolderPath(item.path);
      setSourceKind("library");
      return;
    }
    // For thread uploads, jump to the source chat so the file is seen
    // in its conversational context (artifact URLs aren't directly
    // browseable for non-SDK clients). For library files, open the
    // managed-file URL straight in a new tab.
    if (item.source_thread_id) {
      window.open(
        `/workspace/chats/${item.source_thread_id}`,
        "_blank",
        "noopener,noreferrer",
      );
      return;
    }
    window.open(userFileUrl(item.path), "_blank", "noopener,noreferrer");
  };

  const renderActions = (item: UserFileItem) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-8 rounded-full">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {item.kind === "file" && !item.source_thread_id && (
          <DropdownMenuItem asChild>
            <a href={userFileUrl(item.path, true)}>
              <DownloadIcon className="size-4" />
              下载
            </a>
          </DropdownMenuItem>
        )}
        {item.kind === "file" && item.source_thread_id && (
          <DropdownMenuItem asChild>
            <a
              href={threadUploadDownloadUrl(item.source_thread_id, item.path)}
              target="_blank"
              rel="noopener noreferrer"
            >
              <DownloadIcon className="size-4" />
              下载
            </a>
          </DropdownMenuItem>
        )}
        {item.kind === "file" && item.source_thread_id && (
          <DropdownMenuItem asChild>
            <Link href={`/workspace/chats/${item.source_thread_id}`}>
              <MessageSquareIcon className="size-4" />
              在对话中查看
            </Link>
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          className="text-red-600 focus:text-red-600"
          onClick={() => void handleDelete(item)}
        >
          <Trash2Icon className="size-4" />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const renderSourceLabel = (item: UserFileItem) => {
    if (item.source_thread_id) {
      const title =
        item.source_thread_title ?? item.source_thread_id.slice(0, 8);
      return (
        <span
          className="text-muted-foreground inline-flex max-w-40 items-center gap-1 truncate text-xs"
          title={`来自对话：${title}`}
        >
          <MessageSquareIcon className="size-3 shrink-0" />
          <span className="truncate">{title}</span>
        </span>
      );
    }
    if (item.source === "generated")
      return <span className="text-gray-500">已生成</span>;
    return <span className="text-gray-500">已上传</span>;
  };

  return (
    <div className="flex size-full flex-col bg-[#fafafa]">
      <header className="shrink-0 px-12 pt-7 pb-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal text-black">
              文件
            </h1>
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <Select
                value={sourceKind}
                onValueChange={(value) => {
                  const next = value as SourceKindFilter;
                  setSourceKind(next);
                  if (next === "thread") {
                    setFolderPath("");
                  }
                }}
              >
                <SelectTrigger className="h-8 w-36 rounded-lg border-gray-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(
                    Object.entries(sourceKindLabels) as [
                      SourceKindFilter,
                      string,
                    ][]
                  ).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={source}
                onValueChange={(value) => setSource(value as SourceFilter)}
              >
                <SelectTrigger className="h-8 w-36 rounded-lg border-gray-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(
                    Object.entries(sourceLabels) as [SourceFilter, string][]
                  ).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={type}
                onValueChange={(value) => setType(value as UserFileTypeFilter)}
              >
                <SelectTrigger className="h-8 w-36 rounded-lg border-gray-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(typeLabels).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="relative">
                <SearchIcon className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-gray-400" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索文件"
                  className="h-8 w-52 rounded-lg border-gray-200 bg-white pl-9"
                />
              </div>
            </div>
          </div>
          <div className="flex flex-col items-end gap-4">
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="ghost"
                className="h-9 px-2 text-gray-900 hover:bg-gray-100"
                onClick={() => setFolderDialogOpen(true)}
              >
                <FolderPlusIcon className="size-4" />
                新建文件夹
              </Button>
              <Button
                type="button"
                className="h-9 rounded-lg bg-black px-4 text-white hover:bg-black/90"
                disabled={uploading}
                onClick={() => {
                  setUploadFolderPath(folderPath);
                  fileInputRef.current?.click();
                }}
              >
                {uploading ? (
                  <LoaderCircleIcon className="size-4 animate-spin" />
                ) : (
                  <UploadIcon className="size-4" />
                )}
                上传
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => handleFileSelection(event.target.files)}
              />
            </div>
            <div className="flex rounded-lg border border-gray-200 bg-white p-1">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className={cn(
                  "size-7 rounded-md",
                  viewMode === "list" && "bg-gray-100",
                )}
                onClick={() => setViewMode("list")}
              >
                <ListIcon className="size-4" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className={cn(
                  "size-7 rounded-md",
                  viewMode === "grid" && "bg-gray-100",
                )}
                onClick={() => setViewMode("grid")}
              >
                <Grid2X2Icon className="size-4" />
              </Button>
            </div>
          </div>
        </div>
        <div className="mt-4">
          <FolderBreadcrumb
            folderPath={folderPath}
            onOpen={(path) => {
              setFolderPath(path);
              if (path) setSourceKind("library");
            }}
          />
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-12 pb-8">
        {isLoading ? (
          <div className="flex h-72 items-center justify-center text-sm text-gray-500">
            <LoaderCircleIcon className="mr-2 size-5 animate-spin" />
            正在加载文件
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-80 flex-col items-center justify-center rounded-lg border border-dashed border-gray-200 bg-white text-center">
            <FolderIcon className="size-12 text-gray-300" />
            <p className="mt-4 text-sm font-medium text-gray-950">
              {query.trim() || sourceKind !== "all"
                ? "没有匹配的文件"
                : "当前文件夹为空"}
            </p>
            <p className="mt-2 text-sm text-gray-500">
              {query.trim() || sourceKind !== "all"
                ? "试试调整搜索关键字或筛选条件。"
                : "上传文件、新建文件夹，或在聊天中上传文件后，会显示在这里。"}
            </p>
          </div>
        ) : viewMode === "list" ? (
          <div className="overflow-hidden">
            <div className="grid grid-cols-[minmax(360px,1fr)_120px_80px_180px_44px] border-b border-gray-200 px-3 py-3 text-xs text-gray-500">
              <div>名称</div>
              <div>修改时间</div>
              <div>大小</div>
              <div>位置</div>
              <div />
            </div>
            {items.map((item) => (
              <div
                key={item.id}
                className="grid min-h-15 grid-cols-[minmax(360px,1fr)_120px_80px_180px_44px] items-center px-3 py-2 text-sm hover:bg-white"
              >
                <button
                  type="button"
                  className="flex min-w-0 items-center gap-3 py-1 text-left"
                  onClick={() => openItem(item)}
                >
                  <FileGlyph item={item} />
                  <span
                    className="min-w-0 font-medium break-all whitespace-normal text-black"
                    title={item.name}
                  >
                    {item.name}
                  </span>
                </button>
                <div className="text-gray-500">
                  {formatDate(item.modified_at)}
                </div>
                <div className="text-gray-500">{formatSize(item.size)}</div>
                <div>{renderSourceLabel(item)}</div>
                <div>{renderActions(item)}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5">
            {items.map((item) => (
              <div
                key={item.id}
                className="group rounded-lg border border-gray-200 bg-white p-3"
              >
                <div className="flex items-start justify-between">
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-start gap-3 text-left"
                    onClick={() => openItem(item)}
                  >
                    <FileGlyph item={item} />
                    <span
                      className="min-w-0 text-sm font-medium break-all whitespace-normal text-black"
                      title={item.name}
                    >
                      {item.name}
                    </span>
                  </button>
                  {renderActions(item)}
                </div>
                <div className="mt-4 flex items-center justify-between text-xs text-gray-500">
                  <span>{formatSize(item.size)}</span>
                  {renderSourceLabel(item)}
                </div>
              </div>
            ))}
          </div>
        )}
        {!isLoading && items.length > 0 && (
          <div className="mt-4 text-xs text-gray-400">
            {stats.folders} 个文件夹，{stats.files} 个文件
            {stats.fromThreads > 0 && (
              <span className="ml-1">
                （其中 {stats.fromThreads} 个来自对话）
              </span>
            )}
          </div>
        )}
      </main>

      <Dialog
        open={uploadDialogOpen}
        onOpenChange={(open) => {
          if (uploading) return;
          setUploadDialogOpen(open);
          if (!open) setPendingUploadFiles([]);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>上传文件</DialogTitle>
          </DialogHeader>
          <div className="space-y-5">
            <div className="space-y-2">
              <div className="text-sm font-medium text-gray-900">上传到</div>
              <Select
                value={uploadFolderPath || "__root__"}
                onValueChange={(value) =>
                  setUploadFolderPath(value === "__root__" ? "" : value)
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__root__">全部文件（根目录）</SelectItem>
                  {folders.map((path) => (
                    <SelectItem key={path} value={path}>
                      {path}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-gray-500">
                也可以先进入某个文件夹，再点击上传；默认会选中当前文件夹。
              </p>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium text-gray-900">
                  已选择 {pendingUploadFiles.length} 个文件
                </span>
                <span className="text-gray-500">
                  {formatSize(
                    pendingUploadFiles.reduce(
                      (total, file) => total + file.size,
                      0,
                    ),
                  )}
                </span>
              </div>
              <div className="max-h-40 overflow-y-auto rounded-lg border border-gray-200">
                {pendingUploadFiles.map((file) => (
                  <div
                    key={`${file.name}-${file.size}-${file.lastModified}`}
                    className="flex items-center justify-between gap-4 border-b border-gray-100 px-3 py-2 text-sm last:border-b-0"
                  >
                    <span className="min-w-0 break-all text-gray-900">
                      {file.name}
                    </span>
                    <span className="shrink-0 text-xs text-gray-500">
                      {formatSize(file.size)}
                    </span>
                  </div>
                ))}
              </div>
              <p className="text-xs text-gray-500">
                {uploadConfig
                  ? `单个文件最大 ${uploadConfig.max_upload_label}`
                  : "正在读取上传大小限制…"}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={uploading}
              onClick={() => {
                setUploadDialogOpen(false);
                setPendingUploadFiles([]);
              }}
            >
              取消
            </Button>
            <Button
              type="button"
              disabled={uploading || pendingUploadFiles.length === 0}
              onClick={() => void handleUpload()}
            >
              {uploading && (
                <LoaderCircleIcon className="size-4 animate-spin" />
              )}
              确认上传
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={folderDialogOpen} onOpenChange={setFolderDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>新建文件夹</DialogTitle>
          </DialogHeader>
          <Input
            value={folderName}
            onChange={(event) => setFolderName(event.target.value)}
            placeholder="文件夹名称"
            onKeyDown={(event) => {
              if (event.key === "Enter") void handleCreateFolder();
            }}
          />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setFolderDialogOpen(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              disabled={savingFolder || !folderName.trim()}
              onClick={() => void handleCreateFolder()}
            >
              {savingFolder && (
                <LoaderCircleIcon className="size-4 animate-spin" />
              )}
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
