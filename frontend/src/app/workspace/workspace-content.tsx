import { Toaster } from "sonner";

import { QueryClientProvider } from "@/components/query-client-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { CommandPalette } from "@/components/workspace/command-palette";
import { OnboardingGuideProvider } from "@/components/workspace/onboarding/onboarding-guide-provider";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";

export async function WorkspaceContent({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <QueryClientProvider>
      <OnboardingGuideProvider>
        <SidebarProvider className="h-screen" defaultOpen={true}>
          <WorkspaceSidebar />
          <SidebarInset className="min-w-0">{children}</SidebarInset>
        </SidebarProvider>
      </OnboardingGuideProvider>
      <CommandPalette />
      <Toaster position="top-center" />
    </QueryClientProvider>
  );
}
