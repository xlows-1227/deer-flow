export type PublishedAgentStatus =
  | "draft"
  | "published"
  | "suspended"
  | "archived";

export type AgentKeyStatus = "active" | "overlap" | "revoked" | "expired";

export type AgentChannelStatus = "inactive" | "active" | "deleting";

export type AgentChannelHealth =
  | "unknown"
  | "healthy"
  | "unhealthy"
  | "starting"
  | "stopped";

export type PublishedRunSource = "api" | "feishu";

export type QuotaOverrides = Partial<{
  max_concurrent_runs: number;
  daily_runs: number;
  daily_tokens: number;
  max_run_seconds: number;
  max_tokens_per_run: number;
  max_input_bytes: number;
  inbound_rps: number;
}>;

export interface PublishedAgent {
  id: string;
  slug: string;
  display_name: string;
  description: string | null;
  avatar_ref: string | null;
  status: PublishedAgentStatus;
  current_release_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentDraftSkill {
  skill_name: string;
  source: "public" | "private";
}

export interface AgentConnectorGrant {
  connector_instance_id: string;
  capability: string;
}

export interface AgentDraft {
  agent_id: string;
  agent_markdown: string;
  soul_markdown: string;
  model_name: string | null;
  tool_groups: string[];
  quota_overrides: QuotaOverrides;
  revision: number;
  skill_mode?: "inherit" | "explicit";
  updated_at?: string | null;
  updated_by?: string | null;
  skills: AgentDraftSkill[];
  connector_grants: AgentConnectorGrant[];
}

export interface PublishedAgentDetail extends PublishedAgent {
  draft: AgentDraft;
}

export interface CreatePublishedAgentInput {
  slug: string;
  display_name: string;
  description?: string | null;
  avatar_ref?: string | null;
}

export interface UpdateAgentDraftInput {
  revision: number;
  agent_markdown?: string;
  soul_markdown?: string;
  model_name?: string | null;
  tool_groups?: string[];
  quota_overrides?: QuotaOverrides;
  skills?: AgentDraftSkill[];
  connector_grants?: AgentConnectorGrant[];
}

export interface DraftSandboxRun {
  agent_id: string;
  thread_id: string;
  run_id: string;
  status: string;
  draft_revision: number;
  billable: false;
}

export interface DraftSandboxThread {
  agent_id: string;
  agent_slug: string;
  thread_id: string;
  draft_revision: number;
  skill_names: string[];
  connector_ids: string[];
  model_name?: string | null;
  billable: false;
}

export interface AgentReleaseSkill {
  skill_revision_id: string;
  skill_name?: string | null;
}

export interface AgentRelease {
  id: string;
  agent_id: string;
  release_no: number;
  agent_markdown: string;
  soul_markdown: string;
  model_name: string | null;
  tool_groups: string[];
  quota_overrides: QuotaOverrides;
  manifest_checksum: string;
  created_by: string;
  created_at: string;
  skills: AgentReleaseSkill[];
  connector_grants: AgentConnectorGrant[];
}

export interface PublishViolation {
  code: string;
  message: string;
  field: string | null;
}

export interface PublishResult {
  release_id: string;
  release_no: number;
  published_at: string;
}

export interface RollbackResult {
  release_id: string;
  release_no: number;
}

export interface AgentApiKey {
  id: string;
  agent_id: string;
  name: string;
  key_prefix: string;
  last_four: string;
  status: AgentKeyStatus;
  quota_overrides: QuotaOverrides;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  rotation_of: string | null;
}

export interface RevealedAgentApiKey extends AgentApiKey {
  api_key: string;
  warning: string;
}

export interface CreateAgentKeyInput {
  name: string;
  quota_overrides?: QuotaOverrides;
}

export interface UpdateAgentKeyInput {
  name?: string;
  quota_overrides?: QuotaOverrides;
}

export interface AgentChannel {
  id: string;
  agent_id: string;
  channel_type: "feishu";
  app_id: string;
  connection_mode: "websocket";
  status: AgentChannelStatus;
  health: AgentChannelHealth;
  health_detail: string | null;
  secret_configured: boolean;
  created_at: string;
  updated_at: string;
  last_started_at: string | null;
}

export interface AgentChannelCredentials {
  app_id: string;
  app_secret: string;
  verification_token: string;
  encrypt_key?: string | null;
}

export type UpdateAgentChannelCredentials = Partial<
  Pick<AgentChannelCredentials, "app_id" | "encrypt_key">
> &
  Pick<AgentChannelCredentials, "app_secret" | "verification_token">;

export interface AgentUsageDay {
  date: string;
  runs: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_microusd: number;
  statuses: Record<string, number>;
}

export interface AgentUsageTotals {
  runs: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_microusd: number;
}

export interface AgentOperationsMetrics {
  agent_status: PublishedAgentStatus | null;
  agent_active: boolean;
  active_bindings: number;
  unhealthy_bindings: number;
  quota_rejections: number;
  concurrency_saturation: number;
  feishu_event_latency_ms: {
    average: number;
    p95: number;
  };
  connector_failures: number;
  connector_denials: number;
  current_release_id: string | null;
  current_release_runs: number;
  current_release_errors: number;
  current_release_error_rate: number;
}

export interface AgentUsage {
  agent_id: string;
  days: AgentUsageDay[];
  totals: AgentUsageTotals;
  operations: AgentOperationsMetrics;
}

export interface AgentQuotaPolicy {
  agent_id: string;
  platform_defaults: Required<QuotaOverrides>;
  owner_overrides: QuotaOverrides;
  effective: Required<QuotaOverrides>;
}

export interface AgentAuditEvent {
  id: string;
  request_id: string;
  source: PublishedRunSource | null;
  credential_id: string | null;
  category: "quota" | "authentication" | "capability" | "request";
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  skill_name: string | null;
  method: string;
  path_template: string;
  status_code: number;
  duration_ms: number;
  created_at: string;
}

export interface SelectableAgentSkill {
  skill_name: string;
  source: "public" | "private";
  display_name?: string | null;
  description?: string | null;
  description_zh?: string | null;
  declared_connector_caps?: string[];
}

export interface AgentDraftOptions {
  skills: SelectableAgentSkill[];
}
