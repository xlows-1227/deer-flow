"use client";

import {
  ArrowLeftIcon,
  EyeIcon,
  FileTextIcon,
  PencilIcon,
  SaveIcon,
  WandSparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Streamdown } from "streamdown";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  buildSkillMarkdown,
  formatSkillValidationError,
  parseSkillMarkdown,
  slugifySkillName,
  syncSkillFrontmatter,
  validateSkillMarkdownContent,
} from "@/components/workspace/skills/skill-create-utils";
import { createCustomSkill } from "@/core/skills/api";
import { streamdownPlugins } from "@/core/streamdown";

const NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export default function CreateSkillPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [contentDirty, setContentDirty] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const normalizedName = useMemo(() => slugifySkillName(name), [name]);

  useEffect(() => {
    if (contentDirty) return;
    setContent(
      buildSkillMarkdown({
        name: normalizedName,
        displayName,
        description,
      }),
    );
  }, [contentDirty, description, displayName, normalizedName]);

  const canSubmit =
    NAME_PATTERN.test(normalizedName) &&
    displayName.trim().length > 0 &&
    description.trim().length > 0 &&
    content.trim().length > 0 &&
    !isSubmitting;

  async function handleSubmit() {
    if (!canSubmit) return;

    const syncedContent = syncSkillFrontmatter({
      content,
      name: normalizedName,
      displayName,
      description: description.trim(),
    });
    const validation = validateSkillMarkdownContent(
      syncedContent,
      normalizedName,
    );
    if (!validation.valid) {
      toast.error(validation.message);
      return;
    }

    setIsSubmitting(true);
    try {
      await createCustomSkill({
        name: normalizedName,
        description: description.trim(),
        content: syncedContent,
      });
      toast.success("Skill 已创建");
      router.push("/workspace/skills");
    } catch (error) {
      toast.error(
        error instanceof Error
          ? formatSkillValidationError(error.message)
          : "创建 Skill 失败",
      );
    } finally {
      setIsSubmitting(false);
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
            <h1 className="text-xl font-semibold text-gray-900">
              Create Skill
            </h1>
            <p className="mt-1 text-sm text-gray-500">
              直接编写并保存一个自定义 SKILL.md。
            </p>
          </div>
        </div>
        <Button disabled={!canSubmit} onClick={() => void handleSubmit()}>
          <SaveIcon className="h-4 w-4" />
          {isSubmitting ? "保存中" : "创建 Skill"}
        </Button>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto grid h-full w-full max-w-7xl gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
          <aside className="flex min-h-0 flex-col gap-4">
            <section className="rounded-lg border border-gray-200 bg-white p-5">
              <div className="mb-4 flex items-center gap-2">
                <FileTextIcon className="h-4 w-4 text-gray-500" />
                <h2 className="text-sm font-semibold text-gray-900">
                  基础信息
                </h2>
              </div>
              <div className="space-y-4">
                <label className="block space-y-2">
                  <span className="text-xs font-medium text-gray-600">
                    Skill name
                  </span>
                  <Input
                    value={name}
                    onChange={(event) => {
                      setName(event.target.value);
                    }}
                    placeholder="research-brief"
                    className="bg-white"
                  />
                  <span className="block text-xs text-gray-500">
                    保存名称：{normalizedName}
                  </span>
                </label>
                <label className="block space-y-2">
                  <span className="text-xs font-medium text-gray-600">
                    Display name
                  </span>
                  <Input
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                    placeholder=""
                    className="bg-white"
                  />
                  <span className="block text-xs text-gray-500">
                    {displayName
                      ? `文档中会写入：${displayName}（${normalizedName}）`
                      : "请输入中文显示名，例如：图像生成"}
                  </span>
                </label>
                <label className="block space-y-2">
                  <span className="text-xs font-medium text-gray-600">
                    Description
                  </span>
                  <Textarea
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder="为研究简报生成清晰、可复用的执行流程"
                    className="min-h-24 resize-none bg-white"
                  />
                </label>
              </div>
            </section>

            <Button variant="outline" asChild>
              <Link href="/workspace/skills/ai-create/new">
                <WandSparklesIcon className="h-4 w-4" />
                改用 AI Create
              </Link>
            </Button>
          </aside>

          <section className="flex min-h-[640px] flex-col rounded-lg border border-gray-200 bg-white">
            <div className="flex shrink-0 items-center justify-between border-b border-gray-100 px-5 py-3">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">
                  SKILL.md
                </h2>
                <p className="text-xs text-gray-500">
                  可直接编辑，frontmatter 中的 name 必须与保存名称一致。
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setContentDirty(false);
                    setContent(
                      buildSkillMarkdown({
                        name: normalizedName,
                        displayName,
                        description,
                      }),
                    );
                  }}
                >
                  重置模板
                </Button>
              </div>
            </div>
            <Tabs defaultValue="edit" className="min-h-0 flex-1 gap-0">
              <div className="shrink-0 border-b border-gray-100 px-5 py-2">
                <TabsList>
                  <TabsTrigger value="edit">
                    <PencilIcon className="h-4 w-4" />
                    编辑
                  </TabsTrigger>
                  <TabsTrigger value="preview">
                    <EyeIcon className="h-4 w-4" />
                    预览
                  </TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="edit" className="min-h-0">
                <Textarea
                  value={content}
                  onChange={(event) => {
                    const nextContent = event.target.value;
                    const parsed = parseSkillMarkdown(nextContent);
                    setContentDirty(true);
                    setContent(nextContent);
                    if (parsed.name) {
                      setName(parsed.name);
                    }
                    if (
                      parsed.displayName ||
                      nextContent.trimStart().startsWith("---")
                    ) {
                      setDisplayName(parsed.displayName);
                    }
                    if (parsed.description) {
                      setDescription(parsed.description);
                    }
                  }}
                  spellCheck={false}
                  className="h-full min-h-0 flex-1 resize-none rounded-none border-0 bg-white p-5 font-mono text-sm leading-6 shadow-none focus-visible:ring-0"
                />
              </TabsContent>
              <TabsContent value="preview" className="min-h-0 overflow-y-auto">
                <div className="prose prose-sm max-w-none p-6 text-gray-800">
                  <Streamdown {...streamdownPlugins}>{content}</Streamdown>
                </div>
              </TabsContent>
            </Tabs>
          </section>
        </div>
      </main>
    </div>
  );
}
