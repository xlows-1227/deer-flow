"use client";

import {
  ActivityIcon,
  BotIcon,
  FlaskConicalIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCwIcon,
  ShieldAlertIcon,
} from "lucide-react";
import { useState } from "react";
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
import {
  useAgentChannelAction,
  useAgentChannels,
  useCreateAgentChannel,
  useUpdateAgentChannel,
  type AgentChannel,
  type AgentChannelCredentials,
} from "@/core/published-agents";

const emptyCredentials: AgentChannelCredentials = {
  app_id: "",
  app_secret: "",
  verification_token: "",
  encrypt_key: "",
};

function optionalTrim(value: string | null | undefined): string | null {
  const cleaned = value?.trim();
  if (!cleaned) {
    return null;
  }
  return cleaned;
}

function ChannelHealthBadge({ channel }: { channel: AgentChannel }) {
  const { t } = useI18n();
  return (
    <Badge
      variant={
        channel.health === "healthy"
          ? "default"
          : channel.health === "unhealthy"
            ? "destructive"
            : "secondary"
      }
    >
      {t.publishedAgents.integrations.channelHealth(channel.health)}
    </Badge>
  );
}

function CredentialFields({
  prefix,
  value,
  onChange,
  rotation = false,
}: {
  prefix: string;
  value: AgentChannelCredentials;
  onChange: (value: AgentChannelCredentials) => void;
  rotation?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-1.5 sm:col-span-2">
        <label htmlFor={`${prefix}-app-id`} className="text-sm font-medium">
          {t.publishedAgents.integrations.appId}
        </label>
        <Input
          id={`${prefix}-app-id`}
          value={value.app_id}
          placeholder={rotation ? t.publishedAgents.integrations.keepAppId : ""}
          onChange={(event) =>
            onChange({ ...value, app_id: event.target.value })
          }
        />
      </div>
      <div className="space-y-1.5">
        <label htmlFor={`${prefix}-app-secret`} className="text-sm font-medium">
          {t.publishedAgents.integrations.appSecret}
        </label>
        <Input
          id={`${prefix}-app-secret`}
          type="password"
          value={value.app_secret}
          autoComplete="new-password"
          onChange={(event) =>
            onChange({ ...value, app_secret: event.target.value })
          }
        />
      </div>
      <div className="space-y-1.5">
        <label
          htmlFor={`${prefix}-verification-token`}
          className="text-sm font-medium"
        >
          {t.publishedAgents.integrations.verificationToken}
        </label>
        <Input
          id={`${prefix}-verification-token`}
          type="password"
          value={value.verification_token}
          autoComplete="new-password"
          onChange={(event) =>
            onChange({
              ...value,
              verification_token: event.target.value,
            })
          }
        />
      </div>
      <div className="space-y-1.5 sm:col-span-2">
        <label
          htmlFor={`${prefix}-encrypt-key`}
          className="text-sm font-medium"
        >
          {t.publishedAgents.integrations.encryptKey}
        </label>
        <Input
          id={`${prefix}-encrypt-key`}
          type="password"
          value={value.encrypt_key ?? ""}
          autoComplete="new-password"
          onChange={(event) =>
            onChange({ ...value, encrypt_key: event.target.value })
          }
        />
      </div>
    </div>
  );
}

