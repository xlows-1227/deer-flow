export type ConnectorStatus = "active" | "disabled" | "deleted" | "error";

export type ConnectorCredentialRef = {
  provider: "env" | "encrypted_db" | "inline";
  ref?: string | null;
  username?: string | null;
  password?: string | null;
  /**
   * Set by the backend for inline credentials to signal that a password is
   * already stored. The plaintext value is never returned to the client.
   */
  has_password?: boolean;
};

export type ConnectorTypeDefinition = {
  type: string;
  category: string;
  display_name: string;
  auth_modes: string[];
  capabilities: string[];
  config_schema: Record<string, { type?: string; default?: unknown }>;
  credential_schema: Record<string, unknown>;
  default_policy: Record<string, unknown>;
};

export type ConnectorInstance = {
  id: string;
  tenant_id?: string | null;
  owner_id?: string | null;
  name: string;
  display_name?: string | null;
  type: string;
  status: ConnectorStatus;
  config: Record<string, unknown>;
  credential?: ConnectorCredentialRef | null;
  default_policy: Record<string, unknown>;
  health: Record<string, unknown>;
  last_tested_at?: string | null;
  last_used_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ConnectorCreateInput = {
  name: string;
  display_name?: string | null;
  type: string;
  config: Record<string, unknown>;
  credential: ConnectorCredentialRef;
  default_policy?: Record<string, unknown>;
};

export type ConnectorUpdateInput = Partial<{
  name: string;
  display_name: string | null;
  config: Record<string, unknown>;
  credential: ConnectorCredentialRef;
  default_policy: Record<string, unknown>;
}>;

export type ConnectorConfigTestInput = {
  config: Record<string, unknown>;
  credential?: ConnectorCredentialRef;
  default_policy?: Record<string, unknown>;
};

export type ConnectorTestResult = {
  status: "ok" | "error";
  latency_ms?: number | null;
  message?: string | null;
  capabilities: string[];
};
