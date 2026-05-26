import { useEffect, useMemo, useRef, useState } from "react";

import { env } from "@/env";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts";
import { useThread } from "../messages/context";

import { ConversationWorkspacePanel } from "./conversation-workspace-panel";

/** Right-side workspace panel width. Keep in sync with inner content width. */
const WORKSPACE_PANEL_WIDTH_CLASS = "w-80";

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

  return (
    <div className="flex h-full w-full">
      <div className="relative min-w-0 flex-1">{children}</div>
      <aside
        className={cn(
          "h-full shrink-0 overflow-hidden border-l border-slate-200 transition-[width] duration-300 ease-in-out",
          artifactPanelOpen
            ? WORKSPACE_PANEL_WIDTH_CLASS
            : "w-0 border-l-0",
          !artifactsOpen && "opacity-0",
        )}
        aria-hidden={!artifactPanelOpen}
      >
        <div
          className={cn(
            "h-full transition-transform duration-300 ease-in-out",
            WORKSPACE_PANEL_WIDTH_CLASS,
            artifactPanelOpen ? "translate-x-0" : "translate-x-full",
          )}
        >
          <ConversationWorkspacePanel
            threadId={threadId}
            onClose={() => setArtifactsOpen(false)}
          />
        </div>
      </aside>
    </div>
  );
};

export { ChatBox };
