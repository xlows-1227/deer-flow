"use client";

import { FileLockIcon } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { Streamdown } from "streamdown";

import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
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

  const {
    skill: customSkill,
    isLoading: isCustomLoading,
    error: customError,
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

  return (
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
  );
}
