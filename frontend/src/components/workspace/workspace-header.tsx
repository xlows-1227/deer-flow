"use client";

import { MessageSquarePlus, SearchIcon } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";
import { env } from "@/env";
import { cn } from "@/lib/utils";

function ShisanxiangBrand() {
  return (
    <span
      className="inline-flex min-w-0 items-center gap-2"
      aria-label="十三香"
    >
      <Image
        src="/images/shisanxiang-icon.png"
        alt=""
        width={32}
        height={32}
        className="size-8 shrink-0 rounded-lg object-cover shadow-sm"
      />
      <span
        aria-hidden="true"
        className="flex items-center text-[17px] leading-none font-black tracking-[0.04em] text-gray-950 dark:text-gray-100"
      >
        <span>十三</span>
        <span className="text-[#c93229]">香</span>
      </span>
    </span>
  );
}

export function WorkspaceHeader({ className }: { className?: string }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const { open } = useSidebar();
  return (
    <>
      <div
        className={cn(
          "group/workspace-header flex h-10 flex-col justify-center group-data-[collapsible=icon]:items-center",
          className,
        )}
      >
        <div className="flex items-center justify-between gap-2">
          {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? (
            <Link
              href="/"
              prefetch={false}
              className="ml-1 flex min-w-0 items-center gap-2 group-data-[collapsible=icon]:hidden"
            >
              <ShisanxiangBrand />
            </Link>
          ) : (
            <div className="ml-1 flex min-w-0 cursor-default items-center gap-2 group-data-[collapsible=icon]:hidden">
              <ShisanxiangBrand />
            </div>
          )}
          <SidebarTrigger
            className="mr-1 size-7 shrink-0 opacity-70 group-data-[collapsible=icon]:mr-0 hover:opacity-100"
            aria-label={
              open ? t.sidebar.collapseSidebar : t.sidebar.expandSidebar
            }
            title={open ? t.sidebar.collapseSidebar : t.sidebar.expandSidebar}
          />
        </div>
      </div>
      <SidebarMenu className="gap-1 px-3 pb-1 group-data-[collapsible=icon]:px-2">
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname === "/workspace/chats/new"}
            asChild
            tooltip={t.sidebar.newChat}
            className="h-9 rounded-lg border border-gray-300 bg-gray-100 text-gray-900 hover:bg-gray-200 data-[active=true]:bg-gray-200 data-[active=true]:text-gray-900"
          >
            <Link href="/workspace/chats/new" prefetch={false}>
              <MessageSquarePlus size={16} />
              <span>{t.sidebar.newChat}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname === "/workspace/chats"}
            asChild
            tooltip={t.chats.searchChats}
            className="h-9 rounded-lg text-gray-600 hover:bg-gray-200 data-[active=true]:bg-gray-200 data-[active=true]:text-gray-900"
          >
            <Link href="/workspace/chats" prefetch={false}>
              <SearchIcon size={16} />
              <span>{t.chats.searchChats}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}
