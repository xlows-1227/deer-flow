"use client";

import {
  CheckCircleIcon,
  ChevronDownIcon,
  DownloadIcon,
  Loader2Icon,
  PencilIcon,
  PlugIcon,
  PlusIcon,
  RefreshCwIcon,
  TerminalIcon,
  Trash2Icon,
  WrenchIcon,
  XCircleIcon,
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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  createMCPServer,
  deleteMCPServer,
  fetchMCPServerTools,
  loadMCPConfig,
  setMCPServerEnabled,
  updateMCPServer,
} from "@/core/mcp/api";
import type {
  MCPConfig,
  MCPServerConfig,
  MCPToolInfo,
} from "@/core/mcp/types";
import { cn } from "@/lib/utils";

type MCPForm = {
  originalName: string | null;
  name: string;
  description: string;
  type: string;
  enabled: boolean;
  command: string;
  argsText: string;
  url: string;
  envText: string;
  headersText: string;
};

const MCP_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_-]*$/;

type MCPToolCheck = {
  status: "idle" | "loading" | "success" | "error";
  tools: MCPToolInfo[];
  error: string | null;
  expanded: boolean;
};

const emptyToolCheck: MCPToolCheck = {
  status: "idle",
  tools: [],
  error: null,
  expanded: false,
};

const emptyForm: MCPForm = {
  originalName: null,
  name: "",
  description: "",
  type: "stdio",
  enabled: true,
  command: "",
  argsText: "[]",
  url: "",
  envText: "{}",
  headersText: "{}",
};

