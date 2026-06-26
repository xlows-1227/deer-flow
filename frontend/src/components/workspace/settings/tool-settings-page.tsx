"use client";

import {
  CheckCircleIcon,
  ImageIcon,
  KeyRoundIcon,
  SaveIcon,
  XCircleIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { MCPManagementPage } from "@/app/workspace/mcp/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import {
  loadImageGenerationConfig,
  updateImageGenerationConfig,
  type ImageGenerationConfig,
  type ImageGenerationProviderConfig,
} from "@/core/tools";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

type ApiKeyDraft = Record<string, string>;

function nonEmpty(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;
  return trimmed;
}

function fallbackText(value: string | undefined, fallback: string): string {
  return value?.trim() ? value : fallback;
}

function updateProvider(
  config: ImageGenerationConfig,
  name: string,
  patch: Partial<ImageGenerationProviderConfig>,
): ImageGenerationConfig {
  const provider = config.providers[name];
  if (!provider) return config;
  return {
    ...config,
    providers: {
      ...config.providers,
      [name]: {
        ...provider,
        ...patch,
      },
    },
  };
}

function providerNames(config: ImageGenerationConfig | null) {
  if (!config) return [];
  return Object.keys(config.providers).sort((a, b) => {
    if (a === config.default_provider) return -1;
    if (b === config.default_provider) return 1;
    return a.localeCompare(b);
  });
}

