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
import { useAuth } from "@/core/auth/AuthProvider";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

type AdminUserItem = {
  id: string;
  email: string;
  system_role: "admin" | "user";
  oauth_id?: string | null;
};

type ConfirmAction =
  | { type: "reset"; user: AdminUserItem }
  | { type: "delete"; user: AdminUserItem }
  | null;

type SamEditState = { user: AdminUserItem } | null;
type SamConfirmState = { user: AdminUserItem; newOauthId: string } | null;

const PAGE_SIZE = 20;

export function UserManagementSettingsPage() {
  const { t } = useI18n();
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  // `searchInput` is the value bound to the input box; `search` is the
  // value actually sent to the backend. They only sync when the user
  // explicitly submits (click button or press Enter), so typing no longer
  // fires a request per keystroke.
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [resetPasswordValue, setResetPasswordValue] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [actionLoading, setActionLoading] = useState<boolean>(false);

  // SAM account editing flow: first dialog (form), then second dialog
  // (double confirmation) before the actual API call.
  const [samEdit, setSamEdit] = useState<SamEditState>(null);
  const [samInput, setSamInput] = useState("");
  const [samConfirm, setSamConfirm] = useState<SamConfirmState>(null);
  const [samLoading, setSamLoading] = useState<boolean>(false);

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

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Manual search submit: only fire a request when the user clicks the
  // search button or presses Enter inside the input box.
  const handleSearchSubmit = useCallback(() => {
    const next = searchInput.trim();
    if (next === search) {
      // Same query — if user edited it back to the original, still reset
      // to page 1 so they see the first page of the same filter.
      if (page !== 1) setPage(1);
      return;
    }
    setSearch(next);
    if (page !== 1) setPage(1);
  }, [searchInput, search, page]);

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

  // ── SAM account editing ────────────────────────────────────────────

  const openSamEdit = (user: AdminUserItem) => {
    setSamEdit({ user });
    setSamInput(user.oauth_id ?? "");
    setSamLoading(false);
    setError("");
    setMessage("");
  };

  const closeSamEdit = () => {
    setSamEdit(null);
    setSamInput("");
  };

  // First dialog "确认": stash the input and open the second confirmation.
  const submitSamEdit = () => {
    if (!samEdit) return;
    setSamConfirm({ user: samEdit.user, newOauthId: samInput.trim() });
    setSamEdit(null);
  };

  const closeSamConfirm = () => {
    setSamConfirm(null);
    setSamInput("");
  };

  // Second dialog "确认": actually call the API.
  const handleSamConfirm = async () => {
    if (!samConfirm) return;
    setSamLoading(true);
    setError("");
    setMessage("");
    try {
      const res = await fetch(
        `/api/admin/users/${samConfirm.user.id}/oauth-id`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            ...getCsrfHeaders(),
          },
          body: JSON.stringify({
            oauth_id: samConfirm.newOauthId || null,
          }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
        const detail = (body && (body.detail?.message || body.detail || body.message)) || t.settings.userManagement.operationFailed;
        setError(typeof detail === "string" ? detail : t.settings.userManagement.operationFailed);
        setSamConfirm(null);
        return;
      }
      setMessage(t.settings.userManagement.samUpdated);
      setSamConfirm(null);
      setSamInput("");
      await loadUsers();
    } catch {
      setError(t.settings.userManagement.operationFailed);
      setSamConfirm(null);
    } finally {
      setSamLoading(false);
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

        {/* Search bar — manual submit (button or Enter) to avoid a
            request per keystroke under heavy load. */}
        <div className="mb-3 flex items-center gap-2">
          <Input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleSearchSubmit();
              }
            }}
            placeholder={t.settings.userManagement.searchPlaceholder}
            className="max-w-xs"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleSearchSubmit}
          >
            {t.common.search}
          </Button>
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
                    <th className="px-3 py-2 text-left font-medium">
                      {t.settings.userManagement.samAccountColumn}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t.settings.userManagement.actionsColumn}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => {
                    const isSelf =
                      !!currentUser &&
                      user.id.toLowerCase() ===
                        currentUser.id.toLowerCase();
                    return (
                    <tr key={user.id} className="border-t">
                      <td className="px-3 py-2 align-middle">{user.email}</td>
                      <td className="px-3 py-2 align-middle text-muted-foreground">
                        {user.oauth_id ?? "—"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => openSamEdit(user)}
                          >
                            {t.settings.userManagement.editSamAccount}
                          </Button>
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
                            disabled={isSelf}
                            title={
                              isSelf
                                ? t.settings.userManagement.cannotDeleteSelf
                                : undefined
                            }
                            onClick={() =>
                              setConfirmAction({ type: "delete", user })
                            }
                          >
                            {t.settings.userManagement.deleteUser}
                          </Button>
                        </div>
                      </td>
                    </tr>
                    );
                  })}
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

      {/* Reset / Delete confirmation dialog */}
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

      {/* SAM account edit dialog — step 1: enter new value */}
      <Dialog
        open={samEdit !== null}
        onOpenChange={(open) => {
          if (!open) closeSamEdit();
        }}
      >
        <DialogContent className="sm:max-w-md" showCloseButton>
          <DialogHeader>
            <DialogTitle>
              {t.settings.userManagement.editSamTitle}
            </DialogTitle>
            <DialogDescription>
              {t.settings.userManagement.editSamDescription.replace(
                "{email}",
                samEdit?.user.email ?? "",
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-muted-foreground text-sm">
                {t.settings.userManagement.oldSamAccount}
              </label>
              <Input
                type="text"
                value={samEdit?.user.oauth_id ?? ""}
                disabled
                readOnly
              />
            </div>
            <div className="space-y-1">
              <label className="text-muted-foreground text-sm">
                {t.settings.userManagement.newSamAccount}
              </label>
              <Input
                type="text"
                value={samInput}
                onChange={(e) => setSamInput(e.target.value)}
                placeholder={t.settings.userManagement.newSamPlaceholder}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    submitSamEdit();
                  }
                }}
              />
            </div>
          </div>
          <DialogFooter className="mt-4 flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={closeSamEdit}
            >
              {t.settings.userManagement.cancel}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={submitSamEdit}
            >
              {t.settings.userManagement.confirm}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* SAM account edit dialog — step 2: double confirmation */}
      <Dialog
        open={samConfirm !== null}
        onOpenChange={(open) => {
          if (!samLoading && !open) {
            closeSamConfirm();
          }
        }}
      >
        <DialogContent className="sm:max-w-md" showCloseButton={!samLoading}>
          <DialogHeader>
            <DialogTitle>
              {t.settings.userManagement.editSamConfirmTitle}
            </DialogTitle>
            <DialogDescription>
              {t.settings.userManagement.editSamConfirmBody.replace(
                "{value}",
                samConfirm?.newOauthId || "(空 — 清除绑定)",
              )}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-4 flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={samLoading}
              onClick={closeSamConfirm}
            >
              {t.settings.userManagement.cancel}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={samLoading}
              onClick={() => void handleSamConfirm()}
            >
              {samLoading
                ? t.common.loading
                : t.settings.userManagement.confirm}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
