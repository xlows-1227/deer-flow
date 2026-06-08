import { ChatProviders } from "@/app/workspace/chats/[thread_id]/providers";

export default function SkillEditorLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <ChatProviders>{children}</ChatProviders>;
}
