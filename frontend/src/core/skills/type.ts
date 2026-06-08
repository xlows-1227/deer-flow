export interface Skill {
  name: string;
  description: string;
  display_name: string | null;
  description_zh: string | null;
  category: string;
  license: string | null;
  enabled: boolean;
}

export interface CustomSkill extends Skill {
  content: string;
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
