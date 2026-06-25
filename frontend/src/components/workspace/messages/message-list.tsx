import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { ChevronUpIcon, Loader2Icon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import {
  extractMessageChoiceOptions,
  type MessageChoiceOptions as ParsedMessageChoiceOptions,
} from "@/core/messages/choice-options";
import {
  buildTokenDebugSteps,
  type TokenUsageInlineMode,
} from "@/core/messages/usage-model";
import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  extractTextFromMessage,
  formatMessageTime,
  getAssistantTurnCopyData,
  getAssistantTurnUsageMessages,
  getMessageGroupRenderKey,
  getMessageGroups,
  getMessageRenderKey,
  getMessageTimestamp,
  getStreamingMessageLookup,
  hasContent,
  hasPresentFiles,
  hasReasoning,
  isAssistantMessageGroupStreaming,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import type { Subtask } from "@/core/tasks";
import { useUpdateSubtask } from "@/core/tasks/context";
import { parseSubtaskResult } from "@/core/tasks/subtask-result";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { CopyButton } from "../copy-button";
import { StreamingIndicator } from "../streaming-indicator";

import { MarkdownContent } from "./markdown-content";
import { MessageChoiceOptions } from "./message-choice-options";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import {
  MessageTokenUsageDebugList,
  MessageTokenUsageList,
} from "./message-token-usage";
import { MessageListSkeleton } from "./skeleton";
import { SubtaskCard } from "./subtask-card";

export const MESSAGE_LIST_DEFAULT_PADDING_BOTTOM = 24;

const LOAD_MORE_HISTORY_THROTTLE_MS = 1200;

function LoadMoreHistoryIndicator({
  isLoading,
  hasMore,
  loadMore,
}: {
  isLoading?: boolean;
  hasMore?: boolean;
  loadMore?: () => void;
}) {
  const { t } = useI18n();
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoadRef = useRef(0);

  const throttledLoadMore = useCallback(() => {
    if (!hasMore || isLoading) {
      return;
    }

    const now = Date.now();
    const remaining =
      LOAD_MORE_HISTORY_THROTTLE_MS - (now - lastLoadRef.current);

    if (remaining <= 0) {
      lastLoadRef.current = now;
      loadMore?.();
      return;
    }

    if (timeoutRef.current) {
      return;
    }

    timeoutRef.current = setTimeout(() => {
      timeoutRef.current = null;
      if (!hasMore || isLoading) {
        return;
      }
      lastLoadRef.current = Date.now();
      loadMore?.();
    }, remaining);
  }, [hasMore, isLoading, loadMore]);

  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasMore) {
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          throttledLoadMore();
        }
      },
      {
        rootMargin: "120px 0px 0px 0px",
      },
    );

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [hasMore, throttledLoadMore]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  if (!hasMore && !isLoading) {
    return null;
  }

  return (
    <div ref={sentinelRef} className="flex w-full justify-center">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="text-muted-foreground hover:text-foreground rounded-full px-3"
        disabled={(isLoading ?? false) || !hasMore}
        onClick={throttledLoadMore}
      >
        {isLoading ? (
          <>
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            {t.common.loading}
          </>
        ) : (
          <>
            <ChevronUpIcon className="mr-2 size-4" />
            {t.common.loadMore}
          </>
        )}
      </Button>
    </div>
  );
}

