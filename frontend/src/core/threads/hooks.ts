import type { AIMessage, Message, Run } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";

import { getAPIClient } from "../api";
import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";
import { useI18n } from "../i18n/hooks";
import {
  extractTextFromMessage,
  getMessageTimestamp,
  repairDynamicContextUserMessageOrder,
  stripUploadedFilesTag,
  type FileInMessage,
} from "../messages/utils";
import { sandboxFilesQueryKey } from "../sandbox";
import type { LocalSettings } from "../settings";
import { useUpdateSubtask } from "../tasks/context";
import type { UploadedFileInfo } from "../uploads";
import { promptInputFilePartToFile, uploadFiles } from "../uploads";

import { fetchThreadTokenUsage } from "./api";
import { threadTokenUsageQueryKey } from "./token-usage";
import type {
  AgentThread,
  AgentThreadState,
  RunMessage,
  ThreadTokenUsageResponse,
} from "./types";

export type ToolEndEvent = {
  name: string;
  data: unknown;
};

export type ThreadStreamOptions = {
  threadId?: string | null | undefined;
  context: LocalSettings["context"];
  threadMetadata?: Record<string, unknown>;
  isMock?: boolean;
  onSend?: (threadId: string) => void;
  onStart?: (threadId: string, runId: string) => void;
  onFinish?: (state: AgentThreadState) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
};

type SendMessageOptions = {
  additionalKwargs?: Record<string, unknown>;
  multitaskStrategy?: "reject" | "interrupt" | "rollback" | "enqueue";
};

const THREAD_RUNS_PAGE_SIZE = 100;
const RUN_MESSAGES_PAGE_SIZE = 200;

type RunMessagesResponse = {
  data?: RunMessage[];
  has_more?: boolean;
  hasMore?: boolean;
};

function waitForNextPaint(): Promise<void> {
  if (typeof window === "undefined" || !window.requestAnimationFrame) {
    return new Promise((resolve) => setTimeout(resolve, 0));
  }

  return new Promise((resolve) => {
    let resolved = false;
    const finish = () => {
      if (resolved) {
        return;
      }
      resolved = true;
      resolve();
    };
    const timeout = window.setTimeout(finish, 50);
    window.requestAnimationFrame(() => {
      window.clearTimeout(timeout);
      finish();
    });
  });
}

function uploadedFileSizeToNumber(size: UploadedFileInfo["size"]): number {
  const normalized =
    typeof size === "string" ? Number.parseInt(size, 10) : size;
  return Number.isFinite(normalized) ? normalized : 0;
}

function isNonEmptyString(value: string | undefined): value is string {
  return typeof value === "string" && value.length > 0;
}

function messageIdentity(message: Message): string | undefined {
  if (
    "tool_call_id" in message &&
    typeof message.tool_call_id === "string" &&
    message.tool_call_id.length > 0
  ) {
    return `tool:${message.tool_call_id}`;
  }
  if (typeof message.id === "string" && message.id.length > 0) {
    return `message:${message.id}`;
  }
  return undefined;
}

function dedupeMessagesByIdentity(messages: Message[]): Message[] {
  const lastIndexByIdentity = new Map<string, number>();

  messages.forEach((message, index) => {
    const identity = messageIdentity(message);
    if (identity) {
      lastIndexByIdentity.set(identity, index);
    }
  });

  return messages.filter((message, index) => {
    const identity = messageIdentity(message);
    return !identity || lastIndexByIdentity.get(identity) === index;
  });
}

function withMessageTimestamp(
  message: Message,
  timestamp?: string | null,
): Message {
  if (!timestamp || getMessageTimestamp(message)) {
    return message;
  }

  return {
    ...message,
    additional_kwargs: {
      ...(message.additional_kwargs ?? {}),
      timestamp,
    },
  } as Message;
}

function mergeMissingTimestamps(
  sourceMessages: Message[],
  targetMessages: Message[],
): Message[] {
  const timestampByIdentity = new Map<string, string>();

  for (const message of sourceMessages) {
    const identity = messageIdentity(message);
    const timestamp = getMessageTimestamp(message);
    if (identity && timestamp) {
      timestampByIdentity.set(identity, timestamp);
    }
  }

  if (timestampByIdentity.size === 0) {
    return targetMessages;
  }

  return targetMessages.map((message) => {
    const identity = messageIdentity(message);
    return withMessageTimestamp(
      message,
      identity ? timestampByIdentity.get(identity) : null,
    );
  });
}

function findLatestUnloadedRunIndex(
  runs: Run[],
  loadedRunIds: ReadonlySet<string>,
): number {
  for (let i = runs.length - 1; i >= 0; i--) {
    const run = runs[i];
    if (run && !loadedRunIds.has(run.run_id)) {
      return i;
    }
  }
  return -1;
}

