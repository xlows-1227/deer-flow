"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";

interface SecurityOverridePanelProps {
  isSubmitting: boolean;
  onConfirm: (reason?: string) => void | Promise<void>;
  onCancel: () => void;
}

/**
 * Shared "I understand the risk, use it anyway" confirmation panel.
 *
 * The upload page and the skill detail dialog both render this so the copy,
 * the checkbox gating and the submit behaviour stay identical. Visibility is
 * decided by the caller (the dialog only shows it to the owner or an admin).
 */
export function SecurityOverridePanel({
  isSubmitting,
  onConfirm,
  onCancel,
}: SecurityOverridePanelProps) {
  const [confirmed, setConfirmed] = useState(false);
  const [reason, setReason] = useState("");

  return (
    <div className="mt-3 rounded border border-red-200 bg-white p-3 text-xs text-gray-800">
      <p className="text-sm font-medium text-gray-900">确认放行</p>
      <p className="mt-1 leading-5 text-gray-600">
        放行仅对当前内容版本生效；之后任何编辑都会让该 Skill
        重新进入待安检状态。本次操作会记录操作人、时间与原因。
      </p>
      <label className="mt-3 flex items-start gap-2 text-gray-800">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        <span>我已确认该 Skill 来源可信，并了解其被标记的风险。</span>
      </label>
      <textarea
        className="mt-3 w-full rounded border border-gray-200 p-2 text-xs text-gray-800"
        rows={2}
        maxLength={500}
        placeholder="放行原因（可选，会写入审计记录）"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      />
      <div className="mt-3 flex gap-2">
        <Button
          type="button"
          size="sm"
          variant="destructive"
          disabled={!confirmed || isSubmitting}
          onClick={() => void onConfirm(reason.trim() || undefined)}
        >
          {isSubmitting ? "放行中" : "确认放行"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  );
}