export function MessageList({
  className,
  threadId,
  thread,
  paddingBottom = MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
  tokenUsageInlineMode = "off",
  hasMoreHistory,
  loadMoreHistory,
  isHistoryLoading,
  onChoiceSelect,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  paddingBottom?: number;
  tokenUsageInlineMode?: TokenUsageInlineMode;
  hasMoreHistory?: boolean;
  loadMoreHistory?: () => void;
  isHistoryLoading?: boolean;
  onChoiceSelect?: (choice: string) => void;
}) {
  const { t } = useI18n();
  const rehypePlugins = useRehypeSplitWordsIntoSpans(thread.isLoading);
  const updateSubtask = useUpdateSubtask();
  const messages = thread.messages;
  const groupedMessages = getMessageGroups(messages);
  const parsedChoicesByGroupId = useMemo(() => {
    const parsed = new Map<string, ParsedMessageChoiceOptions>();

    for (const group of groupedMessages) {
      if (group.type !== "assistant:clarification" || !group.id) {
        continue;
      }
      const message = group.messages[0];
      if (!message || !hasContent(message)) {
        continue;
      }
      const choices = extractMessageChoiceOptions(
        extractContentFromMessage(message),
      );
      if (choices) {
        parsed.set(group.id, choices);
      }
    }

    return parsed;
  }, [groupedMessages]);
  const activeChoiceGroupId = useMemo(() => {
    if (thread.isLoading || !onChoiceSelect) {
      return null;
    }

    for (let index = groupedMessages.length - 1; index >= 0; index -= 1) {
      const group = groupedMessages[index];
      if (!group) {
        continue;
      }
      if (group.type === "human") {
        return null;
      }
      if (group.id && parsedChoicesByGroupId.has(group.id)) {
        return group.id;
      }
    }

    return null;
  }, [
    groupedMessages,
    onChoiceSelect,
    parsedChoicesByGroupId,
    thread.isLoading,
  ]);
  const turnUsageMessagesByGroupIndex =
    getAssistantTurnUsageMessages(groupedMessages);
  const tokenDebugSteps = useMemo(
    () => buildTokenDebugSteps(messages, t),
    [messages, t],
  );
  const streamingMessages = useMemo(
    () =>
      getStreamingMessageLookup(
        messages,
        thread.isLoading,
        thread.getMessagesMetadata,
      ),
    [messages, thread.getMessagesMetadata, thread.isLoading],
  );

  const renderAssistantCopyButton = useCallback(
    (messages: Message[], isStreaming: boolean) => {
      const clipboardData = getAssistantTurnCopyData(messages, { isStreaming });

      if (!clipboardData) {
        return null;
      }

      return (
        <div className="mt-2 flex justify-start opacity-0 transition-opacity delay-200 duration-300 group-hover/assistant-turn:opacity-100">
          <CopyButton clipboardData={clipboardData} />
        </div>
      );
    },
    [],
  );

  const [timestampMap, setTimestampMap] = useState<Map<string, string>>(
    new Map(),
  );

  // Populate frontend-render timestamps for groups that do not have a backend
  // timestamp.  This is done in an effect to avoid mutating refs during render.
  useEffect(() => {
    setTimestampMap((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const group of groupedMessages) {
        if (group.type !== "assistant") continue;
        const aiMessage = group.messages.find((m) => m.type === "ai");
        const backendTimestamp = formatMessageTime(
          getMessageTimestamp(aiMessage ?? group.messages[0]!),
        );
        if (!backendTimestamp && group.id && !next.has(group.id)) {
          next.set(group.id, formatMessageTime(new Date().toISOString()));
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [groupedMessages]);

  // Sync subagent task states from the rendered message groups.  Keeping this
  // in an effect avoids calling setState while rendering.
  useEffect(() => {
    const tasksToUpdate: Subtask[] = [];
    for (const group of groupedMessages) {
      if (group.type !== "assistant:subagent") continue;
      for (const message of group.messages) {
        if (message.type === "ai") {
          for (const toolCall of message.tool_calls ?? []) {
            if (toolCall.name === "task") {
              tasksToUpdate.push({
                id: toolCall.id!,
                subagent_type: toolCall.args.subagent_type,
                description: toolCall.args.description,
                prompt: toolCall.args.prompt,
                status: "in_progress",
              });
            }
          }
        } else if (message.type === "tool") {
          const taskId = message.tool_call_id;
          if (taskId) {
            const parsed = parseSubtaskResult(extractTextFromMessage(message));
            tasksToUpdate.push({ id: taskId, ...parsed } as Subtask);
          }
        }
      }
    }
    for (const task of tasksToUpdate) {
      updateSubtask(task);
    }
  }, [groupedMessages, updateSubtask]);

  const renderTokenUsage = useCallback(
    ({
      messages,
      turnUsageMessages,
      inlineDebug = true,
      debugMessageIds,
      groupId,
    }: {
      messages: Message[];
      turnUsageMessages?: Message[] | null;
      inlineDebug?: boolean;
      debugMessageIds?: string[];
      groupId?: string;
    }) => {
      const aiMessage = messages.find((m) => m.type === "ai");
      let aiTimestamp = formatMessageTime(
        getMessageTimestamp(aiMessage ?? messages[0]!),
      );

      // Fallback: use frontend render time if no backend timestamp
      if (!aiTimestamp && groupId) {
        aiTimestamp = timestampMap.get(groupId)!;
      }

      if (tokenUsageInlineMode === "per_turn") {
        return (
          <MessageTokenUsageList
            enabled={true}
            isLoading={thread.isLoading}
            messages={turnUsageMessages ?? []}
            timestamp={aiTimestamp}
          />
        );
      }

      if (tokenUsageInlineMode === "step_debug" && inlineDebug) {
        const messageIds = new Set(
          debugMessageIds ??
            messages
              .filter((message) => message.type === "ai")
              .map((message) => message.id)
              .filter((id): id is string => typeof id === "string"),
        );
        return (
          <>
            <MessageTokenUsageDebugList
              enabled={true}
              isLoading={thread.isLoading}
              steps={tokenDebugSteps.filter((step) =>
                messageIds.has(step.messageId),
              )}
            />
            {aiTimestamp && (
              <div className="mt-1 text-right text-[10px] text-slate-400">
                {aiTimestamp}
              </div>
            )}
          </>
        );
      }

      if (aiTimestamp) {
        return (
          <div className="mt-1 text-right text-[10px] text-slate-400">
            {aiTimestamp}
          </div>
        );
      }

      return null;
    },
    [thread.isLoading, timestampMap, tokenDebugSteps, tokenUsageInlineMode],
  );

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }

  return (
    <Conversation
      className={cn("flex size-full flex-col justify-center", className)}
    >
      <ConversationContent className="mx-auto w-full max-w-(--container-width-md) gap-8 pt-8">
        <LoadMoreHistoryIndicator
          isLoading={isHistoryLoading}
          hasMore={hasMoreHistory}
          loadMore={loadMoreHistory}
        />
        {groupedMessages.map((group, groupIndex) => {
          const turnUsageMessages = turnUsageMessagesByGroupIndex[groupIndex];
          const groupKey = getMessageGroupRenderKey(group, groupIndex);

          if (group.type === "human" || group.type === "assistant") {
            return (
              <div
                key={groupKey}
                className={cn(
                  "w-full",
                  group.type === "assistant" && "group/assistant-turn",
                )}
              >
                {group.messages.map((msg, messageIndex) => {
                  return (
                    <MessageListItem
                      key={getMessageRenderKey(
                        group,
                        groupIndex,
                        msg,
                        messageIndex,
                      )}
                      message={msg}
                      isLoading={thread.isLoading}
                      threadId={threadId}
                      showCopyButton={group.type !== "assistant"}
                    />
                  );
                })}
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                  groupId: group.id,
                })}
                {group.type === "assistant" &&
                  renderAssistantCopyButton(
                    group.messages,
                    isAssistantMessageGroupStreaming(
                      group.messages,
                      streamingMessages,
                    ),
                  )}
              </div>
            );
          } else if (group.type === "assistant:clarification") {
            const message = group.messages[0];
            if (message && hasContent(message)) {
              const parsedChoices = group.id
                ? parsedChoicesByGroupId.get(group.id)
                : undefined;
              return (
                <div key={groupKey} className="w-full">
                  <MarkdownContent
                    content={
                      parsedChoices?.prompt ??
                      extractContentFromMessage(message)
                    }
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                  />
                  {parsedChoices && (
                    <MessageChoiceOptions
                      options={parsedChoices.options}
                      disabled={group.id !== activeChoiceGroupId}
                      onSelect={onChoiceSelect}
                    />
                  )}
                  {renderTokenUsage({
                    messages: group.messages,
                    turnUsageMessages,
                  })}
                </div>
              );
            }
            return null;
          } else if (group.type === "assistant:present-files") {
            const files: string[] = [];
            for (const message of group.messages) {
              if (hasPresentFiles(message)) {
                const presentFiles = extractPresentFilesFromMessage(message);
                files.push(...presentFiles);
              }
            }
            return (
              <div className="w-full" key={groupKey}>
                {group.messages[0] && hasContent(group.messages[0]) && (
                  <MarkdownContent
                    content={extractContentFromMessage(group.messages[0])}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                    className="mb-4"
                  />
                )}
                <ArtifactFileList files={files} threadId={threadId} />
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                  groupId: group.id,
                })}
              </div>
            );
          } else if (group.type === "assistant:subagent") {
            const tasks = new Set<Subtask>();
            for (const message of group.messages) {
              if (message.type === "ai") {
                for (const toolCall of message.tool_calls ?? []) {
                  if (toolCall.name === "task") {
                    const task: Subtask = {
                      id: toolCall.id!,
                      subagent_type: toolCall.args.subagent_type,
                      description: toolCall.args.description,
                      prompt: toolCall.args.prompt,
                      status: "in_progress",
                    };
                    tasks.add(task);
                  }
                }
              } else if (message.type === "tool") {
                const taskId = message.tool_call_id;
                if (taskId) {
                  const parsed = parseSubtaskResult(
                    extractTextFromMessage(message),
                  );
                  tasks.add({ id: taskId, ...parsed } as Subtask);
                }
              }
            }

            const results: React.ReactNode[] = [];
            const subagentDebugMessageIds: string[] = [];
            if (tasks.size > 0) {
              results.push(
                <div
                  key="subtask-count"
                  className="text-muted-foreground pt-2 text-sm font-normal"
                >
                  {t.subtasks.executing(tasks.size)}
                </div>,
              );
            }
            for (const message of group.messages.filter(
              (message) => message.type === "ai",
            )) {
              if (hasReasoning(message)) {
                results.push(
                  <MessageGroup
                    key={"thinking-group-" + message.id}
                    messages={[message]}
                    isLoading={thread.isLoading}
                    tokenDebugSteps={tokenDebugSteps.filter(
                      (step) => step.messageId === message.id,
                    )}
                    showTokenDebugSummaries={
                      tokenUsageInlineMode === "step_debug"
                    }
                  />,
                );
              } else if (message.id) {
                subagentDebugMessageIds.push(message.id);
              }
              const taskIds = message.tool_calls
                ?.filter((toolCall) => toolCall.name === "task")
                .map((toolCall) => toolCall.id);
              for (const taskId of taskIds ?? []) {
                results.push(
                  <SubtaskCard
                    key={"task-group-" + taskId}
                    taskId={taskId!}
                    isLoading={thread.isLoading}
                  />,
                );
              }
            }
            return (
              <div
                key={`subtask-group-${groupKey}`}
                className="relative z-1 flex flex-col gap-2"
              >
                {results}
                {renderTokenUsage({
                  messages: group.messages,
                  turnUsageMessages,
                  debugMessageIds: subagentDebugMessageIds,
                  groupId: group.id,
                })}
              </div>
            );
          }
          return (
            <div key={`group-${groupKey}`} className="w-full">
              <MessageGroup
                messages={group.messages}
                isLoading={thread.isLoading}
                tokenDebugSteps={tokenDebugSteps.filter((step) =>
                  group.messages.some(
                    (message) => message.id === step.messageId,
                  ),
                )}
                showTokenDebugSummaries={tokenUsageInlineMode === "step_debug"}
              />
              {renderTokenUsage({
                messages: group.messages,
                turnUsageMessages,
                inlineDebug: false,
                groupId: group.id,
              })}
            </div>
          );
        })}
        {thread.isLoading && <StreamingIndicator className="my-4" />}
        <div style={{ height: `${paddingBottom}px` }} />
      </ConversationContent>
    </Conversation>
  );
}