export function FeishuBindingPanel({
  agentId,
  isPublished,
}: {
  agentId: string;
  isPublished: boolean;
}) {
  const { t } = useI18n();
  const { channels, isLoading, error } = useAgentChannels(
    isPublished ? agentId : null,
    {
      polling: isPublished,
    },
  );
  const createChannel = useCreateAgentChannel(agentId);
  const updateChannel = useUpdateAgentChannel(agentId);
  const channelAction = useAgentChannelAction(agentId);
  const [createOpen, setCreateOpen] = useState(false);
  const [credentials, setCredentials] =
    useState<AgentChannelCredentials>(emptyCredentials);
  const [rotateTarget, setRotateTarget] = useState<AgentChannel | null>(null);
  const [rotationCredentials, setRotationCredentials] =
    useState<AgentChannelCredentials>(emptyCredentials);

  function credentialsValid(
    value: AgentChannelCredentials,
    appIdRequired: boolean,
  ): boolean {
    return (
      (!appIdRequired || Boolean(value.app_id.trim())) &&
      Boolean(value.app_secret.trim()) &&
      Boolean(value.verification_token.trim())
    );
  }

  async function submitCreate() {
    try {
      await createChannel.mutateAsync({
        ...credentials,
        encrypt_key: optionalTrim(credentials.encrypt_key),
      });
      setCreateOpen(false);
      setCredentials(emptyCredentials);
      toast.success(t.publishedAgents.integrations.bindingCreated);
    } catch (createError) {
      toast.error(
        createError instanceof Error
          ? createError.message
          : String(createError),
      );
    }
  }

  function openRotation(channel: AgentChannel) {
    setRotateTarget(channel);
    setRotationCredentials({
      ...emptyCredentials,
      app_id: channel.app_id,
    });
  }

  async function submitRotation() {
    if (!rotateTarget) {
      return;
    }
    try {
      await updateChannel.mutateAsync({
        bindingId: rotateTarget.id,
        input: {
          app_id: optionalTrim(rotationCredentials.app_id) ?? undefined,
          app_secret: rotationCredentials.app_secret,
          verification_token: rotationCredentials.verification_token,
          encrypt_key: optionalTrim(rotationCredentials.encrypt_key),
        },
      });
      setRotateTarget(null);
      setRotationCredentials(emptyCredentials);
      toast.success(t.publishedAgents.integrations.credentialsRotated);
    } catch (rotationError) {
      toast.error(
        rotationError instanceof Error
          ? rotationError.message
          : String(rotationError),
      );
    }
  }

  async function runAction(
    channel: AgentChannel,
    action: "test" | "start" | "stop" | "restart",
  ) {
    try {
      const result = await channelAction.mutateAsync({
        bindingId: channel.id,
        action,
      });
      if (
        action === "test" &&
        "health" in result &&
        "detail" in result &&
        result.health === "unhealthy"
      ) {
        toast.error(result.detail);
      } else {
        toast.success(
          t.publishedAgents.integrations.channelActionSuccess(action),
        );
      }
    } catch (actionError) {
      toast.error(
        actionError instanceof Error
          ? actionError.message
          : String(actionError),
      );
    }
  }

  return (
    <>
      <Card className="shadow-none">
        <CardHeader>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-start gap-3">
              <div className="bg-muted flex size-9 shrink-0 items-center justify-center rounded-lg">
                <BotIcon className="size-4" />
              </div>
              <div>
                <CardTitle className="text-base">
                  {t.publishedAgents.integrations.feishuTitle}
                </CardTitle>
                <p className="text-muted-foreground mt-1 text-sm leading-6">
                  {t.publishedAgents.integrations.feishuDescription}
                </p>
              </div>
            </div>
            <Button disabled={!isPublished} onClick={() => setCreateOpen(true)}>
              <PlusIcon />
              {t.publishedAgents.integrations.addBinding}
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
          {error ? (
            <Alert variant="destructive">
              <ShieldAlertIcon />
              <AlertTitle>
                {t.publishedAgents.integrations.channelLoadError}
              </AlertTitle>
              <AlertDescription>
                {error instanceof Error ? error.message : String(error)}
              </AlertDescription>
            </Alert>
          ) : null}
          <div className="space-y-3">
            {isLoading ? (
              <p className="text-muted-foreground rounded-lg border p-5 text-sm">
                {t.publishedAgents.integrations.loading}
              </p>
            ) : channels.length === 0 ? (
              <p className="text-muted-foreground rounded-lg border p-5 text-sm">
                {t.publishedAgents.integrations.noBindings}
              </p>
            ) : (
              channels.map((channel) => {
                const deleting = channel.status === "deleting";
                return (
                <div key={channel.id} className="rounded-lg border p-4">
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-mono text-sm font-semibold">
                          {channel.app_id}
                        </p>
                        <ChannelHealthBadge channel={channel} />
                        <Badge variant="outline">
                          {t.publishedAgents.integrations.channelStatus(
                            channel.status,
                          )}
                        </Badge>
                      </div>
                      <p className="text-muted-foreground mt-2 flex items-center gap-1.5 text-xs">
                        <ActivityIcon className="size-3" />
                        {channel.health_detail ??
                          t.publishedAgents.integrations.noHealthDetail}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={channelAction.isPending || deleting}
                        onClick={() => void runAction(channel, "test")}
                      >
                        <FlaskConicalIcon />
                        {t.publishedAgents.integrations.testConnection}
                      </Button>
                      {channel.status === "active" ? (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={channelAction.isPending || deleting}
                          onClick={() => void runAction(channel, "stop")}
                        >
                          <PauseIcon />
                          {t.publishedAgents.integrations.stop}
                        </Button>
                      ) : (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={channelAction.isPending || deleting}
                          onClick={() => void runAction(channel, "start")}
                        >
                          <PlayIcon />
                          {t.publishedAgents.integrations.start}
                        </Button>
                      )}
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={channelAction.isPending || deleting}
                        onClick={() => void runAction(channel, "restart")}
                      >
                        <RefreshCwIcon />
                        {t.publishedAgents.integrations.restart}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={deleting}
                        onClick={() => openRotation(channel)}
                      >
                        <RotateCwIcon />
                        {t.publishedAgents.integrations.rotateCredentials}
                      </Button>
                    </div>
                  </div>
                </div>
                );
              })
            )}
          </div>
        </CardContent>
      </Card>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {t.publishedAgents.integrations.createBindingTitle}
            </DialogTitle>
            <DialogDescription>
              {t.publishedAgents.integrations.createBindingDescription}
            </DialogDescription>
          </DialogHeader>
          <CredentialFields
            prefix="create-feishu"
            value={credentials}
            onChange={setCredentials}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t.publishedAgents.publish.cancel}
            </Button>
            <Button
              disabled={
                !credentialsValid(credentials, true) || createChannel.isPending
              }
              onClick={() => void submitCreate()}
            >
              {t.publishedAgents.integrations.createBinding}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(rotateTarget)}
        onOpenChange={(open) => {
          if (!open) {
            setRotateTarget(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {t.publishedAgents.integrations.rotateCredentialsTitle}
            </DialogTitle>
            <DialogDescription>
              {t.publishedAgents.integrations.rotateCredentialsDescription}
            </DialogDescription>
          </DialogHeader>
          <CredentialFields
            prefix="rotate-feishu"
            value={rotationCredentials}
            onChange={setRotationCredentials}
            rotation
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRotateTarget(null)}>
              {t.publishedAgents.publish.cancel}
            </Button>
            <Button
              disabled={
                !credentialsValid(rotationCredentials, false) ||
                updateChannel.isPending
              }
              onClick={() => void submitRotation()}
            >
              {t.publishedAgents.integrations.confirmCredentialRotation}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
