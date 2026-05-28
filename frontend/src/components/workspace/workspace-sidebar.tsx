"use client";

import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
} from "@/components/ui/sidebar";

import { RecentChatList } from "./recent-chat-list";
import { ScheduledTaskRunList } from "./scheduled-task-run-list";
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
        className="bg-background border-r border-gray-200"
        {...props}
      >
        <SidebarHeader className="bg-background border-b border-gray-200 px-0 py-0.5">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent className="bg-background gap-0 overflow-hidden">
          <div className="shrink-0">
            <WorkspaceNavChatList />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <RecentChatList />
            <ScheduledTaskRunList />
          </div>
        </SidebarContent>
        <SidebarFooter className="bg-background border-t border-gray-200 px-2 py-1.5">
          <WorkspaceNavMenu />
        </SidebarFooter>
      </Sidebar>
    </>
  );
}
