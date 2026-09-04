"use client";

import { DownloadIcon, FileLockIcon, Share2Icon } from "lucide-react";
import { Fragment, useMemo, useState, type ReactNode } from "react";
import { Streamdown } from "streamdown";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { SkillShareDialog } from "@/components/workspace/skills/skill-share-dialog";
import { stripSkillFrontmatter } from "@/components/workspace/skills/skill-create-utils";
import { useAuth } from "@/core/auth/AuthProvider";
import { isAdminUser } from "@/core/auth/types";
import { useCustomSkill, usePublicSkill } from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";
import { streamdownPlugins } from "@/core/streamdown";
import { cn } from "@/lib/utils";

function prepareSkillPreviewContent(content: string) {
  let body = stripSkillFrontmatter(content);
  // Title is already shown in the dialog header — hide duplicate leading h1.
  body = body.replace(/^#\s+[^\n]+\n+/, "");
  return body.trimStart();
}

function SidebarSection({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "rounded-lg border border-gray-200/80 bg-white px-4 py-3.5 shadow-sm",
        className,
      )}
    >
      <h4 className="text-[11px] font-semibold tracking-wider text-gray-400 uppercase">
        {label}
      </h4>
      <div className="mt-2">{children}</div>
    </section>
  );
}

export function SkillDetailDialog({
  skill,
  onClose,
}: {
  skill: Skill | null;
  onClose: () => void;
}) {
  const { user } = useAuth();
  const isAdmin = isAdminUser(user);
  const isCustom = skill?.category === "custom";
  const isPublic = skill?.category === "public";
  const canViewContent = isCustom || (isPublic && isAdmin);
  const isOwner = isCustom && user && skill?.owner_user_id === user.id;
  const canShare = isCustom && (isOwner || isAdmin);
  const canDownload = isCustom && !!skill?.download_url && (isOwner || isAdmin);

  const [shareOpen, setShareOpen] = useState(false);

  const {
    skill: customSkill,
    isLoading: isCustomLoading,
    error: customError,
    refetch: refetchCustom,
  } = useCustomSkill(isCustom ? skill.name : null);
  const {
    skill: publicSkill,
    isLoading: isPublicLoading,
    error: publicError,
  } = usePublicSkill(isPublic && isAdmin ? skill.name : null);

  const contentSkill = isCustom ? customSkill : publicSkill;
  const isLoading = isCustom ? isCustomLoading : isPublicLoading;
  const error = isCustom ? customError : publicError;
  const displaySkill = contentSkill ?? skill;

  const previewContent = useMemo(
    () =>
      contentSkill?.content
        ? prepareSkillPreviewContent(contentSkill.content)
        : "",
    [contentSkill?.content],
  );

  async function handleDownload(event: React.MouseEvent) {
    event.preventDefault();
    event.stopPropagation();
    const url = displaySkill?.download_url;
    if (!url) return;
    try {
      const resp = await fetch(url, { credentials: "include" });
      if (!resp.ok) {
        let msg = `下载失败 (${resp.status})`;
        try {
          const errData = await resp.json();
          if (errData?.detail) msg = String(errData.detail);
        } catch {
          /* ignore */
        }
        throw new Error(msg);
      }
      const blob = await resp.blob();
      const disposition = resp.headers.get("Content-Disposition");
      let filename = `${displaySkill?.name ?? "skill"}.zip`;
      if (disposition) {
        const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
        if (utf8Match?.[1]) {
          try { filename = decodeURIComponent(utf8Match[1]); } catch { /* keep default */ }
        } else {
          const quotedMatch = /filename="([^"]+)"/i.exec(disposition);
          if (quotedMatch?.[1]) filename = quotedMatch[1];
          else {
            const simpleMatch = /filename=([^;]+)/i.exec(disposition);
            if (simpleMatch?.[1]) filename = simpleMatch[1].trim();
          }
        }
      }
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(objectUrl);
      toast.success("开始下载 Skill 压缩包");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "下载失败");
    }
  }

  function handleOpenShare(event: React.MouseEvent) {
    event.preventDefault();
    event.stopPropagation();
    setShareOpen(true);
  }

  return (
    <Fragment>
      <Dialog open={!!skill} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex h-[86vh] max-h-[980px] w-[calc(100vw-2rem)] max-w-none flex-col overflow-hidden p-0 sm:max-w-6xl">
        <DialogHeader className="shrink-0 border-b border-gray-100 bg-white px-6 py-4 sm:px-8">
          <DialogTitle className="text-lg font-semibold tracking-tight sm:text-xl">
            {displaySkill?.display_name ?? displaySkill?.name ?? skill?.name}
          </DialogTitle>
          <DialogDescription className="sr-only">
            {displaySkill?.description ??
              skill?.description ??
              "查看 Skill 详情"}
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <aside className="shrink-0 border-b border-gray-100 bg-gray-50/70 md:w-[min(100%,320px)] md:border-r md:border-b-0">
            <ScrollArea className="h-full max-h-[220px] md:max-h-none">
              <div className="space-y-3 p-4 sm:p-6">
                {!displaySkill ? (
                  <div className="text-sm text-gray-500">加载中...</div>
                ) : (
                  <>
                    <SidebarSection label="描述">
                      <p className="text-sm leading-relaxed text-gray-700">
                        {displaySkill.description}
                      </p>
                    </SidebarSection>
                    <SidebarSection label="分类">
                      <div className="flex flex-wrap gap-2">
                        <Badge variant="secondary" className="capitalize">
                          {displaySkill.category}
                        </Badge>
                        {displaySkill.license ? (
                          <Badge variant="outline">
                            {displaySkill.license}
                          </Badge>
                        ) : null}
                      </div>
                    </SidebarSection>
                    <SidebarSection label="状态">
                      <div className="flex items-center gap-2 text-sm text-gray-700">
                        <span
                          className={cn(
                            "inline-block h-2 w-2 rounded-full",
                            displaySkill.enabled
                              ? "bg-emerald-500"
                              : "bg-gray-300",
                          )}
                        />
                        {displaySkill.enabled ? "已启用" : "已禁用"}
                      </div>
                    </SidebarSection>
                    {(isCustom && displaySkill?.owner_email) || canDownload || canShare ? (
                      <SidebarSection label="归属">
                        {isCustom && displaySkill?.owner_email && (
                          <div className="mb-3 flex items-center gap-2 text-sm text-gray-700">
                            <span className="text-xs uppercase tracking-wider text-gray-400">
                              创建者：
                            </span>
                            <span className="truncate">{displaySkill.owner_email}</span>
                          </div>
                        )}
                        {isCustom && (displaySkill as Skill).shared_with !== undefined && ((displaySkill as Skill).shared_with?.length ?? 0) > 0 && (
                          <div className="mb-3">
                            <div className="text-xs uppercase tracking-wider text-gray-400 mb-1">
                              已共享给：
                            </div>
                            <div className="flex flex-wrap gap-1">
                              {(displaySkill as Skill).shared_with!.slice(0, 6).map((u) => (
                                <Badge key={u.id} variant="outline" className="text-[10px] font-normal">
                                  {u.email}
                                </Badge>
                              ))}
                              {(displaySkill as Skill).shared_with!.length > 6 && (
                                <Badge variant="outline" className="text-[10px] font-normal">
                                  +{(displaySkill as Skill).shared_with!.length - 6}
                                </Badge>
                              )}
                            </div>
                          </div>
                        )}
                        <div className="flex flex-col gap-2">
                          {canDownload && (
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={handleDownload}
                              className="w-full justify-start"
                            >
                              <DownloadIcon className="mr-2 size-3.5" />
                              下载 Skill
                            </Button>
                          )}
                          {canShare && (
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={handleOpenShare}
                              className="w-full justify-start"
                            >
                              <Share2Icon className="mr-2 size-3.5" />
                              管理共享
                            </Button>
                          )}
                        </div>
                      </SidebarSection>
                    ) : null}
                  </>
                )}
              </div>
            </ScrollArea>
          </aside>

          <div className="min-h-0 flex-1 bg-white">
            {canViewContent ? (
              <ScrollArea className="h-full">
                <div className="px-4 py-5 sm:px-8 sm:py-6">
                  {isLoading ? (
                    <div className="py-16 text-center text-sm text-gray-500">
                      加载内容中...
                    </div>
                  ) : error ? (
                    <div className="py-16 text-center text-sm text-red-600">
                      加载失败：
                      {error instanceof Error ? error.message : "未知错误"}
                    </div>
                  ) : contentSkill ? (
                    <div className="mx-auto w-full max-w-3xl">
                      {previewContent ? (
                        <Streamdown
                          {...streamdownPlugins}
                          className={cn(
                            "size-full text-sm leading-relaxed text-gray-800",
                            "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
                            "[&_h2]:mt-8 [&_h2]:mb-3 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-gray-900",
                            "[&_h3]:mt-6 [&_h3]:mb-2 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-gray-900",
                            "[&_p]:my-3 [&_p]:leading-relaxed",
                            "[&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5",
                            "[&_li]:my-1 [&_li]:leading-relaxed",
                            "[&_code]:rounded [&_code]:bg-gray-100 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em]",
                            "[&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-gray-200 [&_pre]:bg-gray-50 [&_pre]:p-4",
                            "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
                            "[&_blockquote]:my-4 [&_blockquote]:border-l-4 [&_blockquote]:border-gray-200 [&_blockquote]:pl-4 [&_blockquote]:text-gray-600",
                            "[&_hr]:my-6 [&_hr]:border-gray-200",
                          )}
                        >
                          {previewContent}
                        </Streamdown>
                      ) : (
                        <div className="py-16 text-center text-sm text-gray-500">
                          暂无正文内容
                        </div>
                      )}
                    </div>
                  ) : null}
                </div>
              </ScrollArea>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-gray-400">
                <FileLockIcon className="h-12 w-12" />
                <p className="text-center text-sm">
                  公共 Skill 的详细文件内容仅管理员可查看
                </p>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>

    <SkillShareDialog
      skillName={isCustom ? skill?.name ?? null : null}
      skillDisplayName={
        isCustom ? (displaySkill?.display_name ?? displaySkill?.name ?? null) : null
      }
      ownerUserId={isCustom ? (displaySkill as Skill).owner_user_id ?? null : null}
      open={shareOpen}
      onClose={() => setShareOpen(false)}
    />
    </Fragment>
  );
}