function ProviderModelInput({
  provider,
  disabled,
  onChange,
}: {
  provider: ImageGenerationProviderConfig;
  disabled: boolean;
  onChange: (model: string) => void;
}) {
  const { t } = useI18n();
  const modelOptions = useMemo(() => {
    const options = new Set(provider.metadata?.models ?? []);
    const current = provider.model.trim();
    if (current) {
      options.add(current);
    }
    return Array.from(options);
  }, [provider.metadata?.models, provider.model]);

  const placeholder = fallbackText(
    provider.metadata?.default_model,
    t.settings.tools.imageGeneration.selectModel,
  );

  if (modelOptions.length === 0) {
    return (
      <Input
        value={provider.model}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    );
  }

  return (
    <Select
      value={provider.model ?? provider.metadata?.default_model ?? undefined}
      disabled={disabled}
      onValueChange={onChange}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {modelOptions.map((model) => (
          <SelectItem key={model} value={model}>
            {model}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function ProviderCard({
  name,
  provider,
  apiKeyDraft,
  disabled,
  onProviderChange,
  onApiKeyChange,
}: {
  name: string;
  provider: ImageGenerationProviderConfig;
  apiKeyDraft: string;
  disabled: boolean;
  onProviderChange: (patch: Partial<ImageGenerationProviderConfig>) => void;
  onApiKeyChange: (value: string) => void;
}) {
  const { t } = useI18n();
  const supportedParameters = provider.metadata?.supported_parameters ?? [];
  return (
    <Card className="rounded-lg">
      <CardHeader className="gap-3">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="truncate text-base">
                {provider.display_name || name}
              </CardTitle>
              <Badge variant={provider.enabled ? "default" : "outline"}>
                {provider.enabled ? (
                  <CheckCircleIcon className="h-3 w-3" />
                ) : (
                  <XCircleIcon className="h-3 w-3" />
                )}
                {provider.enabled
                  ? t.settings.tools.imageGeneration.enabled
                  : t.settings.tools.imageGeneration.disabled}
              </Badge>
              {provider.has_api_key && (
                <Badge variant="secondary">
                  <KeyRoundIcon className="h-3 w-3" />
                  {t.settings.tools.imageGeneration.keyConfigured}
                </Badge>
              )}
            </div>
            <CardDescription className="mt-2">
              {t.settings.tools.imageGeneration.adapter}: {provider.provider}
            </CardDescription>
          </div>
          <Switch
            checked={provider.enabled}
            disabled={disabled}
            onCheckedChange={(enabled) => onProviderChange({ enabled })}
          />
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">
              {t.settings.tools.imageGeneration.defaultModel}
            </span>
            <ProviderModelInput
              provider={provider}
              disabled={disabled}
              onChange={(model) => onProviderChange({ model })}
            />
          </label>
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">
              {t.settings.tools.imageGeneration.apiKey}
            </span>
            <Input
              type="password"
              value={apiKeyDraft}
              disabled={disabled}
              onChange={(event) => onApiKeyChange(event.target.value)}
              placeholder={
                provider.has_api_key
                  ? t.settings.tools.imageGeneration.keepExistingKey
                  : t.settings.tools.imageGeneration.enterApiKey
              }
            />
          </label>
        </div>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_9rem]">
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">
              {t.settings.tools.imageGeneration.baseUrl}
            </span>
            <Input
              value={provider.base_url}
              disabled={disabled}
              onChange={(event) =>
                onProviderChange({ base_url: event.target.value })
              }
              placeholder={fallbackText(
                provider.metadata?.default_base_url,
                "https://...",
              )}
            />
          </label>
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">
              {t.settings.tools.imageGeneration.timeoutSeconds}
            </span>
            <Input
              type="number"
              min={1}
              value={provider.timeout_seconds}
              disabled={disabled}
              onChange={(event) =>
                onProviderChange({
                  timeout_seconds: Number(event.target.value || 120),
                })
              }
            />
          </label>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {supportedParameters.map((parameter) => (
            <Badge key={parameter} variant="secondary">
              {parameter}
            </Badge>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ImageGenerationSettings() {
  const { t } = useI18n();
  const [config, setConfig] = useState<ImageGenerationConfig | null>(null);
  const [apiKeys, setApiKeys] = useState<ApiKeyDraft>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const names = useMemo(() => providerNames(config), [config]);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      setConfig(await loadImageGenerationConfig());
      setApiKeys({});
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function handleSave() {
    if (!config) return;
    setSaving(true);
    try {
      const updated = await updateImageGenerationConfig({
        enabled: config.enabled,
        default_provider: config.default_provider,
        output_subdir: config.output_subdir,
        providers: Object.fromEntries(
          Object.entries(config.providers).map(([name, provider]) => [
            name,
            {
              enabled: provider.enabled,
              provider: provider.provider,
              display_name: provider.display_name,
              api_key: nonEmpty(apiKeys[name]),
              base_url: provider.base_url,
              model: provider.model,
              timeout_seconds: provider.timeout_seconds,
              trust_env: provider.trust_env,
              params: provider.params ?? {},
            },
          ]),
        ),
      });
      setConfig(updated);
      setApiKeys({});
      toast.success(t.settings.tools.imageGeneration.saveSuccess);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  const img = t.settings.tools.imageGeneration;

  return (
    <SettingsSection
      title={
        <span className="inline-flex items-center gap-2">
          <ImageIcon className="h-5 w-5" />
          {img.title}
        </span>
      }
      description={img.description}
    >
      {loading ? (
        <div className="text-muted-foreground flex h-24 items-center text-sm">
          {img.loading}
        </div>
      ) : error && !config ? (
        <div className="grid gap-3 rounded-lg border p-4">
          <div className="font-medium">{img.loadFailed}</div>
          <div className="text-muted-foreground text-sm">{error}</div>
          <div>
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              {img.retry}
            </Button>
          </div>
        </div>
      ) : !config ? (
        <div className="text-muted-foreground flex h-24 items-center text-sm">
          {img.noConfig}
        </div>
      ) : (
        <div className="space-y-5">
          <div className="grid gap-4 rounded-lg border p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="font-medium">{img.enableTool}</div>
                <div className="text-muted-foreground mt-1 text-sm">
                  {img.enableToolDescription}
                </div>
              </div>
              <Switch
                checked={config.enabled}
                disabled={saving}
                onCheckedChange={(enabled) =>
                  setConfig((current) =>
                    current ? { ...current, enabled } : current,
                  )
                }
              />
            </div>
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <label className="grid gap-1.5 text-sm">
                <span className="font-medium">{img.defaultProvider}</span>
                <select
                  className="border-input bg-background h-9 rounded-md border px-3 text-sm"
                  value={config.default_provider ?? ""}
                  disabled={saving}
                  onChange={(event) =>
                    setConfig((current) =>
                      current
                        ? { ...current, default_provider: event.target.value }
                        : current,
                    )
                  }
                >
                  {names.map((name) => (
                    <option key={name} value={name}>
                      {fallbackText(config.providers[name]?.display_name, name)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1.5 text-sm">
                <span className="font-medium">{img.outputDir}</span>
                <Input
                  value={config.output_subdir}
                  disabled={saving}
                  onChange={(event) =>
                    setConfig((current) =>
                      current
                        ? { ...current, output_subdir: event.target.value }
                        : current,
                    )
                  }
                />
              </label>
            </div>
          </div>

          <div className="grid gap-4">
            {names.map((name) => {
              const provider = config.providers[name];
              if (!provider) return null;
              return (
                <ProviderCard
                  key={name}
                  name={name}
                  provider={provider}
                  apiKeyDraft={apiKeys[name] ?? ""}
                  disabled={saving}
                  onApiKeyChange={(value) =>
                    setApiKeys((current) => ({ ...current, [name]: value }))
                  }
                  onProviderChange={(patch) =>
                    setConfig((current) =>
                      current ? updateProvider(current, name, patch) : current,
                    )
                  }
                />
              );
            })}
          </div>

          <div className="flex justify-end">
            <Button onClick={() => void handleSave()} disabled={saving}>
              <SaveIcon className="h-4 w-4" />
              {saving ? img.saving : img.saveConfig}
            </Button>
          </div>
        </div>
      )}
    </SettingsSection>
  );
}

export function ToolSettingsPage() {
  return (
    <div className={cn("flex flex-col gap-8")}>
      <ImageGenerationSettings />
      <Separator />
      <MCPManagementPage embedded />
    </div>
  );
}
