"use client";

import { BotIcon } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { AgentWelcome } from "@/components/workspace/agent-welcome";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import {
  ChatBox,
  useEnsureThreadAccessible,
  useThreadChat,
} from "@/components/workspace/chats";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { InputBox } from "@/components/workspace/input-box";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { useNotification } from "@/core/notification/hooks";
import {
  useDraftSandboxThread,
  usePublishedAgent,
} from "@/core/published-agents";
import type { PublishedAgentDetail } from "@/core/published-agents/types";
import {
  copyThreadSettings,
  useLocalSettings,
  useThreadSettings,
} from "@/core/settings";
import { useThreadStream, useThreadTokenUsage } from "@/core/threads/hooks";
import type { AgentThreadContext } from "@/core/threads/types";
import { threadTokenUsageToTokenUsage } from "@/core/threads/token-usage";
import { pathOfThread, textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export default function AgentChatPage() {
  const { t } = useI18n();

  const { agent_name } = useParams<{
    agent_name: string;
  }>();

  const { agent: publishedAgent } = usePublishedAgent(agent_name);
  const agent = useMemo<{
    name: string;
    description?: string;
  } | null>(() => {
    const pa = publishedAgent as PublishedAgentDetail | null | undefined;
    if (!pa) return null;
    return {
      name: pa.display_name || pa.slug || agent_name,
      description: pa.description ?? undefined,
    };
  }, [publishedAgent, agent_name]);

  const { threadId, setThreadId, isNewThread, setIsNewThread, isMock } =
    useThreadChat();
  useEnsureThreadAccessible(pathOfThread("new", { agent_name: agent_name }));
  // `isNewThread` gates history/token-usage fetches until the backend creates
  // the thread. `isWelcomeMode` controls only the centered welcome layout, so
  // it can flip immediately on submit without triggering eager history loads.
  const [isWelcomeMode, setIsWelcomeMode] = useState(isNewThread);
  const [settings, setSettings] = useThreadSettings(threadId);
  const [localSettings, setLocalSettings] = useLocalSettings();
  const sandboxScopeQuery = useDraftSandboxThread(
    isNewThread || isMock ? undefined : threadId,
  );
  const sandboxScope = sandboxScopeQuery.sandbox;

  // Track whether the user has explicitly overridden the model selection.
  // This allows the user to test with a different model from the agent's config.
  const userModelOverrideRef = useRef<string | undefined>(undefined);

  // Override onContextChange to detect and track user model changes
  const handleContextChange = useCallback(
    (context: Partial<AgentThreadContext>) => {
      // If the user changed the model from the authoritative one, track it
      const authoritativeModel = sandboxScope?.model_name ?? publishedAgent?.draft?.model_name;
      if (
        context.model_name &&
        authoritativeModel &&
        context.model_name !== authoritativeModel &&
        context.model_name !== userModelOverrideRef.current
      ) {
        userModelOverrideRef.current = context.model_name;
      } else if (
        context.model_name &&
        authoritativeModel &&
        context.model_name === authoritativeModel
      ) {
        // User reset back to the authoritative model
        userModelOverrideRef.current = undefined;
      }
      setSettings("context", context);
    },
    [setSettings, sandboxScope, publishedAgent],
  );

  const effectiveContext = useMemo(() => {
    const selectedSkillName =
      typeof settings.context.skill_name === "string"
        ? settings.context.skill_name
        : undefined;
    const selectedConnectorIds = Array.isArray(settings.context.connector_ids)
      ? settings.context.connector_ids.filter(
          (connectorId): connectorId is string =>
            typeof connectorId === "string",
        )
      : undefined;

    // Determine model_name with proper priority:
    // 1. User's explicit override (via model selector)
    // 2. Sandbox thread's model (from backend, for existing threads)
    // 3. Agent's draft model (for new threads or when sandbox not available)
    // 4. Thread's saved model (from localStorage, lowest priority)
    const agentDraftModelName = publishedAgent?.draft?.model_name ?? undefined;
    const threadModelName = settings.context.model_name;
    const userOverride = userModelOverrideRef.current;
    const resolvedModelName =
      userOverride ??
      sandboxScope?.model_name ??
      agentDraftModelName ??
      threadModelName ??
      undefined;

    if (!sandboxScope) {
      return {
        ...settings.context,
        model_name: resolvedModelName,
        skill_name: selectedSkillName,
        connector_ids: selectedConnectorIds,
      };
    }
    const allowedSkills = new Set(sandboxScope.skill_names);
    const allowedConnectors = new Set(sandboxScope.connector_ids);
    const connectorIds = selectedConnectorIds?.filter((connectorId) =>
      allowedConnectors.has(connectorId),
    );
    return {
      ...settings.context,
      model_name: resolvedModelName,
      skill_name:
        selectedSkillName && allowedSkills.has(selectedSkillName)
          ? selectedSkillName
          : undefined,
      connector_ids:
        connectorIds && connectorIds.length > 0 ? connectorIds : undefined,
    };
  }, [sandboxScope, settings.context, publishedAgent, isNewThread]);
  const { tokenUsageEnabled } = useModels();
  const threadTokenUsage = useThreadTokenUsage(
    isNewThread || isMock ? undefined : threadId,
    { enabled: tokenUsageEnabled && !isMock },
  );
  const backendTokenUsage = threadTokenUsageToTokenUsage(threadTokenUsage.data);

  const { showNotification } = useNotification();

  useEffect(() => {
    setIsWelcomeMode(isNewThread);
  }, [isNewThread]);

  const {
    thread,
    pendingUsageMessages,
    sendMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    context: { ...effectiveContext, agent_name: agent_name },
    isMock,
    onSend: () => {
      setIsWelcomeMode(false);
    },
    onStart: (createdThreadId) => {
      copyThreadSettings(threadId, createdThreadId);
      setThreadId(createdThreadId);
      setIsNewThread(false);
      // ! Important: Never use next.js router for navigation in this case, otherwise it will cause the thread to re-mount and lose all states. Use native history API instead.
      history.replaceState(
        null,
        "",
        `/workspace/agents/${agent_name}/chats/${createdThreadId}`,
      );
    },
    onFinish: (state) => {
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages[state.messages.length - 1];
        if (lastMessage) {
          const textContent = textOfMessage(lastMessage);
          if (textContent) {
            body =
              textContent.length > 200
                ? textContent.substring(0, 200) + "..."
                : textContent;
          }
        }
        showNotification(state.title, { body });
      }
    },
  });

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      if (sandboxScopeQuery.isLoading || sandboxScopeQuery.error) {
        return;
      }
      const sendPromise = sendMessage(threadId, message, { agent_name });
      if (message.files.length > 0) {
        return sendPromise;
      }
      void sendPromise;
    },
    [
      sendMessage,
      threadId,
      agent_name,
      sandboxScopeQuery.error,
      sandboxScopeQuery.isLoading,
    ],
  );
  const handleChoiceSelect = useCallback(
    (choice: string) => {
      if (
        isUploading ||
        thread.isLoading ||
        sandboxScopeQuery.isLoading ||
        sandboxScopeQuery.error ||
        env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
      ) {
        return;
      }

      void sendMessage(threadId, { text: choice, files: [] }, { agent_name });
    },
    [
      agent_name,
      isUploading,
      sandboxScopeQuery.error,
      sandboxScopeQuery.isLoading,
      sendMessage,
      thread.isLoading,
      threadId,
    ],
  );

  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);

  const tokenUsageInlineMode = tokenUsageEnabled
    ? localSettings.tokenUsage.inlineMode
    : "off";
  const hasTodos = (thread.values.todos?.length ?? 0) > 0;

  return (
    <ThreadContext.Provider value={{ thread }}>
      <ChatBox
        threadId={threadId}
        sandboxFilesEnabled={!isNewThread && !isMock}
      >
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-4",
              isWelcomeMode
                ? "bg-background/0 backdrop-blur-none"
                : "bg-background/80 shadow-xs backdrop-blur",
            )}
          >
            {/* Agent badge */}
            <div className="flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1">
              <BotIcon className="text-primary h-3.5 w-3.5" />
              <span className="text-xs font-medium">
                {agent?.name ?? agent_name}
              </span>
            </div>

            <div className="flex w-full items-center text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
            </div>
            <div className="mr-4 flex items-center">
              <TokenUsageIndicator
                threadId={isNewThread ? undefined : threadId}
                backendUsage={backendTokenUsage}
                enabled={tokenUsageEnabled}
                messages={thread.messages}
                pendingMessages={pendingUsageMessages}
                preferences={localSettings.tokenUsage}
                onPreferencesChange={(preferences) =>
                  setLocalSettings("tokenUsage", preferences)
                }
              />
              <ExportTrigger threadId={threadId} />
              <ArtifactTrigger />
            </div>
          </header>

          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex min-h-0 flex-1 justify-center">
              <MessageList
                className={cn("size-full", !isWelcomeMode && "pt-10")}
                threadId={threadId}
                thread={thread}
                paddingBottom={MESSAGE_LIST_DEFAULT_PADDING_BOTTOM}
                hasMoreHistory={hasMoreHistory}
                loadMoreHistory={loadMoreHistory}
                isHistoryLoading={isHistoryLoading}
                tokenUsageInlineMode={tokenUsageInlineMode}
                onChoiceSelect={handleChoiceSelect}
              />
            </div>

            <div
              className={cn(
                "right-0 bottom-0 left-0 z-30 flex justify-center px-4",
                isWelcomeMode ? "absolute" : "relative shrink-0 pb-4",
              )}
            >
              <div
                className={cn(
                  "relative w-full",
                  isWelcomeMode && "-translate-y-[calc(50vh-96px)]",
                  isWelcomeMode
                    ? "max-w-[720px]"
                    : "max-w-(--container-width-md)",
                )}
              >
                {hasTodos && (
                  <div
                    className={cn(
                      "right-0 left-0 z-0",
                      isWelcomeMode ? "absolute -top-4" : "relative",
                    )}
                  >
                    <div
                      className={cn(
                        "right-0 bottom-0 left-0",
                        isWelcomeMode ? "absolute" : "relative",
                      )}
                    >
                      <TodoList
                        className="bg-background/5"
                        todos={thread.values.todos ?? []}
                        hidden={false}
                      />
                    </div>
                  </div>
                )}

                <InputBox
                  className={cn(
                    "bg-background/5 w-full",
                    isWelcomeMode && "-translate-y-4",
                  )}
                  isWelcomeMode={isWelcomeMode}
                  threadId={threadId}
                  autoFocus={isWelcomeMode}
                  status={
                    thread.error
                      ? "error"
                      : thread.isLoading
                        ? "streaming"
                        : "ready"
                  }
                  context={effectiveContext}
                  allowedSkillNames={sandboxScope?.skill_names}
                  allowedConnectorIds={sandboxScope?.connector_ids}
                  extraHeader={
                    isWelcomeMode && (
                      <AgentWelcome agent={agent} agentName={agent_name} />
                    )
                  }
                  disabled={
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
                    isUploading ||
                    sandboxScopeQuery.isLoading ||
                    Boolean(sandboxScopeQuery.error)
                  }
                  onContextChange={handleContextChange}
                  onSubmit={handleSubmit}
                  onStop={handleStop}
                />
                {sandboxScopeQuery.error && (
                  <div
                    role="alert"
                    className="text-destructive mt-2 text-center text-xs"
                  >
                    {sandboxScopeQuery.error instanceof Error
                      ? sandboxScopeQuery.error.message
                      : "Unable to load the draft sandbox capability scope."}
                  </div>
                )}
                {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                  <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                    {t.common.notAvailableInDemoMode}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      </ChatBox>
    </ThreadContext.Provider>
  );
}
