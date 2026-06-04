"use client";

import {
  ArrowLeftIcon,
  ArchiveIcon,
  CheckCircleIcon,
  FileArchiveIcon,
  Loader2Icon,
  UploadIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { uploadSkillArchive } from "@/core/skills/api";
import { cn } from "@/lib/utils";

function isSupportedArchive(file: File | null) {
  if (!file) return false;
  const name = file.name.toLowerCase();
  return name.endsWith(".zip") || name.endsWith(".skill");
}

export default function UploadSkillPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  const canUpload = isSupportedArchive(file) && !isUploading;

  async function handleUpload() {
    if (!file || !canUpload) return;
    setIsUploading(true);
    try {
      const result = await uploadSkillArchive(file);
      if (!result.success) {
        toast.error(result.message || "上传失败");
        return;
      }
      toast.success(result.message || "Skill 已上传");
      router.push("/workspace/skills");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "上传失败");
    } finally {
      setIsUploading(false);
    }
  }

  function selectFile(nextFile: File | null) {
    if (isUploading) return;
    setFile(nextFile);
    if (nextFile && !isSupportedArchive(nextFile)) {
      toast.error("请上传 .zip 或 .skill 文件");
    }
  }

  function openFilePicker() {
    if (isUploading) return;
    inputRef.current?.click();
  }

  return (
    <div className="flex size-full flex-col bg-[#fafafa]">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon-sm" asChild>
            <Link href="/workspace/skills" aria-label="返回 Skill 管理">
              <ArrowLeftIcon className="h-4 w-4" />
            </Link>
          </Button>
          <div>
            <h1 className="text-xl font-semibold text-gray-900">Upload ZIP</h1>
            <p className="mt-1 text-sm text-gray-500">
              上传可迁移的 Skill 归档。
            </p>
          </div>
        </div>
        <Button disabled={!canUpload} onClick={() => void handleUpload()}>
          {isUploading ? (
            <Loader2Icon className="h-4 w-4 animate-spin" />
          ) : (
            <UploadIcon className="h-4 w-4" />
          )}
          {isUploading ? "上传中" : "安装 skill"}
        </Button>
      </header>

      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
          <section
            className={cn(
              "flex min-h-[360px] flex-col items-center justify-center rounded-lg border border-dashed bg-white p-10 text-center transition-colors",
              isUploading
                ? "cursor-not-allowed border-gray-200 opacity-60"
                : "cursor-pointer",
              !isUploading &&
                (isDragging
                  ? "border-gray-900 bg-gray-50"
                  : "border-gray-300 hover:border-gray-500"),
            )}
            onClick={openFilePicker}
            onDragEnter={(event) => {
              if (isUploading) return;
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragOver={(event) => {
              if (isUploading) return;
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(event) => {
              if (isUploading) return;
              event.preventDefault();
              setIsDragging(false);
              selectFile(event.dataTransfer.files[0] ?? null);
            }}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".zip,.skill"
              className="hidden"
              disabled={isUploading}
              onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
            />
            <div className="mb-5 flex size-14 items-center justify-center rounded-lg bg-gray-100">
              <ArchiveIcon className="h-7 w-7 text-gray-600" />
            </div>
            <h2 className="text-lg font-semibold text-gray-900">
              选择或拖入 Skill 归档
            </h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-gray-500">
              支持 `.zip` 和 `.skill`。归档中需要包含一个有效的 SKILL.md，
              上传后会安装到 DeerFlow custom skills。
            </p>
            <Button
              type="button"
              variant="outline"
              className="mt-6"
              disabled={isUploading}
              onClick={(event) => {
                event.stopPropagation();
                openFilePicker();
              }}
            >
              <FileArchiveIcon className="h-4 w-4" />
              Browse archive
            </Button>
          </section>

          {file ? (
            <section className="rounded-lg border border-gray-200 bg-white p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="flex min-w-0 items-start gap-3">
                  <FileArchiveIcon className="mt-1 h-5 w-5 shrink-0 text-gray-500" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-900">
                      {file.name}
                    </p>
                    <p className="mt-1 text-xs text-gray-500">
                      {(file.size / 1024).toFixed(1)} KB
                    </p>
                  </div>
                </div>
                {isSupportedArchive(file) ? (
                  <CheckCircleIcon className="h-5 w-5 shrink-0 text-emerald-600" />
                ) : null}
              </div>
            </section>
          ) : null}

          <Alert className="border-blue-200 bg-blue-50 text-blue-900">
            <CheckCircleIcon className="h-4 w-4" />
            <AlertDescription className="text-blue-800">
              请按照skill的格式上传,上传后会安装到 Friday custom skills。
            </AlertDescription>
          </Alert>
        </div>
      </main>
    </div>
  );
}
