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
  MoreHorizontalIcon,
  SearchIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import {
  createUserFolder,
  deleteUserFile,
  listUserFiles,
  uploadUserFiles,
  userFileUrl,
} from "@/core/files";
import type { UserFileItem, UserFileTypeFilter } from "@/core/files";
import { cn } from "@/lib/utils";

type ViewMode = "list" | "grid";

const sourceLabels: Record<string, string> = {
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

function fileType(item: UserFileItem): UserFileTypeFilter {
  if (item.kind === "folder") return "folder";
  if (item.mime_type?.startsWith("image/")) return "image";
  if (item.mime_type?.startsWith("audio/")) return "audio";
  if (
    [".pdf", ".doc", ".docx", ".md", ".txt", ".csv", ".xls", ".xlsx"].includes(
      item.extension,
    )
  ) {
    return "document";
  }
  return "other";
}

function FileGlyph({ item }: { item: UserFileItem }) {
  const type = fileType(item);
  const iconClass = "size-5";
  if (item.preview_url) {
    return (
      <img
        src={userFileUrl(item.path)}
        alt=""
        className="size-10 rounded-md object-cover"
      />
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
              className={cn(index === parts.length - 1 && "font-medium text-gray-950")}
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
  const [items, setItems] = useState<UserFileItem[]>([]);
  const [folderPath, setFolderPath] = useState("");
  const [source, setSource] = useState("all");
  const [type, setType] = useState<UserFileTypeFilter>("all");
  const [query, setQuery] = useState("");
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [loading, setLoading] = useState(true);
  const [folderDialogOpen, setFolderDialogOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [savingFolder, setSavingFolder] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadFiles = useCallback(async () => {
    setLoading(true);
    try {
      const result = await listUserFiles({
        folderPath,
        source,
        type,
        q: query,
      });
      setItems(result.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载文件失败");
    } finally {
      setLoading(false);
    }
  }, [folderPath, query, source, type]);

  useEffect(() => {
    void loadFiles();
  }, [loadFiles]);

  const stats = useMemo(() => {
    const files = items.filter((item) => item.kind === "file").length;
    const folders = items.length - files;
    return { files, folders };
  }, [items]);

  const handleUpload = async (fileList: FileList | null) => {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;
    setUploading(true);
    try {
      await uploadUserFiles(files, folderPath);
      toast.success(`已上传 ${files.length} 个文件`);
      await loadFiles();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "上传文件失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
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
      await loadFiles();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "新建文件夹失败");
    } finally {
      setSavingFolder(false);
    }
  };

  const handleDelete = async (item: UserFileItem) => {
    if (!window.confirm(`确定删除「${item.name}」吗？`)) return;
    try {
      await deleteUserFile(item.path);
      toast.success("已删除");
      await loadFiles();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    }
  };

  const openItem = (item: UserFileItem) => {
    if (item.kind === "folder") {
      setFolderPath(item.path);
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
        {item.kind === "file" && (
          <DropdownMenuItem asChild>
            <a href={userFileUrl(item.path, true)}>
              <DownloadIcon className="size-4" />
              下载
            </a>
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

  return (
    <div className="flex size-full flex-col bg-[#fafafa]">
      <header className="shrink-0 px-12 pt-7 pb-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal text-black">
              文件
            </h1>
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <Select value={source} onValueChange={setSource}>
                <SelectTrigger className="h-8 w-36 rounded-lg border-gray-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(sourceLabels).map(([value, label]) => (
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
                onClick={() => fileInputRef.current?.click()}
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
                onChange={(event) => void handleUpload(event.target.files)}
              />
            </div>
            <div className="flex rounded-lg border border-gray-200 bg-white p-1">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className={cn("size-7 rounded-md", viewMode === "list" && "bg-gray-100")}
                onClick={() => setViewMode("list")}
              >
                <ListIcon className="size-4" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className={cn("size-7 rounded-md", viewMode === "grid" && "bg-gray-100")}
                onClick={() => setViewMode("grid")}
              >
                <Grid2X2Icon className="size-4" />
              </Button>
            </div>
          </div>
        </div>
        <div className="mt-4">
          <FolderBreadcrumb folderPath={folderPath} onOpen={setFolderPath} />
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-12 pb-8">
        {loading ? (
          <div className="flex h-72 items-center justify-center text-sm text-gray-500">
            <LoaderCircleIcon className="mr-2 size-5 animate-spin" />
            正在加载文件
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-80 flex-col items-center justify-center rounded-lg border border-dashed border-gray-200 bg-white text-center">
            <FolderIcon className="size-12 text-gray-300" />
            <p className="mt-4 text-sm font-medium text-gray-950">
              当前文件夹为空
            </p>
            <p className="mt-2 text-sm text-gray-500">
              上传文件或新建文件夹后，会显示在这里。
            </p>
          </div>
        ) : viewMode === "list" ? (
          <div className="overflow-hidden">
            <div className="grid grid-cols-[minmax(360px,1fr)_120px_80px_90px_44px] border-b border-gray-200 px-3 py-3 text-xs text-gray-500">
              <div>名称</div>
              <div>修改时间</div>
              <div>大小</div>
              <div>来源</div>
              <div />
            </div>
            {items.map((item) => (
              <div
                key={item.path}
                className="grid min-h-15 grid-cols-[minmax(360px,1fr)_120px_80px_90px_44px] items-center px-3 py-2 text-sm hover:bg-white"
              >
                <button
                  type="button"
                  className="flex min-w-0 items-center gap-3 text-left"
                  onClick={() => openItem(item)}
                >
                  <FileGlyph item={item} />
                  <span className="truncate font-medium text-black">{item.name}</span>
                </button>
                <div className="text-gray-500">{formatDate(item.modified_at)}</div>
                <div className="text-gray-500">{formatSize(item.size)}</div>
                <div className="text-gray-500">
                  {item.kind === "folder"
                    ? "-"
                    : item.source === "generated"
                      ? "已生成"
                      : "已上传"}
                </div>
                <div>{renderActions(item)}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5">
            {items.map((item) => (
              <div
                key={item.path}
                className="group rounded-lg border border-gray-200 bg-white p-3"
              >
                <div className="flex items-start justify-between">
                  <button
                    type="button"
                    className="flex min-w-0 items-center gap-3 text-left"
                    onClick={() => openItem(item)}
                  >
                    <FileGlyph item={item} />
                    <span className="line-clamp-2 text-sm font-medium text-black">
                      {item.name}
                    </span>
                  </button>
                  {renderActions(item)}
                </div>
                <div className="mt-4 flex justify-between text-xs text-gray-500">
                  <span>{formatSize(item.size)}</span>
                  <span>{item.kind === "folder" ? "文件夹" : sourceLabels[item.source ?? "uploaded"]}</span>
                </div>
              </div>
            ))}
          </div>
        )}
        {!loading && items.length > 0 && (
          <div className="mt-4 text-xs text-gray-400">
            {stats.folders} 个文件夹，{stats.files} 个文件
          </div>
        )}
      </main>

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
              {savingFolder && <LoaderCircleIcon className="size-4 animate-spin" />}
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
