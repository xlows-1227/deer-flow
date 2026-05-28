"use client";

import {
  CheckCircleIcon,
  DownloadIcon,
  PencilIcon,
  PlugIcon,
  PlusIcon,
  TerminalIcon,
  Trash2Icon,
  UploadIcon,
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
import { loadMCPConfig, updateMCPConfig } from "@/core/mcp/api";
import type { MCPConfig, MCPServerConfig } from "@/core/mcp/types";
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

function toServerConfig(form: MCPForm): MCPServerConfig {
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
  const [config, setConfig] = useState<MCPConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [form, setForm] = useState<MCPForm>(emptyForm);
  const [importText, setImportText] = useState("");

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

  async function persist(nextConfig: MCPConfig, message: string) {
    setSaving(true);
    try {
      const updated = await updateMCPConfig(nextConfig);
      setConfig(updated);
      toast.success(message);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  function openCreate() {
    setForm(emptyForm);
    setFormOpen(true);
  }

  function openEdit(name: string, server: MCPServerConfig) {
    setForm(toForm(name, server));
    setFormOpen(true);
  }

  async function handleSave() {
    const trimmedName = form.name.trim();
    if (!trimmedName) {
      toast.error("请输入 MCP 名称");
      return;
    }
    if (!config) return;

    try {
      const nextServers = { ...config.mcp_servers };
      if (form.originalName && form.originalName !== trimmedName) {
        delete nextServers[form.originalName];
      }
      nextServers[trimmedName] = toServerConfig({ ...form, name: trimmedName });
      await persist({ mcp_servers: nextServers }, "MCP 配置已保存");
      setFormOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleDelete(name: string) {
    if (!config || !window.confirm(`确定删除 MCP「${name}」吗？`)) return;
    const nextServers = { ...config.mcp_servers };
    delete nextServers[name];
    await persist({ mcp_servers: nextServers }, "MCP 已删除");
  }

  async function handleToggle(name: string, enabled: boolean) {
    if (!config) return;
    const existing = config.mcp_servers[name];
    if (!existing) return;
    await persist(
      {
        mcp_servers: {
          ...config.mcp_servers,
          [name]: {
            ...existing,
            enabled,
          },
        },
      },
      enabled ? "MCP 已启用" : "MCP 已停用",
    );
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

  async function handleImport() {
    if (!config) return;
    try {
      const parsed = JSON.parse(importText) as {
        mcpServers?: Record<string, MCPServerConfig>;
        mcp_servers?: Record<string, MCPServerConfig>;
      };
      const imported = parsed.mcpServers ?? parsed.mcp_servers;
      if (!imported || typeof imported !== "object") {
        throw new Error("JSON 需要包含 mcpServers 或 mcp_servers");
      }
      await persist(
        { mcp_servers: { ...config.mcp_servers, ...imported } },
        "MCP 配置已导入",
      );
      setImportOpen(false);
      setImportText("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
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
          <h1 className="text-xl font-semibold">MCP 管理</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            管理 Model Context Protocol 工具服务器，支持新增、编辑、导入和导出。
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleExport}>
            <DownloadIcon className="h-4 w-4" />
            导出
          </Button>
          <Button variant="outline" onClick={() => setImportOpen(true)}>
            <UploadIcon className="h-4 w-4" />
            导入
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
              {servers.map(([name, server]) => (
                <Card key={name} className="rounded-lg">
                  <CardHeader className="gap-3">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <CardTitle className="truncate">{name}</CardTitle>
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
                        </div>
                        <CardDescription className="mt-2">
                          {server.description || "暂无描述"}
                        </CardDescription>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Switch
                          checked={server.enabled}
                          disabled={saving}
                          onCheckedChange={(enabled) =>
                            void handleToggle(name, enabled)
                          }
                        />
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
                          onClick={() => void handleDelete(name)}
                        >
                          <Trash2Icon className="h-4 w-4" />
                        </Button>
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
                  </CardContent>
                </Card>
              ))}
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
              STDIO 类型填写 command 和 args；HTTP/SSE 类型填写 URL。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium">名称</label>
              <Input
                value={form.name}
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

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>导入 MCP JSON</DialogTitle>
            <DialogDescription>
              粘贴 mcp.json 内容，支持 mcpServers 或 mcp_servers 字段。
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={importText}
            onChange={(event) => setImportText(event.target.value)}
            className="min-h-64 font-mono text-xs"
            placeholder={
              '{\n  "mcpServers": {\n    "memory": { "type": "stdio", "command": "npx", "args": [] }\n  }\n}'
            }
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setImportOpen(false)}>
              取消
            </Button>
            <Button disabled={saving} onClick={() => void handleImport()}>
              导入
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
