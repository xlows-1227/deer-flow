"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

type AdminUserItem = {
  id: string;
  email: string;
  system_role: "admin" | "user";
};

type ConfirmAction =
  | { type: "reset"; user: AdminUserItem }
  | { type: "delete"; user: AdminUserItem }
  | null;

const PAGE_SIZE = 20;

export function UserManagementSettingsPage() {
  const { t } = useI18n();
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [resetPasswordValue, setResetPasswordValue] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [actionLoading, setActionLoading] = useState<boolean>(false);

  // Debounced search: hold the latest input in a ref, fire after 300ms idle.
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleSearchChange = useCallback((value: string) => {
    setSearch(value);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setPage(1);
    }, 300);
  }, []);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (search.trim()) params.set("search", search.trim());
    try {
      const [listRes, configRes] = await Promise.all([
        fetch(`/api/admin/users?${params.toString()}`),
        fetch("/api/admin/users/reset-password-config"),
      ]);
      if (!listRes.ok || !configRes.ok) {
        throw new Error("Failed to load");
      }
      const listBody = (await listRes.json()) as { users: AdminUserItem[]; total: number };
      const configBody = (await configRes.json()) as { value: string };
      setUsers(listBody.users ?? []);
      setTotal(listBody.total ?? 0);
      setResetPasswordValue(configBody.value ?? "");
    } catch {
      setError(t.settings.userManagement.loadError);
    } finally {
      setLoading(false);
    }
  }, [page, search, t.settings.userManagement.loadError]);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers]);

  // Cleanup debounce timer on unmount
  useEffect(() => {
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleConfirm = async () => {
    if (!confirmAction) return;
    setActionLoading(true);
    setError("");
    setMessage("");
    const { type, user } = confirmAction;
    try {
      const url =
        type === "reset"
          ? `/api/admin/users/${user.id}/reset-password`
          : `/api/admin/users/${user.id}`;
      const res = await fetch(url, {
        method: type === "reset" ? "POST" : "DELETE",
        headers: getCsrfHeaders(),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
        const detail = (body && (body.detail || body.message)) || t.settings.userManagement.operationFailed;
        setError(typeof detail === "string" ? detail : t.settings.userManagement.operationFailed);
        return;
      }
      setMessage(
        type === "reset"
          ? t.settings.userManagement.resetSuccess
          : t.settings.userManagement.deleteSuccess,
      );
      setConfirmAction(null);
      await loadUsers();
    } catch {
      setError(t.settings.userManagement.operationFailed);
    } finally {
      setActionLoading(false);
    }
  };

  const renderConfirmBody = () => {
    if (!confirmAction) return null;
    if (confirmAction.type === "reset") {
      return t.settings.userManagement.resetConfirmBody.replace(
        "{value}",
        resetPasswordValue,
      );
    }
    return t.settings.userManagement.deleteConfirmBody.replace(
      "{email}",
      confirmAction.user.email,
    );
  };

  return (
    <div className="space-y-8">
      <SettingsSection
        title={t.settings.userManagement.title}
        description={t.settings.userManagement.description}
      >
        {error && <p className="mb-3 text-sm text-red-500">{error}</p>}
        {message && <p className="mb-3 text-sm text-green-500">{message}</p>}

        {/* Search bar */}
        <div className="mb-3 flex items-center gap-2">
          <Input
            type="text"
            value={search}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder={t.settings.userManagement.searchPlaceholder}
            className="max-w-xs"
          />
        </div>

        {loading ? (
          <p className="text-muted-foreground text-sm">
            {t.common.loading}...
          </p>
        ) : users.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            {search.trim() ? t.settings.userManagement.noResults : t.settings.userManagement.empty}
          </p>
        ) : (
          <>
            <div className="overflow-hidden rounded-lg border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">
                      {t.settings.userManagement.emailColumn}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t.settings.userManagement.actionsColumn}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id} className="border-t">
                      <td className="px-3 py-2 align-middle">{user.email}</td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              setConfirmAction({ type: "reset", user })
                            }
                          >
                            {t.settings.userManagement.resetPassword}
                          </Button>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="text-red-600 hover:bg-red-50 hover:text-red-700"
                            onClick={() =>
                              setConfirmAction({ type: "delete", user })
                            }
                          >
                            {t.settings.userManagement.deleteUser}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="mt-3 flex items-center justify-between">
              <span className="text-muted-foreground text-xs">
                {t.settings.userManagement.pageOf
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
                  {t.settings.userManagement.prevPage}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages || loading}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                >
                  {t.settings.userManagement.nextPage}
                </Button>
              </div>
            </div>
          </>
        )}
      </SettingsSection>

      <Dialog
        open={confirmAction !== null}
        onOpenChange={(open) => {
          if (!actionLoading && !open) {
            setConfirmAction(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md" showCloseButton={!actionLoading}>
          <DialogHeader>
            <DialogTitle>
              {confirmAction?.type === "reset"
                ? t.settings.userManagement.resetConfirmTitle
                : t.settings.userManagement.deleteConfirmTitle}
            </DialogTitle>
            <DialogDescription>{renderConfirmBody()}</DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-4 flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={actionLoading}
              onClick={() => setConfirmAction(null)}
            >
              {t.settings.userManagement.cancel}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={actionLoading}
              onClick={handleConfirm}
              className={
                confirmAction?.type === "delete"
                  ? "bg-red-600 text-white hover:bg-red-700"
                  : ""
              }
            >
              {t.settings.userManagement.confirm}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
