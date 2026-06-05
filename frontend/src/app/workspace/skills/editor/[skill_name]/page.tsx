"use client";

import { useParams } from "next/navigation";

import { SkillEditorWorkspace } from "@/components/workspace/skills/editor/skill-editor-workspace";

export default function SkillEditorPage() {
  const { skill_name: skillName } = useParams<{ skill_name: string }>();

  return <SkillEditorWorkspace skillName={decodeURIComponent(skillName)} />;
}
