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
import { SecurityOverridePanel } from "@/components/workspace/skills/security-override-panel";
import { overrideSkillSecurity, uploadSkillArchive } from "@/core/skills/api";
import type { SkillUploadErrorDetail } from "@/core/skills/api";
import { securityCategoryInfo } from "@/core/skills/security-category";
import type {
  SkillSecurityStatus,
  SkillSecurityWarning,
} from "@/core/skills/type";
import { cn } from "@/lib/utils";

function isSupportedArchive(file: File | null) {
  if (!file) return false;
  const name = file.name.toLowerCase();
  return name.endsWith(".zip") || name.endsWith(".skill");
}

interface InstallResult {
  skillName: string;
  warnings: SkillSecurityWarning[];
  securityStatus: SkillSecurityStatus | null;
  securityReason: string | null;
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
  const [showOverride, setShowOverride] = useState(false);
  const [isOverriding, setIsOverriding] = useState(false);

  const canUpload = isSupportedArchive(file) && !isUploading;
  const installFailed = installResult?.securityStatus === "failed";

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
          },
        );
        toast.error(result.message || "上传失败");
        return;
      }
      setFile(null);
      setInstallResult({
        skillName: result.skill_name,
        warnings: result.warnings ?? [],
        securityStatus: result.security_status ?? null,
        securityReason: result.security_reason ?? null,
      });
      setShowOverride(false);
      toast.success(result.message || "Skill 已上传");
    } catch (error) {
      const message = error instanceof Error ? error.message : "上传失败";
      setUploadError({
        code: "upload_failed",
        message,
        reason: message,
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

  async function handleOverride(reason?: string) {
    if (!installResult || isOverriding) return;
    setIsOverriding(true);
    try {
      const report = await overrideSkillSecurity(installResult.skillName, reason);
      setInstallResult((previous) =>
        previous
          ? {
              ...previous,
              securityStatus: report.status ?? "overridden",
              securityReason: report.reason ?? null,
            }
          : previous,
      );
      setShowOverride(false);
      toast.success("已放行，该 Skill 在对话中可立即使用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "放行失败");
    } finally {
      setIsOverriding(false);
    }
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
                {uploadError.code === "invalid_skill_structure" ? (
                  <p className="mt-2 text-xs text-red-800">
                    归档结构不合法（例如包含嵌套 SKILL.md），无法安装；请调整归档结构后重试。
                  </p>
                ) : null}
              </AlertDescription>
            </Alert>
          ) : null}

          {installResult ? (
            <section
              className={cn(
                "rounded-lg border p-5",
                installFailed
                  ? "border-red-200 bg-red-50"
                  : "border-emerald-200 bg-emerald-50",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-2">
                  {installFailed ? (
                    <AlertTriangleIcon className="mt-0.5 h-5 w-5 shrink-0 text-red-600" />
                  ) : (
                    <CheckCircleIcon className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
                  )}
                  <div>
                    <p
                      className={cn(
                        "text-sm font-medium",
                        installFailed ? "text-red-900" : "text-emerald-900",
                      )}
                    >
                      {installFailed
                        ? `${installResult.skillName} 已安装，但因安检未通过暂未启用`
                        : `${installResult.skillName} 安装完成`}
                    </p>
                    <p
                      className={cn(
                        "mt-1 text-xs",
                        installFailed ? "text-red-800" : "text-emerald-800",
                      )}
                    >
                      {installFailed
                        ? "安检未通过（规则命中、文件无法扫描或模型判定），当前不会注入对话。确认来源可信后可人工放行。"
                        : installResult.warnings.length > 0
                          ? "以下为提示性安全告警，不影响使用。"
                          : "安全规则已通过，未发现告警。"}
                    </p>
                    {installFailed && installResult.securityReason ? (
                      <p className="mt-1 text-xs text-red-800">
                        原因：{installResult.securityReason}
                      </p>
                    ) : null}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {installFailed ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="destructive"
                      onClick={() => setShowOverride((previous) => !previous)}
                    >
                      我已知悉风险，仍要使用
                    </Button>
                  ) : null}
                  <Button type="button" size="sm" variant="outline" asChild>
                    <Link href="/workspace/skills">查看技能</Link>
                  </Button>
                </div>
              </div>
              {installFailed && showOverride ? (
                <SecurityOverridePanel
                  isSubmitting={isOverriding}
                  onConfirm={handleOverride}
                  onCancel={() => setShowOverride(false)}
                />
              ) : null}
              {installResult.warnings.length > 0 ? (
                <ul className="mt-4 space-y-2">
                  {installResult.warnings.map((warning, index) => {
                    const categoryInfo = securityCategoryInfo(warning.category);
                    return (
                      <li
                        key={`${warning.code}-${warning.file}-${warning.line}-${index}`}
                        className="rounded border border-amber-200 bg-white p-3 text-xs text-amber-950"
                      >
                        <div className="flex flex-wrap items-center gap-2 font-medium">
                          <AlertTriangleIcon className="h-3.5 w-3.5 text-amber-600" />
                          <span>{warning.code}</span>
                          {categoryInfo ? (
                            <span className="rounded bg-amber-100 px-1 text-[10px] font-semibold">
                              {categoryInfo.label}
                            </span>
                          ) : null}
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
                        {categoryInfo ? (
                          <p className="mt-1 leading-5 text-gray-600">
                            {categoryInfo.advice}
                          </p>
                        ) : null}
                      </li>
                    );
                  })}
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
