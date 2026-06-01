export interface Skill {
  name: string;
  description: string;
  category: string;
  license: string;
  enabled: boolean;
}

export interface CustomSkill extends Skill {
  content: string;
}
