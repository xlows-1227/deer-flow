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
  detectToolOmissions,
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
  getToolCalls,
  hasContent,
  hasPresentFiles,
  hasReasoning,
  hasToolCalls,
  isAiMessage,
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
        <div className="ml-auto flex shrink-0 justify-start opacity-0 transition-opacity delay-150 duration-200 group-focus-within/assistant-turn:opacity-100 group-hover/assistant-turn:opacity-100">
          <CopyButton clipboardData={clipboardData} />
        </div>
      );
    },
    [],
  );

  const [timestampMap, setTimestampMap] = useState<Map<string, string>>(
    new Map(),
  );

  // NOTE: We intentionally do NOT fallback to new Date() when a message
  // lacks a backend timestamp.  The previous code did `new Date().toISOString()`
  // which caused timestamps to change on EVERY page refresh (because useState
  // resets to empty Map, then useEffect re-fills with "now").  Showing a blank
  // is vastly preferable to showing a constantly-changing fake time.
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
        // Only fill when we somehow DO have a valid timestamp to persist
        // across re-renders — do NOT synthesize with new Date().
        if (backendTimestamp && group.id && !next.has(group.id)) {
          next.set(group.id, backendTimestamp);
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
        if (isAiMessage(message)) {
          for (const toolCall of getToolCalls(message)) {
            if (toolCall.name === "task") {
              const args = toolCall.args as { subagent_type: string; description: string; prompt: string };
              tasksToUpdate.push({
                id: toolCall.id!,
                subagent_type: args.subagent_type,
                description: args.description,
                prompt: args.prompt,
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
        aiMessage ? getMessageTimestamp(aiMessage) : null,
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

      // Per-message timestamps are rendered in MessageListItem. Only show a
      // turn-level fallback for assistant turns that lack a backend timestamp.
      if (!aiMessage || getMessageTimestamp(aiMessage)) {
        return null;
      }

      const fallbackTimestamp =
        aiTimestamp ?? (groupId ? timestampMap.get(groupId) : undefined);
      if (!fallbackTimestamp) {
        return null;
      }

      return (
        <div className="mt-1 text-right text-[10px] text-slate-400">
          {fallbackTimestamp}
        </div>
      );
    },
    [thread.isLoading, timestampMap, tokenDebugSteps, tokenUsageInlineMode],
  );

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }

  return (
    <Conversation
      className={cn("flex size-full flex-col", className)}
    >
      <ConversationContent className="mx-auto flex min-h-full w-full max-w-[58rem] flex-col justify-end gap-7 px-4 pt-8 sm:px-6 lg:px-8">
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
                  group.type === "assistant" &&
                    "group/assistant-turn before:bg-foreground/14 relative max-w-[52rem] pl-4 before:absolute before:top-1 before:bottom-1 before:left-0 before:w-0.5 before:rounded-full sm:pl-5",
                )}
              >
                {group.messages.map((msg, messageIndex) => {
                  // 预计算：如果有[工具调用已省略]标记，从当前消息中提取工具名
                  // 注意：只传入当前消息，不要传入整个线程的messages，
                  // 否则会导致其他消息的工具调用被错误地应用到当前消息上
                  let precomputedToolNames: string[][] | undefined = undefined;
                  if (isAiMessage(msg)) {
                    const rawContent = extractContentFromMessage(msg);
                    if (rawContent && rawContent.includes("[工具调用")) {
                      const { toolNames } = detectToolOmissions(rawContent, [msg]);
                      if (toolNames.length > 0) {
                        precomputedToolNames = toolNames;
                      }
                    }
                  }
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
                      precomputedToolNames={precomputedToolNames}
                      showTimestamp={
                        group.type !== "assistant" ||
                        tokenUsageInlineMode !== "per_turn"
                      }
                    />
                  );
                })}
                {group.type === "assistant" && (
                  <div className="flex min-h-7 w-full items-center gap-2">
                    {renderTokenUsage({
                      messages: group.messages,
                      turnUsageMessages,
                      groupId: group.id,
                    })}
                    {renderAssistantCopyButton(
                      group.messages,
                      isAssistantMessageGroupStreaming(
                        group.messages,
                        streamingMessages,
                      ),
                    )}
                  </div>
                )}
              </div>
            );
          } else if (group.type === "assistant:clarification") {
            const message = group.messages[0];
            if (message && hasContent(message)) {
              const parsedChoices = group.id
                ? parsedChoicesByGroupId.get(group.id)
                : undefined;
              const rawContent = parsedChoices?.prompt ?? extractContentFromMessage(message);
              const { count, toolNames, cleaned } = detectToolOmissions(rawContent, [message]);
              return (
                <div key={groupKey} className="w-full">
                  <MarkdownContent
                    content={cleaned}
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
                {group.messages[0] && (hasContent(group.messages[0]) || hasToolCalls(group.messages[0])) && (() => {
                  const rawContent = extractContentFromMessage(group.messages[0]);
                  const { count, toolNames, cleaned } = detectToolOmissions(rawContent, group.messages[0] ? [group.messages[0]] : []);
                  return (
                    <>
                      <MarkdownContent
                        content={cleaned}
                        isLoading={thread.isLoading}
                        rehypePlugins={rehypePlugins}
                        className="mb-4"
                      />
                    </>
                  );
                })()}
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
              if (isAiMessage(message)) {
                for (const toolCall of getToolCalls(message)) {
                  if (toolCall.name === "task") {
                    const args = toolCall.args as { subagent_type: string; description: string; prompt: string };
                    const task: Subtask = {
                      id: toolCall.id!,
                      subagent_type: args.subagent_type,
                      description: args.description,
                      prompt: args.prompt,
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
              const taskIds = getToolCalls(message)
                .filter((toolCall) => toolCall.name === "task")
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
          // 处理 assistant:processing / 其他组 - 显示工具调用横幅 + ChainOfThought
          const processingContent = (() => {
            // 合并所有 AI 消息的内容
            const contents: string[] = [];
            for (const m of group.messages) {
              if (isAiMessage(m)) {
                const c = extractContentFromMessage(m);
                if (c) contents.push(c);
              }
            }
            const combinedRawContent = contents.join("\n\n");
            const { count, toolNames, cleaned } = detectToolOmissions(combinedRawContent, group.messages);
            // Processing groups render concrete tool cards via <MessageGroup/> —
            // the omission banner would be a redundant repeat of the same info,
            // so suppress it when any AI message in the group has tool_calls.
            const hasAnyConcreteToolCalls = group.messages.some(
              (m) => isAiMessage(m) && getToolCalls(m).length > 0,
            );
            const visibleCount = hasAnyConcreteToolCalls ? 0 : count;
            return { count: visibleCount, toolNames, cleaned, hasAnyContent: combinedRawContent.trim().length > 0 };
          })();
          return (
            <div key={`group-${groupKey}`} className="w-full max-w-[52rem]">
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
              {processingContent.hasAnyContent && processingContent.cleaned.trim().length > 0 && (
                <MarkdownContent
                  content={processingContent.cleaned}
                  isLoading={thread.isLoading}
                  rehypePlugins={rehypePlugins}
                  className="mb-4"
                />
              )}
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
