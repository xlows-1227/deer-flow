"use client";

import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
} from "@/components/ui/sidebar";

import { RecentChatList } from "./recent-chat-list";
import { WorkspaceHeader } from "./workspace-header";
import { WorkspaceNavChatList } from "./workspace-nav-chat-list";
import { WorkspaceNavMenu } from "./workspace-nav-menu";

export function WorkspaceSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  return (
    <>
      <Sidebar
        variant="sidebar"
        collapsible="none"
        className="border-r border-gray-200 bg-background"
        {...props}
      >
        <SidebarHeader className="border-b border-gray-200 bg-background px-0 py-1">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent className="gap-0 bg-background">
          <WorkspaceNavChatList />
          <RecentChatList />
        </SidebarContent>
        <SidebarFooter className="border-t border-gray-200 bg-background p-3">
          <WorkspaceNavMenu />
        </SidebarFooter>
      </Sidebar>
    </>
  );
}
