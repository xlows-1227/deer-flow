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
import { getMessageTimestamp, type FileInMessage } from "../messages/utils";
import { sandboxFilesQueryKey } from "../sandbox";
import type { LocalSettings } from "../settings";
import { useUpdateSubtask } from "../tasks/context";
import type { UploadedFileInfo } from "../uploads";
import { promptInputFilePartToFile, uploadFiles } from "../uploads";

import { fetchThreadTokenUsage } from "./api";
import {
  dedupeMessagesByIdentity,
  getHumanMessageVisibilityKeys,
  getMessagesAfterBaseline,
  getVisibleOptimisticMessagesForServerMessages,
  hasServerReplacementForOptimisticHuman,
  mergeMessages,
  messageIdentity,
  normalizeHumanMessageText,
} from "./merge";
import {
  fetchRunMessages,
  findLatestUnloadedRunIndex,
  mergeLoadedRunMessages,
  sortRunsChronologically,
  useThreadRuns,
  withMessageTimestamp,
  type LoadedRunMessage,
} from "./query";
import { findRunToRejoin } from "./rejoin";
import { threadTokenUsageQueryKey } from "./token-usage";
import type {
  AgentThread,
  AgentThreadState,
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
  // Suppress auto-rejoin after an intentional stop so long tool calls
  // (e.g. query_database) are not reattached ~500ms later.
  const suppressAutoRejoinRef = useRef(false);
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
    // Switching chats clears stop suppression so scheduled/other-client runs
    // can still be auto-joined on the newly selected thread.
    suppressAutoRejoinRef.current = false;
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
        const e = event as { type: "llm_retry"; attempt: number; max_attempts: number; message: string };
        // Don't show intermediate retry toasts — they flicker too fast and
        // confuse users.  Only surface the FINAL failure via the message
        // bubble.  Retries are invisible to the user; only the final
        // result matters.
        if (e.attempt >= e.max_attempts) {
          toast("LLM 服务繁忙，已达最大重试次数", { id: "llm-retry-final", duration: 5000 });
        }
      }
    },
    onError(error) {
      const eventThreadId = activeRunThreadIdRef.current;
      if (!eventThreadId || !isCurrentStreamThread(eventThreadId)) {
        return;
      }
      setOptimisticMessages([]);
      setIsUploading(false);
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
      // The stream has completed successfully, so any remaining optimistic
      // messages (human echo placeholder, "uploading files" mock AI, etc.)
      // have been superseded by actual server-returned messages and should
      // not be rendered anymore. Without this, pure-file sends had a habit
      // of lingering because the earlier echo-detection logic required a
      // non-empty text to match against.
      setOptimisticMessages([]);
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
        const activeRun = findRunToRejoin(runs, {
          suppressRejoin: suppressAutoRejoinRef.current,
        });
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
        const activeRun = findRunToRejoin(runs, {
          suppressRejoin: suppressAutoRejoinRef.current,
        });
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
  // Persists optimistic human timestamps after the optimistic bubbles are
  // cleared, so the timestamp can still be copied onto the server-backed
  // human message that replaces it (server messages often lack a timestamp
  // during streaming — only the history/backfill path adds one on refresh).
  const optimisticTimestampsRef = useRef<Map<string, string>>(new Map());
  // Tracks the previous threadId so we can distinguish "new conversation
  // created" (threadId: "new" → real ID) from "switch to another chat"
  // (real ID A → real ID B). The former must NOT clear optimistic
  // timestamps — sendMessage just stored one that's still needed.
  const prevThreadIdRef = useRef<string>("");

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
    // Only clear optimistic timestamps when switching between real threads.
    // When creating a new conversation, threadId transitions from "new" to a
    // real ID — sendMessage just stored a timestamp that's still needed to
    // backfill the server-backed human message that replaces the optimistic
    // bubble. Clearing here would lose it.
    const prevThreadId = prevThreadIdRef.current;
    prevThreadIdRef.current = threadId;
    if (prevThreadId && prevThreadId !== "new" && prevThreadId !== threadId) {
      optimisticTimestampsRef.current = new Map();
    }
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
      // A new user turn may need auto-rejoin again if the SSE drops mid-run.
      suppressAutoRejoinRef.current = false;

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
        const optimisticTs = new Date().toISOString();
        // Persist the timestamp so it can be copied onto the server-backed
        // human message that replaces this optimistic bubble (server messages
        // often lack a timestamp during streaming).
        if (text) {
          optimisticTimestampsRef.current.set(text, optimisticTs);
        }
        newOptimistic.push({
          type: "human",
          id: `opt-human-${Date.now()}`,
          content: text ? [{ type: "text", text }] : "",
          additional_kwargs: {
            ...optimisticAdditionalKwargs,
            timestamp: optimisticTs,
          },
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
            streamMode: ["values", "messages"],
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
              // Per-mode defaults; a global override from the Settings page
              // (context.reasoning_effort) wins when set. Pro defaults to
              // "low": its planning power comes from the TodoList middleware,
              // not reasoning depth, and "low" is significantly faster.
              reasoning_effort:
                context.mode === "flash"
                  ? undefined
                  : (context.reasoning_effort ??
                    (context.mode === "ultra" ? "high" : "low")),
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

  let mergedMessages = mergeMessages(
    history,
    thread.messages,
    visibleOptimisticMessages,
  );

  // Copy timestamps from optimistic human messages to server human messages
  // that lack a timestamp.  The optimistic bubbles are cleared once the
  // server's human message arrives, but the server copy often lacks a
  // timestamp during streaming (only the history/backfill path adds one on
  // refresh).  We persist the optimistic timestamp in a ref so it survives
  // the clear and can be copied onto the server message across re-renders.
  if (optimisticTimestampsRef.current.size > 0) {
    mergedMessages = mergedMessages.map((m) => {
      if (m.type !== "human") return m;
      if (getMessageTimestamp(m)) return m; // already has timestamp
      const text = normalizeHumanMessageText(m);
      if (!text) return m;
      const ts = optimisticTimestampsRef.current.get(text);
      if (!ts) return m;
      return {
        ...m,
        additional_kwargs: {
          ...m.additional_kwargs,
          timestamp: ts,
        },
      };
    });
  }
  const pendingUsageMessages = thread.isLoading
    ? getMessagesAfterBaseline(
        thread.messages,
        pendingUsageBaselineMessageIdsRef.current,
      )
    : [];

  const stop = useCallback(async () => {
    // Set before awaiting stop so the isLoading→false rejoin effect cannot
    // race cancel and reattach a still-running backend tool call.
    suppressAutoRejoinRef.current = true;
    await thread.stop();
  }, [thread]);

  // Merge history, live stream, and optimistic messages for display
  // History messages may overlap with thread.messages; thread.messages take precedence
  const mergedThread = {
    ...thread,
    messages: mergedMessages,
    stop,
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
  const messagesByRunIdRef = useRef<Map<string, LoadedRunMessage[]>>(new Map());
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
        // run messages 接口只返回 run 执行期间的事件消息（middleware
        // summary、AI 回复等），不包含用户原始输入。用户输入只存在于
        // run.kwargs.input.messages 中。如果不补充，history 会缺少 Q，
        // 导致 merge 时 Q 被 dedupeByIdentity 放到 A 之后（因为 A 在
        // history 中有副本，被提前到 history 位置，而 Q 只在 thread 中）。
        // 这里将 kwargs.input.messages 作为 run 的第一条消息加入 history。
        const runCreatedAt = (run as { created_at?: string | null })
          .created_at;
        const kwargs = (run as { kwargs?: { input?: { messages?: unknown[] } } })
          .kwargs;
        const inputMessages = (kwargs?.input?.messages ?? []) as Message[];
        const inputEntries: { seq: number; message: Message }[] =
          inputMessages
            .filter((m): m is Message => m != null && typeof m === "object")
            .map((m, i) => ({
              seq: -1 - i, // 排在所有 event 消息之前
              message: withMessageTimestamp(
                m,
                runCreatedAt ?? getMessageTimestamp(m) ?? undefined,
              ),
            }));
        const _messages = [
          ...inputEntries,
          ...runMessages
            .filter((m) => !m.metadata?.caller?.startsWith("middleware:"))
            .map((m) => ({
              seq: m.seq,
              message: withMessageTimestamp(
                m.content,
                m.created_at ??
                  getMessageTimestamp(m.content) ??
                  runCreatedAt ??
                  undefined,
              ),
            })),
        ];
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
        // Load only one run per call to preserve the "load more" button.
        // Timestamps are handled by the runCreatedAt fallback in
        // withMessageTimestamp and the thread state's own timestamps.
      } while (false);
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


// Re-export useThreadRuns from query module
export { useThreadRuns } from "./query";

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
