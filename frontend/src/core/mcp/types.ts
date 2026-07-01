export type McpServerSource = "system" | "user";

export interface MCPServerConfig extends Record<string, unknown> {
  name?: string;
  enabled: boolean;
  description: string;
  type?: string;
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
  source?: McpServerSource;
  editable?: boolean;
}

export interface MCPConfig {
  mcp_servers: Record<string, MCPServerConfig>;
}

export interface MCPServerCreatePayload {
  name: string;
  enabled?: boolean;
  description?: string;
  type?: string;
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
}

export interface MCPServerUpdatePayload {
  enabled?: boolean;
  description?: string;
  type?: string;
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  headers?: Record<string, string>;
}
