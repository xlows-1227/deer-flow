import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  MCPConfig,
  MCPServerConfig,
  MCPServerCreatePayload,
  MCPServerUpdatePayload,
} from "./types";

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // ignore
  }
  return `Request failed (${response.status})`;
}

export async function loadMCPConfig() {
  const response = await fetch(`${getBackendBaseURL()}/api/mcp/config`);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json() as Promise<MCPConfig>;
}

export async function setMCPServerEnabled(name: string, enabled: boolean) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/servers/${encodeURIComponent(name)}/enabled`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json() as Promise<MCPServerConfig>;
}

export async function createMCPServer(payload: MCPServerCreatePayload) {
  const response = await fetch(`${getBackendBaseURL()}/api/mcp/servers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json() as Promise<MCPServerConfig>;
}

export async function updateMCPServer(
  name: string,
  payload: MCPServerUpdatePayload,
) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/servers/${encodeURIComponent(name)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json() as Promise<MCPServerConfig>;
}

export async function deleteMCPServer(name: string) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/mcp/servers/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
}
