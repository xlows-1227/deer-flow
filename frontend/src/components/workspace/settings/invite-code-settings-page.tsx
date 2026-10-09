"use client";

import { CopyIcon, CheckIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

type InviteCodeItem = {
  code: string;
  used: boolean;
  used_by_email: string | null;
  created_at: string | null;
  used_at: string | null;
};

type InviteCodesListResponse = {
  codes: InviteCodeItem[];
  total: number;
  page: number;
  page_size: number;
};

const PAGE_SIZE = 20;

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function InviteCodeSettingsPage() {
  const { t } = useI18n();
  const [codes, setCodes] = useState<InviteCodeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // Batch create form
  const [countInput, setCountInput] = useState("");
  const [creating, setCreating] = useState(false);
  const [copiedCode, setCopiedCode] = useState<string | null>(null);

  const loadCodes = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        `/api/admin/invite-codes?page=${page}&page_size=${PAGE_SIZE}`,
      );
      if (!res.ok) throw new Error("load failed");
      const body = (await res.json()) as InviteCodesListResponse;
      setCodes(body.codes ?? []);
      setTotal(body.total ?? 0);
    } catch {
      setError(t.settings.inviteCodes.loadError);
    } finally {
      setLoading(false);
    }
  }, [page, t.settings.inviteCodes.loadError]);

  useEffect(() => {
    void loadCodes();
  }, [loadCodes]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleBatchCreate = async () => {
    const count = parseInt(countInput, 10);
    if (!Number.isInteger(count) || count <= 0 || count > 500) {
      setError(t.settings.inviteCodes.createFailed);
      return;
    }
    setCreating(true);
    setError("");
    setMessage("");
    try {
      const res = await fetch("/api/admin/invite-codes/batch", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        body: JSON.stringify({ count }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
        const detail = (body && (body.detail || body.message)) || t.settings.inviteCodes.createFailed;
        setError(typeof detail === "string" ? detail : t.settings.inviteCodes.createFailed);
        return;
      }
      const body = (await res.json()) as { created: string[] };
      setMessage(
        t.settings.inviteCodes.createSuccess.replace(
          "{count}",
          String(body.created.length),
        ),
      );
      setCountInput("");
      setPage(1);
      await loadCodes();
    } catch {
      setError(t.settings.inviteCodes.createFailed);
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopiedCode(code);
      setTimeout(() => setCopiedCode(null), 2000);
    } catch {
      // clipboard not available
    }
  };

  return (
    <div className="space-y-8">
      <SettingsSection
        title={t.settings.inviteCodes.title}
        description={t.settings.inviteCodes.description}
      >
        {error && <p className="mb-3 text-sm text-red-500">{error}</p>}
        {message && <p className="mb-3 text-sm text-green-500">{message}</p>}

        {/* Batch create form */}
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="text-muted-foreground text-sm">
              {t.settings.inviteCodes.countLabel}
            </label>
            <Input
              type="number"
              min={1}
              max={500}
              step={1}
              value={countInput}
              onChange={(e) => setCountInput(e.target.value)}
              placeholder={t.settings.inviteCodes.countPlaceholder}
              className="w-48"
              disabled={creating}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleBatchCreate();
                }
              }}
            />
          </div>
          <Button
            type="button"
            size="sm"
            disabled={creating || !countInput}
            onClick={() => void handleBatchCreate()}
          >
            {creating
              ? t.settings.inviteCodes.generating
              : t.settings.inviteCodes.generate}
          </Button>
        </div>

        {/* Table */}
        {loading ? (
          <p className="text-muted-foreground text-sm">
            {t.common.loading}...
          </p>
        ) : codes.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            {t.settings.inviteCodes.empty}
          </p>
        ) : (
          <>
            <div className="overflow-hidden rounded-lg border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">
                      {t.settings.inviteCodes.columnCode}
                    </th>
                    <th className="px-3 py-2 text-left font-medium">
                      {t.settings.inviteCodes.columnUsed}
                    </th>
                    <th className="px-3 py-2 text-left font-medium">
                      {t.settings.inviteCodes.columnUsedBy}
                    </th>
                    <th className="px-3 py-2 text-left font-medium">
                      {t.settings.inviteCodes.columnCreatedAt}
                    </th>
                    <th className="px-3 py-2 text-left font-medium">
                      {t.settings.inviteCodes.columnUsedAt}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t.common.view}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {codes.map((item) => (
                    <tr key={item.code} className="border-t">
                      <td className="px-3 py-2 align-middle font-mono">
                        {item.code}
                      </td>
                      <td className="px-3 py-2 align-middle">
                        <span
                          className={
                            item.used
                              ? "text-muted-foreground"
                              : "font-medium text-green-600"
                          }
                        >
                          {item.used
                            ? t.settings.inviteCodes.used
                            : t.settings.inviteCodes.unused}
                        </span>
                      </td>
                      <td className="px-3 py-2 align-middle">
                        {item.used_by_email ?? "—"}
                      </td>
                      <td className="px-3 py-2 align-middle text-muted-foreground">
                        {formatDateTime(item.created_at)}
                      </td>
                      <td className="px-3 py-2 align-middle text-muted-foreground">
                        {formatDateTime(item.used_at)}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="gap-1"
                          onClick={() => void handleCopy(item.code)}
                        >
                          {copiedCode === item.code ? (
                            <>
                              <CheckIcon className="size-4" />
                              {t.settings.inviteCodes.copied}
                            </>
                          ) : (
                            <>
                              <CopyIcon className="size-4" />
                              {t.settings.inviteCodes.copy}
                            </>
                          )}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="mt-3 flex items-center justify-between">
              <span className="text-muted-foreground text-xs">
                {t.settings.inviteCodes.pageOf
                  .replace("{current}", String(page))
                  .replace("{total}", String(totalPages))
                  .replace("{count}", String(total))}
              </span>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={page <= 1 || loading}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  {t.settings.inviteCodes.prevPage}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages || loading}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                >
                  {t.settings.inviteCodes.nextPage}
                </Button>
              </div>
            </div>
          </>
        )}
      </SettingsSection>
    </div>
  );
}
