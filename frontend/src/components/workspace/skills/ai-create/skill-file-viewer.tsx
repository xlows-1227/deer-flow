"use client";

import {
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  PencilIcon,
  RefreshCwIcon,
  SparklesIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { type BundledLanguage } from "shiki";
import { toast } from "sonner";
import { Streamdown } from "streamdown";

import { CodeBlock } from "@/components/ai-elements/code-block";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { parseSkillMarkdown } from "@/components/workspace/skills/skill-create-utils";
import { streamdownPlugins } from "@/core/streamdown";
import { checkCodeFile, getFileName } from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { isMarkdownPath } from "./utils";

function getDefaultContentMode(path: string): "edit" | "preview" {
  if (isMarkdownPath(path)) return "preview";
  if (checkCodeFile(path).isCodeFile) return "preview";
  return "edit";
}

function downloadTextFile(path: string, content: string) {
  const fileName = getFileName(path);
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

export interface OpenFileTab {
  path: string;
  content: string;
  dirty: boolean;
}

interface SkillFileViewerProps {
  tabs: OpenFileTab[];
  activePath: string | null;
  isLoading?: boolean;
  isSaving?: boolean;
  onSelectTab: (path: string) => void;
  onCloseTab: (path: string) => void;
  onChangeContent: (path: string, content: string) => void;
  onSave: (path: string) => void;
  onRefresh: (path: string) => void;
}

function MetadataCard({ content }: { content: string }) {
  const parsed = useMemo(() => parseSkillMarkdown(content), [content]);
  if (!parsed.name && !parsed.description) {
    return null;
  }

  return (
    <div className="mb-6 rounded-xl border border-gray-200 bg-gray-50/80 p-4">
      <div className="mb-3 text-xs font-medium text-gray-500">元数据</div>
      <pre className="overflow-x-auto font-mono text-xs leading-6 text-gray-700">{`---
name: ${parsed.name ?? "untitled-skill"}
${parsed.displayName ? `display_name: ${parsed.displayName}\n` : ""}description: ${parsed.description ?? "Custom DeerFlow skill"}
---`}</pre>
    </div>
  );
}

export function SkillFileViewer({
  tabs,
  activePath,
  isLoading,
  isSaving,
  onSelectTab,
  onCloseTab,
  onChangeContent,
  onSave,
  onRefresh,
}: SkillFileViewerProps) {
  const [contentMode, setContentMode] = useState<"edit" | "preview">("preview");
  const activeTab = tabs.find((tab) => tab.path === activePath) ?? null;

  useEffect(() => {
    if (!activeTab?.path) return;
    setContentMode(getDefaultContentMode(activeTab.path));
  }, [activeTab?.path]);

  async function handleCopy(content: string) {
    try {
      await navigator.clipboard.writeText(content);
      toast.success("已复制到剪贴板");
    } catch {
      toast.error("复制失败");
    }
  }

  if (!activeTab) {
    return (
      <div className="flex h-full flex-col items-center justify-center bg-white text-center">
        <SparklesIcon className="mb-3 size-10 text-gray-300" />
        <p className="text-sm font-medium text-gray-700">选择文件开始预览</p>
        <p className="mt-1 text-xs text-gray-400">
          在左侧文件树中点击 SKILL.md 或其他文件
        </p>
      </div>
    );
  }

  const isMarkdown = isMarkdownPath(activeTab.path);
  const codeFile = checkCodeFile(activeTab.path);
  const isCodeFile = codeFile.isCodeFile;
  const showModeToggle = isMarkdown || isCodeFile;
  const codeLanguage = isCodeFile
    ? (codeFile.language as BundledLanguage)
    : undefined;

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-gray-100 px-3 py-2">
        {tabs.map((tab) => (
          <button
            key={tab.path}
            type="button"
            className={cn(
              "inline-flex max-w-48 items-center gap-1 rounded-md border px-2.5 py-1 text-xs transition-colors",
              tab.path === activePath
                ? "border-sky-200 bg-sky-50 text-sky-700"
                : "border-transparent bg-gray-50 text-gray-600 hover:bg-gray-100",
            )}
            onClick={() => onSelectTab(tab.path)}
          >
            <span className="truncate">{tab.path}</span>
            {tab.dirty ? <span className="text-amber-500">•</span> : null}
            <span
              className="rounded p-0.5 hover:bg-black/5"
              onClick={(event) => {
                event.stopPropagation();
                onCloseTab(tab.path);
              }}
            >
              <XIcon className="size-3" />
            </span>
          </button>
        ))}
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-gray-100 px-4 py-2">
        {showModeToggle ? (
          <div className="inline-flex items-center rounded-lg border border-gray-200 bg-gray-50/80 p-0.5">
            <button
              type="button"
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-sm transition-colors",
                contentMode === "edit"
                  ? "bg-white text-gray-900 shadow-xs"
                  : "text-gray-600 hover:text-gray-900",
              )}
              onClick={() => setContentMode("edit")}
            >
              <PencilIcon className="size-4" />
              编辑
            </button>
            <button
              type="button"
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-sm transition-colors",
                contentMode === "preview"
                  ? "bg-white text-gray-900 shadow-xs"
                  : "text-gray-600 hover:text-gray-900",
              )}
              onClick={() => setContentMode("preview")}
            >
              <EyeIcon className="size-4" />
              预览
            </button>
          </div>
        ) : (
          <div />
        )}

        <div className="flex items-center gap-1">
          {activeTab.dirty ? (
            <Button
              variant="outline"
              size="sm"
              className="h-8"
              disabled={isSaving}
              onClick={() => onSave(activeTab.path)}
            >
              {isSaving ? "保存中" : "保存"}
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-8"
            onClick={() => onRefresh(activeTab.path)}
          >
            <RefreshCwIcon className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-8"
            aria-label="下载文件"
            onClick={() => {
              downloadTextFile(activeTab.path, activeTab.content);
              toast.success("已开始下载");
            }}
          >
            <DownloadIcon className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            className="size-8"
            aria-label="复制内容"
            onClick={() => void handleCopy(activeTab.content)}
          >
            <CopyIcon className="size-4" />
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-gray-400">
            加载文件中...
          </div>
        ) : contentMode === "preview" && isMarkdown ? (
          <div className="h-full min-h-0 overflow-y-auto">
            <div className="mx-auto w-full max-w-3xl p-6">
              <MetadataCard content={activeTab.content} />
              <div className="prose prose-sm max-w-none text-gray-800">
                <Streamdown {...streamdownPlugins}>
                  {activeTab.content}
                </Streamdown>
              </div>
            </div>
          </div>
        ) : contentMode === "preview" && isCodeFile && codeLanguage ? (
          <div className="h-full min-h-0 overflow-y-auto p-4">
            <CodeBlock
              code={activeTab.content}
              language={codeLanguage}
              showLineNumbers
              className="min-h-full border-gray-200"
            />
          </div>
        ) : (
          <Textarea
            value={activeTab.content}
            onChange={(event) =>
              onChangeContent(activeTab.path, event.target.value)
            }
            spellCheck={false}
            className="h-full min-h-0 flex-1 resize-none rounded-none border-0 bg-white p-5 font-mono text-sm leading-6 shadow-none focus-visible:ring-0"
          />
        )}
      </div>
    </div>
  );
}
