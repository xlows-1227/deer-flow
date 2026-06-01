export interface Skill {
  name: string;
  description: string;
  display_name: string | null;
  description_zh: string | null;
  category: string;
  license: string;
  enabled: boolean;
}

export interface CustomSkill extends Skill {
  content: string;
}
