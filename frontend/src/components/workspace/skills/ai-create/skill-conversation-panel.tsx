"use client";

import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { PuzzleIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { FlipDisplay } from "@/components/workspace/flip-display";
import { InputBox } from "@/components/workspace/input-box";
import { MessageList } from "@/components/workspace/messages";
import { useThreadSettings } from "@/core/settings";
import type { AgentThreadState } from "@/core/threads/types";

import { resolveSkillConversationTopic } from "./utils";

const SKILL_CREATOR_NAME = "skill-creator";

interface SkillConversationPanelProps {
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  isWelcomeMode: boolean;
  isHistoryLoading?: boolean;
  hasMoreHistory?: boolean;
  loadMoreHistory?: () => void;
  initialPrompt?: string;
  disabled?: boolean;
  showWelcomeSuggestions?: boolean;
  onSubmit: (message: PromptInputMessage) => void | Promise<void>;
  onStop: () => void;
}

function SkillConversationEmptyState() {
  return (
    <div className="flex flex-col items-center justify-center px-6 text-center">
      <div className="mb-6 flex size-14 items-center justify-center rounded-2xl bg-violet-50 text-violet-600">
        <PuzzleIcon className="size-7" />
      </div>
      <h2 className="font-['Poppins'] text-3xl leading-none text-gray-900">
        Chat to Build Great Skills
      </h2>
      <p className="mt-3 max-w-md text-sm text-gray-500">
        只需一句话，将你的想法转化为可执行的专业技能。
      </p>
    </div>
  );
}

export function SkillConversationPanel({
  threadId,
  thread,
  isWelcomeMode,
  isHistoryLoading,
  hasMoreHistory,
  loadMoreHistory,
  initialPrompt,
  disabled,
  showWelcomeSuggestions,
  onSubmit,
  onStop,
}: SkillConversationPanelProps) {
  const [settings, setSettings] = useThreadSettings(threadId);
  const [mounted, setMounted] = useState(false);
  const hasMessages = thread.messages.length > 0;
  const showEmptyHero = isWelcomeMode && !isHistoryLoading && !hasMessages;

  const topicTitle = useMemo(
    () => resolveSkillConversationTopic(thread),
    [thread, thread.messages, thread.values?.title],
  );

  const context = useMemo(
    () => ({
      ...settings.context,
      mode: settings.context.mode ?? "pro",
      skill_name: SKILL_CREATOR_NAME,
    }),
    [settings.context],
  );

  useEffect(() => {
    setMounted(true);
  }, []);

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      void onSubmit(message);
    },
    [onSubmit],
  );

  const handleChoiceSelect = useCallback(
    (choice: string) => {
      if (disabled || thread.isLoading) return;
      void onSubmit({ text: choice, files: [] });
    },
    [disabled, onSubmit, thread.isLoading],
  );

  const inputBox = mounted ? (
    <InputBox
      className="w-full bg-white shadow-md ring-1 ring-gray-200/80 *:data-[slot='input-group']:bg-white"
      footerExtensionClassName="bg-white"
      threadId={threadId}
      isWelcomeMode={showEmptyHero}
      autoFocus={showEmptyHero}
      initialValue={initialPrompt}
      lockedSkillName={SKILL_CREATOR_NAME}
      context={context}
      status={thread.error ? "error" : thread.isLoading ? "streaming" : "ready"}
      disabled={disabled}
      showWelcomeSuggestions={showWelcomeSuggestions}
      onContextChange={(nextContext) => setSettings("context", nextContext)}
      onSubmit={handleSubmit}
      onStop={onStop}
    />
  ) : (
    <div
      aria-hidden="true"
      className="h-28 w-full rounded-2xl border border-gray-200/80 bg-white shadow-md"
    />
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <header className="flex h-12 shrink-0 items-center border-b border-gray-100 px-4">
        <FlipDisplay
          uniqueKey={topicTitle}
          className="min-w-0 flex-1 truncate text-sm font-medium text-gray-900"
        >
          <span className="block truncate">{topicTitle}</span>
        </FlipDisplay>
      </header>

      {showEmptyHero ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto">
            <SkillConversationEmptyState />
          </div>
          <div className="shrink-0 border-t border-gray-100 bg-white px-4 pt-3 pb-5">
            {inputBox}
          </div>
        </div>
      ) : (
        <>
          <div className="min-h-0 flex-1 overflow-hidden">
            <MessageList
              className="size-full pt-2"
              threadId={threadId}
              thread={thread}
              paddingBottom={16}
              hasMoreHistory={hasMoreHistory}
              loadMoreHistory={loadMoreHistory}
              isHistoryLoading={isHistoryLoading}
              onChoiceSelect={handleChoiceSelect}
            />
          </div>
          <div className="shrink-0 border-t border-gray-100 bg-white px-3 pt-3 pb-4">
            {inputBox}
          </div>
        </>
      )}
    </div>
  );
}