function getRunCreatedAtMs(run: Run): number {
  const createdAt = (run as { created_at?: string | null }).created_at;
  if (!createdAt) {
    return 0;
  }
  const timestamp = Date.parse(createdAt);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function sortRunsChronologically(runs: Run[]): Run[] {
  return [...runs].sort((a, b) => getRunCreatedAtMs(a) - getRunCreatedAtMs(b));
}

export function mergeLoadedRunMessages(
  runs: Run[],
  messagesByRunId: ReadonlyMap<string, Message[]>,
  appendedMessages: Message[] = [],
): Message[] {
  return dedupeMessagesByIdentity([
    ...sortRunsChronologically(runs).flatMap(
      (run) => messagesByRunId.get(run.run_id) ?? [],
    ),
    ...appendedMessages,
  ]);
}

function messageIsAssistantSide(message: Message): boolean {
  return message.type === "ai" || message.type === "tool";
}

// During streaming, the backend can temporarily expose the current turn as
// [AI/tool..., human]. Only repair that narrow tail shape. Historical slices
// may contain multiple user turns; moving the first human there scrambles the
// conversation.
function moveSingleTrailingHumanInputToFront(messages: Message[]): Message[] {
  const humanIndexes = messages.flatMap((message, index) =>
    message.type === "human" ? [index] : [],
  );
  if (humanIndexes.length !== 1) {
    return messages;
  }
  const firstHumanIndex = humanIndexes[0]!;
  if (
    firstHumanIndex <= 0 ||
    !messages.slice(0, firstHumanIndex).every(messageIsAssistantSide)
  ) {
    return messages;
  }

  const human = messages[firstHumanIndex]!;
  return [
    human,
    ...messages.slice(0, firstHumanIndex),
    ...messages.slice(firstHumanIndex + 1),
  ];
}

function findLastMessageIndex(
  messages: Message[],
  predicate: (message: Message) => boolean,
  beforeIndex = messages.length,
): number {
  for (
    let index = Math.min(beforeIndex, messages.length) - 1;
    index >= 0;
    index--
  ) {
    const message = messages[index];
    if (message && predicate(message)) {
      return index;
    }
  }
  return -1;
}

// When history has not caught up, thread.messages may already contain completed
// prior turns plus the current in-flight tail. If a second assistant message
// appears after an earlier completed reply, treat only the trailing block as the
// new turn so optimistic input stays after prior turns.
function splitThreadForOptimisticHuman(messages: Message[]): {
  established: Message[];
  currentTail: Message[];
} {
  const lastAiIndex = findLastMessageIndex(messages, messageIsAssistantSide);
  if (lastAiIndex === -1) {
    return { established: messages, currentTail: [] };
  }

  const priorAiIndex = findLastMessageIndex(
    messages,
    (message) => message.type === "ai",
    lastAiIndex,
  );

  if (priorAiIndex === -1) {
    if (messages.length === 1) {
      return {
        established: [],
        currentTail: moveSingleTrailingHumanInputToFront(messages),
      };
    }
    return { established: messages, currentTail: [] };
  }

  const trailingStart = priorAiIndex + 1;
  return {
    established: messages.slice(0, trailingStart),
    currentTail: moveSingleTrailingHumanInputToFront(
      messages.slice(trailingStart),
    ),
  };
}

function messagesEquivalent(
  historyMessage: Message,
  threadMessage: Message,
): boolean {
  const historyId = messageIdentity(historyMessage);
  const threadId = messageIdentity(threadMessage);
  if (historyId && threadId && historyId === threadId) {
    return true;
  }
  if (historyMessage.type !== threadMessage.type) {
    return false;
  }
  if (historyMessage.type === "human") {
    const historyText = normalizeHumanMessageText(historyMessage);
    const threadText = normalizeHumanMessageText(threadMessage);
    return historyText.length > 0 && historyText === threadText;
  }
  if (historyMessage.type === "ai") {
    const historyText = extractTextFromMessage(historyMessage).trim();
    const threadText = extractTextFromMessage(threadMessage).trim();
    return historyText.length > 0 && historyText === threadText;
  }
  return false;
}

function findHistoryThreadOverlapCutoff(
  historyMessages: Message[],
  threadMessages: Message[],
): number {
  const maxOverlap = Math.min(historyMessages.length, threadMessages.length);
  for (let overlapLen = maxOverlap; overlapLen >= 1; overlapLen -= 1) {
    const historySuffix = historyMessages.slice(
      historyMessages.length - overlapLen,
    );
    const threadPrefix = threadMessages.slice(0, overlapLen);
    if (
      historySuffix.every((message, index) =>
        messagesEquivalent(message, threadPrefix[index]!),
      )
    ) {
      return historyMessages.length - overlapLen;
    }
  }
  return historyMessages.length;
}

function mergeThreadAndOptimisticMessages(
  establishedThreadPrefix: Message[],
  threadNewSegment: Message[],
  optimisticMessages: Message[],
): Message[] {
  const humanOptimistic = optimisticMessages.filter(
    (message) => message.type === "human",
  );
  const otherOptimistic = optimisticMessages.filter(
    (message) => message.type !== "human",
  );

  if (humanOptimistic.length === 0) {
    const currentTurnTail =
      moveSingleTrailingHumanInputToFront(threadNewSegment);
    return [...establishedThreadPrefix, ...currentTurnTail, ...otherOptimistic];
  }

  const { established: peeledEstablished, currentTail } =
    splitThreadForOptimisticHuman(threadNewSegment);
  const established = [...establishedThreadPrefix, ...peeledEstablished];
  const base = [...established, ...currentTail];

  const firstStreamingIndex = currentTail.findIndex(messageIsAssistantSide);
  if (firstStreamingIndex === -1) {
    return [...base, ...humanOptimistic, ...otherOptimistic];
  }

  const insertAt = established.length + firstStreamingIndex;
  return [
    ...base.slice(0, insertAt),
    ...humanOptimistic,
    ...otherOptimistic,
    ...base.slice(insertAt),
  ];
}

function humanMessageVisibilityKey(message: Message): string | null {
  if (message.type !== "human") {
    return null;
  }
  const identity = messageIdentity(message);
  if (identity) {
    return identity;
  }
  const text = normalizeHumanMessageText(message);
  return text ? `human-content:${text}` : null;
}

function getHumanMessageVisibilityKeys(messages: Message[]): Set<string> {
  const keys = new Set<string>();
  for (const message of messages) {
    const key = humanMessageVisibilityKey(message);
    if (key) {
      keys.add(key);
    }
  }
  return keys;
}

function normalizeHumanMessageText(message: Message): string {
  if (message.type !== "human") {
    return "";
  }
  return stripUploadedFilesTag(extractTextFromMessage(message)).trim();
}

function hasServerReplacementForOptimisticHuman(
  optimisticMessages: Message[],
  baselineHumanMessageKeys: ReadonlySet<string>,
  serverMessages: Message[],
): boolean {
  const optimisticHumanTexts = new Set(
    optimisticMessages
      .filter((message) => message.type === "human")
      .map(normalizeHumanMessageText)
      .filter((text) => text.length > 0),
  );

  if (optimisticHumanTexts.size === 0) {
    return false;
  }

  return serverMessages.some((message) => {
    if (message.type !== "human") {
      return false;
    }
    const key = humanMessageVisibilityKey(message);
    if (key && baselineHumanMessageKeys.has(key)) {
      return false;
    }
    return optimisticHumanTexts.has(normalizeHumanMessageText(message));
  });
}

export function getVisibleOptimisticMessagesForServerMessages(
  optimisticMessages: Message[],
  baselineHumanMessageKeys: ReadonlySet<string>,
  serverMessages: Message[],
): Message[] {
  if (
    optimisticMessages.some((message) => message.type === "human") &&
    hasServerReplacementForOptimisticHuman(
      optimisticMessages,
      baselineHumanMessageKeys,
      serverMessages,
    )
  ) {
    return [];
  }
  return optimisticMessages;
}

export function mergeMessages(
  historyMessages: Message[],
  threadMessages: Message[],
  optimisticMessages: Message[],
): Message[] {
  const timestampedThreadMessages = mergeMissingTimestamps(
    historyMessages,
    threadMessages,
  );

  // History is a suffix-aligned prefix of thread. Match by id when available and
  // by human text when run-event ids differ from live thread state.
  const cutoff = findHistoryThreadOverlapCutoff(
    historyMessages,
    timestampedThreadMessages,
  );
  const overlapLen = historyMessages.length - cutoff;
  const establishedThreadPrefix = timestampedThreadMessages.slice(
    0,
    overlapLen,
  );
  const threadNewSegment = timestampedThreadMessages.slice(overlapLen);

  return repairDynamicContextUserMessageOrder(
    dedupeMessagesByIdentity([
      ...historyMessages.slice(0, cutoff),
      ...mergeThreadAndOptimisticMessages(
        establishedThreadPrefix,
        threadNewSegment,
        optimisticMessages,
      ),
    ]),
  );
}

function getMessagesAfterBaseline(
  messages: Message[],
  baselineMessageIds: ReadonlySet<string>,
): Message[] {
  return messages.filter((message) => {
    const id = messageIdentity(message);
    return !id || !baselineMessageIds.has(id);
  });
}

export function getVisibleOptimisticMessages(
  optimisticMessages: Message[],
  previousHumanMessageCount: number,
  currentHumanMessageCount: number,
): Message[] {
  if (
    optimisticMessages.some((message) => message.type === "human") &&
    currentHumanMessageCount > previousHumanMessageCount
  ) {
    return [];
  }
  return optimisticMessages;
}

function getStreamErrorMessage(error: unknown): string {
  if (typeof error === "string" && error.trim()) {
    return error;
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  if (typeof error === "object" && error !== null) {
    const message = Reflect.get(error, "message");
    if (typeof message === "string" && message.trim()) {
      return message;
    }
    const nestedError = Reflect.get(error, "error");
    if (nestedError instanceof Error && nestedError.message.trim()) {
      return nestedError.message;
    }
    if (typeof nestedError === "string" && nestedError.trim()) {
      return nestedError;
    }
  }
  return "Request failed.";
}

async function fetchRunMessages(
  threadId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<RunMessage[]> {
  const pages: RunMessage[][] = [];
  let beforeSeq: number | null = null;

  for (let page = 0; page < 1000; page += 1) {
    const params = new URLSearchParams({
      limit: String(RUN_MESSAGES_PAGE_SIZE),
    });
    if (beforeSeq !== null) {
      params.set("before_seq", String(beforeSeq));
    }

    const response = await fetch(
      `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/messages?${params.toString()}`,
      {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        signal,
      },
    );

    if (!response.ok) {
      throw new Error("Failed to load run messages.");
    }

    const result = (await response.json()) as RunMessagesResponse;
    const data = Array.isArray(result.data) ? result.data : [];
    if (data.length === 0) {
      break;
    }

    pages.unshift(data);

    const hasMore = result.has_more ?? result.hasMore ?? false;
    const firstSeq = data[0]?.seq;
    if (!hasMore || typeof firstSeq !== "number" || firstSeq === beforeSeq) {
      break;
    }
    beforeSeq = firstSeq;
  }

  return pages.flat();
}

export function useThreadStream({
  threadId,
  context,
  threadMetadata,
  isMock,
  onSend,
  onStart,
  onFinish,
  onToolEnd,
}: ThreadStreamOptions) {
  const { t } = useI18n();
  // Track the thread ID that is currently streaming to handle thread changes during streaming
  const [onStreamThreadId, setOnStreamThreadId] = useState(() => threadId);
  // Ref to track current thread ID across async callbacks without causing re-renders,
  // and to allow access to the current thread id in onUpdateEvent
  const threadIdRef = useRef<string | null>(threadId ?? null);
  const activeRunThreadIdRef = useRef<string | null>(null);
  const startedRef = useRef(false);
  const pendingUsageBaselineMessageIdsRef = useRef<Set<string>>(new Set());
  const listeners = useRef({
    onSend,
    onStart,
    onFinish,
    onToolEnd,
  });

  const isCurrentStreamThread = useCallback((candidate?: string | null) => {
    return Boolean(candidate) && threadIdRef.current === candidate;
  }, []);

  const {
    messages: history,
    hasMore: hasMoreHistory,
    loadMore: loadMoreHistory,
    loading: isHistoryLoading,
    appendMessages,
  } = useThreadHistory(onStreamThreadId ?? "");

  // Keep listeners ref updated with latest callbacks
  useEffect(() => {
    listeners.current = { onSend, onStart, onFinish, onToolEnd };
  }, [onSend, onStart, onFinish, onToolEnd]);

  useEffect(() => {
    const normalizedThreadId = threadId ?? null;
    if (
      activeRunThreadIdRef.current &&
      activeRunThreadIdRef.current !== normalizedThreadId
    ) {
      activeRunThreadIdRef.current = null;
    }
    if (!normalizedThreadId) {
      // Reset when the UI moves back to a brand new unsaved thread.
      startedRef.current = false;
      setOnStreamThreadId(normalizedThreadId);
    } else {
      setOnStreamThreadId(normalizedThreadId);
    }
    threadIdRef.current = normalizedThreadId;
  }, [threadId]);

  const handleStreamStart = useCallback((_threadId: string, _runId: string) => {
    threadIdRef.current = _threadId;
    activeRunThreadIdRef.current = _threadId;
    if (!startedRef.current) {
      listeners.current.onStart?.(_threadId, _runId);
      startedRef.current = true;
    }
    setOnStreamThreadId(_threadId);
  }, []);

  const queryClient = useQueryClient();
  const updateSubtask = useUpdateSubtask();

  const thread = useStream<AgentThreadState>({
    client: getAPIClient(isMock),
    assistantId: "lead_agent",
    threadId: onStreamThreadId,
    reconnectOnMount: true,
    fetchStateHistory: { limit: 1 },
    onCreated(meta) {
      handleStreamStart(meta.thread_id, meta.run_id);
      const metadata: Record<string, unknown> = {
        ...threadMetadata,
        ...(context.agent_name ? { agent_name: context.agent_name } : {}),
      };
      if (Object.keys(metadata).length > 0 && !isMock) {
        void getAPIClient()
          .threads.update(meta.thread_id, { metadata })
          .catch(() => ({}));
      }
    },
    onLangChainEvent(event) {
      if (event.event === "on_tool_end") {
        listeners.current.onToolEnd?.({
          name: event.name,
          data: event.data,
        });
      }
    },
    onUpdateEvent(data) {
      const eventThreadId = activeRunThreadIdRef.current;
      if (!eventThreadId || !isCurrentStreamThread(eventThreadId)) {
        return;
      }
      if (data["SummarizationMiddleware.before_model"]) {
        const _messages = [
          ...(data["SummarizationMiddleware.before_model"].messages ?? []),
        ];

        if (_messages.length < 2) {
          return;
        }
        for (const m of _messages) {
          if (m.name === "summary" && m.type === "human") {
            summarizedRef.current?.add(m.id ?? "");
          }
        }
        const _lastKeepMessage = _messages[2];
        const _currentMessages = [...messagesRef.current];
        const _movedMessages: Message[] = [];
        for (const m of _currentMessages) {
          if (m.id !== undefined && m.id === _lastKeepMessage?.id) {
            break;
          }
          if (!summarizedRef.current?.has(m.id ?? "")) {
            _movedMessages.push(m);
          }
        }
        appendMessages(_movedMessages);
        messagesRef.current = [];
      }

      const updates: Array<Partial<AgentThreadState> | null> = Object.values(
        data || {},
      );
      for (const update of updates) {
        if (update && "title" in update && update.title) {
          void queryClient.setQueriesData(
            {
              queryKey: ["threads", "search"],
              exact: false,
            },
            (oldData: Array<AgentThread> | undefined) => {
              return oldData?.map((t) => {
                if (t.thread_id === threadIdRef.current) {
                  return {
                    ...t,
                    values: {
                      ...t.values,
                      title: update.title,
                    },
                  };
                }
                return t;
              });
            },
          );
        }
      }
    },
    onCustomEvent(event: unknown) {
      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "task_running"
      ) {
        const e = event as {
          type: "task_running";
          task_id: string;
          message: AIMessage;
        };
        updateSubtask({ id: e.task_id, latestMessage: e.message });
        return;
      }

      if (
        typeof event === "object" &&
        event !== null &&
        "type" in event &&
        event.type === "llm_retry" &&
        "message" in event &&
        typeof event.message === "string" &&
        event.message.trim()
      ) {
        const e = event as { type: "llm_retry"; message: string };
        toast(e.message);
      }
    },
    onError(error) {
      const eventThreadId = activeRunThreadIdRef.current;
      if (!eventThreadId || !isCurrentStreamThread(eventThreadId)) {
        return;
      }
      setOptimisticMessages([]);
      toast.error(getStreamErrorMessage(error));
      pendingUsageBaselineMessageIdsRef.current = new Set(
        messagesRef.current
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      if (eventThreadId && !isMock) {
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(eventThreadId),
        });
      }
    },
    onFinish(state) {
      const eventThreadId = activeRunThreadIdRef.current;
      if (!eventThreadId || !isCurrentStreamThread(eventThreadId)) {
        return;
      }
      listeners.current.onFinish?.(state.values);
      pendingUsageBaselineMessageIdsRef.current = new Set(
        messagesRef.current
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      if (eventThreadId && !isMock) {
        void queryClient.invalidateQueries({
          queryKey: sandboxFilesQueryKey(eventThreadId),
        });
        void queryClient.invalidateQueries({
          queryKey: threadTokenUsageQueryKey(eventThreadId),
        });
      }
    },
  });

  const threadRef = useRef(thread);
  threadRef.current = thread;

  // Re-attach to a still-running backend task when the SSE stream drops
  // (common during long tool calls such as image generation). The SDK only
  // auto-reconnects once on mount; this covers mid-conversation disconnects.
  useEffect(() => {
    const currentThreadId = onStreamThreadId;
    if (!currentThreadId || isMock || thread.isLoading) return;
    const streamThreadId = currentThreadId;

    let cancelled = false;

    async function rejoinActiveRun() {
      try {
        const apiClient = getAPIClient(isMock);
        const runs = await apiClient.runs.list(streamThreadId);
        if (
          cancelled ||
          threadRef.current?.isLoading ||
          !isCurrentStreamThread(streamThreadId)
        ) {
          return;
        }
        const activeRun = runs.find(
          (r) => r.status === "pending" || r.status === "running",
        );
        if (
          activeRun &&
          threadRef.current &&
          isCurrentStreamThread(streamThreadId)
        ) {
          activeRunThreadIdRef.current = streamThreadId;
          await threadRef.current.joinStream(activeRun.run_id);
        }
      } catch {
        // Silently ignore — run may have finished before we could join
      }
    }

    const timer = window.setTimeout(() => void rejoinActiveRun(), 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [onStreamThreadId, isMock, thread.isLoading, isCurrentStreamThread]);

  // Auto-join active runs for threads that were not started from this client
  // (e.g. scheduled tasks). reconnectOnMount only works when sessionStorage
  // contains the run id from a previous submit() on this tab.
  useEffect(() => {
    const currentThreadId = onStreamThreadId;
    if (!currentThreadId || isMock) return;
    const streamThreadId = currentThreadId;

    const sessionKey = `lg:stream:${streamThreadId}`;
    if (
      typeof window !== "undefined" &&
      window.sessionStorage.getItem(sessionKey)
    ) {
      return;
    }

    let cancelled = false;

    async function tryJoinRunning() {
      try {
        const apiClient = getAPIClient(isMock);
        const runs = await apiClient.runs.list(streamThreadId);
        if (cancelled || !isCurrentStreamThread(streamThreadId)) return;
        const activeRun = runs.find(
          (r) => r.status === "pending" || r.status === "running",
        );
        if (
          activeRun &&
          threadRef.current &&
          !threadRef.current.isLoading &&
          isCurrentStreamThread(streamThreadId)
        ) {
          activeRunThreadIdRef.current = streamThreadId;
          await threadRef.current.joinStream(activeRun.run_id);
        }
      } catch {
        // Silently ignore — run may have finished before we could join
      }
    }

    const timer = window.setTimeout(() => void tryJoinRunning(), 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [onStreamThreadId, isMock, isCurrentStreamThread]);

  // Optimistic messages shown before the server stream responds
  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const sendInFlightRef = useRef(false);
  const messagesRef = useRef<Message[]>([]);
  const summarizedRef = useRef<Set<string>>(null);
  const optimisticBaselineHumanKeysRef = useRef<Set<string>>(new Set());

  summarizedRef.current ??= new Set<string>();

  const serverMessagesWithoutOptimistic = mergeMessages(
    history,
    thread.messages,
    [],
  );

  // Reset thread-local pending UI state when switching between threads so
  // optimistic messages and in-flight guards do not leak across chat views.
  useEffect(() => {
    startedRef.current = false;
    if (
      activeRunThreadIdRef.current &&
      activeRunThreadIdRef.current !== threadId
    ) {
      activeRunThreadIdRef.current = null;
    }
    sendInFlightRef.current = false;
    optimisticBaselineHumanKeysRef.current = new Set();
    pendingUsageBaselineMessageIdsRef.current = new Set(
      messagesRef.current
        .map(messageIdentity)
        .filter((id): id is string => Boolean(id)),
    );
  }, [threadId]);

  // When streaming starts without a baseline (e.g. reconnection, run started
  // from another client, or page reload mid-stream), snapshot the current
  // messages so only *new* messages are treated as "pending" for token usage.
  useEffect(() => {
    if (
      thread.isLoading &&
      pendingUsageBaselineMessageIdsRef.current.size === 0
    ) {
      pendingUsageBaselineMessageIdsRef.current = new Set(
        thread.messages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );
    }
  }, [thread.isLoading, thread.messages]);

  // Clear optimistic when server messages arrive.
  // For messages with a human optimistic message, wait until the server's
  // human message has arrived to avoid clearing before the input message
  // appears in the stream (the input message may arrive via "values" events
  // after individual "messages-tuple" events for AI messages).
  const optimisticMessageCount = optimisticMessages.length;
  const hasHumanOptimistic = optimisticMessages.some((m) => m.type === "human");
  const hasServerReplacementForOptimistic =
    hasServerReplacementForOptimisticHuman(
      optimisticMessages,
      optimisticBaselineHumanKeysRef.current,
      serverMessagesWithoutOptimistic,
    );
  useEffect(() => {
    if (optimisticMessageCount === 0) return;

    if (!hasHumanOptimistic || hasServerReplacementForOptimistic) {
      setOptimisticMessages([]);
    }
  }, [
    hasHumanOptimistic,
    hasServerReplacementForOptimistic,
    optimisticMessageCount,
  ]);

  const sendMessage = useCallback(
    async (
      threadId: string,
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
      options?: SendMessageOptions,
    ) => {
      if (sendInFlightRef.current) {
        return;
      }
      sendInFlightRef.current = true;

      const text = message.text.trim();

      // Capture the currently visible server-backed human messages before
      // showing optimistic UI. During streaming, old history can arrive before
      // the server echoes this submission; only the matching new human should
      // replace the optimistic bubble.
      optimisticBaselineHumanKeysRef.current = getHumanMessageVisibilityKeys(
        serverMessagesWithoutOptimistic,
      );
      pendingUsageBaselineMessageIdsRef.current = new Set(
        thread.messages
          .map(messageIdentity)
          .filter((id): id is string => Boolean(id)),
      );

      // Build optimistic files list with uploading status
      const optimisticFiles: FileInMessage[] = (message.files ?? []).map(
        (f) => ({
          filename: f.filename ?? "",
          size: 0,
          status: "uploading" as const,
        }),
      );

      const hideFromUI = options?.additionalKwargs?.hide_from_ui === true;
      const optimisticAdditionalKwargs = {
        ...options?.additionalKwargs,
        ...(optimisticFiles.length > 0 ? { files: optimisticFiles } : {}),
      };

      const newOptimistic: Message[] = [];
      if (!hideFromUI) {
        newOptimistic.push({
          type: "human",
          id: `opt-human-${Date.now()}`,
          content: text ? [{ type: "text", text }] : "",
          additional_kwargs: optimisticAdditionalKwargs,
        });
      }

      if (optimisticFiles.length > 0 && !hideFromUI) {
        // Mock AI message while files are being uploaded
        newOptimistic.push({
          type: "ai",
          id: `opt-ai-${Date.now()}`,
          content: t.uploads.uploadingFiles,
          additional_kwargs: { element: "task" },
        });
      }
      if (newOptimistic.length > 0) {
        flushSync(() => {
          setOptimisticMessages(newOptimistic);
          listeners.current.onSend?.(threadId);
        });
        await waitForNextPaint();
      } else {
        setOptimisticMessages(newOptimistic);
        listeners.current.onSend?.(threadId);
      }

      let uploadedFileInfo: UploadedFileInfo[] = [];

      try {
        // Upload files first if any
        if (message.files && message.files.length > 0) {
          setIsUploading(true);
          try {
            const filePromises = message.files.map((fileUIPart) =>
              promptInputFilePartToFile(fileUIPart),
            );

            const conversionResults = await Promise.all(filePromises);
            const files = conversionResults.filter(
              (file): file is File => file !== null,
            );
            const failedConversions = conversionResults.length - files.length;

            if (failedConversions > 0) {
              throw new Error(
                `Failed to prepare ${failedConversions} attachment(s) for upload. Please retry.`,
              );
            }

            if (!threadId) {
              throw new Error("Thread is not ready for file upload.");
            }

            if (files.length > 0) {
              const uploadResponse = await uploadFiles(threadId, files);
              uploadedFileInfo = uploadResponse.files;
              void queryClient.invalidateQueries({
                queryKey: sandboxFilesQueryKey(threadId),
              });

              // Update optimistic human message with uploaded status + paths
              const uploadedFiles: FileInMessage[] = uploadedFileInfo.map(
                (info) => ({
                  filename: info.filename,
                  size: uploadedFileSizeToNumber(info.size),
                  path: info.virtual_path,
                  status: "uploaded" as const,
                }),
              );
              setOptimisticMessages((messages) => {
                if (messages.length > 1 && messages[0]) {
                  const humanMessage: Message = messages[0];
                  return [
                    {
                      ...humanMessage,
                      additional_kwargs: { files: uploadedFiles },
                    },
                    ...messages.slice(1),
                  ];
                }
                return messages;
              });
            }
          } catch (error) {
            const errorMessage =
              error instanceof Error
                ? error.message
                : "Failed to upload files.";
            toast.error(errorMessage);
            setOptimisticMessages([]);
            throw error;
          } finally {
            setIsUploading(false);
          }
        }

        // Build files metadata for submission (included in additional_kwargs)
        const filesForSubmit: FileInMessage[] = uploadedFileInfo.map(
          (info) => ({
            filename: info.filename,
            size: uploadedFileSizeToNumber(info.size),
            path: info.virtual_path,
            status: "uploaded" as const,
          }),
        );

        await thread.submit(
          {
            messages: [
              {
                type: "human",
                content: [
                  {
                    type: "text",
                    text,
                  },
                ],
                additional_kwargs: {
                  ...options?.additionalKwargs,
                  ...(filesForSubmit.length > 0
                    ? { files: filesForSubmit }
                    : {}),
                },
              },
            ],
          },
          {
            threadId: threadId,
            streamSubgraphs: true,
            streamResumable: true,
            onDisconnect: "continue",
            multitaskStrategy: options?.multitaskStrategy,
            config: {
              recursion_limit: 1000,
            },
            context: {
              ...extraContext,
              ...context,
              thinking_enabled: context.mode !== "flash",
              is_plan_mode: context.mode === "pro" || context.mode === "ultra",
              subagent_enabled: context.mode === "ultra",
              reasoning_effort:
                context.reasoning_effort ??
                (context.mode === "ultra"
                  ? "high"
                  : context.mode === "pro"
                    ? "medium"
                    : context.mode === "thinking"
                      ? "low"
                      : undefined),
              thread_id: threadId,
              skill_name: context.skill_name,
              connector_ids: context.connector_ids,
            },
          },
        );
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      } catch (error) {
        setOptimisticMessages([]);
        setIsUploading(false);
        throw error;
      } finally {
        sendInFlightRef.current = false;
      }
    },
    [
      thread,
      t.uploads.uploadingFiles,
      context,
      queryClient,
      serverMessagesWithoutOptimistic,
    ],
  );

  // Cache the latest thread messages in a ref to compare against incoming history messages for deduplication,
  // and to allow access to the full message list in onUpdateEvent without causing re-renders.
  useEffect(() => {
    if (thread.messages.length >= messagesRef.current.length) {
      messagesRef.current = thread.messages;
    }
  }, [thread.messages]);

  const visibleOptimisticMessages =
    getVisibleOptimisticMessagesForServerMessages(
      optimisticMessages,
      optimisticBaselineHumanKeysRef.current,
      serverMessagesWithoutOptimistic,
    );

  const mergedMessages = mergeMessages(
    history,
    thread.messages,
    visibleOptimisticMessages,
  );
  const pendingUsageMessages = thread.isLoading
    ? getMessagesAfterBaseline(
        thread.messages,
        pendingUsageBaselineMessageIdsRef.current,
      )
    : [];

  // Merge history, live stream, and optimistic messages for display
  // History messages may overlap with thread.messages; thread.messages take precedence
  const mergedThread = {
    ...thread,
    messages: mergedMessages,
  } as typeof thread;

  return {
    thread: mergedThread,
    pendingUsageMessages,
    sendMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } as const;
}

export function useThreadHistory(threadId: string) {
  const runs = useThreadRuns(threadId);
  const threadIdRef = useRef(threadId);
  const runsRef = useRef(runs.data ?? []);
  const indexRef = useRef(-1);
  const loadingRef = useRef(false);
  const pendingLoadRef = useRef(false);
  const loadingRunIdRef = useRef<string | null>(null);
  const loadedRunIdsRef = useRef<Set<string>>(new Set());
  const messagesByRunIdRef = useRef<Map<string, Message[]>>(new Map());
  const appendedMessagesRef = useRef<Message[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);

  const getComposedMessages = useCallback(() => {
    return mergeLoadedRunMessages(
      runsRef.current,
      messagesByRunIdRef.current,
      appendedMessagesRef.current,
    );
  }, []);

  const loadMessages = useCallback(async () => {
    if (loadingRef.current) {
      const pendingRunIndex = findLatestUnloadedRunIndex(
        runsRef.current,
        loadedRunIdsRef.current,
      );
      const pendingRun = runsRef.current[pendingRunIndex];
      if (pendingRun && pendingRun.run_id !== loadingRunIdRef.current) {
        pendingLoadRef.current = true;
      }
      return;
    }
    if (runsRef.current.length === 0) {
      return;
    }

    const requestGeneration = ++generationRef.current;
    loadingRef.current = true;
    setLoading(true);

    let controller: AbortController | null = null;

    try {
      do {
        pendingLoadRef.current = false;

        const nextRunIndex = findLatestUnloadedRunIndex(
          runsRef.current,
          loadedRunIdsRef.current,
        );
        indexRef.current = nextRunIndex;

        const run = runsRef.current[nextRunIndex];
        if (!run) {
          indexRef.current = -1;
          return;
        }

        const requestThreadId = threadIdRef.current;
        loadingRunIdRef.current = run.run_id;
        controller = new AbortController();
        abortControllerRef.current = controller;
        const runMessages = await fetchRunMessages(
          requestThreadId,
          run.run_id,
          controller.signal,
        );
        const _messages = runMessages
          .filter((m) => !m.metadata?.caller?.startsWith("middleware:"))
          .map((m) => withMessageTimestamp(m.content, m.created_at));
        if (
          threadIdRef.current !== requestThreadId ||
          generationRef.current !== requestGeneration
        ) {
          return;
        }
        messagesByRunIdRef.current.set(run.run_id, _messages);
        loadedRunIdsRef.current.add(run.run_id);
        setMessages(getComposedMessages());
        indexRef.current = findLatestUnloadedRunIndex(
          runsRef.current,
          loadedRunIdsRef.current,
        );
      } while (pendingLoadRef.current);
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        return;
      }
      console.error(err);
    } finally {
      // Only clear shared loading state if no newer request has taken over.
      if (generationRef.current === requestGeneration) {
        loadingRef.current = false;
        loadingRunIdRef.current = null;
        setLoading(false);
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
      }
    }
  }, [getComposedMessages]);

  // Reset all thread-local state when the active thread changes. This also
  // aborts any in-flight fetch for the previous thread and bumps the request
  // generation so stale finally blocks cannot overwrite the new thread's state.
  useEffect(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    generationRef.current += 1;
    runsRef.current = [];
    indexRef.current = -1;
    pendingLoadRef.current = false;
    loadingRunIdRef.current = null;
    loadedRunIdsRef.current = new Set();
    messagesByRunIdRef.current = new Map();
    appendedMessagesRef.current = [];
    loadingRef.current = false;
    setLoading(false);
    setMessages([]);
    threadIdRef.current = threadId;

    return () => {
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
      generationRef.current += 1;
    };
  }, [threadId]);

  // Load history when the runs list changes (e.g. new runs during streaming).
  // We replace the runs snapshot without aborting an in-flight load so that
  // messages for already-fetched runs are not discarded.
  useEffect(() => {
    const currentRuns = runs.data ?? [];
    if (currentRuns.length === 0) {
      return;
    }
    runsRef.current = sortRunsChronologically(currentRuns);
    indexRef.current = findLatestUnloadedRunIndex(
      runsRef.current,
      loadedRunIdsRef.current,
    );
    setMessages(getComposedMessages());
    loadMessages().catch(() => {
      toast.error("Failed to load thread history.");
    });
  }, [runs.data, getComposedMessages, loadMessages]);

  const appendMessages = useCallback(
    (_messages: Message[]) => {
      appendedMessagesRef.current = dedupeMessagesByIdentity([
        ...appendedMessagesRef.current,
        ..._messages,
      ]);
      setMessages(getComposedMessages());
    },
    [getComposedMessages],
  );
  const hasMore = indexRef.current >= 0 || !runs.data;
  return {
    runs: runs.data,
    messages,
    loading,
    appendMessages,
    hasMore,
    loadMore: loadMessages,
  };
}

export function useThreads(
  params: Parameters<ThreadsClient["search"]>[0] = {
    limit: 50,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values", "metadata"],
  },
  { enabled = true }: { enabled?: boolean } = {},
) {
  const apiClient = getAPIClient();
  return useQuery<AgentThread[]>({
    queryKey: ["threads", "search", params],
    queryFn: async () => {
      const maxResults = params.limit;
      const initialOffset = params.offset ?? 0;
      const DEFAULT_PAGE_SIZE = 50;

      // Preserve prior semantics: if a non-positive limit is explicitly provided,
      // delegate to a single search call with the original parameters.
      if (maxResults !== undefined && maxResults <= 0) {
        const response =
          await apiClient.threads.search<AgentThreadState>(params);
        return response as AgentThread[];
      }

      const pageSize =
        typeof maxResults === "number" && maxResults > 0
          ? Math.min(DEFAULT_PAGE_SIZE, maxResults)
          : DEFAULT_PAGE_SIZE;

      const threads: AgentThread[] = [];
      let offset = initialOffset;

      while (true) {
        if (typeof maxResults === "number" && threads.length >= maxResults) {
          break;
        }

        const currentLimit =
          typeof maxResults === "number"
            ? Math.min(pageSize, maxResults - threads.length)
            : pageSize;

        if (typeof maxResults === "number" && currentLimit <= 0) {
          break;
        }

        const response = (await apiClient.threads.search<AgentThreadState>({
          ...params,
          limit: currentLimit,
          offset,
        })) as AgentThread[];

        threads.push(...response);

        if (response.length < currentLimit) {
          break;
        }

        offset += response.length;
      }

      return threads;
    },
    enabled,
    refetchOnWindowFocus: false,
  });
}

export function useThreadRuns(threadId?: string) {
  return useQuery<Run[]>({
    queryKey: ["thread", threadId],
    queryFn: async () => {
      if (!threadId) {
        return [];
      }
      const runs: Run[] = [];
      let offset = 0;

      while (true) {
        const params = new URLSearchParams({
          limit: String(THREAD_RUNS_PAGE_SIZE),
          offset: String(offset),
        });
        const response = await fetch(
          `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/runs?${params.toString()}`,
          {
            method: "GET",
            headers: {
              "Content-Type": "application/json",
            },
            credentials: "include",
          },
        );

        if (!response.ok) {
          throw new Error("Failed to load thread runs.");
        }

        const page = (await response.json()) as Run[];
        runs.push(...page);

        if (page.length < THREAD_RUNS_PAGE_SIZE) {
          break;
        }
        offset += page.length;
      }

      return runs;
    },
    refetchOnWindowFocus: false,
  });
}

export function useThreadTokenUsage(
  threadId?: string | null,
  { enabled = true }: { enabled?: boolean } = {},
) {
  return useQuery<ThreadTokenUsageResponse | null>({
    queryKey: threadTokenUsageQueryKey(threadId),
    queryFn: async () => {
      if (!threadId) {
        return null;
      }
      return fetchThreadTokenUsage(threadId);
    },
    enabled: enabled && Boolean(threadId),
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useRunDetail(threadId: string, runId: string) {
  const apiClient = getAPIClient();
  return useQuery<Run>({
    queryKey: ["thread", threadId, "run", runId],
    queryFn: async () => {
      const response = await apiClient.runs.get(threadId, runId);
      return response;
    },
    refetchOnWindowFocus: false,
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({ threadId }: { threadId: string }) => {
      await apiClient.threads.delete(threadId);

      const response = await fetch(
        `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}`,
        {
          method: "DELETE",
        },
      );

      if (!response.ok) {
        const error = await response
          .json()
          .catch(() => ({ detail: "Failed to delete local thread data." }));
        throw new Error(error.detail ?? "Failed to delete local thread data.");
      }
    },
    onSuccess(_, { threadId }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread> | undefined) => {
          if (oldData == null) {
            return oldData;
          }
          return oldData.filter((t) => t.thread_id !== threadId);
        },
      );
    },
    onSettled() {
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    },
  });
}

export function useRenameThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      title,
    }: {
      threadId: string;
      title: string;
    }) => {
      await apiClient.threads.updateState(threadId, {
        values: { title },
      });
    },
    onSuccess(_, { threadId, title }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread>) => {
          return oldData.map((t) => {
            if (t.thread_id === threadId) {
              return {
                ...t,
                values: {
                  ...t.values,
                  title,
                },
              };
            }
            return t;
          });
        },
      );
    },
  });
}
