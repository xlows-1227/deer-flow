"use client";

import {
  CheckIcon,
  ClipboardIcon,
  Code2Icon,
  KeyRoundIcon,
  PlusIcon,
  ShieldAlertIcon,
  Trash2Icon,
} from "lucide-react";
import { useMemo, useState } from "react";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/core/i18n/hooks";
import { copyTextToClipboard } from "@/lib/clipboard";
import {
  useAgentKeys,
  useCreateAgentKey,
  useDeleteAgentKey,
  type AgentApiKey,
  type RevealedAgentApiKey,
} from "@/core/published-agents";

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

function ApiExample({ title, code }: { title: string; code: string }) {
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
    <div className="relative">
      <pre
        aria-label={title}
        className="max-h-72 overflow-auto rounded-lg border bg-zinc-950 p-4 pr-12 font-mono text-xs leading-5 text-zinc-100"
      >
        {code}
      </pre>
      <Button
        variant="ghost"
        size="icon-sm"
        className="absolute top-2 right-2 text-zinc-300 hover:bg-zinc-800 hover:text-white"
        aria-label={t.publishedAgents.integrations.copyExample}
        onClick={() => void copy()}
      >
        {copied ? <CheckIcon /> : <ClipboardIcon />}
      </Button>
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

  const apiBase = `/api/v1/agents/${agentId}/conversations/$CONVERSATION_ID/runs`;
  const examples = useMemo(
    () => ({
      sync: `curl --request POST "$DEER_FLOW_URL${apiBase}/wait" \\
  --header "Authorization: Bearer $AGENT_API_KEY" \\
  --header "Content-Type: application/json" \\
  --data '{"message":"Summarize today’s incidents"}'`,
      stream: `curl --no-buffer --request POST "$DEER_FLOW_URL${apiBase}/stream" \\
  --header "Authorization: Bearer $AGENT_API_KEY" \\
  --header "Content-Type: application/json" \\
  --data '{"message":"Stream an incident report"}'`,
      async: `curl --request POST "$DEER_FLOW_URL${apiBase}" \\
  --header "Authorization: Bearer $AGENT_API_KEY" \\
  --header "Idempotency-Key: your-stable-request-id" \\
  --header "Content-Type: application/json" \\
  --data '{"message":"Start the analysis"}'`,
    }),
    [apiBase],
  );

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
        <CardContent>
          <Tabs defaultValue="sync">
            <TabsList>
              <TabsTrigger value="sync">
                {t.publishedAgents.integrations.sync}
              </TabsTrigger>
              <TabsTrigger value="stream">
                {t.publishedAgents.integrations.sse}
              </TabsTrigger>
              <TabsTrigger value="async">
                {t.publishedAgents.integrations.async}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="sync">
              <ApiExample
                title={t.publishedAgents.integrations.sync}
                code={examples.sync}
              />
            </TabsContent>
            <TabsContent value="stream">
              <ApiExample
                title={t.publishedAgents.integrations.sse}
                code={examples.stream}
              />
            </TabsContent>
            <TabsContent value="async">
              <ApiExample
                title={t.publishedAgents.integrations.async}
                code={examples.async}
              />
            </TabsContent>
          </Tabs>
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
