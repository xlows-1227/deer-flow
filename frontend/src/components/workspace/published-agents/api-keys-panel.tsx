"use client";

import {
  CheckIcon,
  ClipboardIcon,
  Code2Icon,
  KeyRoundIcon,
  Loader2Icon,
  PlusIcon,
  ShieldAlertIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";
import {
  useAgentKeys,
  useCreateAgentKey,
  useDeleteAgentKey,
  type AgentApiKey,
  type RevealedAgentApiKey,
} from "@/core/published-agents";
import { copyTextToClipboard } from "@/lib/clipboard";

function formatTimestamp(value: string | null): string {
  if (!value) {
    return "—";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function KeyStatusBadge({ keyStatus }: { keyStatus: AgentApiKey["status"] }) {
  const { t } = useI18n();
  return (
    <Badge
      variant={
        keyStatus === "active"
          ? "default"
          : keyStatus === "revoked" || keyStatus === "expired"
            ? "destructive"
            : "secondary"
      }
    >
      {t.publishedAgents.integrations.keyStatus(keyStatus)}
    </Badge>
  );
}

function ApiExample({
  title,
  code,
  method,
  path,
}: {
  title: string;
  code: string;
  method: string;
  path: string;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  async function copy() {
    const ok = await copyTextToClipboard(code);
    if (!ok) {
      toast.error(t.publishedAgents.integrations.copyKeyUnavailable);
      return;
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_500);
  }

  return (
    <div className="overflow-hidden rounded-lg border">
      {/* Header bar: HTTP method badge + endpoint path + copy button */}
      <div className="flex items-center justify-between gap-2 border-b bg-muted/40 px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-bold tracking-wide text-blue-700 uppercase dark:bg-blue-900 dark:text-blue-300">
            {method}
          </span>
          <code
            className="truncate font-mono text-xs text-muted-foreground"
            title={path}
          >
            {path}
          </code>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={t.publishedAgents.integrations.copyExample}
          onClick={() => void copy()}
        >
          {copied ? <CheckIcon /> : <ClipboardIcon />}
        </Button>
      </div>
      {/* Code block: light theme matching the app */}
      <pre
        aria-label={title}
        className="max-h-72 overflow-auto bg-muted/20 p-4 font-mono text-xs leading-5 text-foreground"
      >
        {code}
      </pre>
    </div>
  );
}

export function ApiKeysPanel({
  agentId,
  isPublished,
}: {
  agentId: string;
  isPublished: boolean;
}) {
  const { t } = useI18n();
  const { keys, isLoading } = useAgentKeys(isPublished ? agentId : null);
  const createKey = useCreateAgentKey(agentId);
  const deleteKey = useDeleteAgentKey(agentId);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [revealed, setRevealed] = useState<RevealedAgentApiKey | null>(null);
  const [sessionSecrets, setSessionSecrets] = useState<Record<string, string>>(
    {},
  );
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AgentApiKey | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [conversationId, setConversationId] = useState("");
  const [resolvedApiKey, setResolvedApiKey] = useState("");
  const [creating, setCreating] = useState(false);
  const [copiedConversationId, setCopiedConversationId] = useState(false);
  const [activeExample, setActiveExample] = useState<
    "create" | "sync" | "stream" | "async"
  >("create");

  const conversationsUrl = `/api/v1/agents/${agentId}/conversations`;
  const runsUrl = `${conversationsUrl}/${conversationId || "$CONVERSATION_ID"}/runs`;
  // After a successful creation both placeholders resolve to real values.
  const bearerKey = conversationId ? resolvedApiKey : "$AGENT_API_KEY";
  // Same-origin after mount so the examples show this deployment's real URL;
  // placeholder keeps server and first client render identical.
  const [origin, setOrigin] = useState("");
  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);
  const baseUrl = origin || "https://deerflow.example.com";
  const examples = useMemo(
    () => ({
      create: `curl --request POST "${baseUrl}${conversationsUrl}" \\
  --header "Authorization: Bearer ${bearerKey}" \\
  --header "Content-Type: application/json" \\
  --data '{"metadata": {}}'`,
      sync: `curl --request POST "${baseUrl}${runsUrl}/wait" \\
  --header "Authorization: Bearer ${bearerKey}" \\
  --header "Content-Type: application/json" \\
  --data '{"message":"Summarize today’s incidents"}'`,
      stream: `curl --no-buffer --request POST "${baseUrl}${runsUrl}/stream" \\
  --header "Authorization: Bearer ${bearerKey}" \\
  --header "Content-Type: application/json" \\
  --data '{"message":"Stream an incident report"}'`,
      async: `curl --request POST "${baseUrl}${runsUrl}" \\
  --header "Authorization: Bearer ${bearerKey}" \\
  --header "Idempotency-Key: your-stable-request-id" \\
  --header "Content-Type: application/json" \\
  --data '{"message":"Start the analysis"}'`,
    }),
    [baseUrl, bearerKey, conversationsUrl, runsUrl],
  );

  async function submitCreateConversation() {
    const key = apiKeyInput.trim();
    if (!key || creating) {
      return;
    }
    setCreating(true);
    try {
      const res = await fetch(conversationsUrl, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${key}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ metadata: {} }),
      });
      const body = res.headers.get("content-type")?.includes("application/json")
        ? await res.json().catch(() => null)
        : null;
      if (!res.ok) {
        const message =
          (typeof body === "object" &&
            body !== null &&
            "error" in body &&
            typeof (body as { error?: { message?: unknown } }).error?.message ===
              "string" &&
            (body as { error: { message: string } }).error.message) ||
          `HTTP ${res.status}`;
        toast.error(
          `${t.publishedAgents.integrations.createConversationFailed}: ${message}`,
        );
        return;
      }
      const id =
        typeof body === "object" &&
        body !== null &&
        "conversation_id" in body &&
        typeof (body as { conversation_id?: unknown }).conversation_id ===
          "string"
          ? (body as { conversation_id: string }).conversation_id
          : "";
      if (!id) {
        toast.error(t.publishedAgents.integrations.createConversationFailed);
        return;
      }
      setConversationId(id);
      setResolvedApiKey(key);
      toast.success(t.publishedAgents.integrations.conversationCreated);
    } catch (error) {
      toast.error(
        `${t.publishedAgents.integrations.createConversationFailed}: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    } finally {
      setCreating(false);
    }
  }

  async function copyConversationId() {
    const ok = await copyTextToClipboard(conversationId);
    if (!ok) {
      toast.error(t.publishedAgents.integrations.copyKeyUnavailable);
      return;
    }
    setCopiedConversationId(true);
    window.setTimeout(() => setCopiedConversationId(false), 1_500);
  }

  function closeCreate() {
    setCreateOpen(false);
    setCreateName("");
  }

  async function submitCreate() {
    try {
      const result = await createKey.mutateAsync({
        name: createName,
        quota_overrides: {},
      });
      setCreateName("");
      setSessionSecrets((current) => ({
        ...current,
        [result.id]: result.api_key,
      }));
      setCopiedKeyId(null);
      setRevealed(result);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function copyApiKey(keyId: string, apiKey: string) {
    const ok = await copyTextToClipboard(apiKey);
    if (!ok) {
      toast.error(t.publishedAgents.integrations.copyKeyUnavailable);
      return;
    }
    setCopiedKeyId(keyId);
    window.setTimeout(
      () => setCopiedKeyId((current) => (current === keyId ? null : current)),
      1_500,
    );
  }

  async function confirmDelete() {
    if (!deleteTarget) {
      return;
    }
    try {
      await deleteKey.mutateAsync(deleteTarget.id);
      setSessionSecrets((current) => {
        const next = { ...current };
        delete next[deleteTarget.id];
        return next;
      });
      setCopiedKeyId((current) =>
        current === deleteTarget.id ? null : current,
      );
      setDeleteTarget(null);
      toast.success(t.publishedAgents.integrations.keyDeleted);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <>
      <Card className="shadow-none">
        <CardHeader>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-start gap-3">
              <div className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-lg">
                <KeyRoundIcon className="size-4" />
              </div>
              <div>
                <CardTitle className="text-base">
                  {t.publishedAgents.integrations.apiKeysTitle}
                </CardTitle>
                <p className="text-muted-foreground mt-1 text-sm leading-6">
                  {t.publishedAgents.integrations.apiKeysDescription}
                </p>
              </div>
            </div>
            <Button
              disabled={!isPublished}
              onClick={() => {
                setRevealed(null);
                setCreateOpen(true);
              }}
            >
              <PlusIcon />
              {t.publishedAgents.integrations.createApiKey}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {!isPublished ? (
            <Alert>
              <ShieldAlertIcon />
              <AlertTitle>
                {t.publishedAgents.integrations.publishFirstTitle}
              </AlertTitle>
              <AlertDescription>
                {t.publishedAgents.integrations.publishFirstDescription}
              </AlertDescription>
            </Alert>
          ) : null}
          <div className="divide-y rounded-lg border">
            {isLoading ? (
              <p className="text-muted-foreground p-5 text-sm">
                {t.publishedAgents.integrations.loading}
              </p>
            ) : keys.length === 0 ? (
              <p className="text-muted-foreground p-5 text-sm">
                {t.publishedAgents.integrations.noKeys}
              </p>
            ) : (
              keys.map((key) => {
                const sessionSecret = sessionSecrets[key.id];
                const copied = copiedKeyId === key.id;
                return (
                  <div
                    key={key.id}
                    className="flex flex-col gap-4 p-4 xl:flex-row xl:items-center xl:justify-between"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium">{key.name}</p>
                        <KeyStatusBadge keyStatus={key.status} />
                      </div>
                      <p className="text-muted-foreground mt-1 font-mono text-xs">
                        {key.key_prefix}••••{key.last_four}
                      </p>
                      <p className="text-muted-foreground mt-1 text-xs">
                        {t.publishedAgents.integrations.lastUsed}:{" "}
                        {formatTimestamp(key.last_used_at)}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <span
                        title={
                          sessionSecret
                            ? undefined
                            : t.publishedAgents.integrations.copyKeyUnavailable
                        }
                      >
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={!sessionSecret}
                          aria-label={t.publishedAgents.integrations.copyKeyFor(
                            key.name,
                          )}
                          onClick={() => {
                            if (sessionSecret) {
                              void copyApiKey(key.id, sessionSecret);
                            }
                          }}
                        >
                          {copied ? <CheckIcon /> : <ClipboardIcon />}
                          {copied
                            ? t.publishedAgents.integrations.keyCopied
                            : t.publishedAgents.integrations.copy}
                        </Button>
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={deleteKey.isPending}
                        aria-label={t.publishedAgents.integrations.deleteKey(
                          key.name,
                        )}
                        onClick={() => setDeleteTarget(key)}
                      >
                        <Trash2Icon />
                        {t.publishedAgents.integrations.delete}
                      </Button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </CardContent>
      </Card>

      <Card className="shadow-none">
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-lg">
              <Code2Icon className="size-4" />
            </div>
            <div>
              <CardTitle className="text-base">
                {t.publishedAgents.integrations.apiExamplesTitle}
              </CardTitle>
              <p className="text-muted-foreground mt-1 text-sm leading-6">
                {t.publishedAgents.integrations.apiExamplesDescription}
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={apiKeyInput}
              onChange={(event) => setApiKeyInput(event.target.value)}
              placeholder={
                t.publishedAgents.integrations.apiKeyPlaceholder
              }
              aria-label={t.publishedAgents.integrations.apiKeyPlaceholder}
              className="font-mono"
              type="text"
              autoComplete="off"
              spellCheck={false}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void submitCreateConversation();
                }
              }}
            />
            <Button
              className="shrink-0"
              disabled={!apiKeyInput.trim() || creating}
              onClick={() => void submitCreateConversation()}
            >
              {creating ? (
                <Loader2Icon className="animate-spin" />
              ) : (
                <PlusIcon />
              )}
              {t.publishedAgents.integrations.createConversation}
            </Button>
          </div>
          {conversationId ? (
            <div className="bg-muted/30 flex flex-wrap items-center gap-2 rounded-lg border p-3">
              <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                conversation_id
              </span>
              <code className="min-w-0 flex-1 font-mono text-xs break-all">
                {conversationId}
              </code>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                aria-label={
                  copiedConversationId
                    ? t.publishedAgents.integrations.keyCopied
                    : t.publishedAgents.integrations.copy
                }
                onClick={() => void copyConversationId()}
              >
                {copiedConversationId ? (
                  <CheckIcon />
                ) : (
                  <ClipboardIcon />
                )}
                {copiedConversationId
                  ? t.publishedAgents.integrations.keyCopied
                  : t.publishedAgents.integrations.copy}
              </Button>
            </div>
          ) : (
            <p className="text-muted-foreground text-xs leading-5">
              {t.publishedAgents.integrations.conversationHint}
            </p>
          )}
          {/* Example selector: pill buttons with HTTP method badges */}
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {(
                [
                  {
                    id: "create" as const,
                    label: t.publishedAgents.integrations.createConversation,
                    method: "POST",
                    path: conversationsUrl,
                  },
                  {
                    id: "sync" as const,
                    label: t.publishedAgents.integrations.sync,
                    method: "POST",
                    path: `${runsUrl}/wait`,
                  },
                  {
                    id: "stream" as const,
                    label: t.publishedAgents.integrations.sse,
                    method: "POST",
                    path: `${runsUrl}/stream`,
                  },
                  {
                    id: "async" as const,
                    label: t.publishedAgents.integrations.async,
                    method: "POST",
                    path: runsUrl,
                  },
                ]
              ).map((meta) => {
                const isActive = activeExample === meta.id;
                return (
                  <button
                    key={meta.id}
                    type="button"
                    onClick={() => setActiveExample(meta.id)}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                      isActive
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    <span
                      className={cn(
                        "rounded px-1 py-0.5 text-[10px] font-bold tracking-wide uppercase",
                        isActive
                          ? "bg-primary-foreground/20 text-primary-foreground"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {meta.method}
                    </span>
                    {meta.label}
                  </button>
                );
              })}
            </div>
            {(() => {
              const meta = {
                create: {
                  label: t.publishedAgents.integrations.createConversation,
                  path: conversationsUrl,
                },
                sync: {
                  label: t.publishedAgents.integrations.sync,
                  path: `${runsUrl}/wait`,
                },
                stream: {
                  label: t.publishedAgents.integrations.sse,
                  path: `${runsUrl}/stream`,
                },
                async: {
                  label: t.publishedAgents.integrations.async,
                  path: runsUrl,
                },
              }[activeExample];
              return (
                <ApiExample
                  title={meta.label}
                  code={examples[activeExample]}
                  method="POST"
                  path={meta.path}
                />
              );
            })()}
          </div>
        </CardContent>
      </Card>

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          if (open) {
            setCreateOpen(true);
          } else {
            closeCreate();
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          {revealed ? (
            <>
              <DialogHeader>
                <DialogTitle>
                  {t.publishedAgents.integrations.secretTitle}
                </DialogTitle>
                <DialogDescription>
                  {t.publishedAgents.integrations.secretDescription}
                </DialogDescription>
              </DialogHeader>
              <Alert className="border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
                <ShieldAlertIcon />
                <AlertTitle>
                  {t.publishedAgents.integrations.secretOnce}
                </AlertTitle>
                <AlertDescription>
                  {t.publishedAgents.integrations.secretWarning}
                </AlertDescription>
              </Alert>
              <div className="bg-muted/30 flex items-center gap-2 rounded-lg border p-3">
                <code className="min-w-0 flex-1 font-mono text-sm break-all">
                  {revealed.api_key}
                </code>
                <Button
                  variant="outline"
                  className="shrink-0"
                  aria-label={
                    copiedKeyId === revealed.id
                      ? t.publishedAgents.integrations.keyCopied
                      : t.publishedAgents.integrations.copyKey
                  }
                  onClick={() => void copyApiKey(revealed.id, revealed.api_key)}
                >
                  {copiedKeyId === revealed.id ? (
                    <CheckIcon />
                  ) : (
                    <ClipboardIcon />
                  )}
                  {copiedKeyId === revealed.id
                    ? t.publishedAgents.integrations.keyCopied
                    : t.publishedAgents.integrations.copyKey}
                </Button>
              </div>
              <DialogFooter>
                <Button onClick={() => setCreateOpen(false)}>
                  {t.publishedAgents.integrations.storedKey}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>
                  {t.publishedAgents.integrations.createKeyTitle}
                </DialogTitle>
                <DialogDescription>
                  {t.publishedAgents.integrations.createKeyDescription}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-1.5">
                <label htmlFor="new-key-name" className="text-sm font-medium">
                  {t.publishedAgents.integrations.keyName}
                </label>
                <Input
                  id="new-key-name"
                  value={createName}
                  onChange={(event) => setCreateName(event.target.value)}
                  autoFocus
                />
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={closeCreate}>
                  {t.publishedAgents.publish.cancel}
                </Button>
                <Button
                  disabled={!createName.trim() || createKey.isPending}
                  onClick={() => void submitCreate()}
                >
                  {t.publishedAgents.integrations.createKey}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t.publishedAgents.integrations.deleteTitle}
            </DialogTitle>
            <DialogDescription>
              {t.publishedAgents.integrations.deleteDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              {t.publishedAgents.publish.cancel}
            </Button>
            <Button
              variant="destructive"
              disabled={deleteKey.isPending}
              onClick={() => void confirmDelete()}
            >
              {t.publishedAgents.integrations.confirmDelete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
