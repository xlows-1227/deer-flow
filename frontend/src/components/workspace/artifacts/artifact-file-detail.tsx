import {
  Code2Icon,
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  FileIcon,
  LoaderIcon,
  Maximize2Icon,
  PackageIcon,
  SquareArrowOutUpRightIcon,
  XIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Streamdown } from "streamdown";

import {
  Artifact,
  ArtifactAction,
  ArtifactActions,
  ArtifactContent,
  ArtifactHeader,
  ArtifactTitle,
} from "@/components/ai-elements/artifact";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectItem } from "@/components/ui/select";
import {
  SelectContent,
  SelectGroup,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { CodeEditor } from "@/components/workspace/code-editor";
import { useArtifactContent } from "@/core/artifacts/hooks";
import {
  appendHtmlPreviewBaseHref,
  appendHtmlPreviewScrollRestoration,
  createHtmlPreviewScrollKey,
  getArtifactViewState,
  HTML_PREVIEW_SCROLL_MESSAGE_SOURCE,
} from "@/core/artifacts/preview";
import { urlOfArtifact } from "@/core/artifacts/utils";
import { useI18n } from "@/core/i18n/hooks";
import { findToolCallResult } from "@/core/messages/utils";
import { installSkill } from "@/core/skills/api";
import { streamdownPlugins } from "@/core/streamdown";
import { checkCodeFile, getFileName } from "@/core/utils/files";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { ArtifactLink } from "../citations/artifact-link";
import { useThread } from "../messages/context";
import { Tooltip } from "../tooltip";

import { useArtifacts } from "./context";

const WRITE_FILE_PREVIEW_REFRESH_INTERVAL_MS = 3000;

export function ArtifactFileDetail({
  className,
  filepath: filepathFromProps,
  threadId,
}: {
  className?: string;
  filepath: string;
  threadId: string;
}) {
  const { t } = useI18n();
  const { artifacts, setOpen, select } = useArtifacts();
  const { thread, isMock } = useThread();
  const isWriteFile = useMemo(() => {
    return filepathFromProps.startsWith("write-file:");
  }, [filepathFromProps]);
  const filepath = useMemo(() => {
    if (isWriteFile) {
      const url = new URL(filepathFromProps);
      return decodeURIComponent(url.pathname);
    }
    return filepathFromProps;
  }, [filepathFromProps, isWriteFile]);
  const isSkillFile = useMemo(() => {
    return filepath.endsWith(".skill");
  }, [filepath]);
  const { isCodeFile, language } = useMemo(() => {
    if (isWriteFile) {
      let language = checkCodeFile(filepath).language;
      language ??= "text";
      return { isCodeFile: true, language };
    }
    // Treat .skill files as markdown (they contain SKILL.md)
    if (isSkillFile) {
      return { isCodeFile: true, language: "markdown" };
    }
    return checkCodeFile(filepath);
  }, [filepath, isWriteFile, isSkillFile]);
  const isSupportPreview = useMemo(() => {
    return language === "html" || language === "markdown";
  }, [language]);
  const toolResult = (() => {
    if (!isWriteFile) {
      return undefined;
    }
    const url = new URL(filepathFromProps);
    const toolCallId = url.searchParams.get("tool_call_id");
    if (!toolCallId) {
      return undefined;
    }
    return findToolCallResult(toolCallId, thread.messages);
  })();
  const artifactViewState = getArtifactViewState({
    filepath: filepathFromProps,
    isSupportPreview,
    toolResult,
  });
  const { content, url } = useArtifactContent({
    threadId,
    filepath: filepathFromProps,
    enabled: isCodeFile && !isWriteFile,
  });

  const displayContent = content ?? "";
  const isWritingFile = isWriteFile && toolResult === undefined;
  const visibleContent = useThrottledValue(
    displayContent,
    isWritingFile ? WRITE_FILE_PREVIEW_REFRESH_INTERVAL_MS : 0,
    filepathFromProps,
  );

  const [viewMode, setViewMode] = useState<"code" | "preview">(
    artifactViewState.initialViewMode,
  );
  const [isInstalling, setIsInstalling] = useState(false);
  const [zoomDialogOpen, setZoomDialogOpen] = useState(false);
  const [zoomViewMode, setZoomViewMode] = useState<"code" | "preview">("preview");
  // Keep zoom dialog view mode in sync when the underlying file changes
  useEffect(() => {
    setZoomViewMode("preview");
  }, [filepathFromProps]);
  useEffect(() => {
    setViewMode(artifactViewState.initialViewMode);
  }, [artifactViewState.initialViewMode]);

  const handleInstallSkill = useCallback(async () => {
    if (isInstalling) return;

    setIsInstalling(true);
    try {
      const result = await installSkill({
        thread_id: threadId,
        path: filepath,
      });
      if (result.success) {
        toast.success(result.message);
      } else {
        toast.error(result.message ?? "Failed to install skill");
      }
    } catch (error) {
      console.error("Failed to install skill:", error);
      toast.error("Failed to install skill");
    } finally {
      setIsInstalling(false);
    }
  }, [threadId, filepath, isInstalling]);
  return (
    <Artifact className={cn(className)}>
      <ArtifactHeader className="px-2">
        <div className="flex items-center gap-2">
          <ArtifactTitle>
            {isWriteFile ? (
              <div className="px-2">{getFileName(filepath)}</div>
            ) : (
              <Select value={filepath} onValueChange={select}>
                <SelectTrigger className="border-none bg-transparent! shadow-none select-none focus:outline-0 active:outline-0 [&>svg]:hidden">
                  <SelectValue placeholder="Select a file" />
                </SelectTrigger>
                <SelectContent className="select-none">
                  <SelectGroup>
                    {(artifacts ?? []).map((filepath) => (
                      <SelectItem key={filepath} value={filepath}>
                        {getFileName(filepath)}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            )}
          </ArtifactTitle>
        </div>
        <div className="flex min-w-0 grow items-center justify-center">
          {artifactViewState.canPreview && (
            <div className="inline-flex items-center rounded-md border">
              <button
                type="button"
                onClick={() => setViewMode("code")}
                className={cn(
                  "inline-flex h-8 items-center justify-center rounded-l-md px-3 transition-colors",
                  viewMode === "code"
                    ? "bg-accent text-accent-foreground"
                    : "bg-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                title="代码"
              >
                <Code2Icon className="size-4" />
              </button>
              <button
                type="button"
                onClick={() => setViewMode("preview")}
                className={cn(
                  "inline-flex h-8 items-center justify-center rounded-r-md border-l px-3 transition-colors",
                  viewMode === "preview"
                    ? "bg-accent text-accent-foreground"
                    : "bg-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                title="预览"
              >
                <EyeIcon className="size-4" />
              </button>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <ArtifactActions>
            {!isWriteFile && filepath.endsWith(".skill") && (
              <Tooltip content={t.toolCalls.skillInstallTooltip}>
                <ArtifactAction
                  icon={isInstalling ? LoaderIcon : PackageIcon}
                  label={t.common.install}
                  tooltip={t.common.install}
                  disabled={
                    isInstalling ||
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
                  }
                  onClick={handleInstallSkill}
                />
              </Tooltip>
            )}
            <ArtifactAction
              icon={Maximize2Icon}
              label="放大预览"
              tooltip="放大预览"
              onClick={() => setZoomDialogOpen(true)}
            />
            {!isWriteFile && (
              <ArtifactAction
                icon={SquareArrowOutUpRightIcon}
                label={t.common.openInNewWindow}
                tooltip={t.common.openInNewWindow}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({ filepath, threadId, isMock }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            {isCodeFile && (
              <ArtifactAction
                icon={CopyIcon}
                label={t.clipboard.copyToClipboard}
                disabled={!content}
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(visibleContent ?? "");
                    toast.success(t.clipboard.copiedToClipboard);
                  } catch (error) {
                    toast.error("Failed to copy to clipboard");
                    console.error(error);
                  }
                }}
                tooltip={t.clipboard.copyToClipboard}
              />
            )}
            {!isWriteFile && (
              <ArtifactAction
                icon={DownloadIcon}
                label={t.common.download}
                tooltip={t.common.download}
                onClick={() => {
                  const w = window.open(
                    urlOfArtifact({
                      filepath,
                      threadId,
                      download: true,
                      isMock,
                    }),
                    "_blank",
                    "noopener,noreferrer",
                  );
                  if (w) w.opener = null;
                }}
              />
            )}
            <ArtifactAction
              icon={XIcon}
              label={t.common.close}
              onClick={() => setOpen(false)}
              tooltip={t.common.close}
            />
          </ArtifactActions>
        </div>
      </ArtifactHeader>
      <ArtifactContent className="p-0">
        {artifactViewState.canPreview &&
          viewMode === "preview" &&
          (language === "markdown" || language === "html") && (
            <ArtifactFilePreview
              content={visibleContent}
              language={language ?? "text"}
              scrollKey={filepathFromProps}
              url={url}
            />
          )}
        {isCodeFile && viewMode === "code" && (
          <CodeEditor
            className="size-full resize-none rounded-none border-none"
            value={visibleContent ?? ""}
            readonly
          />
        )}
        {!isCodeFile && (
          <NonCodeFilePreview
            filepath={filepath}
            threadId={threadId}
            isMock={isMock ?? false}
          />
        )}
      </ArtifactContent>

      <Dialog open={zoomDialogOpen} onOpenChange={setZoomDialogOpen}>
        <DialogContent
          className="max-h-[95vh] overflow-hidden p-0"
          style={{ width: "98vw", maxWidth: "1800px" }}
          showCloseButton={false}
        >
          <DialogHeader className="flex-row items-center justify-between border-b px-4 py-3">
            <DialogTitle className="text-base">{getFileName(filepath)}</DialogTitle>
            {language === "markdown" && (
              <ToggleGroup
                type="single"
                variant="outline"
                size="sm"
                value={zoomViewMode}
                onValueChange={(value) => {
                  if (value) setZoomViewMode(value as "code" | "preview");
                }}
              >
                <ToggleGroupItem value="code">
                  <Code2Icon />
                </ToggleGroupItem>
                <ToggleGroupItem value="preview">
                  <EyeIcon />
                </ToggleGroupItem>
              </ToggleGroup>
            )}
          </DialogHeader>
          <div className="max-h-[calc(95vh-60px)] overflow-auto p-4">
            {language === "markdown" && zoomViewMode === "preview" && (
              <Streamdown {...streamdownPlugins} components={{ a: ArtifactLink }}>
                {visibleContent ?? ""}
              </Streamdown>
            )}
            {language === "markdown" && zoomViewMode === "code" && (
              <CodeEditor
                className="size-full min-h-[50vh] resize-none rounded-none border-none"
                value={visibleContent ?? ""}
                readonly
              />
            )}
            {language === "html" && (
              <ZoomHtmlPreview
                content={visibleContent ?? ""}
                url={url}
              />
            )}
            {isCodeFile && language !== "markdown" && language !== "html" && (
              <CodeEditor
                className="size-full min-h-[50vh] resize-none rounded-none border-none"
                value={visibleContent ?? ""}
                readonly
              />
            )}
            {!isCodeFile && isImageFile(filepath) && (
              <div className="flex items-center justify-center">
                <img
                  src={urlOfArtifact({ filepath, threadId, isMock })}
                  alt={getFileName(filepath)}
                  className="max-h-[70vh] max-w-full object-contain"
                />
              </div>
            )}
            {!isCodeFile && !isImageFile(filepath) && (
              <div className="flex flex-col items-center justify-center gap-4 py-12 text-slate-400">
                <FileIcon className="size-12" />
                <p>该文件类型暂不支持弹窗预览</p>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </Artifact>
  );
}

export function ArtifactFilePreview({
  content,
  language,
  scrollKey,
  url,
}: {
  content: string;
  language: string;
  scrollKey: string;
  url?: string;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const scrollPositionRef = useRef({ x: 0, y: 0 });
  const scrollMessageKey = useMemo(
    () => createHtmlPreviewScrollKey(scrollKey),
    [scrollKey],
  );
  const [htmlPreviewUrl, setHtmlPreviewUrl] = useState<string>();

  useEffect(() => {
    scrollPositionRef.current = { x: 0, y: 0 };
  }, [scrollMessageKey]);

  useEffect(() => {
    if (language !== "html") {
      return;
    }

    const handleMessage = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) {
        return;
      }
      if (!isArtifactScrollMessage(event.data, scrollMessageKey)) {
        return;
      }

      if (event.data.type === "save") {
        const x = scrollCoordinate(event.data.x);
        const y = scrollCoordinate(event.data.y);
        if (x !== undefined && y !== undefined) {
          scrollPositionRef.current = { x, y };
        }
        return;
      }

      iframeRef.current?.contentWindow?.postMessage(
        {
          source: HTML_PREVIEW_SCROLL_MESSAGE_SOURCE,
          key: scrollMessageKey,
          type: "restore",
          ...scrollPositionRef.current,
        },
        "*",
      );
    };

    window.addEventListener("message", handleMessage);
    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [language, scrollMessageKey]);

  useEffect(() => {
    if (language !== "html") {
      setHtmlPreviewUrl(undefined);
      return;
    }

    const previewContent = appendHtmlPreviewScrollRestoration(
      appendHtmlPreviewBaseHref(content ?? "", url),
      scrollKey,
    );
    const blob = new Blob([previewContent], {
      type: "text/html;charset=utf-8",
    });
    const objectUrl = URL.createObjectURL(blob);
    setHtmlPreviewUrl(objectUrl);

    return () => {
      URL.revokeObjectURL(objectUrl);
    };
  }, [content, language, scrollKey, url]);

  if (language === "markdown") {
    return (
      <div className="size-full px-4">
        <Streamdown
          className="size-full"
          {...streamdownPlugins}
          components={{ a: ArtifactLink }}
        >
          {content ?? ""}
        </Streamdown>
      </div>
    );
  }
  if (language === "html") {
    return (
      <iframe
        ref={iframeRef}
        className="size-full"
        title="Artifact preview"
        sandbox="allow-scripts allow-forms"
        src={htmlPreviewUrl}
      />
    );
  }
  return null;
}

function ZoomHtmlPreview({ content, url }: { content: string; url?: string }) {
  const htmlUrl = useMemo(() => {
    const previewContent = appendHtmlPreviewScrollRestoration(
      appendHtmlPreviewBaseHref(content, url),
      "zoom",
    );
    const blob = new Blob([previewContent], { type: "text/html;charset=utf-8" });
    return URL.createObjectURL(blob);
  }, [content, url]);

  useEffect(() => {
    return () => {
      URL.revokeObjectURL(htmlUrl);
    };
  }, [htmlUrl]);

  return (
    <iframe
      className="h-[70vh] w-full"
      title="Zoom preview"
      sandbox="allow-scripts allow-forms"
      src={htmlUrl}
    />
  );
}

function isArtifactScrollMessage(
  data: unknown,
  key: string,
): data is {
  type: "save" | "restore-request";
  x?: unknown;
  y?: unknown;
} {
  return (
    typeof data === "object" &&
    data !== null &&
    "source" in data &&
    data.source === HTML_PREVIEW_SCROLL_MESSAGE_SOURCE &&
    "key" in data &&
    data.key === key &&
    "type" in data &&
    (data.type === "save" || data.type === "restore-request")
  );
}

function scrollCoordinate(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function useThrottledValue(
  value: string,
  intervalMs: number,
  resetKey: string,
) {
  const [throttledValue, setThrottledValue] = useState(value);
  const latestValueRef = useRef(value);
  const lastFlushAtRef = useRef(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resetKeyRef = useRef(resetKey);

  useEffect(() => {
    latestValueRef.current = value;

    if (resetKeyRef.current !== resetKey) {
      resetKeyRef.current = resetKey;
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = Date.now();
      setThrottledValue(value);
      return;
    }

    if (intervalMs <= 0) {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = Date.now();
      setThrottledValue(value);
      return;
    }

    const now = Date.now();
    const elapsed = now - lastFlushAtRef.current;
    if (lastFlushAtRef.current === 0 || elapsed >= intervalMs) {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      lastFlushAtRef.current = now;
      setThrottledValue(value);
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      lastFlushAtRef.current = Date.now();
      setThrottledValue(latestValueRef.current);
    }, intervalMs - elapsed);
  }, [intervalMs, resetKey, value]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return intervalMs <= 0 || resetKeyRef.current !== resetKey
    ? value
    : throttledValue;
}

const IMAGE_EXTENSIONS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "bmp",
  "ico",
  "svg",
]);

function isImageFile(filepath: string): boolean {
  const ext = filepath.split(".").pop()?.toLowerCase();
  return ext ? IMAGE_EXTENSIONS.has(ext) : false;
}

function NonCodeFilePreview({
  filepath,
  threadId,
  isMock,
}: {
  filepath: string;
  threadId: string;
  isMock: boolean;
}) {
  const { t } = useI18n();
  const artifactUrl = urlOfArtifact({ filepath, threadId, isMock });
  const downloadUrl = urlOfArtifact({
    filepath,
    threadId,
    isMock,
    download: true,
  });

  if (isImageFile(filepath)) {
    return (
      <div className="flex size-full items-center justify-center overflow-auto bg-slate-50 p-4">
        <img
          src={artifactUrl}
          alt={getFileName(filepath)}
          className="max-h-full max-w-full object-contain shadow-sm"
        />
      </div>
    );
  }

  return (
    <div className="flex size-full flex-col items-center justify-center gap-6 bg-slate-50 p-8">
      <FileIcon className="size-16 text-slate-300" />
      <div className="text-center">
        <p className="text-lg font-medium text-slate-700">
          {getFileName(filepath)}
        </p>
        <p className="mt-1 text-sm text-slate-400">
          Preview not available for this file type
        </p>
      </div>
      <div className="flex gap-3">
        <Button
          variant="outline"
          onClick={() => {
            const w = window.open(artifactUrl, "_blank", "noopener,noreferrer");
            if (w) w.opener = null;
          }}
        >
          <SquareArrowOutUpRightIcon className="mr-2 size-4" />
          {t.common.openInNewWindow}
        </Button>
        <Button
          variant="default"
          onClick={() => {
            const w = window.open(downloadUrl, "_blank", "noopener,noreferrer");
            if (w) w.opener = null;
          }}
        >
          <DownloadIcon className="mr-2 size-4" />
          {t.common.download}
        </Button>
      </div>
    </div>
  );
}
