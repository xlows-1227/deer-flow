"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  BotIcon,
  KeyRoundIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/core/i18n/hooks";
import {
  buildApiKeyUpdateValue,
  createCustomModel,
  deleteCustomModel,
  loadCustomModels,
  MASKED_API_KEY,
  updateCustomModel,
  type CustomModel,
  type ModelProvider,
} from "@/core/models/config-api";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

type ModelForm = {
  id: string | null;
  name: string;
  displayName: string;
  provider: ModelProvider;
  model: string;
  baseUrl: string;
  apiKey: string;
  hasStoredKey: boolean;
  enabled: boolean;
};

const DEFAULT_BASE_URLS: Record<ModelProvider, string> = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com",
};

const emptyForm: ModelForm = {
  id: null,
  name: "",
  displayName: "",
  provider: "openai",
  model: "",
  baseUrl: DEFAULT_BASE_URLS.openai,
  apiKey: "",
  hasStoredKey: false,
  enabled: true,
};

type ModelSettingsPageProps = {
  settingsOpen?: boolean;
  onFormOpenChange?: (open: boolean) => void;
};

function formFromModel(model: CustomModel): ModelForm {
  return {
    id: model.id,
    name: model.name,
    displayName: model.display_name ?? "",
    provider: model.provider,
    model: model.model,
    baseUrl: model.base_url ?? DEFAULT_BASE_URLS[model.provider],
    apiKey: model.has_api_key ? MASKED_API_KEY : "",
    hasStoredKey: model.has_api_key,
    enabled: model.enabled,
  };
}

