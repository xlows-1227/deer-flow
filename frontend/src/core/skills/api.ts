import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { CustomSkill, Skill } from "./type";

async function readErrorMessage(response: Response, fallback: string) {
  const errorData = await response.json().catch(() => ({}));
  return errorData.detail ?? fallback;
}

export async function loadSkills() {
  const skills = await fetch(`${getBackendBaseURL()}/api/skills`);
  const json = await skills.json();
  return json.skills as Skill[];
}

export async function loadCustomSkill(skillName: string): Promise<CustomSkill> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}`,
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to load custom skill"),
    );
  }
  const json = await response.json();
  return json as CustomSkill;
}

export async function loadPublicSkill(skillName: string): Promise<CustomSkill> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/public/${encodeURIComponent(skillName)}`,
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to load public skill"),
    );
  }
  const json = await response.json();
  return json as CustomSkill;
}

export async function enableSkill(skillName: string, enabled: boolean) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/${skillName}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        enabled,
      }),
    },
  );
  return response.json();
}

export interface InstallSkillRequest {
  thread_id: string;
  path: string;
}

export interface InstallSkillResponse {
  success: boolean;
  skill_name: string;
  message: string;
}

export interface CreateCustomSkillRequest {
  name: string;
  description: string;
  content?: string;
  allowed_tools?: string[];
}

export interface SkillAIDraftRequest {
  prompt: string;
  name_hint?: string;
  description_hint?: string;
  deep_thinking?: boolean;
  skill_creator_name?: string;
}

export interface SkillAIDraftResponse {
  name: string;
  description: string;
  content: string;
}

export async function installSkill(
  request: InstallSkillRequest,
): Promise<InstallSkillResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/skills/install`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const errorMessage = await readErrorMessage(
      response,
      `HTTP ${response.status}: ${response.statusText}`,
    );
    return {
      success: false,
      skill_name: "",
      message: errorMessage,
    };
  }

  return response.json();
}

export async function createCustomSkill(
  request: CreateCustomSkillRequest,
): Promise<CustomSkill> {
  const response = await fetch(`${getBackendBaseURL()}/api/skills/custom`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to create custom skill"),
    );
  }
  return response.json();
}

export async function generateSkillDraft(
  request: SkillAIDraftRequest,
): Promise<SkillAIDraftResponse> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/ai-draft`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    },
  );
  if (!response.ok) {
    throw new Error(await readErrorMessage(response, "Failed to draft skill"));
  }
  return response.json();
}

export async function uploadSkillArchive(
  file: File,
): Promise<InstallSkillResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${getBackendBaseURL()}/api/skills/upload`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    return {
      success: false,
      skill_name: "",
      message: await readErrorMessage(response, "Failed to upload skill"),
    };
  }
  return response.json();
}
