"use client";

import {
  ActivityIcon,
  ArrowLeftIcon,
  BotIcon,
  BoxesIcon,
  CableIcon,
  FileCode2Icon,
  FlaskConicalIcon,
  LayoutDashboardIcon,
  PlugZapIcon,
  RocketIcon,
  RotateCcwIcon,
  SaveIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useConnectors, useConnectorTypes } from "@/core/connectors/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import {
  DraftRevisionConflictError,
  useAgentDraftOptions,
  usePublishedAgent,
  useUpdateAgentDraft,
  type AgentConnectorGrant,
  type AgentDraft,
  type AgentDraftSkill,
} from "@/core/published-agents";
import {
  prepareAgentInstructions,
  type SoulPresetContentMap,
} from "@/core/published-agents/instructions";

import { ApiKeysPanel } from "./api-keys-panel";
import { ConnectorGrants } from "./connector-grants";
import { DraftSandbox } from "./draft-sandbox";
import { FeishuBindingPanel } from "./feishu-binding-panel";
import { InstructionEditor } from "./instruction-editor";
import { PublishPanel } from "./publish-panel";
import { QuotaPanel } from "./quota-panel";
import { SkillPicker } from "./skill-picker";
import { UsagePanel } from "./usage-panel";

interface DraftForm {
  agentMarkdown: string;
  soulMarkdown: string;
  modelName: string | null;
  skills: AgentDraftSkill[];
  connectorGrants: AgentConnectorGrant[];
  revision: number;
}

const emptyDraft: DraftForm = {
  agentMarkdown: "",
  soulMarkdown: "",
  modelName: null,
  skills: [],
  connectorGrants: [],
  revision: 1,
};

function toDraftForm(
  draft: AgentDraft,
  instructions: {
    agentMarkdown: string;
    soulMarkdown: string;
  },
): DraftForm {
  return {
    agentMarkdown: instructions.agentMarkdown,
    soulMarkdown: instructions.soulMarkdown,
    modelName: draft.model_name,
    skills: draft.skills,
    connectorGrants: draft.connector_grants,
    revision: draft.revision,
  };
}

