import type { Message } from "@langchain/langgraph-sdk";
import {
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
  extractContentFromMessage,
  extractReasoningContentFromMessage,
  formatMessageTime,
  getMessageTimestamp,
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
}: {
  className?: string;
  message: Message;
  isLoading?: boolean;
  threadId: string;
  feedback?: FeedbackData | null;
  runId?: string;
  showCopyButton?: boolean;
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
      />
      {timestamp && (
        <div
          className={cn(
            "mt-0.5 text-[10px] text-slate-400",
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
}: {
  className?: string;
  message: Message;
  isLoading?: boolean;
  threadId: string;
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

  const contentToDisplay = useMemo(() => {
    if (isHuman) {
      return rawContent ? stripUploadedFilesTag(rawContent) : "";
    }
    return rawContent ?? "";
  }, [rawContent, isHuman]);

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
    const messageResponse = contentToDisplay ? (
      <AIElementMessageResponse
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
          <AIElementMessageContent className="w-fit">
            {messageResponse}
          </AIElementMessageContent>
        )}
      </div>
    );
  }

  return (
    <AIElementMessageContent className={className}>
      {filesList}
      <MarkdownContent
        content={contentToDisplay}
        isLoading={isLoading}
        rehypePlugins={[...rehypePlugins, [rehypeKatex, { output: "html" }]]}
        className="my-3"
        components={components}
      />
    </AIElementMessageContent>
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
