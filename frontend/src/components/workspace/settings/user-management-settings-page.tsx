"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

export function UserManagementSettingsPage() {
  const { t } = useI18n();
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [resetPasswordValue, setResetPasswordValue] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");
  const [message, setMessage] = useState<string>("");
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [actionLoading, setActionLoading] = useState<boolean>(false);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [listRes, configRes] = await Promise.all([
        fetch("/api/admin/users"),
        fetch("/api/admin/users/reset-password-config"),
      ]);
      if (!listRes.ok || !configRes.ok) {
        throw new Error("Failed to load");
      }
      const listBody = (await listRes.json()) as { users: AdminUserItem[] };
      const configBody = (await configRes.json()) as { value: string };
      setUsers(listBody.users ?? []);
      setResetPasswordValue(configBody.value ?? "");
    } catch {
      setError(t.settings.userManagement.loadError);
    } finally {
      setLoading(false);
    }
  }, [t.settings.userManagement.loadError]);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers]);

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
        const detail =
          (body && (body.detail || body.message)) ||
          t.settings.userManagement.operationFailed;
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

        {loading ? (
          <p className="text-muted-foreground text-sm">
            {t.common.loading}...
          </p>
        ) : users.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            {t.settings.userManagement.empty}
          </p>
        ) : (
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
