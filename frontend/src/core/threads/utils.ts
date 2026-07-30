import type { Message } from "@langchain/langgraph-sdk";

import type { AgentThread, AgentThreadContext } from "./types";

export const THREAD_SOURCE_SCHEDULED_TASK = "scheduled_task";
export const THREAD_SOURCE_SKILL_SESSION = "skill_session";

export function isVisibleInChatList(
  thread: Pick<AgentThread, "thread_id" | "metadata">,
) {
  const source = thread.metadata?.source;
  return (
    source !== THREAD_SOURCE_SCHEDULED_TASK &&
    source !== THREAD_SOURCE_SKILL_SESSION
  );
}

type ThreadAgentSource = {
  context?: Pick<AgentThreadContext, "agent_name"> | null;
  metadata?: Record<string, unknown> | null;
};

export function agentNameOfThread(thread: ThreadAgentSource) {
  const contextAgent = thread.context?.agent_name;
  if (typeof contextAgent === "string" && contextAgent) {
    return contextAgent;
  }
  const metadataAgent = thread.metadata?.agent_name;
  return typeof metadataAgent === "string" && metadataAgent
    ? metadataAgent
    : undefined;
}

export function agentDisplayNameOfThread(thread: ThreadAgentSource) {
  const displayName = thread.metadata?.agent_display_name;
  return typeof displayName === "string" && displayName
    ? displayName
    : agentNameOfThread(thread);
}

export function draftSandboxAgentIdOfThread(
  thread: Pick<ThreadAgentSource, "metadata">,
) {
  const agentId = thread.metadata?.draft_sandbox_agent_id;
  return typeof agentId === "string" && agentId ? agentId : undefined;
}

type ThreadRouteTarget =
  | string
  | {
      thread_id: string;
      context?: Pick<AgentThreadContext, "agent_name"> | null;
      metadata?: Record<string, unknown> | null;
    };

export function pathOfThread(
  thread: ThreadRouteTarget,
  context?: Pick<AgentThreadContext, "agent_name"> | null,
) {
  const threadId = typeof thread === "string" ? thread : thread.thread_id;
  const agentName =
    typeof thread === "string"
      ? context?.agent_name
      : (agentNameOfThread(thread) ?? context?.agent_name);

  return agentName
    ? `/workspace/agents/${encodeURIComponent(agentName)}/chats/${threadId}`
    : `/workspace/chats/${threadId}`;
}

export function textOfMessage(message: Message) {
  if (typeof message.content === "string") {
    return message.content;
  } else if (Array.isArray(message.content)) {
    for (const part of message.content) {
      if (part.type === "text") {
        return part.text;
      }
    }
  }
  return null;
}

export function titleOfThread(thread: AgentThread) {
  return thread.values?.title ?? "Untitled";
}
