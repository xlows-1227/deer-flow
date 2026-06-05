import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  ConnectorCreateInput,
  ConnectorConfigTestInput,
  ConnectorInstance,
  ConnectorTestResult,
  ConnectorTypeDefinition,
  ConnectorUpdateInput,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  const body = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail?: unknown }).detail
        : null;
    if (detail && typeof detail === "object" && "message" in detail) {
      throw new Error(String((detail as { message?: unknown }).message));
    }
    throw new Error(`Connector request failed with ${response.status}`);
  }
  return body as T;
}

export async function loadConnectorTypes() {
  const response = await fetch(`${getBackendBaseURL()}/api/connector-types`);
  const data = await readJson<{ connector_types: ConnectorTypeDefinition[] }>(
    response,
  );
  return data.connector_types;
}

export async function loadConnectors() {
  const response = await fetch(`${getBackendBaseURL()}/api/connectors`);
  const data = await readJson<{ connectors: ConnectorInstance[] }>(response);
  return data.connectors;
}

export async function createConnector(input: ConnectorCreateInput) {
  const response = await fetch(`${getBackendBaseURL()}/api/connectors`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return readJson<ConnectorInstance>(response);
}

export async function updateConnector(
  connectorId: string,
  input: ConnectorUpdateInput,
) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/connectors/${encodeURIComponent(connectorId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return readJson<ConnectorInstance>(response);
}

export async function deleteConnector(connectorId: string) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/connectors/${encodeURIComponent(connectorId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    await readJson<never>(response);
  }
}

export async function setConnectorEnabled(
  connectorId: string,
  enabled: boolean,
) {
  const action = enabled ? "enable" : "disable";
  const response = await fetch(
    `${getBackendBaseURL()}/api/connectors/${encodeURIComponent(
      connectorId,
    )}/${action}`,
    { method: "POST" },
  );
  return readJson<ConnectorInstance>(response);
}

export async function testConnector(connectorId: string) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/connectors/${encodeURIComponent(
      connectorId,
    )}/test`,
    { method: "POST" },
  );
  return readJson<ConnectorTestResult>(response);
}

export async function testConnectorConfig(
  typeName: string,
  input: ConnectorConfigTestInput,
) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/connector-types/${encodeURIComponent(
      typeName,
    )}/test`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return readJson<ConnectorTestResult>(response);
}

export async function testExistingConnectorConfig(
  connectorId: string,
  input: ConnectorConfigTestInput,
) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/connectors/${encodeURIComponent(
      connectorId,
    )}/test-config`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return readJson<ConnectorTestResult>(response);
}
