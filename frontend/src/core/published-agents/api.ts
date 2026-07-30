import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  AgentApiKey,
  AgentAuditEvent,
  AgentChannel,
  AgentChannelCredentials,
  AgentChannelHealth,
  AgentDraft,
  AgentDraftOptions,
  AgentRelease,
  AgentUsage,
  AgentQuotaPolicy,
  CreateAgentKeyInput,
  CreatePublishedAgentInput,
  DraftSandboxRun,
  DraftSandboxThread,
  PublishResult,
  PublishViolation,
  PublishedAgent,
  PublishedAgentDetail,
  RevealedAgentApiKey,
  RollbackResult,
  UpdateAgentChannelCredentials,
  UpdateAgentDraftInput,
  UpdateAgentKeyInput,
} from "./types";

interface ApiErrorBody {
  detail?: unknown;
  message?: unknown;
}

interface ParsedError {
  code: string | null;
  message: string;
  detail: unknown;
}

export class PublishedAgentApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string | null,
    public readonly detail: unknown,
  ) {
    super(message);
    this.name = "PublishedAgentApiError";
  }
}

export class DraftRevisionConflictError extends PublishedAgentApiError {
  constructor(message: string, code: string | null, detail: unknown) {
    super(message, 409, code, detail);
    this.name = "DraftRevisionConflictError";
  }
}

export class QuotaExceededError extends PublishedAgentApiError {
  constructor(message: string, code: string | null, detail: unknown) {
    super(message, 429, code, detail);
    this.name = "QuotaExceededError";
  }
}

export class PublishValidationError extends PublishedAgentApiError {
  constructor(
    message: string,
    public readonly violations: PublishViolation[],
    detail: unknown,
  ) {
    super(message, 422, "publish_validation_failed", detail);
    this.name = "PublishValidationError";
  }
}

function parseErrorBody(
  body: ApiErrorBody | null,
  fallback: string,
): ParsedError {
  const detail = body?.detail ?? body?.message ?? null;
  if (typeof detail === "string") {
    return { code: null, message: detail, detail };
  }
  if (detail && typeof detail === "object") {
    const value = detail as {
      code?: unknown;
      message?: unknown;
    };
    return {
      code: typeof value.code === "string" ? value.code : null,
      message: typeof value.message === "string" ? value.message : fallback,
      detail,
    };
  }
  return { code: null, message: fallback, detail };
}

async function throwResponseError(response: Response): Promise<never> {
  const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
  const parsed = parseErrorBody(
    body,
    `Published Agent request failed with HTTP ${response.status}`,
  );

  if (
    response.status === 409 &&
    (parsed.code === "revision_conflict" ||
      parsed.code === "draft_revision_conflict")
  ) {
    throw new DraftRevisionConflictError(
      parsed.message,
      parsed.code,
      parsed.detail,
    );
  }
  if (response.status === 429) {
    throw new QuotaExceededError(parsed.message, parsed.code, parsed.detail);
  }
  if (response.status === 422 && parsed.code === "publish_validation_failed") {
    const violationsValue = (parsed.detail as { violations?: unknown } | null)
      ?.violations;
    const violations = Array.isArray(violationsValue)
      ? (violationsValue as PublishViolation[])
      : [];
    throw new PublishValidationError(
      parsed.message ===
        `Published Agent request failed with HTTP ${response.status}`
        ? "The draft is not ready to publish."
        : parsed.message,
      violations,
      parsed.detail,
    );
  }
  throw new PublishedAgentApiError(
    parsed.message,
    response.status,
    parsed.code,
    parsed.detail,
  );
}

