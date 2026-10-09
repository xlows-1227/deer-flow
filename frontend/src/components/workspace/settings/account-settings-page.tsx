"use client";

import { LogOutIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";

import { SettingsSection } from "./settings-section";

export function AccountSettingsPage() {
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // SAM account inline editing
  const [samCurrent, setSamCurrent] = useState<string | null | undefined>(
    user?.oauth_id,
  );
  const [samEditing, setSamEditing] = useState(false);
  const [samInput, setSamInput] = useState("");
  const [samLoading, setSamLoading] = useState(false);
  const [samMessage, setSamMessage] = useState("");
  const [samError, setSamError] = useState("");

  const startSamEdit = () => {
    setSamInput(samCurrent ?? "");
    setSamError("");
    setSamMessage("");
    setSamEditing(true);
  };

  const cancelSamEdit = () => {
    setSamEditing(false);
    setSamInput("");
    setSamError("");
    setSamMessage("");
  };

  const handleSaveSam = async () => {
    setSamError("");
    setSamMessage("");
    setSamLoading(true);
    try {
      const res = await fetch("/api/v1/auth/me/oauth-id", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        body: JSON.stringify({ oauth_id: samInput.trim() || null }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
        const detail = (data && (data.detail?.message || data.detail || data.message)) || t.settings.account.networkError;
        setSamError(typeof detail === "string" ? detail : t.settings.account.networkError);
        return;
      }

      const body = await res.json().catch(() => ({}));
      setSamCurrent(body.oauth_id ?? null);
      setSamEditing(false);
      setSamInput("");
      setSamMessage(t.settings.account.samAccountUpdated);
    } catch {
      setSamError(t.settings.account.networkError);
    } finally {
      setSamLoading(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setMessage("");

    if (newPassword !== confirmPassword) {
      setError(t.settings.account.passwordMismatch);
      return;
    }
    if (newPassword.length < 8) {
      setError(t.settings.account.passwordTooShort);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      setMessage(t.settings.account.passwordChangedSuccess);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch {
      setError(t.settings.account.networkError);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      <SettingsSection title={t.settings.account.profileTitle}>
        <div className="space-y-3">
          <div className="grid grid-cols-[max-content_max-content] items-center gap-4">
            <span className="text-muted-foreground text-sm">
              {t.settings.account.email}
            </span>
            <span className="text-sm font-medium">{user?.email ?? "—"}</span>
            <span className="text-muted-foreground text-sm">
              {t.settings.account.role}
            </span>
            <span className="text-sm font-medium capitalize">
              {user?.system_role ?? "—"}
            </span>
          </div>

          {/* SAM account row */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-muted-foreground text-sm">
              {t.settings.account.samAccount}
            </span>
            {samEditing ? (
              <>
                <Input
                  type="text"
                  value={samInput}
                  onChange={(e) => setSamInput(e.target.value)}
                  placeholder="sAMAccountName"
                  className="max-w-xs"
                  disabled={samLoading}
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void handleSaveSam();
                    } else if (e.key === "Escape") {
                      cancelSamEdit();
                    }
                  }}
                />
                <Button
                  type="button"
                  size="sm"
                  disabled={samLoading}
                  onClick={() => void handleSaveSam()}
                >
                  {samLoading ? t.settings.account.updating : t.common.save}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={samLoading}
                  onClick={cancelSamEdit}
                >
                  {t.common.cancel}
                </Button>
              </>
            ) : (
              <>
                <span className="text-sm font-medium">
                  {samCurrent ?? "—"}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={startSamEdit}
                >
                  {t.settings.account.editSamAccount}
                </Button>
              </>
            )}
          </div>
          {samError && <p className="text-sm text-red-500">{samError}</p>}
          {samMessage && <p className="text-sm text-green-500">{samMessage}</p>}
        </div>
      </SettingsSection>

      <SettingsSection
        title={t.settings.account.changePasswordTitle}
        description={t.settings.account.changePasswordDescription}
      >
        <form onSubmit={handleChangePassword} className="max-w-sm space-y-3">
          <Input
            type="password"
            placeholder={t.settings.account.currentPassword}
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
          />
          <Input
            type="password"
            placeholder={t.settings.account.newPassword}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            minLength={8}
          />
          <Input
            type="password"
            placeholder={t.settings.account.confirmNewPassword}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
          />
          {error && <p className="text-sm text-red-500">{error}</p>}
          {message && <p className="text-sm text-green-500">{message}</p>}
          <Button type="submit" variant="outline" size="sm" disabled={loading}>
            {loading
              ? t.settings.account.updating
              : t.settings.account.updatePassword}
          </Button>
        </form>
      </SettingsSection>

      <SettingsSection title="" description="">
        <Button
          variant="destructive"
          size="sm"
          onClick={logout}
          className="gap-2"
        >
          <LogOutIcon className="size-4" />
          {t.settings.account.signOut}
        </Button>
      </SettingsSection>
    </div>
  );
}
