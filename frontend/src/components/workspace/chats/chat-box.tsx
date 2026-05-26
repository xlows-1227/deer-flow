import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PanelRightOpenIcon } from "lucide-react";

import { env } from "@/env";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts";
import { useThread } from "../messages/context";

import { ConversationWorkspacePanel } from "./conversation-workspace-panel";

const MIN_PANEL_WIDTH = 240;
const MAX_PANEL_WIDTH = 800;
const DEFAULT_PANEL_WIDTH = 320;

const ChatBox: React.FC<{ children: React.ReactNode; threadId: string }> = ({
  children,
  threadId,
}) => {
  const { thread } = useThread();
  const threadIdRef = useRef(threadId);

  const {
    artifacts,
    open: artifactsOpen,
    autoOpen,
    setOpen: setArtifactsOpen,
    setArtifacts,
    select: selectArtifact,
    deselect,
  } = useArtifacts();

  const [autoSelectFirstArtifact, setAutoSelectFirstArtifact] = useState(true);

  const [width, setWidth] = useState(DEFAULT_PANEL_WIDTH);
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => {
    if (threadIdRef.current !== threadId) {
      threadIdRef.current = threadId;
      deselect();
    }

    setArtifacts(thread.values.artifacts);

    if (
      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" &&
      autoSelectFirstArtifact
    ) {
      if (thread?.values?.artifacts?.length > 0) {
        setAutoSelectFirstArtifact(false);
        selectArtifact(thread.values.artifacts[0]!);
      }
    }
  }, [
    threadId,
    autoSelectFirstArtifact,
    deselect,
    selectArtifact,
    setArtifacts,
    thread.values.artifacts,
  ]);

  useEffect(() => {
    if (autoOpen) {
      setArtifactsOpen(true);
    }
  }, [autoOpen, setArtifactsOpen]);

  const artifactPanelOpen = useMemo(() => {
    if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true") {
      return artifactsOpen && artifacts?.length > 0;
    }
    return artifactsOpen;
  }, [artifactsOpen, artifacts]);

  const handleResizeStart = useCallback(() => {
    setIsResizing(true);
  }, []);

  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = window.innerWidth - e.clientX;
      setWidth(
        Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, newWidth)),
      );
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [isResizing]);

  return (
    <div className="flex h-full w-full">
      <div className="relative min-w-0 flex-1">{children}</div>

      {artifactPanelOpen && (
        <div
          className={cn(
            "group/resizer z-20 w-1 shrink-0 transition-colors",
            isResizing
              ? "bg-blue-400/60"
              : "hover:bg-blue-400/40 cursor-col-resize bg-transparent",
          )}
          onMouseDown={handleResizeStart}
          title="拖拽调整宽度"
        />
      )}

      <aside
        className={cn(
          "h-full shrink-0 overflow-hidden border-l border-slate-200",
          !artifactPanelOpen && "border-l-0",
        )}
        style={{
          width: artifactPanelOpen ? width : 0,
          transition: isResizing ? "none" : "width 300ms ease-in-out",
        }}
      >
        <div className="h-full w-full">
          <ConversationWorkspacePanel
            threadId={threadId}
            onCollapse={() => setArtifactsOpen(false)}
          />
        </div>
      </aside>

      {!artifactPanelOpen && (
        <button
          type="button"
          className="flex h-full w-6 shrink-0 cursor-pointer items-center justify-center border-l border-slate-200 bg-background hover:bg-slate-50"
          onClick={() => setArtifactsOpen(true)}
          title="展开工作空间"
        >
          <PanelRightOpenIcon className="size-3.5 text-slate-400" />
        </button>
      )}
    </div>
  );
};

export { ChatBox };
