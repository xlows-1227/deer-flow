"use client";

import { FileLockIcon } from "lucide-react";
import { Streamdown } from "streamdown";

import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useAuth } from "@/core/auth/AuthProvider";
import { isAdminUser } from "@/core/auth/types";
import { useCustomSkill, usePublicSkill } from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";
import { streamdownPlugins } from "@/core/streamdown";

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

  return (
    <Dialog open={!!skill} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex h-[86vh] max-h-[980px] w-[calc(100vw-2rem)] max-w-none flex-col overflow-hidden p-0 sm:max-w-6xl">
        <DialogHeader className="shrink-0 border-b border-gray-100 px-8 py-5">
          <DialogTitle className="text-xl">
            {displaySkill?.display_name ?? displaySkill?.name ?? skill?.name}
          </DialogTitle>
        </DialogHeader>
        <div className="flex min-h-0 flex-1">
          <div className="w-80 shrink-0 border-r border-gray-100 bg-gray-50/60 p-8">
            {!displaySkill ? (
              <div className="text-sm text-gray-500">加载中...</div>
            ) : (
              <div className="flex flex-col gap-6">
                <div>
                  <h4 className="text-xs font-semibold tracking-wider text-gray-400 uppercase">
                    描述
                  </h4>
                  <p className="mt-1.5 text-sm leading-relaxed text-gray-700">
                    {displaySkill.description}
                  </p>
                </div>
                <div>
                  <h4 className="text-xs font-semibold tracking-wider text-gray-400 uppercase">
                    分类
                  </h4>
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    <Badge variant="secondary">{displaySkill.category}</Badge>
                    {displaySkill.license ? (
                      <Badge variant="outline">{displaySkill.license}</Badge>
                    ) : null}
                  </div>
                </div>
                <div>
                  <h4 className="text-xs font-semibold tracking-wider text-gray-400 uppercase">
                    状态
                  </h4>
                  <div className="mt-1.5 flex items-center gap-2 text-sm text-gray-700">
                    <span
                      className={`inline-block h-2 w-2 rounded-full ${displaySkill.enabled ? "bg-emerald-500" : "bg-gray-300"}`}
                    />
                    {displaySkill.enabled ? "已启用" : "已禁用"}
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="min-h-0 flex-1 px-8 py-6">
            {canViewContent ? (
              <ScrollArea className="h-full">
                {isLoading ? (
                  <div className="py-12 text-center text-sm text-gray-500">
                    加载内容中...
                  </div>
                ) : error ? (
                  <div className="py-12 text-center text-sm text-red-600">
                    加载失败：
                    {error instanceof Error ? error.message : "未知错误"}
                  </div>
                ) : contentSkill ? (
                  <div className="min-h-full rounded-xl border border-gray-100 bg-gray-50/80 p-6">
                    <div className="prose prose-sm max-w-none text-gray-800">
                      <Streamdown {...streamdownPlugins}>
                        {contentSkill.content}
                      </Streamdown>
                    </div>
                  </div>
                ) : null}
              </ScrollArea>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-4 text-gray-400">
                <FileLockIcon className="h-12 w-12" />
                <p className="text-sm">
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
