"use client";

import { SettingsIcon } from "lucide-react";
import { useEffect, useState } from "react";

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsDialog } from "./settings";

function NavMenuButtonContent({
  t,
}: {
  t: ReturnType<typeof useI18n>["t"];
}) {
  return (
    <div className="text-muted-foreground flex w-full items-center gap-2 text-left text-sm">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-600">
        U
      </div>
      <span>{t.workspace.settingsAndMore}</span>
      <SettingsIcon className="text-muted-foreground ml-auto size-4" />
    </div>
  );
}

export function WorkspaceNavMenu() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsDefaultSection, setSettingsDefaultSection] = useState<
    "appearance" | "memory" | "tools" | "skills" | "notification"
  >("appearance");
  const [mounted, setMounted] = useState(false);
  const { t } = useI18n();

  useEffect(() => {
    setMounted(true);
  }, []);

  const handleOpenSettings = () => {
    setSettingsDefaultSection("appearance");
    setSettingsOpen(true);
  };

  return (
    <>
      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        defaultSection={settingsDefaultSection}
      />
      <SidebarMenu className="w-full">
        <SidebarMenuItem>
          {mounted ? (
            <SidebarMenuButton
              size="lg"
              className="rounded-lg text-gray-700 hover:bg-gray-200 data-[state=open]:bg-gray-200 data-[state=open]:text-gray-900"
              onClick={handleOpenSettings}
            >
              <NavMenuButtonContent t={t} />
            </SidebarMenuButton>
          ) : (
            <SidebarMenuButton size="lg" className="pointer-events-none">
              <NavMenuButtonContent t={t} />
            </SidebarMenuButton>
          )}
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}