export function ModelSettingsPage({
  settingsOpen = true,
  onFormOpenChange,
}: ModelSettingsPageProps) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [models, setModels] = useState<CustomModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState<ModelForm>(emptyForm);

  const refreshModels = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await loadCustomModels();
      setModels(rows);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.settings.models.loadFailed,
      );
    } finally {
      setLoading(false);
    }
  }, [t.settings.models.loadFailed]);

  useEffect(() => {
    if (settingsOpen) {
      void refreshModels();
    }
  }, [refreshModels, settingsOpen]);

  useEffect(() => {
    onFormOpenChange?.(dialogOpen);
  }, [dialogOpen, onFormOpenChange]);

  const openCreateDialog = () => {
    setForm(emptyForm);
    setDialogOpen(true);
  };

  const openEditDialog = (model: CustomModel) => {
    setForm(formFromModel(model));
    setDialogOpen(true);
  };

  const invalidateModelLists = async () => {
    await queryClient.invalidateQueries({ queryKey: ["models"] });
    await refreshModels();
  };

  const handleSave = async () => {
    const name = form.name.trim();
    const modelId = form.model.trim();
    if (!name || !modelId) {
      toast.error(t.settings.models.validationRequired);
      return;
    }

    setSaving(true);
    try {
      if (form.id) {
        const apiKey = buildApiKeyUpdateValue(form.apiKey, form.hasStoredKey);
        await updateCustomModel(form.id, {
          name,
          display_name: form.displayName.trim() || null,
          provider: form.provider,
          model: modelId,
          base_url: form.baseUrl.trim() || null,
          enabled: form.enabled,
          ...(apiKey !== undefined ? { api_key: apiKey } : {}),
        });
        toast.success(t.settings.models.updateSuccess);
      } else {
        await createCustomModel({
          name,
          display_name: form.displayName.trim() || null,
          provider: form.provider,
          model: modelId,
          base_url: form.baseUrl.trim() || null,
          api_key: form.apiKey.trim() || null,
          enabled: form.enabled,
        });
        toast.success(t.settings.models.createSuccess);
      }
      setDialogOpen(false);
      await invalidateModelLists();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.settings.models.saveFailed,
      );
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (model: CustomModel) => {
    if (
      !window.confirm(
        t.settings.models.deleteConfirm.replace("{name}", model.name),
      )
    ) {
      return;
    }
    try {
      await deleteCustomModel(model.id);
      toast.success(t.settings.models.deleteSuccess);
      await invalidateModelLists();
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t.settings.models.deleteFailed,
      );
    }
  };

  const handleProviderChange = (provider: ModelProvider) => {
    setForm((current) => ({
      ...current,
      provider,
      baseUrl: DEFAULT_BASE_URLS[provider],
    }));
  };

  return (
    <SettingsSection
      title={t.settings.models.title}
      description={t.settings.models.description}
    >
      <div className="flex items-center justify-between gap-3">
        <p className="text-muted-foreground text-sm">
          {t.settings.models.hint}
        </p>
        <Button type="button" size="sm" onClick={openCreateDialog}>
          <PlusIcon className="size-4" />
          {t.settings.models.addModel}
        </Button>
      </div>

      {loading ? (
        <div className="text-muted-foreground flex items-center gap-2 py-8 text-sm">
          <Loader2Icon className="size-4 animate-spin" />
          {t.common.loading}
        </div>
      ) : models.length === 0 ? (
        <Card>
          <CardContent className="text-muted-foreground py-10 text-center text-sm">
            {t.settings.models.empty}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {models.map((model) => (
            <Card key={model.id}>
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <BotIcon className="size-4" />
                      {model.display_name ?? model.name}
                    </CardTitle>
                    <CardDescription>
                      {model.name} · {model.model}
                    </CardDescription>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={model.enabled ? "default" : "secondary"}>
                      {model.enabled
                        ? t.settings.models.enabled
                        : t.settings.models.disabled}
                    </Badge>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => openEditDialog(model)}
                    >
                      <PencilIcon className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => void handleDelete(model)}
                    >
                      <Trash2Icon className="size-4" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="grid gap-2 text-sm md:grid-cols-2">
                <div>
                  <span className="text-muted-foreground">
                    {t.settings.models.provider}:
                  </span>{" "}
                  {model.provider}
                </div>
                <div>
                  <span className="text-muted-foreground">
                    {t.settings.models.baseUrl}:
                  </span>{" "}
                  {model.base_url ?? "—"}
                </div>
                <div>
                  <span className="text-muted-foreground">
                    {t.settings.models.apiKey}:
                  </span>{" "}
                  {model.has_api_key
                    ? t.settings.models.apiKeySet.replace(
                        "{lastFour}",
                        model.api_key_last_four ?? "****",
                      )
                    : t.settings.models.apiKeyMissing}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {form.id
                ? t.settings.models.editModel
                : t.settings.models.addModel}
            </DialogTitle>
            <DialogDescription>
              {t.settings.models.formDescription}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4">
            <label className="grid gap-2 text-sm">
              <span>{t.settings.models.name}</span>
              <Input
                value={form.name}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
                placeholder={t.settings.models.namePlaceholder}
              />
            </label>

            <label className="grid gap-2 text-sm">
              <span>{t.settings.models.displayName}</span>
              <Input
                value={form.displayName}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    displayName: event.target.value,
                  }))
                }
                placeholder={t.settings.models.displayNamePlaceholder}
              />
            </label>

            <label className="grid gap-2 text-sm">
              <span>{t.settings.models.provider}</span>
              <Select
                value={form.provider}
                onValueChange={handleProviderChange}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="openai">OpenAI</SelectItem>
                  <SelectItem value="anthropic">Anthropic</SelectItem>
                </SelectContent>
              </Select>
            </label>

            <label className="grid gap-2 text-sm">
              <span>{t.settings.models.modelId}</span>
              <Input
                value={form.model}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    model: event.target.value,
                  }))
                }
                placeholder={t.settings.models.modelIdPlaceholder}
              />
            </label>

            <label className="grid gap-2 text-sm">
              <span>{t.settings.models.baseUrl}</span>
              <Input
                value={form.baseUrl}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    baseUrl: event.target.value,
                  }))
                }
                placeholder={DEFAULT_BASE_URLS[form.provider]}
              />
            </label>

            <label className="grid gap-2 text-sm">
              <span className="flex items-center gap-2">
                <KeyRoundIcon className="size-4" />
                {t.settings.models.apiKey}
              </span>
              <Input
                type="password"
                value={form.apiKey}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    apiKey: event.target.value,
                  }))
                }
                placeholder={
                  form.hasStoredKey
                    ? t.settings.models.apiKeyKeepExisting
                    : t.settings.models.apiKeyPlaceholder
                }
              />
            </label>

            <div className="flex items-center justify-between rounded-lg border px-3 py-2">
              <div>
                <p className="text-sm font-medium">
                  {t.settings.models.enabled}
                </p>
                <p className="text-muted-foreground text-xs">
                  {t.settings.models.enabledDescription}
                </p>
              </div>
              <Switch
                checked={form.enabled}
                onCheckedChange={(enabled) =>
                  setForm((current) => ({ ...current, enabled }))
                }
              />
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={saving}
            >
              {t.common.cancel}
            </Button>
            <Button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className={cn(saving && "pointer-events-none")}
            >
              {saving ? (
                <>
                  <Loader2Icon className="size-4 animate-spin" />
                  {t.common.loading}
                </>
              ) : (
                t.common.save
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsSection>
  );
}
