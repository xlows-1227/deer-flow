"use client";

import {
  ActivityIcon,
  BotIcon,
  BrainIcon,
  ClockIcon,
  DatabaseIcon,
  PlugIcon,
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
    <SidebarGroup className="border-b border-gray-100 px-3 py-3">
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/agents")}
            asChild
            className={itemClassName}
          >
            <Link href="/workspace/agents">
              <BotIcon />
              <span>{t.sidebar.agents}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/skills")}
            asChild
            className={itemClassName}
          >
            <Link href="/workspace/skills">
              <SparklesIcon />
              <span>Skill 管理</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/knowledge")}
            asChild
            className={itemClassName}
          >
            <Link href="/workspace/knowledge">
              <DatabaseIcon />
              <span>知识库</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/mcp")}
            asChild
            className={itemClassName}
          >
            <Link href="/workspace/mcp">
              <PlugIcon />
              <span>MCP</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/memory")}
            asChild
            className={itemClassName}
          >
            <Link href="/workspace/memory">
              <BrainIcon />
              <span>记忆</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/traces")}
            asChild
            className={itemClassName}
          >
            <Link href="/workspace/traces">
              <ActivityIcon />
              <span>日志链路</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/scheduled-tasks")}
            asChild
            className={itemClassName}
          >
            <Link href="/workspace/scheduled-tasks">
              <ClockIcon />
              <span>定时任务</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  );
}
