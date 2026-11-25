"use client";

import {
  ArrowLeftIcon,
  ArchiveIcon,
  AlertTriangleIcon,
  CheckCircleIcon,
  FileArchiveIcon,
  InfoIcon,
  Loader2Icon,
  UploadIcon,
} from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { uploadSkillArchive } from "@/core/skills/api";
import type { SkillUploadErrorDetail } from "@/core/skills/api";
import type { SkillSecurityWarning } from "@/core/skills/type";
import { cn } from "@/lib/utils";

function isSupportedArchive(file: File | null) {
  if (!file) return false;
  const name = file.name.toLowerCase();
  return name.endsWith(".zip") || name.endsWith(".skill");
}

interface InstallResult {
  skillName: string;
  warnings: SkillSecurityWarning[];
}

export default function UploadSkillPage() {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<SkillUploadErrorDetail | null>(
    null,
  );
  const [installResult, setInstallResult] = useState<InstallResult | null>(null);

  const canUpload = isSupportedArchive(file) && !isUploading;

  async function handleUpload() {
    if (!file || !canUpload) return;
    setIsUploading(true);
    setUploadError(null);
    setInstallResult(null);
    try {
      const result = await uploadSkillArchive(file);
      if (!result.success) {
        const error = "error" in result ? result.error : undefined;
        setUploadError(
          error ?? {
            code: "upload_failed",
            message: result.message || "上传失败",
            reason: result.message || "上传失败",
            can_force: false,
          },
        );
        toast.error(result.message || "上传失败");
        return;
      }
      setFile(null);
      setInstallResult({
        skillName: result.skill_name,
        warnings: result.warnings ?? [],
      });
      toast.success(result.message || "Skill 已上传");
    } catch (error) {
      const message = error instanceof Error ? error.message : "上传失败";
      setUploadError({
        code: "upload_failed",
        message,
        reason: message,
        can_force: false,
      });
      toast.error(message);
    } finally {
      setIsUploading(false);
    }
  }

  function onUploadClick(event: React.MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    void handleUpload();
  }

  function selectFile(nextFile: File | null) {
    if (isUploading) return;
    setFile(nextFile);
    setUploadError(null);
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
        <div className="flex items-center gap-2">
          <Button type="button" disabled={!canUpload} onClick={onUploadClick}>
            {isUploading ? (
              <Loader2Icon className="h-4 w-4 animate-spin" />
            ) : (
              <UploadIcon className="h-4 w-4" />
            )}
            {isUploading ? "上传中" : "安装 skill"}
          </Button>
        </div>
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
                event.preventDefault();
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

          {uploadError ? (
            <Alert className="border-red-200 bg-red-50 text-red-950">
              <AlertTriangleIcon className="h-4 w-4" />
              <AlertTitle>上传失败</AlertTitle>
              <AlertDescription className="text-red-900">
                <p>{uploadError.message}</p>
                {uploadError.reason !== uploadError.message ? (
                  <p>原因：{uploadError.reason}</p>
                ) : null}
                {uploadError.code === "security_scan_failed" ? (
                  <p className="mt-2 text-xs text-red-800">
                    结构性风险不可跳过；请移除命中规则的内容后重新上传。
                  </p>
                ) : null}
              </AlertDescription>
            </Alert>
          ) : null}

          {installResult ? (
            <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-2">
                  <CheckCircleIcon className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
                  <div>
                    <p className="text-sm font-medium text-emerald-900">
                      {installResult.skillName} 安装完成
                    </p>
                    <p className="mt-1 text-xs text-emerald-800">
                      {installResult.warnings.length > 0
                        ? "以下为提示性安全告警，不影响安装。"
                        : "安全规则已通过，未发现告警。"}
                    </p>
                  </div>
                </div>
                <Button type="button" size="sm" variant="outline" asChild>
                  <Link href="/workspace/skills">查看技能</Link>
                </Button>
              </div>
              {installResult.warnings.length > 0 ? (
                <ul className="mt-4 space-y-2">
                  {installResult.warnings.map((warning, index) => (
                    <li
                      key={`${warning.code}-${warning.file}-${warning.line}-${index}`}
                      className="rounded border border-amber-200 bg-white p-3 text-xs text-amber-950"
                    >
                      <div className="flex flex-wrap items-center gap-2 font-medium">
                        <AlertTriangleIcon className="h-3.5 w-3.5 text-amber-600" />
                        <span>{warning.code}</span>
                        {warning.file ? (
                          <span className="text-amber-800">
                            {warning.file}
                            {warning.line ? `:${warning.line}` : ""}
                          </span>
                        ) : null}
                        {warning.excerpt_hash ? (
                          <span className="font-mono text-[10px] text-amber-700">
                            #{warning.excerpt_hash}
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 leading-5">{warning.message}</p>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          ) : null}

          <Alert className="border-blue-200 bg-blue-50 text-blue-900">
            <InfoIcon className="h-4 w-4" />
            <AlertDescription className="text-blue-800">
              请按照skill的格式上传,上传后会安装到 Friday custom skills。
            </AlertDescription>
          </Alert>
        </div>
      </main>
    </div>
  );
}
