export interface UserInfo {
  id: string;
  email: string;
  system_role: "admin" | "user";
}

export interface SkillSharedUser {
  id: string;
  email: string;
  system_role?: "admin" | "user";
}

export interface Skill {
  name: string;
  description: string;
  display_name: string | null;
  description_zh: string | null;
  category: string;
  license: string | null;
  enabled: boolean;
  download_url?: string | null;
  owner_user_id?: string | null;
  owner_email?: string | null;
  shared_with?: SkillSharedUser[];
  can_edit?: boolean;
}

export interface CustomSkill extends Skill {
  content: string;
}

export interface SkillShareState {
  skill_name: string;
  owner_user_id: string;
  owner_email: string;
  sharees: SkillSharedUser[];
}

export interface SkillFileEntry {
  path: string;
  type: "file" | "directory";
  size: number | null;
}

export interface SkillFileContent {
  path: string;
  content: string;
}

export interface SkillVersion {
  seq: number;
  created_at: string;
  author: string;
  action: string;
  message: string | null;
  label: string | null;
  thread_id: string | null;
  restored_from?: number;
  file_count: number;
  size_bytes: number;
}
