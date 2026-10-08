"use client";

import { useMemo } from "react";
import type { AnchorHTMLAttributes } from "react";

import {
  MessageResponse,
  type MessageResponseProps,
} from "@/components/ai-elements/message";
import { preprocessMarkdown } from "@/core/utils/markdown";
import { streamdownPlugins } from "@/core/streamdown";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";

function isExternalUrl(href: string | undefined): boolean {
  return !!href && /^https?:\/\//.test(href);
}

export type MarkdownContentProps = {
  content: string;
  isLoading: boolean;
  rehypePlugins: MessageResponseProps["rehypePlugins"];
  className?: string;
  remarkPlugins?: MessageResponseProps["remarkPlugins"];
  components?: MessageResponseProps["components"];
};

/**
 * 单条消息内容长度上限。LLM 退化时可能生成数万字的重复文本，
 * 完整渲染会导致主线程阻塞。超过此长度时截断并提示用户。
 */
const MAX_RENDERABLE_CONTENT_LENGTH = 20000;

/** Renders markdown content. */
export function MarkdownContent({
  content,
  isLoading,
  rehypePlugins,
  className,
  remarkPlugins = streamdownPlugins.remarkPlugins,
  components: componentsFromProps,
}: MarkdownContentProps) {
  const processedContent = useMemo(() => {
    const text = preprocessMarkdown(content);
    if (text.length <= MAX_RENDERABLE_CONTENT_LENGTH) return text;
    const truncated = text.slice(0, MAX_RENDERABLE_CONTENT_LENGTH);
    return `${truncated}\n\n> ⚠️ 内容过长（${text.length} 字符），已截断显示。`;
  }, [content]);

  const components = useMemo(() => {
    return {
      a: (props: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        if (typeof props.children === "string") {
          const match = /^citation:(.+)$/.exec(props.children);
          if (match) {
            const [, text] = match;
            return <CitationLink {...props}>{text}</CitationLink>;
          }
        }
        const { className, target, rel, ...rest } = props;
        const external = isExternalUrl(props.href);
        return (
          <a
            {...rest}
            className={cn(
              "text-primary decoration-primary/30 hover:decoration-primary/60 underline underline-offset-2 transition-colors",
              className,
            )}
            target={target ?? (external ? "_blank" : undefined)}
            rel={rel ?? (external ? "noopener noreferrer" : undefined)}
          />
        );
      },
      ...componentsFromProps,
    };
  }, [componentsFromProps]);

  if (!content) return null;

  // 流式期间跳过 markdown 解析（remark+rehype 同步执行会阻塞主线程，
  // 导致 token 累积后一次性渲染，用户看不到逐 token 增长）。
  // 直接用原始文本 + whitespace-pre-wrap 显示，React 文本节点更新是 O(1)。
  // 流式结束后切换回 Streamdown 渲染完整 markdown 格式。
  if (isLoading) {
    return (
      <div
        className={cn(
          "whitespace-pre-wrap break-words",
          className,
        )}
      >
        {content}
      </div>
    );
  }

  return (
    <MessageResponse
      className={className}
      remarkPlugins={remarkPlugins}
      rehypePlugins={rehypePlugins}
      components={components}
    >
      {processedContent}
    </MessageResponse>
  );
}
