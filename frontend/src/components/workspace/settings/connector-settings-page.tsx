"use client";

import {
  CircleCheckIcon,
  CircleOffIcon,
  DatabaseIcon,
  Loader2Icon,
  PencilIcon,
  PlusIcon,
  PowerIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  TestTubeDiagonalIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import {
  createConnector,
  deleteConnector,
  loadConnectors,
  loadConnectorTypes,
  setConnectorEnabled,
  testConnector,
  testConnectorConfig,
  testExistingConnectorConfig,
  updateConnector,
  type ConnectorConfigTestInput,
  type ConnectorCreateInput,
  type ConnectorInstance,
  type ConnectorTypeDefinition,
  type ConnectorUpdateInput,
} from "@/core/connectors";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

type ConnectorAuthMode = "env" | "inline";

type ConnectorForm = {
  id: string | null;
  name: string;
  displayName: string;
  type: string;
  host: string;
  port: string;
  database: string;
  ssl: boolean;
  authMode: ConnectorAuthMode;
  credentialRef: string;
  username: string;
  password: string;
  hasStoredPassword: boolean;
  maxRows: string;
  allowedSchemasText: string;
};

const emptyForm: ConnectorForm = {
  id: null,
  name: "",
  displayName: "",
  type: "mysql",
  host: "",
  port: "",
  database: "",
  ssl: false,
  authMode: "env",
  credentialRef: "",
  username: "",
  password: "",
  hasStoredPassword: false,
  maxRows: "10000",
  allowedSchemasText: "",
};

// Shown in the password field when an inline credential already exists.
// The real value is encrypted server-side and never round-tripped to the
// browser, so the dots are purely a visual hint that something is set.
const STORED_PASSWORD_PLACEHOLDER = "••••••••";

function stringValue(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function numberValue(value: unknown, fallback = "") {
  return typeof value === "number" ? String(value) : fallback;
}

function booleanValue(value: unknown, fallback = false) {
  return typeof value === "boolean" ? value : fallback;
}

function getPortKey(definition?: ConnectorTypeDefinition) {
  return definition?.config_schema?.query_port ? "query_port" : "port";
}

function getDefaultPort(definition?: ConnectorTypeDefinition) {
  const portKey = getPortKey(definition);
  const value = definition?.config_schema?.[portKey]?.default;
  return typeof value === "number" ? String(value) : "";
}

function schemaText(value: unknown) {
  if (!Array.isArray(value)) return "";
  return value.filter((item) => typeof item === "string").join("\n");
}

function parseSchemas(text: string) {
  const values = text
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
  return values.length ? values : undefined;
}

function inferAuthMode(
  credential?: ConnectorInstance["credential"],
): ConnectorAuthMode {
  if (credential?.provider === "inline") return "inline";
  return "env";
}

function connectorToForm(
  connector: ConnectorInstance,
  definition?: ConnectorTypeDefinition,
): ConnectorForm {
  const portKey = getPortKey(definition);
  const authMode = inferAuthMode(connector.credential);
  const credential = connector.credential;
  return {
    id: connector.id,
    name: connector.name,
    displayName: connector.display_name ?? "",
    type: connector.type,
    host: stringValue(connector.config.host),
    port: numberValue(connector.config[portKey], getDefaultPort(definition)),
    database: stringValue(connector.config.database),
    ssl: booleanValue(connector.config.ssl),
    authMode,
    credentialRef: authMode === "env" ? (credential?.ref ?? "") : "",
    username: authMode === "inline" ? (credential?.username ?? "") : "",
    // Backend never returns the plaintext password; we keep the field empty
    // and use ``hasStoredPassword`` (sourced from the server) plus a
    // placeholder to communicate "a password is set, leave blank to keep it".
    password: "",
    hasStoredPassword:
      authMode === "inline" && Boolean(credential?.has_password),
    maxRows: numberValue(connector.default_policy.max_rows, "10000"),
    allowedSchemasText: schemaText(connector.default_policy.allowed_schemas),
  };
}

function buildConfig(
  form: ConnectorForm,
  definition?: ConnectorTypeDefinition,
) {
  const portKey = getPortKey(definition);
  const port = Number.parseInt(form.port, 10);
  return {
    host: form.host.trim(),
    [portKey]: Number.isFinite(port)
      ? port
      : Number.parseInt(getDefaultPort(definition), 10),
    database: form.database.trim(),
    ssl: form.ssl,
  };
}

function buildPolicy(form: ConnectorForm) {
  const maxRows = Number.parseInt(form.maxRows, 10);
  return {
    mode: "read_only",
    allow_write: false,
    allow_ddl: false,
    max_rows: Number.isFinite(maxRows) ? maxRows : 10000,
    allowed_schemas: parseSchemas(form.allowedSchemasText),
  };
}

function buildCredentialFromForm(
  form: ConnectorForm,
):
  | { credential: { provider: "env"; ref: string } }
  | { credential: { provider: "inline"; username: string; password: string } }
  | null {
  if (form.authMode === "env") {
    if (!form.credentialRef.trim()) return null;
    return { credential: { provider: "env", ref: form.credentialRef.trim() } };
  }
  if (!form.username.trim() || !form.password) return null;
  return {
    credential: {
      provider: "inline",
      username: form.username.trim(),
      password: form.password,
    },
  };
}

function buildTestCredentialFromForm(
  form: ConnectorForm,
):
  | { provider: "env"; ref: string }
  | { provider: "inline"; username: string; password: string }
  | null {
  // Used by the "Test connection" button — accepts an inline password even
  // when editing (we keep what the user typed for the live ping).
  if (form.authMode === "env") {
    if (!form.credentialRef.trim()) return null;
    return { provider: "env", ref: form.credentialRef.trim() };
  }
  if (!form.username.trim() || !form.password) return null;
  return {
    provider: "inline",
    username: form.username.trim(),
    password: form.password,
  };
}

function formatDateTime(value?: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString();
}

function statusVariant(status: ConnectorInstance["status"]) {
  return status === "active" ? "default" : "outline";
}

function Field({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  // The label sits in its own grid row so its baseline lines up with the
  // sibling fields, even when the texts have different widths (e.g. "账号"
  // vs "密码"). The block div also keeps the control on a fresh row when
  // the Field is placed in a multi-column parent grid.
  return (
    <label className="grid grid-cols-1 items-start gap-2 text-sm font-medium">
      <div className="leading-5">{label}</div>
      <div className="min-w-0">{children}</div>
    </label>
  );
}

export function ConnectorSettingsPage() {
  const { t } = useI18n();
  const copy = t.settings.connectors;
  const [types, setTypes] = useState<ConnectorTypeDefinition[]>([]);
  const [connectors, setConnectors] = useState<ConnectorInstance[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testingForm, setTestingForm] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<ConnectorForm>(emptyForm);

  const typeByName = useMemo(
    () => new Map(types.map((item) => [item.type, item])),
    [types],
  );
  const activeCount = connectors.filter(
    (item) => item.status === "active",
  ).length;
  const databaseTypeCount = types.filter(
    (item) => item.category === "database",
  ).length;
  const selectedType = typeByName.get(form.type);

  async function refresh() {
    setLoading(true);
    try {
      const [nextTypes, nextConnectors] = await Promise.all([
        loadConnectorTypes(),
        loadConnectors(),
      ]);
      setTypes(nextTypes);
      setConnectors(nextConnectors);
      const firstType = nextTypes[0];
      if (firstType && !typeByName.has(form.type)) {
        setForm((current) => ({
          ...current,
          type: firstType.type,
          port: getDefaultPort(firstType),
        }));
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    // The initial load intentionally runs once; form changes should not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openCreate() {
    const firstType = types[0];
    setForm({
      ...emptyForm,
      type: firstType?.type ?? "mysql",
      port: getDefaultPort(firstType),
    });
    setFormOpen(true);
  }

  function openEdit(connector: ConnectorInstance) {
    setForm(connectorToForm(connector, typeByName.get(connector.type)));
    setFormOpen(true);
  }

  function updateForm(values: Partial<ConnectorForm>) {
    setForm((current) => ({ ...current, ...values }));
  }

  function updateType(type: string) {
    const definition = typeByName.get(type);
    updateForm({
      type,
      port: getDefaultPort(definition),
    });
  }

  function validateForm() {
    if (!form.name.trim()) throw new Error(copy.validationName);
    validateConnectionFields();
  }

  function validateConnectionFields() {
    if (!form.host.trim()) throw new Error(copy.validationHost);
    if (!form.database.trim()) throw new Error(copy.validationDatabase);
    if (form.authMode === "env") {
      if (!form.id && !form.credentialRef.trim()) {
        throw new Error(copy.validationCredentialRef);
      }
    } else {
      if (!form.username.trim()) throw new Error(copy.validationUsername);
      // On create, password is required. On edit, blank password = keep current.
      if (!form.id && !form.password) {
        throw new Error(copy.validationPassword);
      }
    }
  }

  function buildTestInput(): ConnectorConfigTestInput {
    const testCred = buildTestCredentialFromForm(form);
    return {
      config: buildConfig(form, selectedType),
      default_policy: buildPolicy(form),
      ...(testCred ? { credential: testCred } : {}),
    };
  }

  function buildCredentialForUpdate(
    form: ConnectorForm,
  ):
    | { provider: "env"; ref: string }
    | { provider: "inline"; username: string; password: string }
    | null {
    // Only emit a credential for PATCH when the user actually supplied one,
    // so a blank password (or blank env ref) leaves the stored secret alone.
    if (form.authMode === "env") {
      if (!form.credentialRef.trim()) return null;
      return { provider: "env", ref: form.credentialRef.trim() };
    }
    if (!form.password) return null;
    return {
      provider: "inline",
      username: form.username.trim(),
      password: form.password,
    };
  }

  async function handleSave() {
    try {
      validateForm();
      setSaving(true);
      const base = {
        name: form.name.trim(),
        display_name: form.displayName.trim() || null,
        config: buildConfig(form, selectedType),
        default_policy: buildPolicy(form),
      };
      if (form.id) {
        const credentialForUpdate = buildCredentialForUpdate(form);
        const input: ConnectorUpdateInput = {
          ...base,
          ...(credentialForUpdate ? { credential: credentialForUpdate } : {}),
        };
        await updateConnector(form.id, input);
        toast.success(copy.updated);
      } else {
        const created = buildCredentialFromForm(form);
        if (!created) {
          throw new Error(
            form.authMode === "env"
              ? copy.validationCredentialRef
              : copy.validationPassword,
          );
        }
        const input: ConnectorCreateInput = {
          ...base,
          type: form.type,
          credential: created.credential,
        };
        await createConnector(input);
        toast.success(copy.created);
      }
      setFormOpen(false);
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(connector: ConnectorInstance) {
    const nextEnabled = connector.status !== "active";
    try {
      await setConnectorEnabled(connector.id, nextEnabled);
      toast.success(nextEnabled ? copy.enabled : copy.disabled);
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleTest(connector: ConnectorInstance) {
    setTestingId(connector.id);
    try {
      const result = await testConnector(connector.id);
      if (result.status === "ok") {
        toast.success(
          result.latency_ms == null
            ? copy.testSuccess
            : copy.testSuccessWithLatency(result.latency_ms),
        );
      } else {
        toast.error(result.message ?? copy.testFailed);
      }
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setTestingId(null);
    }
  }

  async function handleTestForm() {
    try {
      validateConnectionFields();
      setTestingForm(true);
      const input = buildTestInput();
      const result = form.id
        ? await testExistingConnectorConfig(form.id, input)
        : await testConnectorConfig(form.type, input);
      if (result.status === "ok") {
        toast.success(
          result.latency_ms == null
            ? copy.testSuccess
            : copy.testSuccessWithLatency(result.latency_ms),
        );
      } else {
        toast.error(result.message ?? copy.testFailed);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setTestingForm(false);
    }
  }

  async function handleDelete(connector: ConnectorInstance) {
    if (
      !window.confirm(
        copy.deleteConfirm(connector.display_name ?? connector.name),
      )
    ) {
      return;
    }
    try {
      await deleteConnector(connector.id);
      toast.success(copy.deleted);
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <SettingsSection title={copy.title} description={copy.description}>
      <div className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-3">
          <Metric label={copy.total} value={connectors.length} />
          <Metric label={copy.active} value={activeCount} />
          <Metric label={copy.availableTypes} value={databaseTypeCount} />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            {types.map((type) => (
              <Badge key={type.type} variant="secondary">
                <DatabaseIcon className="size-3" />
                {type.display_name || type.type}
              </Badge>
            ))}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              <RefreshCwIcon className="size-4" />
              {copy.refresh}
            </Button>
            <Button
              size="sm"
              onClick={openCreate}
              disabled={types.length === 0}
            >
              <PlusIcon className="size-4" />
              {copy.add}
            </Button>
          </div>
        </div>

        {loading ? (
          <div className="text-muted-foreground flex h-40 items-center justify-center gap-2 text-sm">
            <Loader2Icon className="size-4 animate-spin" />
            {copy.loading}
          </div>
        ) : connectors.length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center">
            <DatabaseIcon className="text-muted-foreground mx-auto size-10" />
            <div className="mt-4 font-medium">{copy.emptyTitle}</div>
            <p className="text-muted-foreground mx-auto mt-1 max-w-md text-sm">
              {copy.emptyDescription}
            </p>
            <Button className="mt-5" onClick={openCreate}>
              <PlusIcon className="size-4" />
              {copy.add}
            </Button>
          </div>
        ) : (
          <div className="grid gap-3">
            {connectors.map((connector) => {
              const definition = typeByName.get(connector.type);
              const lastTested = formatDateTime(connector.last_tested_at);
              const lastUsed = formatDateTime(connector.last_used_at);
              return (
                <Card key={connector.id} className="rounded-lg">
                  <CardHeader className="gap-3">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0 space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <CardTitle className="truncate text-base">
                            {connector.display_name ?? connector.name}
                          </CardTitle>
                          <Badge variant="secondary">
                            <DatabaseIcon className="size-3" />
                            {definition?.display_name ?? connector.type}
                          </Badge>
                          <Badge variant="outline">
                            {connector.credential?.provider === "inline"
                              ? copy.authModeInline
                              : copy.authModeEnv}
                          </Badge>
                          <Badge variant={statusVariant(connector.status)}>
                            {connector.status === "active" ? (
                              <CircleCheckIcon className="size-3" />
                            ) : (
                              <CircleOffIcon className="size-3" />
                            )}
                            {connector.status === "active"
                              ? copy.statusActive
                              : copy.statusDisabled}
                          </Badge>
                        </div>
                        <CardDescription className="break-all">
                          {stringValue(connector.config.host)}
                          {stringValue(connector.config.database)
                            ? ` / ${stringValue(connector.config.database)}`
                            : ""}
                        </CardDescription>
                      </div>
                      <div className="flex shrink-0 flex-wrap items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={testingId === connector.id}
                          onClick={() => void handleTest(connector)}
                        >
                          {testingId === connector.id ? (
                            <Loader2Icon className="size-4 animate-spin" />
                          ) : (
                            <TestTubeDiagonalIcon className="size-4" />
                          )}
                          {copy.test}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => openEdit(connector)}
                          title={copy.edit}
                        >
                          <PencilIcon className="size-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => void handleToggle(connector)}
                          className={cn(
                            connector.status === "active"
                              ? // Active connector → "Disable" action. Red tint
                                // signals "this stops the thing" without
                                // flooding the whole card in a solid fill.
                                "text-red-500 hover:bg-red-500/10 hover:text-red-600 dark:text-red-400 dark:hover:bg-red-500/10 dark:hover:text-red-300"
                              : // Disabled connector → "Enable" action. Green
                                // tint reads as "go / ok / power on".
                                "text-emerald-600 hover:bg-emerald-500/10 hover:text-emerald-700 dark:text-emerald-400 dark:hover:bg-emerald-500/10 dark:hover:text-emerald-300",
                          )}
                          title={
                            connector.status === "active"
                              ? copy.disable
                              : copy.enable
                          }
                        >
                          <PowerIcon className="size-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => void handleDelete(connector)}
                          title={copy.delete}
                        >
                          <Trash2Icon className="size-4" />
                        </Button>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="grid gap-3 text-sm sm:grid-cols-3">
                    <ConnectorFact
                      label={copy.policy}
                      value={copy.maxRows(
                        Number(connector.default_policy.max_rows ?? 10000),
                      )}
                    />
                    <ConnectorFact
                      label={copy.lastTested}
                      value={lastTested ?? copy.never}
                    />
                    <ConnectorFact
                      label={copy.lastUsed}
                      value={lastUsed ?? copy.never}
                    />
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {form.id ? copy.editTitle : copy.createTitle}
            </DialogTitle>
            <DialogDescription>
              {form.id ? copy.editDescription : copy.createDescription}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={copy.name}>
                <Input
                  value={form.name}
                  onChange={(event) => updateForm({ name: event.target.value })}
                  placeholder="orders_prod"
                />
              </Field>
              <Field label={copy.displayName}>
                <Input
                  value={form.displayName}
                  onChange={(event) =>
                    updateForm({ displayName: event.target.value })
                  }
                  placeholder="Production Orders"
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <Field label={copy.type}>
                <Select
                  value={form.type}
                  onValueChange={updateType}
                  disabled={Boolean(form.id)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {types.map((type) => (
                      <SelectItem key={type.type} value={type.type}>
                        {type.display_name || type.type}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label={copy.host}>
                <Input
                  value={form.host}
                  onChange={(event) => updateForm({ host: event.target.value })}
                  placeholder="db.internal"
                />
              </Field>
              <Field label={copy.port}>
                <Input
                  value={form.port}
                  inputMode="numeric"
                  onChange={(event) => updateForm({ port: event.target.value })}
                  placeholder={getDefaultPort(selectedType)}
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_140px]">
              <Field label={copy.database}>
                <Input
                  value={form.database}
                  onChange={(event) =>
                    updateForm({ database: event.target.value })
                  }
                  placeholder="analytics"
                />
              </Field>
              <label className="flex items-end gap-3 pb-2 text-sm font-medium">
                <Switch
                  checked={form.ssl}
                  onCheckedChange={(ssl) => updateForm({ ssl })}
                />
                <span>{copy.ssl}</span>
              </label>
            </div>

            <div className="rounded-lg border p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                <ShieldCheckIcon className="text-muted-foreground size-4" />
                {copy.secretBoundary}
              </div>
              <Field label={copy.authMode}>
                <Select
                  value={form.authMode}
                  onValueChange={(value) =>
                    updateForm({ authMode: value as ConnectorAuthMode })
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="env">{copy.authModeEnv}</SelectItem>
                    <SelectItem value="inline">
                      {copy.authModeInline}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              {form.authMode === "env" ? (
                <Field label={copy.credentialRef}>
                  <Input
                    value={form.credentialRef}
                    onChange={(event) =>
                      updateForm({ credentialRef: event.target.value })
                    }
                    placeholder={
                      form.type === "starrocks"
                        ? "ADS_STARROCKS_URL"
                        : "PROD_MYSQL_URL"
                    }
                  />
                </Field>
              ) : (
                <div className="grid gap-4">
                  <Field label={copy.username}>
                    <Input
                      value={form.username}
                      autoComplete="off"
                      onChange={(event) =>
                        updateForm({ username: event.target.value })
                      }
                      placeholder="readonly_user"
                    />
                  </Field>
                  <Field label={copy.password}>
                    <Input
                      type="password"
                      value={form.password}
                      autoComplete="new-password"
                      onChange={(event) =>
                        updateForm({ password: event.target.value })
                      }
                      placeholder={
                        form.hasStoredPassword
                          ? STORED_PASSWORD_PLACEHOLDER
                          : copy.passwordPlaceholder
                      }
                    />
                  </Field>
                </div>
              )}
              {form.id ? (
                <p className="text-muted-foreground mt-2 text-xs">
                  {form.authMode === "env"
                    ? copy.credentialUpdateHint
                    : copy.credentialUpdateHintInline}
                </p>
              ) : null}
            </div>

            <div className="grid gap-4 sm:grid-cols-[180px_minmax(0,1fr)]">
              <Field label={copy.maxRowsLabel}>
                <Input
                  value={form.maxRows}
                  inputMode="numeric"
                  onChange={(event) =>
                    updateForm({ maxRows: event.target.value })
                  }
                />
              </Field>
              <Field label={copy.allowedSchemas}>
                <Textarea
                  className="min-h-20 resize-none"
                  value={form.allowedSchemasText}
                  onChange={(event) =>
                    updateForm({ allowedSchemasText: event.target.value })
                  }
                  placeholder={copy.allowedSchemasPlaceholder}
                />
              </Field>
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => void handleTestForm()}
              disabled={saving || testingForm}
            >
              {testingForm ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <TestTubeDiagonalIcon className="size-4" />
              )}
              {copy.testConnection}
            </Button>
            <Button
              variant="outline"
              onClick={() => setFormOpen(false)}
              disabled={saving || testingForm}
            >
              {copy.cancel}
            </Button>
            <Button
              onClick={() => void handleSave()}
              disabled={saving || testingForm}
            >
              {saving ? <Loader2Icon className="size-4 animate-spin" /> : null}
              {form.id ? copy.save : copy.create}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SettingsSection>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border p-4">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-muted-foreground mt-1 text-xs">{label}</div>
    </div>
  );
}

function ConnectorFact({ label, value }: { label: string; value: string }) {
  return (
    <div className={cn("bg-muted/60 rounded-md px-3 py-2")}>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-1 truncate font-medium">{value}</div>
    </div>
  );
}
