"use client";

import { MessageSquarePlus, SearchIcon } from "lucide-react";
import Image from "next/image";
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

function FridayBrand() {
  return (
    <>
      <Image
        src="/images/friday-icon.png"
        alt=""
        width={32}
        height={32}
        className="size-8 rounded-lg object-cover shadow-sm"
      />
      <span
        className="flex items-center gap-px text-[17px] leading-none font-black tracking-[0.02em] text-gray-950 uppercase"
        aria-label="Friday"
      >
        <span>Frid</span>
        <span className="relative ml-0.5 inline-block h-[13px] w-[14px] translate-y-px">
          <span className="absolute bottom-0 left-0 h-[3px] w-[13px] rounded-full bg-gray-950" />
          <span className="absolute bottom-[1px] left-[1px] h-[3px] w-[12px] origin-left rotate-[-58deg] rounded-full bg-gray-950" />
          <span className="absolute right-0 bottom-[1px] h-[3px] w-[12px] origin-right rotate-[58deg] rounded-full bg-gray-950" />
        </span>
        <span>y</span>
      </span>
    </>
  );
}

export function WorkspaceHeader({ className }: { className?: string }) {
  const { t } = useI18n();
  const pathname = usePathname();
  return (
    <>
      <div
        className={cn(
          "group/workspace-header flex h-10 flex-col justify-center",
          className,
        )}
      >
        <div className="flex items-center justify-between gap-2">
          {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? (
            <Link href="/" className="ml-1 flex items-center gap-2">
              <FridayBrand />
            </Link>
          ) : (
            <div className="ml-1 flex cursor-default items-center gap-2">
              <FridayBrand />
            </div>
          )}
        </div>
      </div>
      <SidebarMenu className="gap-1 px-3 pb-1">
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
