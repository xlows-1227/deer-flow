"use client";

import { EyeIcon, FileLockIcon, SparklesIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  Item,
  ItemActions,
  ItemTitle,
  ItemContent,
  ItemDescription,
} from "@/components/ui/item";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/core/i18n/hooks";
import { useCustomSkill, useEnableSkill, useSkills } from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";
import { env } from "@/env";

import { SettingsSection } from "./settings-section";

export function SkillSettingsPage({ onClose }: { onClose?: () => void } = {}) {
  const { t } = useI18n();
  const { skills, isLoading, error } = useSkills();
  return (
    <SettingsSection
      title={t.settings.skills.title}
      description={t.settings.skills.description}
    >
      {isLoading ? (
        <div className="text-muted-foreground text-sm">{t.common.loading}</div>
      ) : error ? (
        <div>Error: {error.message}</div>
      ) : (
        <SkillSettingsList skills={skills} onClose={onClose} />
      )}
    </SettingsSection>
  );
}

function SkillSettingsList({
  skills,
  onClose,
}: {
  skills: Skill[];
  onClose?: () => void;
}) {
  const { t } = useI18n();
  const router = useRouter();
  const [filter, setFilter] = useState<string>("public");
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const { mutate: enableSkill } = useEnableSkill();
  const filteredSkills = useMemo(
    () => skills.filter((skill) => skill.category === filter),
    [skills, filter],
  );
  const handleCreateSkill = () => {
    onClose?.();
    router.push("/workspace/chats/new?mode=skill");
  };
  return (
    <div className="flex w-full flex-col gap-4">
      <header className="flex justify-between">
        <div className="flex gap-2">
          <Tabs defaultValue="public" onValueChange={setFilter}>
            <TabsList variant="line">
              <TabsTrigger value="public">{t.common.public}</TabsTrigger>
              <TabsTrigger value="custom">{t.common.custom}</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <div>
          <Button size="sm" onClick={handleCreateSkill}>
            <SparklesIcon className="size-4" />
            {t.settings.skills.createSkill}
          </Button>
        </div>
      </header>
      {filteredSkills.length === 0 && (
        <EmptySkill onCreateSkill={handleCreateSkill} />
      )}
      {filteredSkills.length > 0 &&
        filteredSkills.map((skill) => (
          <Item className="w-full" variant="outline" key={skill.name}>
            <ItemContent>
              <ItemTitle>
                <div className="flex items-center gap-2">{skill.display_name ?? skill.name}</div>
              </ItemTitle>
              <ItemDescription className="line-clamp-4">
                {skill.description}
              </ItemDescription>
            </ItemContent>
            <ItemActions className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 gap-1 text-xs text-gray-500 hover:text-gray-900"
                onClick={() => setSelectedSkill(skill)}
              >
                <EyeIcon className="h-3.5 w-3.5" />
                查看
              </Button>
              <Switch
                checked={skill.enabled}
                disabled={env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"}
                onCheckedChange={(checked) =>
                  enableSkill({ skillName: skill.name, enabled: checked })
                }
              />
            </ItemActions>
          </Item>
        ))}

      <SkillDetailDialog
        skill={selectedSkill}
        onClose={() => setSelectedSkill(null)}
      />
    </div>
  );
}

function SkillDetailDialog({
  skill,
  onClose,
}: {
  skill: Skill | null;
  onClose: () => void;
}) {
  const isCustom = skill?.category === "custom";
  const {
    skill: customSkill,
    isLoading,
    error,
  } = useCustomSkill(isCustom ? skill.name : null);

  const displaySkill = isCustom && customSkill ? customSkill : skill;

  return (
    <Dialog open={!!skill} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex h-[90vh] max-h-[1200px] w-[96vw] max-w-[2400px] flex-col overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-gray-100 px-6 py-4">
          <DialogTitle className="text-lg">
            {displaySkill?.name ?? skill?.name}
          </DialogTitle>
        </DialogHeader>
        <div className="flex min-h-0 flex-1">
          {/* 左侧：Skill 说明 */}
          <div className="w-72 shrink-0 border-r border-gray-100 bg-gray-50/50 p-6">
            {!displaySkill ? (
              <div className="text-sm text-gray-500">加载中...</div>
            ) : (
              <div className="flex flex-col gap-5">
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                    描述
                  </h4>
                  <p className="mt-1.5 text-sm leading-relaxed text-gray-700">
                    {displaySkill.description}
                  </p>
                </div>
                <div>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
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
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
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

          {/* 右侧：详细文件 */}
          <div className="min-h-0 flex-1 px-6 py-5">
            {isCustom ? (
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
                ) : customSkill ? (
                  <pre className="whitespace-pre-wrap rounded-xl bg-gray-50 p-5 text-sm leading-relaxed text-gray-800">
                    {customSkill.content}
                  </pre>
                ) : null}
              </ScrollArea>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-4 text-gray-400">
                <FileLockIcon className="h-12 w-12" />
                <p className="text-sm">公共 Skill 的详细文件内容不可查看</p>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function EmptySkill({ onCreateSkill }: { onCreateSkill: () => void }) {
  const { t } = useI18n();
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <SparklesIcon />
        </EmptyMedia>
        <EmptyTitle>{t.settings.skills.emptyTitle}</EmptyTitle>
        <EmptyDescription>
          {t.settings.skills.emptyDescription}
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Button onClick={onCreateSkill}>{t.settings.skills.emptyButton}</Button>
      </EmptyContent>
    </Empty>
  );
}