function IdentityField({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  return (
    <div>
      <p className="text-muted-foreground text-[10px] font-semibold tracking-wide uppercase">
        {label}
      </p>
      <p className="mt-1.5 min-h-5 text-sm font-medium">{value ?? "—"}</p>
    </div>
  );
}

export function AgentStudio({ agentId }: { agentId: string }) {
  const { t } = useI18n();
  const router = useRouter();
  const {
    agent,
    isLoading,
    error,
    refetch: refetchAgent,
  } = usePublishedAgent(agentId);
  const { skills: skillOptions } = useAgentDraftOptions(agentId);
  const { connectors } = useConnectors();
  const { connectorTypes } = useConnectorTypes();
  const { models } = useModels();
  const updateDraft = useUpdateAgentDraft(agentId);
  const [form, setForm] = useState<DraftForm>(emptyDraft);
  const [dirty, setDirty] = useState(false);
  const [hydratedRevision, setHydratedRevision] = useState<number | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);

  const hydrate = useCallback(
    (draft: AgentDraft) => {
      const soulPresets = t.publishedAgents.studio.soulPresets;
      const soulPresetContents: SoulPresetContentMap = {
        professional: soulPresets.professional.content,
        warm: soulPresets.warm.content,
        concise: soulPresets.concise.content,
        coach: soulPresets.coach.content,
      };
      const instructions = prepareAgentInstructions({
        agentMarkdown: draft.agent_markdown,
        soulMarkdown: draft.soul_markdown,
        defaultAgentTemplate: t.publishedAgents.studio.agentMarkdownTemplate,
        soulPresetContents,
      });
      setForm(toDraftForm(draft, instructions));
      setHydratedRevision(draft.revision);
      setDirty(instructions.modified);
      setConflictMessage(null);
    },
    [
      t.publishedAgents.studio.agentMarkdownTemplate,
      t.publishedAgents.studio.soulPresets,
    ],
  );

  useEffect(() => {
    if (
      agent?.draft &&
      (hydratedRevision === null ||
        (!dirty && agent.draft.revision !== hydratedRevision))
    ) {
      hydrate(agent.draft);
    }
  }, [agent, dirty, hydrate, hydratedRevision]);

  function changeForm(update: Partial<DraftForm>) {
    setForm((current) => ({ ...current, ...update }));
    setDirty(true);
    setConflictMessage(null);
  }

  async function save() {
    try {
      const saved = await updateDraft.mutateAsync({
        revision: form.revision,
        agent_markdown: form.agentMarkdown,
        soul_markdown: form.soulMarkdown,
        model_name: form.modelName,
        skills: form.skills,
        connector_grants: form.connectorGrants,
      });
      hydrate(saved);
      toast.success(t.publishedAgents.studio.saved);
    } catch (saveError) {
      if (saveError instanceof DraftRevisionConflictError) {
        setConflictMessage(saveError.message);
        return;
      }
      toast.error(
        saveError instanceof Error ? saveError.message : String(saveError),
      );
    }
  }

  async function reloadDraft() {
    const result = await refetchAgent();
    if (result.data?.draft) {
      hydrate(result.data.draft);
    }
  }

  if (isLoading) {
    return (
      <div className="flex size-full flex-col p-6">
        <Skeleton className="h-20 w-full" />
        <div className="mt-6 grid flex-1 grid-cols-[220px_1fr] gap-6">
          <Skeleton className="h-64" />
          <Skeleton className="h-96" />
        </div>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="flex size-full items-center justify-center p-6">
        <Alert variant="destructive" className="max-w-xl">
          <TriangleAlertIcon />
          <AlertTitle>{t.publishedAgents.gallery.loadError}</AlertTitle>
          <AlertDescription>
            <p>{error instanceof Error ? error.message : String(error)}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void refetchAgent()}
            >
              {t.publishedAgents.gallery.retry}
            </Button>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="bg-muted/15 flex size-full min-h-0 flex-col">
      <header className="bg-background/95 shrink-0 border-b px-4 py-3 backdrop-blur md:px-6">
        <div className="mx-auto flex max-w-[1500px] items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t.publishedAgents.studio.back}
              onClick={() => router.push("/workspace/agents")}
            >
              <ArrowLeftIcon />
            </Button>
            <div className="bg-muted ring-border hidden size-9 shrink-0 items-center justify-center rounded-xl ring-1 sm:flex">
              <BotIcon className="size-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-sm font-semibold">
                  {agent.display_name}
                </h1>
                <Badge variant="outline" className="hidden font-mono sm:flex">
                  {t.publishedAgents.studio.draftVersion(form.revision)}
                </Badge>
              </div>
              <p className="text-muted-foreground truncate font-mono text-[11px]">
                {t.publishedAgents.studio.eyebrow} · /{agent.slug}
              </p>
            </div>
          </div>

          <Button
            disabled={!dirty || updateDraft.isPending}
            onClick={() => void save()}
          >
            <SaveIcon />
            {updateDraft.isPending
              ? t.publishedAgents.studio.saving
              : t.publishedAgents.studio.saveDraft}
          </Button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-4 py-5 md:px-6 md:py-6">
        <div className="mx-auto max-w-[1500px]">
          {conflictMessage ? (
            <Alert
              variant="destructive"
              className="mb-5 border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
            >
              <TriangleAlertIcon />
              <AlertTitle>{t.publishedAgents.studio.conflictTitle}</AlertTitle>
              <AlertDescription>
                <p>{conflictMessage}</p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void reloadDraft()}
                >
                  <RotateCcwIcon />
                  {t.publishedAgents.studio.reloadDraft}
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}

          <Tabs defaultValue="overview" orientation="vertical">
            <div className="grid items-start gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
              <div className="lg:sticky lg:top-0">
                <TabsList
                  variant="line"
                  className="bg-background grid h-auto w-full grid-cols-2 items-stretch gap-1 rounded-xl border p-2 sm:grid-cols-3 lg:flex lg:flex-col"
                >
                  <TabsTrigger value="overview">
                    <LayoutDashboardIcon />
                    {t.publishedAgents.studio.overviewTab}
                  </TabsTrigger>
                  <TabsTrigger value="instructions">
                    <FileCode2Icon />
                    {t.publishedAgents.studio.instructionsTab}
                  </TabsTrigger>
                  <TabsTrigger value="skills">
                    <BoxesIcon />
                    {t.publishedAgents.studio.skillsTab}
                  </TabsTrigger>
                  <TabsTrigger value="connectors">
                    <CableIcon />
                    {t.publishedAgents.studio.connectorsTab}
                  </TabsTrigger>
                  <TabsTrigger value="sandbox">
                    <FlaskConicalIcon />
                    {t.publishedAgents.studio.sandboxTab}
                  </TabsTrigger>
                  <TabsTrigger value="publish">
                    <RocketIcon />
                    {t.publishedAgents.studio.publishTab}
                  </TabsTrigger>
                  <TabsTrigger value="integrations">
                    <PlugZapIcon />
                    {t.publishedAgents.studio.integrationsTab}
                  </TabsTrigger>
                  <TabsTrigger value="operations">
                    <ActivityIcon />
                    {t.publishedAgents.studio.operationsTab}
                  </TabsTrigger>
                </TabsList>
              </div>

              <div className="min-w-0">
                <TabsContent value="overview">
                  <div className="space-y-5">
                    <div>
                      <h2 className="text-xl font-semibold">
                        {t.publishedAgents.studio.overviewTitle}
                      </h2>
                      <p className="text-muted-foreground mt-1 text-sm leading-6">
                        {t.publishedAgents.studio.overviewDescription}
                      </p>
                    </div>

                    <Card className="shadow-none">
                      <CardHeader>
                        <CardTitle className="text-base">
                          {t.publishedAgents.studio.stableIdentity}
                        </CardTitle>
                        <p className="text-muted-foreground text-sm leading-6">
                          {t.publishedAgents.studio.stableIdentityDescription}
                        </p>
                      </CardHeader>
                      <CardContent className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
                        <IdentityField
                          label={t.publishedAgents.studio.slug}
                          value={agent.slug}
                        />
                        <IdentityField
                          label={t.publishedAgents.studio.displayName}
                          value={agent.display_name}
                        />
                        <IdentityField
                          label={t.publishedAgents.studio.description}
                          value={agent.description}
                        />
                        <IdentityField
                          label={t.publishedAgents.studio.avatar}
                          value={
                            agent.avatar_ref ??
                            t.publishedAgents.studio.notConfigured
                          }
                        />
                      </CardContent>
                    </Card>

                    <Card className="shadow-none">
                      <CardContent className="max-w-xl pt-6">
                        <div className="space-y-2">
                          <label
                            htmlFor="published-agent-model"
                            className="text-sm font-medium"
                          >
                            {t.publishedAgents.studio.model}
                          </label>
                          <Select
                            value={form.modelName ?? "__default__"}
                            onValueChange={(value) =>
                              changeForm({
                                modelName:
                                  value === "__default__" ? null : value,
                              })
                            }
                          >
                            <SelectTrigger
                              id="published-agent-model"
                              className="w-full"
                            >
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__default__">
                                {t.publishedAgents.studio.inheritModel}
                              </SelectItem>
                              {models.map((model) => (
                                <SelectItem key={model.name} value={model.name}>
                                  {model.display_name ?? model.name}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                </TabsContent>

                <TabsContent value="instructions">
                  <div className="mb-5">
                    <h2 className="text-xl font-semibold">
                      {t.publishedAgents.studio.instructionsTitle}
                    </h2>
                    <p className="text-muted-foreground mt-1 text-sm leading-6">
                      {t.publishedAgents.studio.instructionsDescription}
                    </p>
                  </div>
                  <InstructionEditor
                    agentMarkdown={form.agentMarkdown}
                    soulMarkdown={form.soulMarkdown}
                    onAgentMarkdownChange={(agentMarkdown) =>
                      changeForm({ agentMarkdown })
                    }
                    onSoulMarkdownChange={(soulMarkdown) =>
                      changeForm({ soulMarkdown })
                    }
                  />
                </TabsContent>

                <TabsContent value="skills">
                  <div className="mb-5">
                    <h2 className="text-xl font-semibold">
                      {t.publishedAgents.studio.skillsTitle}
                    </h2>
                    <p className="text-muted-foreground mt-1 text-sm leading-6">
                      {t.publishedAgents.studio.skillsDescription}
                    </p>
                  </div>
                  <SkillPicker
                    skills={skillOptions}
                    selected={form.skills}
                    grants={form.connectorGrants}
                    onChange={(skills) => changeForm({ skills })}
                  />
                </TabsContent>

                <TabsContent value="connectors">
                  <div className="mb-5">
                    <h2 className="text-xl font-semibold">
                      {t.publishedAgents.studio.connectorsTitle}
                    </h2>
                    <p className="text-muted-foreground mt-1 text-sm leading-6">
                      {t.publishedAgents.studio.connectorsDescription}
                    </p>
                  </div>
                  <ConnectorGrants
                    connectors={connectors}
                    connectorTypes={connectorTypes}
                    grants={form.connectorGrants}
                    onChange={(connectorGrants) =>
                      changeForm({ connectorGrants })
                    }
                  />
                </TabsContent>

                <TabsContent value="sandbox">
                  <DraftSandbox agentId={agent.id} agentSlug={agent.slug} />
                </TabsContent>

                <TabsContent value="publish">
                  <PublishPanel
                    agent={agent}
                    draft={agent.draft}
                    hasUnsavedChanges={dirty}
                  />
                </TabsContent>

                <TabsContent value="integrations">
                  <div className="space-y-5">
                    <div>
                      <h2 className="text-xl font-semibold">
                        {t.publishedAgents.integrations.title}
                      </h2>
                      <p className="text-muted-foreground mt-1 text-sm leading-6">
                        {t.publishedAgents.integrations.description}
                      </p>
                    </div>
                    <ApiKeysPanel
                      agentId={agent.id}
                      isPublished={Boolean(agent.current_release_id)}
                    />
                    <FeishuBindingPanel
                      agentId={agent.id}
                      isPublished={Boolean(agent.current_release_id)}
                    />
                  </div>
                </TabsContent>

                <TabsContent value="operations">
                  <div className="space-y-5">
                    <div>
                      <h2 className="text-xl font-semibold">
                        {t.publishedAgents.ops.title}
                      </h2>
                      <p className="text-muted-foreground mt-1 text-sm leading-6">
                        {t.publishedAgents.ops.description}
                      </p>
                    </div>
                    <UsagePanel agentId={agent.id} />
                    <QuotaPanel
                      agentId={agent.id}
                      draft={agent.draft}
                      hasUnsavedChanges={dirty}
                    />
                  </div>
                </TabsContent>
              </div>
            </div>
          </Tabs>
        </div>
      </main>
    </div>
  );
}
