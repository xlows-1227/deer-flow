"use client";

import {
  BotIcon,
  BrainIcon,
  ClockIcon,
  FolderIcon,
  SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";

const itemClassName =
  "h-9 rounded-lg text-gray-700 hover:bg-gray-200 data-[active=true]:bg-gray-200 data-[active=true]:text-gray-900";

export function WorkspaceNavChatList() {
  const { t } = useI18n();
  const pathname = usePathname();

  return (
    <SidebarGroup className="border-b border-gray-100 px-3 pt-1 pb-2 group-data-[collapsible=icon]:px-2">
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/agents")}
            asChild
            tooltip={t.sidebar.agents}
            className={itemClassName}
          >
            <Link href="/workspace/agents" prefetch={false}>
              <BotIcon />
              <span>{t.sidebar.agents}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/skills")}
            asChild
            tooltip={t.sidebar.skills}
            className={itemClassName}
          >
            <Link href="/workspace/skills" prefetch={false}>
              <SparklesIcon />
              <span>{t.sidebar.skills}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={
              pathname.startsWith("/workspace/files") ||
              pathname.startsWith("/workspace/knowledge")
            }
            asChild
            tooltip={t.sidebar.files}
            className={itemClassName}
          >
            <Link href="/workspace/files" prefetch={false}>
              <FolderIcon />
              <span>{t.sidebar.files}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/memory")}
            asChild
            tooltip={t.sidebar.memory}
            className={itemClassName}
          >
            <Link href="/workspace/memory" prefetch={false}>
              <BrainIcon />
              <span>{t.sidebar.memory}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/scheduled-tasks")}
            asChild
            tooltip={t.sidebar.scheduledTasks}
            className={itemClassName}
          >
            <Link href="/workspace/scheduled-tasks" prefetch={false}>
              <ClockIcon />
              <span>{t.sidebar.scheduledTasks}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  );
}
