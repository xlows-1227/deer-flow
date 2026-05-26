"use client";

import { MessageSquarePlus, SearchIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export function WorkspaceHeader({ className }: { className?: string }) {
  const { t } = useI18n();
  const pathname = usePathname();
  return (
    <>
      <div
        className={cn(
          "group/workspace-header flex h-12 flex-col justify-center",
          className,
        )}
      >
        <div className="flex items-center justify-between gap-2">
          {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? (
            <Link href="/" className="ml-1 flex items-center gap-2">
              <span className="flex size-8 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white">
                WA
              </span>
              <span className="text-sm font-semibold text-gray-900">
                Work-Agent
              </span>
            </Link>
          ) : (
            <div className="ml-1 flex cursor-default items-center gap-2">
              <span className="flex size-8 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white">
                WA
              </span>
              <span className="text-sm font-semibold text-gray-900">
                Work-Agent
              </span>
            </div>
          )}
        </div>
      </div>
      <SidebarMenu className="px-3 pb-3">
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname === "/workspace/chats/new"}
            asChild
            className="h-9 rounded-lg border border-gray-300 bg-gray-100 text-gray-900 hover:bg-gray-200 data-[active=true]:bg-gray-200 data-[active=true]:text-gray-900"
          >
            <Link href="/workspace/chats/new">
              <MessageSquarePlus size={16} />
              <span>{t.sidebar.newChat}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname === "/workspace/chats"}
            asChild
            className="h-9 rounded-lg text-gray-600 hover:bg-gray-200 data-[active=true]:bg-gray-200 data-[active=true]:text-gray-900"
          >
            <Link href="/workspace/chats">
              <SearchIcon size={16} />
              <span>对话搜索</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}
