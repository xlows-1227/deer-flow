"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { FlickeringGrid } from "@/components/ui/flickering-grid";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/core/auth/AuthProvider";
import { encryptLoginPassword } from "@/core/auth/login-encryption";
import { parseAuthError } from "@/core/auth/types";

/**
 * Validate next parameter
 * Prevent open redirect attacks
 * Per RFC-001: Only allow relative paths starting with /
 */
function validateNextParam(next: string | null): string | null {
  if (!next) {
    return null;
  }

  // Need start with / (relative path)
  if (!next.startsWith("/")) {
    return null;
  }

  // Disallow protocol-relative URLs
  if (
    next.startsWith("//") ||
    next.startsWith("http://") ||
    next.startsWith("https://")
  ) {
    return null;
  }

  // Disallow URLs with different protocols (e.g., javascript:, data:, etc)
  if (next.includes(":") && !next.startsWith("/")) {
    return null;
  }

  // Valid relative path
  return next;
}

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated } = useAuth();
  const { theme, resolvedTheme } = useTheme();

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [ldapAccount, setLdapAccount] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [isLogin, setIsLogin] = useState(true);
  const [ldapEnabled, setLdapEnabled] = useState(false);
  const [authConfigLoaded, setAuthConfigLoaded] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  // Get next parameter for validated redirect
  const nextParam = searchParams.get("next");
  const redirectPath = validateNextParam(nextParam) ?? "/workspace";

  // Redirect if already authenticated (client-side, post-login)
  useEffect(() => {
    if (isAuthenticated) {
      router.push(redirectPath);
    }
  }, [isAuthenticated, redirectPath, router]);

  // Redirect to setup if the system has no users yet; also learn LDAP mode.
  useEffect(() => {
    let cancelled = false;

    void fetch("/api/v1/auth/setup-status")
      .then((r) => r.json())
      .then((data: { needs_setup?: boolean; ldap_enabled?: boolean }) => {
        if (cancelled) return;
        if (data.needs_setup) {
          router.push("/setup");
          return;
        }
        setLdapEnabled(Boolean(data.ldap_enabled));
        setAuthConfigLoaded(true);
      })
      .catch(() => {
        setAuthConfigLoaded(true);
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    setLoading(true);

    try {
      const endpoint = isLogin ? "/api/v1/auth/login" : "/api/v1/auth/register";

      const loginPassword = isLogin
        ? await encryptLoginPassword(password)
        : password;

      const body = isLogin
        ? JSON.stringify({ username: identifier, password: loginPassword })
        : ldapEnabled
          ? JSON.stringify({
              email: inviteEmail,
              ldap_account: ldapAccount,
              invite_code: inviteCode,
            })
          : JSON.stringify({
              email: inviteEmail,
              password,
              invite_code: inviteCode,
            });

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        credentials: "include", // Important: include HttpOnly cookie
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        if (authError.code === "user_not_registered") {
          const account = identifier.trim();
          setError(
            authError.message || `域账号 ${account} 尚未注册，请先完成注册。`,
          );
          if (ldapEnabled && account) {
            setLdapAccount(account);
            setIsLogin(false);
            setSuccess("");
          }
        } else {
          setError(authError.message);
        }
        return;
      }

      if (!isLogin) {
        const loginHandle = ldapEnabled ? ldapAccount : inviteEmail;
        setIsLogin(true);
        setIdentifier(loginHandle);
        setPassword("");
        setInviteEmail("");
        setLdapAccount("");
        setInviteCode("");
        setSuccess("注册成功，请登录。");
        return;
      }

      router.push(redirectPath);
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const actualTheme = theme === "system" ? resolvedTheme : theme;

  return (
    <div className="bg-background relative flex min-h-screen items-center justify-center overflow-x-hidden overflow-y-auto">
      <FlickeringGrid
        className="absolute inset-0 z-0 mask-[url(/images/deer.svg)] mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
        squareSize={4}
        gridGap={4}
        color={actualTheme === "dark" ? "white" : "black"}
        maxOpacity={0.3}
        flickerChance={0.25}
      />
      <div className="border-border/20 bg-background/5 w-full max-w-md space-y-6 rounded-3xl border p-8 backdrop-blur-sm">
        <div className="text-center">
          <h1 className="text-foreground font-serif text-3xl">Friday</h1>
          <p className="text-muted-foreground mt-2">
            {isLogin ? "Sign in to your account" : "Create a new account"}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-2">
          {isLogin ? (
            <div className="flex flex-col space-y-1">
              <label htmlFor="identifier" className="text-sm font-medium">
                用户名 / Username
              </label>
              <Input
                id="identifier"
                type="text"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                placeholder="sAMAccountName（不含 @yumchina.com）"
                required
                autoComplete="username"
              />
              <p className="text-muted-foreground text-xs">
                内网账户请使用 sAMAccountName 登录（不含
                @yumchina.com）；管理员账户使用完整邮箱。
              </p>
            </div>
          ) : ldapEnabled ? (
            <>
              <div className="flex flex-col space-y-1">
                <label htmlFor="ldapAccount" className="text-sm font-medium">
                  域账号 / Domain account
                </label>
                <Input
                  id="ldapAccount"
                  type="text"
                  value={ldapAccount}
                  onChange={(e) => setLdapAccount(e.target.value)}
                  placeholder="sAMAccountName（不含 @yumchina.com）"
                  required
                  autoComplete="username"
                />
              </div>
              <div className="flex flex-col space-y-1">
                <label htmlFor="inviteEmail" className="text-sm font-medium">
                  Email
                </label>
                <Input
                  id="inviteEmail"
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                  autoComplete="email"
                />
              </div>
            </>
          ) : (
            <div className="flex flex-col space-y-1">
              <label htmlFor="inviteEmail" className="text-sm font-medium">
                Email
              </label>
              <Input
                id="inviteEmail"
                type="email"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder="you@example.com"
                required
                autoComplete="email"
              />
            </div>
          )}
          {(isLogin || !ldapEnabled) && (
            <div className="flex flex-col space-y-1">
              <label htmlFor="password" className="text-sm font-medium">
                Password
              </label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="•••••••"
                required
                minLength={isLogin ? 6 : 8}
              />
            </div>
          )}
          {!isLogin && (
            <div className="flex flex-col space-y-1">
              <label htmlFor="inviteCode" className="text-sm font-medium">
                Invite Code
              </label>
              <Input
                id="inviteCode"
                type="text"
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                placeholder="Enter your invite code"
                required
              />
            </div>
          )}

          {success && <p className="text-sm text-green-600">{success}</p>}
          {error && <p className="text-sm text-red-500">{error}</p>}

          <Button
            type="submit"
            className="w-full"
            disabled={loading || !authConfigLoaded}
          >
            {loading
              ? "Please wait..."
              : !authConfigLoaded
                ? "Loading..."
                : isLogin
                  ? "Sign In"
                  : "Create Account"}
          </Button>
        </form>

        <div className="text-center text-sm">
          <button
            type="button"
            onClick={() => {
              setIsLogin(!isLogin);
              setError("");
              setSuccess("");
              setIdentifier("");
              setInviteEmail("");
              setLdapAccount("");
              setInviteCode("");
            }}
            className="text-blue-500 hover:underline"
          >
            {isLogin
              ? "Don't have an account? Sign up"
              : "Already have an account? Sign in"}
          </button>
        </div>
      </div>
    </div>
  );
}
