import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  CustomSkill,
  Skill,
  SkillFileContent,
  SkillFileEntry,
  SkillVersion,
} from "./type";

async function readErrorData(response: Response) {
  const errorData = await response.json().catch(() => ({}));
  return errorData;
}

function normalizeErrorDetail(
  detail: unknown,
  fallback: string,
): { message: string; error?: SkillUploadErrorDetail } {
  if (typeof detail === "string") {
    return { message: detail };
  }
  if (detail && typeof detail === "object") {
    const value = detail as Partial<SkillUploadErrorDetail>;
    const message =
      typeof value.message === "string"
        ? value.message
        : typeof value.reason === "string"
          ? value.reason
          : fallback;
    return {
      message,
      error: {
        code: typeof value.code === "string" ? value.code : "upload_failed",
        message,
        reason: typeof value.reason === "string" ? value.reason : message,
        can_force: value.can_force === true,
      },
    };
  }
  return { message: fallback };
}

async function readErrorMessage(response: Response, fallback: string) {
  const errorData = await readErrorData(response);
  return normalizeErrorDetail(errorData.detail, fallback).message;
}

export async function loadSkills() {
  const skills = await fetch(`${getBackendBaseURL()}/api/skills`, {
    headers: getCsrfHeaders(),
  });
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

export async function deleteCustomSkill(skillName: string): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}`,
    {
      method: "DELETE",
    },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to delete custom skill"),
    );
  }
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

export interface SkillUploadErrorDetail {
  code: string;
  message: string;
  reason: string;
  can_force: boolean;
}

export interface SkillUploadFailure {
  success: false;
  skill_name: "";
  message: string;
  error?: SkillUploadErrorDetail;
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
  options?: { force?: boolean },
): Promise<InstallSkillResponse | SkillUploadFailure> {
  const formData = new FormData();
  formData.append("file", file);
  const query = options?.force ? "?force=true" : "";
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/upload${query}`,
    {
      method: "POST",
      body: formData,
    },
  );
  if (!response.ok) {
    const errorData = await readErrorData(response);
    const { message, error } = normalizeErrorDetail(
      errorData.detail,
      "Failed to upload skill",
    );
    return {
      success: false,
      skill_name: "",
      message,
      error,
    };
  }
  return response.json();
}

export async function loadCustomSkills(): Promise<Skill[]> {
  const response = await fetch(`${getBackendBaseURL()}/api/skills/custom`);
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to load custom skills"),
    );
  }
  const json = await response.json();
  return json.skills as Skill[];
}

export async function listCustomSkillFiles(
  skillName: string,
): Promise<SkillFileEntry[]> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/files`,
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to list custom skill files"),
    );
  }
  const json = await response.json();
  return json.files as SkillFileEntry[];
}

export async function readCustomSkillFile(
  skillName: string,
  path: string,
): Promise<SkillFileContent> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/file?path=${encodeURIComponent(path)}`,
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to read custom skill file"),
    );
  }
  return response.json() as Promise<SkillFileContent>;
}

export async function writeCustomSkillFile(
  skillName: string,
  path: string,
  content: string,
): Promise<SkillFileContent> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/file`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ path, content }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to write custom skill file"),
    );
  }
  return response.json() as Promise<SkillFileContent>;
}

export async function deleteCustomSkillFile(
  skillName: string,
  path: string,
): Promise<void> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/file?path=${encodeURIComponent(path)}`,
    {
      method: "DELETE",
    },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to delete custom skill file"),
    );
  }
}

export async function createCustomSkillDirectory(
  skillName: string,
  path: string,
): Promise<SkillFileEntry> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/directories`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ path }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to create directory"),
    );
  }
  return response.json() as Promise<SkillFileEntry>;
}

export async function uploadCustomSkillFiles(
  skillName: string,
  entries: { path: string; file: File }[],
): Promise<{ paths: string[] }> {
  const formData = new FormData();
  for (const entry of entries) {
    formData.append("files", entry.file);
    formData.append("paths", entry.path);
  }
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/upload`,
    {
      method: "POST",
      body: formData,
    },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to upload custom skill files"),
    );
  }
  return response.json() as Promise<{ paths: string[] }>;
}

export async function updateCustomSkill(
  skillName: string,
  content: string,
): Promise<CustomSkill> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ content }),
    },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to update custom skill"),
    );
  }
  return response.json();
}

export interface CreateSkillVersionSnapshotRequest {
  action?: string;
  message?: string;
  thread_id?: string | null;
}

export async function listCustomSkillVersions(
  skillName: string,
): Promise<SkillVersion[]> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/versions`,
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(response, "Failed to list custom skill versions"),
    );
  }
  const json = await response.json();
  return json.versions as SkillVersion[];
}

export async function createCustomSkillVersionSnapshot(
  skillName: string,
  request: CreateSkillVersionSnapshotRequest,
): Promise<SkillVersion> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/versions`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
    },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(
        response,
        "Failed to create custom skill version snapshot",
      ),
    );
  }
  return response.json() as Promise<SkillVersion>;
}

export async function listCustomSkillVersionFiles(
  skillName: string,
  seq: number,
): Promise<SkillFileEntry[]> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/versions/${seq}/files`,
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(
        response,
        "Failed to list custom skill version files",
      ),
    );
  }
  const json = await response.json();
  return json.files as SkillFileEntry[];
}

export async function readCustomSkillVersionFile(
  skillName: string,
  seq: number,
  path: string,
): Promise<SkillFileContent> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/versions/${seq}/file?path=${encodeURIComponent(path)}`,
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(
        response,
        "Failed to read custom skill version file",
      ),
    );
  }
  return response.json() as Promise<SkillFileContent>;
}

export async function restoreCustomSkillVersion(
  skillName: string,
  seq: number,
): Promise<{ version: SkillVersion }> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/custom/${encodeURIComponent(skillName)}/versions/${seq}/restore`,
    { method: "POST" },
  );
  if (!response.ok) {
    throw new Error(
      await readErrorMessage(
        response,
        "Failed to restore custom skill version",
      ),
    );
  }
  return response.json() as Promise<{ version: SkillVersion }>;
}
