import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  ImageGenerationConfig,
  ImageGenerationConfigUpdate,
} from "./types";

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      detail?: unknown;
      message?: unknown;
    };
    const detail = body.detail ?? body.message;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  } catch {
    // Fall through to the generic status message below.
  }
  return `Request failed with HTTP ${response.status}`;
}

export async function loadImageGenerationConfig() {
  const response = await fetch(
    `${getBackendBaseURL()}/api/tools/image-generation/config`,
  );
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json() as Promise<ImageGenerationConfig>;
}

export async function updateImageGenerationConfig(
  config: ImageGenerationConfigUpdate,
) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/tools/image-generation/config`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(config),
    },
  );
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json() as Promise<ImageGenerationConfig>;
}