async function request<T>(
  path: string,
  init: RequestInit = { method: "GET" },
): Promise<T> {
  const response = await fetch(`${getBackendBaseURL()}${path}`, init);
  if (!response.ok) {
    await throwResponseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function jsonInit(method: "POST" | "PATCH", body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}

function agentPath(agentId: string): string {
  return `/api/published-agents/${encodeURIComponent(agentId)}`;
}

export function listPublishedAgents(): Promise<PublishedAgent[]> {
  return request("/api/published-agents", { method: "GET" });
}

export function getPublishedAgent(
  agentId: string,
): Promise<PublishedAgentDetail> {
  return request(agentPath(agentId), { method: "GET" });
}

export function createPublishedAgent(
  input: CreatePublishedAgentInput,
): Promise<PublishedAgent> {
  return request("/api/published-agents", jsonInit("POST", input));
}

export function updateAgentDraft(
  agentId: string,
  input: UpdateAgentDraftInput,
): Promise<AgentDraft> {
  return request(`${agentPath(agentId)}/draft`, jsonInit("PATCH", input));
}

export function createDraftSandboxRun(
  agentId: string,
  message: string,
): Promise<DraftSandboxRun> {
  return request(
    `${agentPath(agentId)}/draft/sandbox-runs`,
    jsonInit("POST", { message }),
  );
}

export async function getDraftSandboxThread(
  threadId: string,
): Promise<DraftSandboxThread | null> {
  try {
    return await request(
      `/api/published-agents/draft/sandbox-threads/${encodeURIComponent(threadId)}`,
      { method: "GET" },
    );
  } catch (error) {
    if (error instanceof PublishedAgentApiError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

export function getAgentDraftOptions(
  agentId: string,
): Promise<AgentDraftOptions> {
  return request(`${agentPath(agentId)}/draft/options`, { method: "GET" });
}

export function changePublishedAgentStatus(
  agentId: string,
  action: "archive" | "suspend" | "resume",
): Promise<PublishedAgent> {
  return request(`${agentPath(agentId)}/${action}`, jsonInit("POST"));
}

export function publishAgent(agentId: string): Promise<PublishResult> {
  return request(`${agentPath(agentId)}/releases`, jsonInit("POST"));
}

export function listAgentReleases(agentId: string): Promise<AgentRelease[]> {
  return request(`${agentPath(agentId)}/releases`, { method: "GET" });
}

export function getAgentRelease(
  agentId: string,
  releaseNo: number,
): Promise<AgentRelease> {
  return request(`${agentPath(agentId)}/releases/${releaseNo}`, {
    method: "GET",
  });
}

export function rollbackAgent(
  agentId: string,
  releaseNo: number,
): Promise<RollbackResult> {
  return request(
    `${agentPath(agentId)}/rollback`,
    jsonInit("POST", { release_no: releaseNo }),
  );
}

export function listAgentKeys(agentId: string): Promise<AgentApiKey[]> {
  return request(`${agentPath(agentId)}/keys`, { method: "GET" });
}

export function createAgentKey(
  agentId: string,
  input: CreateAgentKeyInput,
): Promise<RevealedAgentApiKey> {
  return request(`${agentPath(agentId)}/keys`, jsonInit("POST", input));
}

export function updateAgentKey(
  agentId: string,
  keyId: string,
  input: UpdateAgentKeyInput,
): Promise<AgentApiKey> {
  return request(
    `${agentPath(agentId)}/keys/${encodeURIComponent(keyId)}`,
    jsonInit("PATCH", input),
  );
}

export function deleteAgentKey(agentId: string, keyId: string): Promise<void> {
  return request(`${agentPath(agentId)}/keys/${encodeURIComponent(keyId)}`, {
    method: "DELETE",
  });
}

export function listAgentChannels(agentId: string): Promise<AgentChannel[]> {
  return request(`${agentPath(agentId)}/channels`, { method: "GET" });
}

export function createAgentChannel(
  agentId: string,
  input: AgentChannelCredentials,
): Promise<AgentChannel> {
  return request(`${agentPath(agentId)}/channels`, jsonInit("POST", input));
}

export function updateAgentChannel(
  agentId: string,
  bindingId: string,
  input: UpdateAgentChannelCredentials,
): Promise<AgentChannel> {
  return request(
    `${agentPath(agentId)}/channels/${encodeURIComponent(bindingId)}`,
    jsonInit("PATCH", input),
  );
}

export function runAgentChannelAction(
  agentId: string,
  bindingId: string,
  action: "test" | "start" | "stop" | "restart",
): Promise<AgentChannel | { health: AgentChannelHealth; detail: string }> {
  return request(
    `${agentPath(agentId)}/channels/${encodeURIComponent(bindingId)}/${action}`,
    jsonInit("POST"),
  );
}

export function deleteAgentChannel(
  agentId: string,
  bindingId: string,
): Promise<{ deleted: boolean }> {
  return request(
    `${agentPath(agentId)}/channels/${encodeURIComponent(bindingId)}`,
    { method: "DELETE" },
  );
}

export function getAgentUsage(
  agentId: string,
  days = 30,
  source?: "api" | "feishu",
  keyId?: string,
): Promise<AgentUsage> {
  const query = new URLSearchParams({ days: String(days) });
  if (source) {
    query.set("source", source);
  }
  if (keyId) {
    query.set("key_id", keyId);
  }
  return request(`${agentPath(agentId)}/usage?${query}`, { method: "GET" });
}

export function getAgentQuotaPolicy(
  agentId: string,
): Promise<AgentQuotaPolicy> {
  return request(`${agentPath(agentId)}/quota`, { method: "GET" });
}

export function listAgentAuditEvents(
  agentId: string,
  limit = 20,
): Promise<AgentAuditEvent[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  return request(`${agentPath(agentId)}/audit?${query}`, {
    method: "GET",
  });
}