function parseJsonRecord(text: string, field: string): Record<string, string> {
  const parsed = JSON.parse(text) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${field} 必须是 JSON 对象`);
  }
  return parsed as Record<string, string>;
}

function parseJsonArray(text: string, field: string): string[] {
  const parsed = JSON.parse(text) as unknown;
  if (
    !Array.isArray(parsed) ||
    parsed.some((item) => typeof item !== "string")
  ) {
    throw new Error(`${field} 必须是字符串数组`);
  }
  return parsed;
}

function stringValue(value: unknown, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function stringArrayValue(value: unknown) {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value
    : [];
}

function stringRecordValue(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, string>;
}

function isEditable(server: MCPServerConfig) {
  return server.editable !== false && server.source !== "system";
}

function toForm(name: string, server: MCPServerConfig): MCPForm {
  return {
    originalName: name,
    name,
    description: stringValue(server.description),
    type: stringValue(server.type, "stdio"),
    enabled: Boolean(server.enabled),
    command: stringValue(server.command),
    argsText: JSON.stringify(stringArrayValue(server.args), null, 2),
    url: stringValue(server.url),
    envText: JSON.stringify(stringRecordValue(server.env), null, 2),
    headersText: JSON.stringify(stringRecordValue(server.headers), null, 2),
  };
}

function toCreatePayload(form: MCPForm) {
  return {
    name: form.name.trim(),
    enabled: form.enabled,
    description: form.description,
    type: form.type,
    command: form.command || undefined,
    args: parseJsonArray(form.argsText, "Args"),
    url: form.url || undefined,
    env: parseJsonRecord(form.envText, "Env"),
    headers: parseJsonRecord(form.headersText, "Headers"),
  };
}

function toUpdatePayload(form: MCPForm) {
  return {
    enabled: form.enabled,
    description: form.description,
    type: form.type,
    command: form.command || undefined,
    args: parseJsonArray(form.argsText, "Args"),
    url: form.url || undefined,
    env: parseJsonRecord(form.envText, "Env"),
    headers: parseJsonRecord(form.headersText, "Headers"),
  };
}

export function MCPManagementPage({
  embedded = false,
}: {
  embedded?: boolean;
}) {
  const { t } = useI18n();
  const [config, setConfig] = useState<MCPConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<MCPForm>(emptyForm);
  const [toolChecks, setToolChecks] = useState<Record<string, MCPToolCheck>>(
    {},
  );

  function getToolCheck(name: string): MCPToolCheck {
    return toolChecks[name] ?? emptyToolCheck;
  }

  function updateToolCheck(name: string, patch: Partial<MCPToolCheck>) {
    setToolChecks((current) => ({
      ...current,
      [name]: { ...(current[name] ?? emptyToolCheck), ...patch },
    }));
  }

  function toggleToolList(name: string) {
    updateToolCheck(name, { expanded: !getToolCheck(name).expanded });
  }

  async function handleRefreshTools(name: string) {
    updateToolCheck(name, { status: "loading", error: null });
    try {
      const result = await fetchMCPServerTools(name);
      updateToolCheck(name, {
        status: "success",
        tools: result.tools,
        error: null,
        expanded: true,
      });
      toast.success(t.settings.tools.mcp.connectSuccess(name), {
        description: t.settings.tools.mcp.connectSuccessDescription(
          result.tools.length,
        ),
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      updateToolCheck(name, {
        status: "error",
        tools: [],
        error: message,
        expanded: true,
      });
      toast.error(t.settings.tools.mcp.connectFailedTitle(name), {
        description: message,
      });
    }
  }

  const servers = useMemo(
    () => Object.entries(config?.mcp_servers ?? {}),
    [config],
  );
  const enabledCount = servers.filter(([, server]) => server.enabled).length;

  async function refresh() {
    setLoading(true);
    try {
      setConfig(await loadMCPConfig());
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  function patchServer(name: string, server: MCPServerConfig) {
    setConfig((current) =>
      current
        ? {
            mcp_servers: {
              ...current.mcp_servers,
              [name]: server,
            },
          }
        : current,
    );
  }

  function openCreate() {
    setForm(emptyForm);
    setFormOpen(true);
  }

  function openEdit(name: string, server: MCPServerConfig) {
    if (!isEditable(server)) return;
    setForm(toForm(name, server));
    setFormOpen(true);
  }

  async function handleSave() {
    const trimmedName = form.name.trim();
    if (!trimmedName) {
      toast.error(t.settings.tools.mcp.nameRequired);
      return;
    }
    if (trimmedName.length > 32 || !MCP_NAME_PATTERN.test(trimmedName)) {
      toast.error(t.settings.tools.mcp.invalidName);
      return;
    }

    setSaving(true);
    try {
      if (form.originalName) {
        const updated = await updateMCPServer(
          form.originalName,
          toUpdatePayload(form),
        );
        if (form.originalName !== trimmedName) {
          await refresh();
        } else {
          patchServer(trimmedName, { ...updated, name: trimmedName });
        }
      } else {
        const created = await createMCPServer(
          toCreatePayload({ ...form, name: trimmedName }),
        );
        patchServer(trimmedName, { ...created, name: trimmedName });
      }
      toast.success("MCP 配置已保存");
      setFormOpen(false);
      if (form.originalName && form.originalName !== trimmedName) {
        updateToolCheck(form.originalName, emptyToolCheck);
      }
      updateToolCheck(trimmedName, emptyToolCheck);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(name: string, server: MCPServerConfig) {
    if (!isEditable(server) || !window.confirm(`确定删除 MCP「${name}」吗？`))
      return;
    setSaving(true);
    try {
      await deleteMCPServer(name);
      setConfig((current) => {
        if (!current) return current;
        const nextServers = { ...current.mcp_servers };
        delete nextServers[name];
        return { mcp_servers: nextServers };
      });
      toast.success("MCP 已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(name: string, enabled: boolean) {
    setSaving(true);
    try {
      const updated = await setMCPServerEnabled(name, enabled);
      patchServer(name, updated);
      if (!enabled) {
        updateToolCheck(name, emptyToolCheck);
      }
      toast.success(enabled ? "MCP 已启用" : "MCP 已停用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  function handleExport() {
    const data = {
      mcpServers: Object.fromEntries(
        servers.map(([name, server]) => [name, server]),
      ),
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "mcp.json";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className={cn("flex flex-col", embedded ? "min-h-0" : "size-full")}>
      <header
        className={cn(
          "flex shrink-0 flex-wrap items-center justify-between gap-4 border-b",
          embedded ? "px-0 pb-4" : "px-6 py-4",
        )}
      >
        <div>
          <h1 className="text-xl font-semibold">{t.settings.tools.tabs.mcp}</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            {t.settings.tools.mcp.readOnlyHint}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleExport}>
            <DownloadIcon className="h-4 w-4" />
            导出
          </Button>
          <Button onClick={openCreate}>
            <PlusIcon className="h-4 w-4" />
            添加 MCP
          </Button>
        </div>
      </header>

      <main className={cn(embedded ? "pt-4" : "flex-1 overflow-y-auto p-6")}>
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
          <div className="grid gap-3 sm:grid-cols-3">
            <Card className="rounded-lg py-4">
              <CardContent className="px-4">
                <div className="text-2xl font-semibold">{servers.length}</div>
                <div className="text-muted-foreground text-xs">总服务器</div>
              </CardContent>
            </Card>
            <Card className="rounded-lg py-4">
              <CardContent className="px-4">
                <div className="text-2xl font-semibold">{enabledCount}</div>
                <div className="text-muted-foreground text-xs">已启用</div>
              </CardContent>
            </Card>
            <Card className="rounded-lg py-4">
              <CardContent className="px-4">
                <div className="text-2xl font-semibold">
                  {servers.length - enabledCount}
                </div>
                <div className="text-muted-foreground text-xs">已停用</div>
              </CardContent>
            </Card>
          </div>

          {loading ? (
            <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
              加载 MCP 配置中...
            </div>
          ) : servers.length === 0 ? (
            <Card className="rounded-lg p-10 text-center">
              <PlugIcon className="text-muted-foreground mx-auto h-10 w-10" />
              <p className="mt-4 font-medium">暂无 MCP 服务器</p>
              <p className="text-muted-foreground mt-1 text-sm">
                添加 MCP 后，Agent 就可以调用更多外部工具。
              </p>
              <Button className="mt-5" onClick={openCreate}>
                <PlusIcon className="h-4 w-4" />
                添加 MCP
              </Button>
            </Card>
          ) : (
            <div className="grid gap-4">
              {servers.map(([name, server]) => {
                const toolCheck = getToolCheck(name);
                return (
                  <Card key={name} className="rounded-lg">
                  <CardHeader className="gap-3">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <CardTitle className="truncate">{name}</CardTitle>
                          <Badge variant="outline">
                            {server.source === "system"
                              ? t.settings.tools.mcp.systemBadge
                              : t.settings.tools.mcp.userBadge}
                          </Badge>
                          <Badge variant="secondary">
                            <TerminalIcon className="h-3 w-3" />
                            {stringValue(server.type, "stdio")}
                          </Badge>
                          <Badge
                            variant={server.enabled ? "default" : "outline"}
                          >
                            {server.enabled ? (
                              <CheckCircleIcon className="h-3 w-3" />
                            ) : (
                              <XCircleIcon className="h-3 w-3" />
                            )}
                            {server.enabled ? "启用" : "停用"}
                          </Badge>
                          {server.enabled &&
                          toolCheck.status === "loading" ? (
                            <Badge variant="secondary">
                              <Loader2Icon className="h-3 w-3 animate-spin" />
                              {t.settings.tools.mcp.connecting}
                            </Badge>
                          ) : null}
                          {server.enabled && toolCheck.status === "success" ? (
                            <Badge className="border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                              <CheckCircleIcon className="h-3 w-3" />
                              {t.settings.tools.mcp.connectedCount(
                                toolCheck.tools.length,
                              )}
                            </Badge>
                          ) : null}
                          {server.enabled && toolCheck.status === "error" ? (
                            <Badge
                              variant="outline"
                              className="border-destructive/40 bg-destructive/10 text-destructive"
                            >
                              <XCircleIcon className="h-3 w-3" />
                              {t.settings.tools.mcp.connectFailed}
                            </Badge>
                          ) : null}
                          {server.enabled && toolCheck.status === "idle" ? (
                            <Badge variant="outline">
                              {t.settings.tools.mcp.untested}
                            </Badge>
                          ) : null}
                        </div>
                        <CardDescription className="mt-2">
                          {server.description || "暂无描述"}
                        </CardDescription>
                        {toolCheck.status === "error" && toolCheck.error ? (
                          <p className="text-destructive mt-1 text-xs">
                            {toolCheck.error}
                          </p>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={
                            !server.enabled ||
                            saving ||
                            toolCheck.status === "loading"
                          }
                          onClick={() => void handleRefreshTools(name)}
                        >
                          <RefreshCwIcon
                            className={cn(
                              "h-3.5 w-3.5",
                              toolCheck.status === "loading" && "animate-spin",
                            )}
                          />
                          {toolCheck.status === "loading"
                            ? t.settings.tools.mcp.refreshing
                            : t.settings.tools.mcp.refreshConnection}
                        </Button>
                        <Switch
                          checked={server.enabled}
                          disabled={saving}
                          onCheckedChange={(enabled) =>
                            void handleToggle(name, enabled)
                          }
                        />
                        {isEditable(server) ? (
                          <>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() => openEdit(name, server)}
                            >
                              <PencilIcon className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              onClick={() => void handleDelete(name, server)}
                            >
                              <Trash2Icon className="h-4 w-4" />
                            </Button>
                          </>
                        ) : null}
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2 text-xs">
                    {stringValue(server.command) ? (
                      <code className="bg-muted block rounded-md px-3 py-2">
                        {stringValue(server.command)}{" "}
                        {stringArrayValue(server.args).join(" ")}
                      </code>
                    ) : null}
                    {stringValue(server.url) ? (
                      <code className="bg-muted block rounded-md px-3 py-2">
                        {stringValue(server.url)}
                      </code>
                    ) : null}
                    <div className="border-t pt-2">
                      <button
                        type="button"
                        aria-expanded={toolCheck.expanded}
                        className="hover:bg-muted/60 flex w-full items-center justify-between gap-2 rounded-md py-1.5 text-left"
                        onClick={() => toggleToolList(name)}
                      >
                        <span className="flex items-center gap-1.5 text-xs font-medium">
                          <WrenchIcon className="h-3.5 w-3.5" />
                          {t.settings.tools.mcp.toolList}
                          {toolCheck.status === "success" ? (
                            <span className="text-muted-foreground font-normal">
                              {toolCheck.tools.length}
                            </span>
                          ) : null}
                        </span>
                        <ChevronDownIcon
                          className={cn(
                            "text-muted-foreground h-4 w-4 transition-transform",
                            toolCheck.expanded && "rotate-180",
                          )}
                        />
                      </button>
                      {toolCheck.expanded ? (
                        <div className="mt-1" data-role="tool-list-panel">
                          {toolCheck.status === "success" ? (
                            toolCheck.tools.length === 0 ? (
                              <p className="text-muted-foreground px-1 py-2 text-xs">
                                {t.settings.tools.mcp.toolListEmpty}
                              </p>
                            ) : (
                              <>
                                <ul className="max-h-52 space-y-2 overflow-y-auto pr-1">
                                  {toolCheck.tools.map((tool) => (
                                    <li
                                      key={tool.name}
                                      className="rounded-md border px-3 py-2"
                                    >
                                      <div className="font-mono text-xs break-all">
                                        {tool.name}
                                      </div>
                                      {tool.description ? (
                                        <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
                                          {tool.description}
                                        </p>
                                      ) : null}
                                    </li>
                                  ))}
                                </ul>
                                {toolCheck.tools.length > 4 ? (
                                  <p className="text-muted-foreground mt-2 text-xs">
                                    {t.settings.tools.mcp.toolListHint(
                                      toolCheck.tools.length,
                                    )}
                                  </p>
                                ) : null}
                              </>
                            )
                          ) : toolCheck.status === "loading" ? (
                            <p className="text-muted-foreground flex items-center gap-2 px-1 py-2 text-xs">
                              <Loader2Icon className="h-3.5 w-3.5 animate-spin" />
                              {t.settings.tools.mcp.refreshing}
                            </p>
                          ) : toolCheck.status === "error" ? (
                            <p className="bg-destructive/10 text-destructive rounded-md px-3 py-2 text-xs break-all">
                              {toolCheck.error}
                            </p>
                          ) : (
                            <p className="text-muted-foreground px-1 py-2 text-xs">
                              {server.enabled
                                ? t.settings.tools.mcp.toolListIdle
                                : t.settings.tools.mcp.toolListDisabled}
                            </p>
                          )}
                        </div>
                      ) : null}
                    </div>
                  </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
        </div>
      </main>

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {form.originalName ? "编辑 MCP" : "添加 MCP"}
            </DialogTitle>
            <DialogDescription>
              仅可添加属于自己的 MCP 服务器。STDIO 类型填写 command 和
              args；HTTP/SSE 类型填写 URL。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium">名称</label>
              <Input
                value={form.name}
                disabled={Boolean(form.originalName)}
                maxLength={32}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
              />
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">描述</label>
              <Input
                value={form.description}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    description: event.target.value,
                  }))
                }
              />
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="grid gap-2">
                <label className="text-sm font-medium">类型</label>
                <select
                  className="border-input bg-background h-9 rounded-md border px-3 text-sm"
                  value={form.type}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      type: event.target.value,
                    }))
                  }
                >
                  <option value="stdio">stdio</option>
                  <option value="sse">sse</option>
                  <option value="http">http</option>
                  <option value="streamable_http">streamable_http</option>
                </select>
              </div>
              <div className="flex items-end gap-2 pb-2">
                <Switch
                  checked={form.enabled}
                  onCheckedChange={(enabled) =>
                    setForm((current) => ({ ...current, enabled }))
                  }
                />
                <span className="text-sm">创建后启用</span>
              </div>
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">Command</label>
              <Input
                value={form.command}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    command: event.target.value,
                  }))
                }
                placeholder="npx"
              />
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">Args JSON</label>
              <Textarea
                value={form.argsText}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    argsText: event.target.value,
                  }))
                }
                className="min-h-20 font-mono text-xs"
              />
            </div>
            <div className="grid gap-2">
              <label className="text-sm font-medium">URL</label>
              <Input
                value={form.url}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    url: event.target.value,
                  }))
                }
                placeholder="https://..."
              />
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="grid gap-2">
                <label className="text-sm font-medium">Env JSON</label>
                <Textarea
                  value={form.envText}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      envText: event.target.value,
                    }))
                  }
                  className="min-h-24 font-mono text-xs"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-medium">Headers JSON</label>
                <Textarea
                  value={form.headersText}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      headersText: event.target.value,
                    }))
                  }
                  className="min-h-24 font-mono text-xs"
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>
              取消
            </Button>
            <Button disabled={saving} onClick={() => void handleSave()}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function WorkspaceMCPPage() {
  return <MCPManagementPage />;
}
