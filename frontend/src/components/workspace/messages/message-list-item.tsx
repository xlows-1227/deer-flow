import type { Message } from "@langchain/langgraph-sdk";
import {
  AlertTriangleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  ExternalLinkIcon,
  FileIcon,
  LibraryIcon,
  Loader2Icon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";
import {
  memo,
  useCallback,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type ImgHTMLAttributes,
} from "react";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";

import { Loader } from "@/components/ai-elements/loader";
import {
  Message as AIElementMessage,
  MessageContent as AIElementMessageContent,
  MessageResponse as AIElementMessageResponse,
  MessageToolbar,
} from "@/components/ai-elements/message";
import {
  Reasoning,
  ReasoningContent,
  ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Task, TaskTrigger } from "@/components/ai-elements/task";
import { Badge } from "@/components/ui/badge";
import {
  deleteFeedback,
  upsertFeedback,
  type FeedbackData,
} from "@/core/api/feedback";
import { resolveArtifactURL } from "@/core/artifacts/utils";
import { userFileUrl } from "@/core/files/api";
import type { ReferencedFile } from "@/core/files/type";
import { useI18n } from "@/core/i18n/hooks";
import {
  detectToolOmissions,
  extractContentFromMessage,
  extractReasoningContentFromMessage,
  formatMessageTime,
  friendlyAiErrorMessage,
  getMessageTimestamp,
  getToolCalls,
  isAiMessage,
  parseUploadedFiles,
  stripUploadedFilesTag,
  type FileInMessage,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { humanMessagePlugins } from "@/core/streamdown";
import { cn } from "@/lib/utils";

import { CopyButton } from "../copy-button";

import { MarkdownContent } from "./markdown-content";

function FeedbackButtons({
  threadId,
  runId,
  initialFeedback,
}: {
  threadId: string;
  runId: string;
  initialFeedback: FeedbackData | null;
}) {
  const [feedback, setFeedback] = useState<FeedbackData | null>(
    initialFeedback,
  );
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleClick = useCallback(
    async (rating: number) => {
      if (isSubmitting) return;
      setIsSubmitting(true);
      try {
        if (feedback?.rating === rating) {
          await deleteFeedback(threadId, runId);
          setFeedback(null);
        } else {
          const result = await upsertFeedback(threadId, runId, rating);
          setFeedback(result);
        }
      } catch {
        // Revert on error — feedback state unchanged on catch
      } finally {
        setIsSubmitting(false);
      }
    },
    [threadId, runId, feedback, isSubmitting],
  );

  return (
    <div className="flex gap-1">
      <button
        type="button"
        className={cn(
          "text-muted-foreground hover:text-foreground rounded-md p-1 transition-colors",
          feedback?.rating === 1 && "text-foreground",
        )}
        onClick={() => handleClick(1)}
        disabled={isSubmitting}
      >
        <ThumbsUpIcon
          className={cn("size-4", feedback?.rating === 1 && "fill-current")}
        />
      </button>
      <button
        type="button"
        className={cn(
          "text-muted-foreground hover:text-foreground rounded-md p-1 transition-colors",
          feedback?.rating === -1 && "text-foreground",
        )}
        onClick={() => handleClick(-1)}
        disabled={isSubmitting}
      >
        <ThumbsDownIcon
          className={cn("size-4", feedback?.rating === -1 && "fill-current")}
        />
      </button>
    </div>
  );
}

export function MessageListItem({
  className,
  message,
  isLoading,
  feedback,
  runId,
  threadId,
  showCopyButton = true,
  precomputedToolNames,
  showTimestamp = true,
}: {
  className?: string;
  message: Message;
  isLoading?: boolean;
  threadId: string;
  feedback?: FeedbackData | null;
  runId?: string;
  showCopyButton?: boolean;
  precomputedToolNames?: string[][];
  showTimestamp?: boolean;
}) {
  const isHuman = message.type === "human";
  const timestamp = formatMessageTime(getMessageTimestamp(message));
  return (
    <AIElementMessage
      className={cn("group/conversation-message relative w-full", className)}
      from={isHuman ? "user" : "assistant"}
    >
      <MessageContent
        className={isHuman ? "w-fit" : "w-full"}
        message={message}
        isLoading={isLoading}
        threadId={threadId}
        precomputedToolNames={precomputedToolNames}
      />
      {showTimestamp && timestamp && (
        <div
          className={cn(
            "text-muted-foreground/65 mt-1 font-mono text-[10px] tracking-tight tabular-nums",
            isHuman ? "text-right" : "text-left",
          )}
        >
          {timestamp}
        </div>
      )}
      {!isLoading && showCopyButton && (
        <MessageToolbar
          className={cn(
            isHuman
              ? "absolute right-0 -bottom-9 left-0 justify-end"
              : "absolute right-0 bottom-0 left-0",
            "pointer-events-none z-20 opacity-0 transition-opacity delay-200 duration-300 group-hover/conversation-message:pointer-events-auto group-hover/conversation-message:opacity-100",
          )}
        >
          <div className="flex gap-1">
            <CopyButton
              clipboardData={
                extractContentFromMessage(message) ??
                extractReasoningContentFromMessage(message) ??
                ""
              }
            />
            {feedback !== undefined && runId && threadId && (
              <FeedbackButtons
                threadId={threadId}
                runId={runId}
                initialFeedback={feedback}
              />
            )}
          </div>
        </MessageToolbar>
      )}
    </AIElementMessage>
  );
}

/**
 * Custom image component that handles artifact URLs
 */
function MessageImage({
  src,
  alt,
  threadId,
  maxWidth = "90%",
  ...props
}: React.ImgHTMLAttributes<HTMLImageElement> & {
  threadId: string;
  maxWidth?: string;
}) {
  if (!src) return null;

  const imgClassName = cn("overflow-hidden rounded-lg", `max-w-[${maxWidth}]`);

  if (typeof src !== "string") {
    return <img className={imgClassName} src={src} alt={alt} {...props} />;
  }

  const url = src.startsWith("/mnt/") ? resolveArtifactURL(src, threadId) : src;

  return (
    <a href={url} target="_blank" rel="noopener noreferrer">
      <img className={imgClassName} src={url} alt={alt} {...props} />
    </a>
  );
}

function MessageContent_({
  className,
  message,
  isLoading = false,
  threadId,
  precomputedToolNames,
}: {
  className?: string;
  message: Message;
  isLoading?: boolean;
  threadId: string;
  precomputedToolNames?: string[][];
}) {
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const isHuman = message.type === "human";
  const components = useMemo(
    () => ({
      img: (props: ImgHTMLAttributes<HTMLImageElement>) => (
        <MessageImage {...props} threadId={threadId} maxWidth="90%" />
      ),
      a: ({ href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        if (href?.startsWith("/mnt/")) {
          const url = resolveArtifactURL(href, threadId);
          return (
            <a
              {...props}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
            />
          );
        }
        return <a {...props} href={href} />;
      },
    }),
    [threadId],
  );

  const rawContent = extractContentFromMessage(message);
  const reasoningContent = extractReasoningContentFromMessage(message);

  const files = useMemo(() => {
    const files = message.additional_kwargs?.files;
    if (!Array.isArray(files) || files.length === 0) {
      if (rawContent.includes("<uploaded_files>")) {
        // If the content contains the <uploaded_files> tag, we return the parsed files from the content for backward compatibility.
        return parseUploadedFiles(rawContent);
      }
      return null;
    }
    return files as FileInMessage[];
  }, [message.additional_kwargs?.files, rawContent]);

  // `@`-picked files from the chat input. The frontend ships them in the
  // human message's `additional_kwargs.referenced_files`; we surface them
  // as chips above the message text so the user has a visual reminder of
  // which library files they attached to this turn.
  const referencedFiles = useMemo<ReferencedFile[]>(() => {
    const raw = message.additional_kwargs?.referenced_files;
    if (!Array.isArray(raw) || raw.length === 0) {
      return [];
    }
    return raw.filter(
      (entry): entry is ReferencedFile =>
        typeof entry === "object" &&
        entry !== null &&
        typeof entry.id === "string" &&
        typeof entry.name === "string" &&
        typeof entry.path === "string",
    );
  }, [message.additional_kwargs?.referenced_files]);

  const friendlyErrorResult = useMemo(() => {
    if (isHuman) return null;
    const result = friendlyAiErrorMessage(rawContent ?? "");
    return result.tier === "none" ? null : result;
  }, [rawContent, isHuman]);

  const contentToDisplay = useMemo(() => {
    if (isHuman) {
      return rawContent ? stripUploadedFilesTag(rawContent) : "";
    }
    // Always preserve the real content — the error banner is rendered
    // separately below.  Previously, a detected error would replace the
    // entire content with a one-line friendly sentence, silently dropping
    // any legitimate text the model managed to produce before failing.
    //
    // Strip the <!--DF_RAW_ERROR:xxx--> HTML comment that the backend
    // appends when an LLM call fails mid-stream.  Leaving it in would
    // break Streamdown's markdown parser and produce a wall of raw text.
    return (rawContent ?? "").replace(/\n<!--DF_RAW_ERROR:[\s\S]*?-->\s*$/, "");
  }, [rawContent, isHuman]);

  /**
   * Whether the AI message body is substantially more than just the
   * error sentence itself.  When `false`, rendering both the banner and
   * the markdown would show the same text twice — so we skip markdown.
   * When `true` (partial content survived before the failure), we show
   * only the content without the misleading error banner.
   */
  const hasSubstantiveContent = useMemo(() => {
    if (!friendlyErrorResult) return !!contentToDisplay;
    const stripped = contentToDisplay
      .replace(/<!--DF_RAW_ERROR:[\s\S]*?-->\s*$/, "")
      .replace(friendlyErrorResult.message, "")
      .trim();
    // Anything left beyond a trivial amount is "substantive"
    return stripped.length >= 20;
  }, [contentToDisplay, friendlyErrorResult]);

  /**
   * Only show the error banner when the AI message has NO substantive
   * content.  When the model produced a real answer (even if the stream
   * ended with a non-critical error), the banner is misleading.
   */
  const showErrorBanner = friendlyErrorResult && !hasSubstantiveContent;

  const toolOmissionResult = useMemo(() => {
    if (isHuman || (!contentToDisplay && !(isAiMessage(message) && getToolCalls(message).length > 0))) {
      return { count: 0, toolNames: [] as string[][], content: contentToDisplay };
    }
    // If this AI message already has concrete tool_calls attached,
    // getMessageGroups will render a dedicated processing group with
    // real tool cards — don't also emit the omission banner to avoid
    // showing the same tool call twice.
    const hasConcreteToolCalls = isAiMessage(message) && getToolCalls(message).length > 0;
    const effectiveCount = (toolNames: string[][]) => hasConcreteToolCalls ? 0 : toolNames.length;
    // 如果有预计算的工具名，使用预计算的名称，但仍需清理内容中的标记
    if (precomputedToolNames && precomputedToolNames.length > 0) {
      const { cleaned } = detectToolOmissions(contentToDisplay, [message]);
      return { count: effectiveCount(precomputedToolNames), toolNames: precomputedToolNames, content: cleaned };
    }
    const result = detectToolOmissions(contentToDisplay, [message]);
    return { count: effectiveCount(result.toolNames), toolNames: result.toolNames, content: result.cleaned };
  }, [contentToDisplay, isHuman, message, precomputedToolNames]);

  const filesList =
    files && files.length > 0 ? (
      <RichFilesList files={files} threadId={threadId} />
    ) : null;

  const referencedFilesList =
    referencedFiles.length > 0 ? (
      <ReferencedFilesList files={referencedFiles} />
    ) : null;

  // Uploading state: mock AI message shown while files upload
  if (message.additional_kwargs?.element === "task") {
    return (
      <AIElementMessageContent className={className}>
        <Task defaultOpen={false}>
          <TaskTrigger title="">
            <div className="text-muted-foreground flex w-full cursor-default items-center gap-2 text-sm select-none">
              <Loader className="size-4" />
              <span>{contentToDisplay}</span>
            </div>
          </TaskTrigger>
        </Task>
      </AIElementMessageContent>
    );
  }

  // Reasoning-only AI message (no main response content yet)
  if (!isHuman && reasoningContent && !rawContent) {
    return (
      <AIElementMessageContent className={className}>
        <Reasoning isStreaming={isLoading}>
          <ReasoningTrigger />
          <ReasoningContent>{reasoningContent}</ReasoningContent>
        </Reasoning>
      </AIElementMessageContent>
    );
  }

  if (isHuman) {
    const isShortSingleLineMessage =
      contentToDisplay.length <= 12 && !contentToDisplay.includes("\n");
    const messageResponse = contentToDisplay ? (
      <AIElementMessageResponse
        className={cn(isShortSingleLineMessage && "whitespace-nowrap")}
        remarkPlugins={humanMessagePlugins.remarkPlugins}
        rehypePlugins={humanMessagePlugins.rehypePlugins}
        components={components}
        parseIncompleteMarkdown={false}
      >
        {contentToDisplay}
      </AIElementMessageResponse>
    ) : null;
    return (
      <div className={cn("ml-auto flex flex-col gap-2", className)}>
        {filesList}
        {referencedFilesList}
        {messageResponse && (
          <AIElementMessageContent
            className="w-fit"
            style={
              isShortSingleLineMessage
                ? { minWidth: `${contentToDisplay.length + 2.5}em` }
                : undefined
            }
          >
            {messageResponse}
          </AIElementMessageContent>
        )}
      </div>
    );
  }

  return (
    <AIElementMessageContent className={className}>
      {filesList}
      {showErrorBanner ? (
        <FriendlyAiErrorBanner
          tier={friendlyErrorResult!.tier as "known" | "fallback"}
          message={friendlyErrorResult!.message}
          original={friendlyErrorResult!.original}
        />
      ) : null}
      {hasSubstantiveContent ? (
        <MarkdownContent
          content={toolOmissionResult.content}
          isLoading={isLoading}
          rehypePlugins={[rehypeRaw, ...rehypePlugins, [rehypeKatex, { output: "html" }]]}
          className="assistant-prose my-3"
          components={components}
        />
      ) : !showErrorBanner && contentToDisplay ? (
        <MarkdownContent
          content={toolOmissionResult.content}
          isLoading={isLoading}
          rehypePlugins={[rehypeRaw, ...rehypePlugins, [rehypeKatex, { output: "html" }]]}
          className="assistant-prose my-3"
          components={components}
        />
      ) : null}
    </AIElementMessageContent>
  );
}
/**
 * Visually highlighted banner for LLM runtime errors detected via
 * {@link friendlyAiErrorMessage}.  Known errors get a mild warning look;
 * generic / unmapped errors get a stronger style plus a collapsible
 * "原始错误详情" section so users and admins can inspect the raw provider
 * response without ugly Python dict / HTTP body text leaking into the chat.
 */
function FriendlyAiErrorBanner({
  tier,
  message,
  original,
}: {
  tier: "known" | "fallback";
  message: string;
  original?: string;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const isFallback = tier === "fallback";
  const bannerStyle = isFallback
    ? "bg-destructive/10 border-destructive/30 text-destructive"
    : "bg-amber-500/10 border-amber-500/30 text-amber-800 dark:text-amber-300";
  const Chevron = open ? ChevronDownIcon : ChevronRightIcon;
  const rawText = (original ?? "").trim();
  const rawDiffers = Boolean(rawText && rawText !== message);

  const copyRaw = useCallback(async () => {
    if (!rawText) return;
    try {
      await navigator.clipboard.writeText(rawText);
      setCopied(true);
      const t = setTimeout(() => setCopied(false), 1500);
      // no-op cleanup, just clear the visual feedback flag
      return () => clearTimeout(t);
    } catch {
      /* clipboard API may be restricted on some browsers */
    }
    return undefined;
  }, [rawText]);

  return (
    <div
      className={cn(
        "my-3 rounded-lg border px-3 py-2.5 text-sm shadow-sm",
        bannerStyle,
      )}
      role={isFallback ? "alert" : "status"}
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangleIcon className="mt-0.5 size-[18px] shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold leading-snug">
              {isFallback ? "模型请求出错" : "请求提示"}
            </span>
          </div>
          <p className="mt-1 leading-relaxed whitespace-pre-wrap break-words">
            {message}
          </p>
          {rawDiffers && (
            <div className="mt-2 flex flex-wrap items-center gap-1">
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium opacity-80 transition-opacity hover:opacity-100 hover:bg-black/5 dark:hover:bg-white/10"
              >
                <Chevron className="size-3.5" />
                {open ? "收起详情" : "查看原始错误详情"}
              </button>
              <button
                type="button"
                onClick={copyRaw}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium opacity-70 transition-opacity hover:opacity-100 hover:bg-black/5 dark:hover:bg-white/10"
                aria-label="复制原始错误文本"
              >
                <CopyIcon className="size-3.5" />
                {copied ? "已复制" : "复制"}
              </button>
            </div>
          )}
          {open && rawDiffers && (
            <pre className="mt-2 max-h-48 overflow-auto rounded border bg-black/5 p-2 text-[11px] leading-relaxed dark:bg-white/10 font-mono opacity-90 whitespace-pre-wrap break-all select-text">
              {rawText}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}



/**
 * Get file extension and check helpers
 */
const getFileExt = (filename: string) =>
  filename.split(".").pop()?.toLowerCase() ?? "";

const FILE_TYPE_MAP: Record<string, string> = {
  json: "JSON",
  csv: "CSV",
  txt: "TXT",
  md: "Markdown",
  py: "Python",
  js: "JavaScript",
  ts: "TypeScript",
  tsx: "TSX",
  jsx: "JSX",
  html: "HTML",
  css: "CSS",
  xml: "XML",
  yaml: "YAML",
  yml: "YAML",
  pdf: "PDF",
  png: "PNG",
  jpg: "JPG",
  jpeg: "JPEG",
  gif: "GIF",
  svg: "SVG",
  zip: "ZIP",
  tar: "TAR",
  gz: "GZ",
};

const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"];

function getFileTypeLabel(filename: string): string {
  const ext = getFileExt(filename);
  return FILE_TYPE_MAP[ext] ?? (ext.toUpperCase() || "FILE");
}

function isImageFile(filename: string): boolean {
  return IMAGE_EXTENSIONS.includes(getFileExt(filename));
}

/**
 * Format bytes to human-readable size string
 */
function formatBytes(bytes: number): string {
  if (bytes === 0) return "—";
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

/**
 * List of files from additional_kwargs.files (with optional upload status)
 */
function RichFilesList({
  files,
  threadId,
}: {
  files: FileInMessage[];
  threadId: string;
}) {
  if (files.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap justify-end gap-2">
      {files.map((file, index) => (
        <RichFileCard
          key={`${file.filename}-${index}`}
          file={file}
          threadId={threadId}
        />
      ))}
    </div>
  );
}

/**
 * Single file card that handles FileInMessage (supports uploading state)
 */
function RichFileCard({
  file,
  threadId,
}: {
  file: FileInMessage;
  threadId: string;
}) {
  const { t } = useI18n();
  const isUploading = file.status === "uploading";
  const isImage = isImageFile(file.filename);

  if (isUploading) {
    return (
      <div className="bg-background border-border/40 flex max-w-50 min-w-30 flex-col gap-1 rounded-lg border p-3 opacity-60 shadow-sm">
        <div className="flex items-start gap-2">
          <Loader2Icon className="text-muted-foreground mt-0.5 size-4 shrink-0 animate-spin" />
          <span
            className="text-foreground truncate text-sm font-medium"
            title={file.filename}
          >
            {file.filename}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <Badge
            variant="secondary"
            className="rounded px-1.5 py-0.5 text-[10px] font-normal"
          >
            {getFileTypeLabel(file.filename)}
          </Badge>
          <span className="text-muted-foreground text-[10px]">
            {t.uploads.uploading}
          </span>
        </div>
      </div>
    );
  }

  if (!file.path) return null;

  const fileUrl = resolveArtifactURL(file.path, threadId);

  if (isImage) {
    return (
      <a
        href={fileUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="group border-border/40 relative block overflow-hidden rounded-lg border"
      >
        <img
          src={fileUrl}
          alt={file.filename}
          className="h-32 w-auto max-w-60 object-cover transition-transform group-hover:scale-105"
        />
      </a>
    );
  }

  return (
    <div className="bg-background border-border/40 flex max-w-50 min-w-30 flex-col gap-1 rounded-lg border p-3 shadow-sm">
      <div className="flex items-start gap-2">
        <FileIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <span
          className="text-foreground truncate text-sm font-medium"
          title={file.filename}
        >
          {file.filename}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <Badge
          variant="secondary"
          className="rounded px-1.5 py-0.5 text-[10px] font-normal"
        >
          {getFileTypeLabel(file.filename)}
        </Badge>
        <span className="text-muted-foreground text-[10px]">
          {formatBytes(file.size)}
        </span>
      </div>
    </div>
  );
}

/**
 * List of `@`-referenced files from the user document library. Mirrors
 * {@link RichFilesList} visually but renders library files (no upload
 * state, no per-message sandbox path) and links each card to the file
 * URL on the backend rather than to a per-thread artifact.
 */
function ReferencedFilesList({ files }: { files: ReferencedFile[] }) {
  if (files.length === 0) return null;
  return (
    <div
      className="flex flex-wrap justify-end gap-2"
      data-testid="referenced-files-in-message"
    >
      {files.map((file) => (
        <ReferencedFileCard key={file.id} file={file} />
      ))}
    </div>
  );
}

/**
 * Single library file card. Images get a thumbnail (clicking opens the
 * file in a new tab); other files get a compact name + type + size card
 * with a "From library" badge so the user can tell the source at a
 * glance.
 */
function ReferencedFileCard({ file }: { file: ReferencedFile }) {
  const { t } = useI18n();
  const fileUrl = userFileUrl(file.path);
  const isImage = isImageFile(file.name);

  if (isImage) {
    return (
      <a
        href={fileUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="group border-border/40 relative block overflow-hidden rounded-lg border"
      >
        <img
          src={fileUrl}
          alt={file.name}
          className="h-32 w-auto max-w-60 object-cover transition-transform group-hover:scale-105"
        />
      </a>
    );
  }

  return (
    <a
      href={fileUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="bg-background border-border/40 hover:border-border hover:bg-accent/30 flex max-w-50 min-w-30 cursor-pointer flex-col gap-1 rounded-lg border p-3 shadow-sm transition-colors"
      title={t.inputBox.referencedFileOpenInLibrary}
    >
      <div className="flex items-start gap-2">
        <FileIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <span
          className="text-foreground truncate text-sm font-medium"
          title={file.name}
        >
          {file.name}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <Badge
          variant="secondary"
          className="rounded px-1.5 py-0.5 text-[10px] font-normal"
        >
          <LibraryIcon className="mr-0.5 size-2.5" />
          {t.inputBox.referencedFileFromLibrary}
        </Badge>
        <span className="text-muted-foreground flex items-center gap-0.5 text-[10px]">
          {file.size > 0 ? formatBytes(file.size) : getFileTypeLabel(file.name)}
          <ExternalLinkIcon className="size-2.5" />
        </span>
      </div>
    </a>
  );
}

const MessageContent = memo(MessageContent_);
