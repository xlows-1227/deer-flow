"use client";

import {
  ChevronRightIcon,
  CopyIcon,
  DownloadIcon,
  FileArchiveIcon,
  FileAudioIcon,
  FileImageIcon,
  FileTextIcon,
  FolderIcon,
  FolderPlusIcon,
  Globe2Icon,
  Grid2X2Icon,
  ListIcon,
  LoaderCircleIcon,
  Link2OffIcon,
  LockIcon,
  MessageSquareIcon,
  MoreHorizontalIcon,
  SearchIcon,
  Share2Icon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Streamdown } from "streamdown";

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
  SYSTEM_FOLDERS,
  SYSTEM_FOLDER_NAMES,
  SHARED_HTML_IFRAME_SANDBOX,
  cancelFilePublication,
  createUserFolder,
  deleteUserFile,
  isReservedSystemFolderPath,
  isPublishableGeneratedHtml,
  loadSharedFileText,
  publishGeneratedHtml,
  shareFileWithUser,
  threadUploadDownloadUrl,
  threadGeneratedFileUrl,
  uploadUserFiles,
  useAllUserFiles,
  useFilePublications,
  useSharedFiles,
  useUserFileUploadConfig,
  useUserFolders,
  userFileUrl,
} from "@/core/files";
import type {
  FilePublicationRecord,
  SystemFileFolder,
  UserFileItem,
  UserFileTypeFilter,
} from "@/core/files";
import { streamdownPlugins } from "@/core/streamdown";
import { deleteUploadedFile } from "@/core/uploads/api";
import { copyTextToClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

type ViewMode = "list" | "grid";

/** Filter for the existing "来源" dropdown (uploaded vs generated). */
type SourceFilter = "all" | "uploaded" | "generated";

const sourceLabels: Record<SourceFilter, string> = {
  all: "所有来源",
  uploaded: "已上传",
  generated: "已生成",
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
  if (item.system_folder) {
    return (
      <div className="relative flex size-10 items-center justify-center rounded-md bg-amber-50 text-amber-600">
        <FolderIcon className={iconClass} />
        <span className="absolute right-0.5 bottom-0.5 flex size-4 items-center justify-center rounded-full bg-white shadow-sm">
          <LockIcon className="size-2.5" />
        </span>
      </div>
    );
  }
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
  systemFolder,
  onOpen,
}: {
  folderPath: string;
  systemFolder: SystemFileFolder | null;
  onOpen: (path: string) => void;
}) {
  const parts = folderPath ? folderPath.split("/") : [];
  return (
    <div className="flex min-h-6 flex-wrap items-center gap-1 text-sm text-gray-500">
      <button
        type="button"
        className={cn(
          !folderPath && !systemFolder && "font-medium text-gray-950",
        )}
        onClick={() => onOpen("")}
      >
        全部文件
      </button>
      {systemFolder && (
        <span className="flex items-center gap-1 font-medium text-gray-950">
          <ChevronRightIcon className="size-3.5" />
          <LockIcon className="size-3.5 text-gray-400" />
          {SYSTEM_FOLDER_NAMES[systemFolder]}
        </span>
      )}
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
  const [systemFolder, setSystemFolder] = useState<SystemFileFolder | null>(
    null,
  );
  const [source, setSource] = useState<SourceFilter>("all");
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
  const [shareItem, setShareItem] = useState<UserFileItem | null>(null);
  const [shareEmail, setShareEmail] = useState("");
  const [sharing, setSharing] = useState(false);
  const [publicationActionFileId, setPublicationActionFileId] = useState<
    string | null
  >(null);
  const [previewItem, setPreviewItem] = useState<UserFileItem | null>(null);
  const [previewContent, setPreviewContent] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { folders: allFolders, refetch: refetchFolders } = useUserFolders();
  const { config: uploadConfig } = useUserFileUploadConfig();
  const folders = useMemo(
    () => allFolders.filter((path) => !isReservedSystemFolderPath(path)),
    [allFolders],
  );

  // Library filters (folder/source/q) drive the backend request for
  // the library half. The `type` switch is library-only and we apply
  // it client-side on the merged list below; thread uploads are filtered
  // client-side too because they live across many threads and we'd
  // rather avoid per-thread re-requests when the user toggles a filter.
  const conversationFolder = systemFolder === "shared" ? null : systemFolder;
  const ownFiles = useAllUserFiles(
    {
      folder_path: folderPath,
      source: source === "all" ? "all" : source,
      q: query,
    },
    {
      enabled: systemFolder !== "shared",
      conversationSource: conversationFolder,
    },
  );
  const sharedFiles = useSharedFiles({ enabled: systemFolder === "shared" });
  const filePublications = useFilePublications({
    enabled: systemFolder === "generated",
  });
  const publicationBySource = useMemo(
    () =>
      new Map(
        filePublications.publications.map((publication) => [
          `${publication.thread_id}:${publication.path}`,
          publication,
        ]),
      ),
    [filePublications.publications],
  );
  const rawItems =
    systemFolder === "shared" ? sharedFiles.files : ownFiles.files;
  const isLoading =
    systemFolder === "shared" ? sharedFiles.isLoading : ownFiles.isLoading;
  const refetch =
    systemFolder === "shared" ? sharedFiles.refetch : ownFiles.refetch;

  // Conversation files are collected client-side across recent threads, so
  // re-apply all filters after they have been normalized. At the library root
  // the locked system folders are prepended instead of exposing thread
  // files as loose rows.
  const items = useMemo<UserFileItem[]>(() => {
    const q = query.trim().toLowerCase();
    const matchesFilters = (item: UserFileItem) => {
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
    };

    const filteredItems = rawItems
      .filter(
        (item) =>
          systemFolder !== null ||
          folderPath !== "" ||
          item.kind !== "folder" ||
          !isReservedSystemFolderPath(item.path),
      )
      .filter(matchesFilters);
    if (!systemFolder && !folderPath) {
      return [...SYSTEM_FOLDERS.filter(matchesFilters), ...filteredItems];
    }
    return filteredItems;
  }, [folderPath, rawItems, query, source, systemFolder, type]);

  const stats = useMemo(() => {
    const files = items.filter((item) => item.kind === "file").length;
    const folders = items.length - files;
    const fromThreads = items.filter((item) => item.source_thread_id).length;
    return { files, folders, fromThreads };
  }, [items]);

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
      setSystemFolder(null);
      setFolderPath(uploadFolderPath);
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
    if (!folderName.trim() || systemFolder) return;
    const requestedPath = `${folderPath}/${folderName.trim()}`.replace(
      /^\/+|\/+$/g,
      "",
    );
    if (isReservedSystemFolderPath(requestedPath)) {
      toast.error("“对话上传”“对话生成”和“他人分享”是锁定的系统文件夹");
      return;
    }
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
    if (
      item.system_folder ||
      item.shared_file_id ||
      item.conversation_source === "generated"
    )
      return;
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

  const handleShare = async () => {
    if (!shareItem || !shareEmail.trim()) return;
    setSharing(true);
    try {
      await shareFileWithUser(shareItem, shareEmail);
      toast.success(`已分享给 ${shareEmail.trim()}`);
      setShareItem(null);
      setShareEmail("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "分享文件失败");
    } finally {
      setSharing(false);
    }
  };

  const handlePublish = async (item: UserFileItem) => {
    setPublicationActionFileId(item.id);
    try {
      await publishGeneratedHtml(item);
      toast.success("外链已发布");
      await filePublications.refetch();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "发布外链失败");
    } finally {
      setPublicationActionFileId(null);
    }
  };

  const handleCopyPublication = async (publication: FilePublicationRecord) => {
    const publicUrl = new URL(
      publication.public_url,
      window.location.origin,
    ).toString();
    if (await copyTextToClipboard(publicUrl)) {
      toast.success("外链已复制");
    } else {
      toast.error("复制外链失败");
    }
  };

  const handleCancelPublication = async (
    item: UserFileItem,
    publication: FilePublicationRecord,
  ) => {
    if (!window.confirm(`确定取消发布「${item.name}」吗？原外链将立即失效。`)) {
      return;
    }
    setPublicationActionFileId(item.id);
    try {
      await cancelFilePublication(publication.id);
      toast.success("已取消发布");
      await filePublications.refetch();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "取消发布失败");
    } finally {
      setPublicationActionFileId(null);
    }
  };

  const openSharedItem = async (item: UserFileItem) => {
    if (!item.shared_file_id) return;
    const extension = normalizeExtension(item.extension);
    if (![".md", ".markdown", ".html", ".htm"].includes(extension)) {
      const url = item.preview_url ?? item.download_url;
      if (url) {
        window.open(resolveBackendUrl(url), "_blank", "noopener,noreferrer");
      }
      return;
    }

    setPreviewItem(item);
    setPreviewContent("");
    setPreviewError(null);
    setPreviewLoading(true);
    try {
      setPreviewContent(await loadSharedFileText(item.shared_file_id));
    } catch (error) {
      setPreviewError(
        error instanceof Error ? error.message : "加载分享文件失败",
      );
    } finally {
      setPreviewLoading(false);
    }
  };

  const openItem = (item: UserFileItem) => {
    if (item.system_folder) {
      setSystemFolder(item.system_folder);
      setFolderPath("");
      setSource(item.system_folder === "shared" ? "all" : item.system_folder);
      setType("all");
      setQuery("");
      return;
    }
    if (item.kind === "folder") {
      setSystemFolder(null);
      setFolderPath(item.path);
      return;
    }
    if (item.shared_file_id) {
      void openSharedItem(item);
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

  const renderActions = (item: UserFileItem) => {
    if (item.system_folder) return null;

    if (item.shared_file_id) {
      return (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8 rounded-full">
              <MoreHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {item.download_url && (
              <DropdownMenuItem asChild>
                <a href={resolveBackendUrl(item.download_url)}>
                  <DownloadIcon className="size-4" />
                  下载
                </a>
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      );
    }

    const publication = item.source_thread_id
      ? publicationBySource.get(`${item.source_thread_id}:${item.path}`)
      : undefined;

    return (
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
                href={
                  item.conversation_source === "generated"
                    ? threadGeneratedFileUrl(
                        item.source_thread_id,
                        item.path,
                        true,
                      )
                    : threadUploadDownloadUrl(item.source_thread_id, item.path)
                }
                target="_blank"
                rel="noopener noreferrer"
              >
                <DownloadIcon className="size-4" />
                下载
              </a>
            </DropdownMenuItem>
          )}
          {item.kind === "file" && (
            <DropdownMenuItem
              onClick={() => {
                setShareEmail("");
                setShareItem(item);
              }}
            >
              <Share2Icon className="size-4" />
              分享
            </DropdownMenuItem>
          )}
          {isPublishableGeneratedHtml(item) && !publication && (
            <DropdownMenuItem
              disabled={publicationActionFileId === item.id}
              onClick={() => void handlePublish(item)}
            >
              <Globe2Icon className="size-4" />
              发布外链
            </DropdownMenuItem>
          )}
          {isPublishableGeneratedHtml(item) && publication && (
            <>
              <DropdownMenuItem
                onClick={() => void handleCopyPublication(publication)}
              >
                <CopyIcon className="size-4" />
                复制外链
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={publicationActionFileId === item.id}
                className="text-red-600 focus:text-red-600"
                onClick={() => void handleCancelPublication(item, publication)}
              >
                <Link2OffIcon className="size-4" />
                取消发布
              </DropdownMenuItem>
            </>
          )}
          {item.kind === "file" && item.source_thread_id && (
            <DropdownMenuItem asChild>
              <Link href={`/workspace/chats/${item.source_thread_id}`}>
                <MessageSquareIcon className="size-4" />
                在对话中查看
              </Link>
            </DropdownMenuItem>
          )}
          {item.conversation_source !== "generated" && (
            <DropdownMenuItem
              className="text-red-600 focus:text-red-600"
              onClick={() => void handleDelete(item)}
            >
              <Trash2Icon className="size-4" />
              删除
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    );
  };

  const renderSourceLabel = (item: UserFileItem) => {
    if (item.system_folder) {
      return (
        <span className="inline-flex items-center gap-1 text-gray-500">
          <LockIcon className="size-3" />
          系统文件夹
        </span>
      );
    }
    if (item.shared_file_id) {
      return (
        <span
          className="text-muted-foreground inline-flex max-w-40 items-center gap-1 truncate text-xs"
          title={`由 ${item.shared_by_email ?? "其他用户"} 分享`}
        >
          <Share2Icon className="size-3 shrink-0" />
          <span className="truncate">{item.shared_by_email}</span>
        </span>
      );
    }
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
                value={source}
                onValueChange={(value) => setSource(value as SourceFilter)}
                disabled={systemFolder !== null}
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
                disabled={systemFolder !== null}
                title={
                  systemFolder
                    ? "系统文件夹已锁定，不能在其中新建文件夹"
                    : undefined
                }
                onClick={() => setFolderDialogOpen(true)}
              >
                <FolderPlusIcon className="size-4" />
                新建文件夹
              </Button>
              <Button
                type="button"
                className="h-9 rounded-lg bg-black px-4 text-white hover:bg-black/90"
                disabled={uploading || systemFolder !== null}
                title={
                  systemFolder
                    ? "系统文件夹已锁定，不能人工上传文件"
                    : undefined
                }
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
            systemFolder={systemFolder}
            onOpen={(path) => {
              setSystemFolder(null);
              setFolderPath(path);
              if (!path) setSource("all");
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
              {query.trim() || source !== "all" || type !== "all"
                ? "没有匹配的文件"
                : "当前文件夹为空"}
            </p>
            <p className="mt-2 text-sm text-gray-500">
              {query.trim() || source !== "all" || type !== "all"
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
                  {item.system_folder ? "-" : formatDate(item.modified_at)}
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

      <Dialog
        open={shareItem !== null}
        onOpenChange={(open) => {
          if (sharing) return;
          if (!open) {
            setShareItem(null);
            setShareEmail("");
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>分享文件</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-gray-600">
              将「{shareItem?.name}
              」分享给已注册用户。对方会在“他人分享”中只读查看。
            </p>
            <Input
              type="email"
              value={shareEmail}
              autoFocus
              placeholder="请输入对方的账号邮箱"
              onChange={(event) => setShareEmail(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleShare();
              }}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={sharing}
              onClick={() => {
                setShareItem(null);
                setShareEmail("");
              }}
            >
              取消
            </Button>
            <Button
              type="button"
              disabled={sharing || !shareEmail.trim()}
              onClick={() => void handleShare()}
            >
              {sharing && <LoaderCircleIcon className="size-4 animate-spin" />}
              确认分享
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={previewItem !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPreviewItem(null);
            setPreviewContent("");
            setPreviewError(null);
          }
        }}
      >
        <DialogContent className="flex h-[80vh] max-w-5xl flex-col sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>{previewItem?.name}</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-gray-200 bg-white">
            {previewLoading ? (
              <div className="flex size-full items-center justify-center text-sm text-gray-500">
                <LoaderCircleIcon className="mr-2 size-5 animate-spin" />
                正在加载文件
              </div>
            ) : previewError ? (
              <div className="flex size-full items-center justify-center p-8 text-sm text-red-600">
                {previewError}
              </div>
            ) : [".html", ".htm"].includes(
                normalizeExtension(previewItem?.extension ?? ""),
              ) ? (
              <iframe
                className="size-full border-0"
                title={previewItem?.name ?? "HTML 文件预览"}
                sandbox={SHARED_HTML_IFRAME_SANDBOX}
                referrerPolicy="no-referrer"
                srcDoc={previewContent}
              />
            ) : (
              <Streamdown className="p-6" {...streamdownPlugins}>
                {previewContent}
              </Streamdown>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
