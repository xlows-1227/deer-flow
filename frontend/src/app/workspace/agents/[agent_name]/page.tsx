import { AgentStudio } from "@/components/workspace/published-agents/agent-studio";

export default async function AgentStudioPage({
  params,
}: {
  params: Promise<{ agent_name: string }>;
}) {
  const { agent_name: agentId } = await params;

  return <AgentStudio agentId={agentId} />;
}
