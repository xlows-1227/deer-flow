"use client";

import { useParams } from "next/navigation";

import { SkillAiCreateWorkspace } from "@/components/workspace/skills/ai-create/skill-ai-create-workspace";

export default function SkillAiCreateThreadPage() {
  const { thread_id: threadId } = useParams<{ thread_id: string }>();

  return (
    <SkillAiCreateWorkspace
      key={threadId}
      initialThreadId={threadId}
      initialIsNewThread={false}
    />
  );
}
