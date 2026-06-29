import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

export type ModelProvider = "openai" | "anthropic";

export interface CustomModel {
  id: string;
  user_id: string;
  name: string;
  display_name: string | null;
  provider: ModelProvider;
  model: string;
  base_url: string | null;
  enabled: boolean;
  has_api_key: boolean;
  api_key_last_four: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CustomModelCreateInput {
  name: string;
  display_name?: string | null;
  provider: ModelProvider;
  model: string;
  base_url?: string | null;
  api_key?: string | null;
  enabled?: boolean;
}

export interface CustomModelUpdateInput {
  name?: string;
  display_name?: string | null;
  provider?: ModelProvider;
  model?: string;
  base_url?: string | null;
  api_key?: string | null;
  enabled?: boolean;
}

export const MASKED_API_KEY = "***";

async function readJson<T>(response: Response): Promise<T> {
  const body = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail?: unknown }).detail
        : null;
    if (typeof detail === "string") {
      throw new Error(detail);
    }
    if (detail && typeof detail === "object" && "message" in detail) {
      throw new Error(String((detail as { message?: unknown }).message));
    }
    throw new Error(`Custom model request failed with ${response.status}`);
  }
  return body as T;
}

export async function loadCustomModels(): Promise<CustomModel[]> {
  const response = await fetch(`${getBackendBaseURL()}/api/models/custom`);
  const data = await readJson<{ models: CustomModel[] }>(response);
  return data.models ?? [];
}

export async function createCustomModel(
  input: CustomModelCreateInput,
): Promise<CustomModel> {
  const response = await fetch(`${getBackendBaseURL()}/api/models/custom`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return readJson<CustomModel>(response);
}

export async function updateCustomModel(
  modelId: string,
  input: CustomModelUpdateInput,
): Promise<CustomModel> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/models/custom/${encodeURIComponent(modelId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return readJson<CustomModel>(response);
}

export async function deleteCustomModel(modelId: string): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/models/custom/${encodeURIComponent(modelId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    await readJson(response);
  }
}

export function buildApiKeyUpdateValue(
  draft: string,
  hasStoredKey: boolean,
): string | undefined {
  const trimmed = draft.trim();
  if (!trimmed) {
    return undefined;
  }
  if (hasStoredKey && trimmed === MASKED_API_KEY) {
    return MASKED_API_KEY;
  }
  return trimmed;
}
