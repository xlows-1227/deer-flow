"use client";

import { ChevronRightIcon, Loader2, Settings2, Upload } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

interface SkillQuickActionsProps {
  showDraftBadge?: boolean;
  isCompleting?: boolean;
  canComplete?: boolean;
  onSettingsClick: () => void;
  onCompleteClick: () => void;
}

function ActionRow({
  icon,
  label,
  trailing,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  trailing?: React.ReactNode;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex h-12 w-full items-center gap-2 px-3 text-left transition-colors",
        onClick && !disabled ? "hover:bg-gray-50" : "cursor-default",
        disabled && "cursor-not-allowed opacity-60",
      )}
      disabled={disabled}
      onClick={onClick}
    >
      <div className="flex size-6 shrink-0 items-center justify-center text-gray-500">
        {icon}
      </div>
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-gray-900">
        {label}
      </span>
      {trailing}
      <ChevronRightIcon className="size-4 shrink-0 text-gray-400" />
    </button>
  );
}

export function SkillQuickActions({
  showDraftBadge,
  isCompleting,
  canComplete,
  onSettingsClick,
  onCompleteClick,
}: SkillQuickActionsProps) {
  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
      <ActionRow
        icon={<Settings2 className="size-4" />}
        label="设置"
        onClick={onSettingsClick}
      />
      <Separator />
      <ActionRow
        icon={<Upload className="size-4" />}
        label="发布"
        disabled={!canComplete || isCompleting}
        onClick={canComplete ? onCompleteClick : undefined}
        trailing={
          isCompleting ? (
            <Loader2 className="size-4 animate-spin text-gray-400" />
          ) : showDraftBadge ? (
            <Badge className="border-transparent bg-amber-50 px-2 py-0.5 text-[12px] font-normal text-amber-500 hover:bg-amber-50">
              未发布变更
            </Badge>
          ) : null
        }
      />
    </div>
  );
}
