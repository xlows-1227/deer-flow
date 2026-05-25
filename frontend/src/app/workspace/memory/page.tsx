"use client";

import {
  BrainIcon,
  DownloadIcon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import {
  useClearMemory,
  useCreateMemoryFact,
  useDeleteMemoryFact,
  useImportMemory,
  useMemory,
  useUpdateMemoryFact,
} from "@/core/memory/hooks";
import type { MemoryFact, UserMemory } from "@/core/memory/types";

type FactForm = {
  id: string | null;
  content: string;
  category: string;
  confidence: string;
};

const emptyFactForm: FactForm = {
  id: null,
  content: "",
  category: "context",
  confidence: "0.7",
};

function downloadJson(memory: UserMemory) {
  const blob = new Blob([JSON.stringify(memory, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "deerflow-memory.json";
  anchor.click();
  URL.revokeObjectURL(url);
}

function summaryItems(memory: UserMemory | null) {
  if (!memory) return [];
  const items: Array<[string, string]> = [
    ["工作上下文", memory.user.workContext.summary],
    ["个人上下文", memory.user.personalContext.summary],
    ["近期关注", memory.user.topOfMind.summary],
    ["近几个月", memory.history.recentMonths.summary],
    ["更早上下文", memory.history.earlierContext.summary],
    ["长期背景", memory.history.longTermBackground.summary],
  ];
  return items.filter(([, value]) => value.trim().length > 0);
}

export default function WorkspaceMemoryPage() {
  const { memory, isLoading, error } = useMemory();
  const createFact = useCreateMemoryFact();
  const updateFact = useUpdateMemoryFact();
  const deleteFact = useDeleteMemoryFact();
  const clearMemory = useClearMemory();
  const importMemory = useImportMemory();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<FactForm>(emptyFactForm);

  const summaries = useMemo(() => summaryItems(memory), [memory]);
  const facts = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return (memory?.facts ?? []).filter((fact) => {
      return (
        !normalizedQuery ||
        fact.content.toLowerCase().includes(normalizedQuery) ||
        fact.category.toLowerCase().includes(normalizedQuery)
      );
    });
  }, [memory, query]);

  function openCreate() {
    setForm(emptyFactForm);
    setFormOpen(true);
  }

  function openEdit(fact: MemoryFact) {
    setForm({
      id: fact.id,
      content: fact.content,
      category: fact.category,
      confidence: String(fact.confidence),
    });
    setFormOpen(true);
  }

  async function handleSaveFact() {
    const content = form.content.trim();
    const confidence = Number(form.confidence);
    if (!content) {
      toast.error("事实内容不能为空");
      return;
    }
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      toast.error("置信度必须是 0 到 1 之间的数字");
      return;
    }
    const category = form.category.trim();
    const normalizedCategory = category.length > 0 ? category : "context";
    try {
      if (form.id) {
        await updateFact.mutateAsync({
          factId: form.id,
          input: {
            content,
            category: normalizedCategory,
            confidence,
          },
        });
        toast.success("记忆事实已更新");
      } else {
        await createFact.mutateAsync({
          content,
          category: normalizedCategory,
          confidence,
        });
        toast.success("记忆事实已添加");
      }
      setFormOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleDeleteFact(fact: MemoryFact) {
    if (!window.confirm("确定删除这条记忆事实吗？")) return;
    try {
      await deleteFact.mutateAsync(fact.id);
      toast.success("记忆事实已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleClear() {
    if (!window.confirm("确定清空所有记忆吗？此操作无法撤销。")) return;
    try {
      await clearMemory.mutateAsync();
      toast.success("记忆已清空");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  async function handleImportFile(file: File | undefined) {
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text) as UserMemory;
      await importMemory.mutateAsync(parsed);
      toast.success("记忆已导入");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  return (
    <div className="flex size-full flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">记忆管理</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            查看自动沉淀的上下文，手动维护事实记忆，并支持导入导出备份。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(event) => void handleImportFile(event.target.files?.[0])}
          />
          <Button
            variant="outline"
            onClick={() => fileInputRef.current?.click()}
          >
            <UploadIcon className="h-4 w-4" />
            导入
          </Button>
          <Button
            variant="outline"
            disabled={!memory}
            onClick={() => memory && downloadJson(memory)}
          >
            <DownloadIcon className="h-4 w-4" />
            导出
          </Button>
          <Button onClick={openCreate}>
            <PlusIcon className="h-4 w-4" />
            添加事实
          </Button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
          {isLoading ? (
            <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
              加载记忆中...
            </div>
          ) : error ? (
            <Card className="text-destructive rounded-lg p-6 text-sm">
              加载失败：{error.message}
            </Card>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <Card className="rounded-lg py-4">
                  <CardContent className="px-4">
                    <div className="text-2xl font-semibold">
                      {memory?.facts.length ?? 0}
                    </div>
                    <div className="text-muted-foreground text-xs">
                      事实记忆
                    </div>
                  </CardContent>
                </Card>
                <Card className="rounded-lg py-4">
                  <CardContent className="px-4">
                    <div className="text-2xl font-semibold">
                      {summaries.length}
                    </div>
                    <div className="text-muted-foreground text-xs">
                      上下文摘要
                    </div>
                  </CardContent>
                </Card>
                <Card className="rounded-lg py-4">
                  <CardContent className="px-4">
                    <div className="truncate text-sm font-medium">
                      {memory?.lastUpdated ?? "尚未更新"}
                    </div>
                    <div className="text-muted-foreground text-xs">
                      最后更新
                    </div>
                  </CardContent>
                </Card>
              </div>

              <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.75fr)]">
                <section className="flex flex-col gap-3">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h2 className="font-semibold">事实</h2>
                      <p className="text-muted-foreground text-sm">
                        可编辑的结构化记忆，Agent 会在后续对话中参考。
                      </p>
                    </div>
                    <Input
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="搜索事实"
                      className="w-56"
                    />
                  </div>

                  {facts.length === 0 ? (
                    <Card className="rounded-lg p-10 text-center">
                      <BrainIcon className="text-muted-foreground mx-auto h-10 w-10" />
                      <p className="mt-4 font-medium">暂无匹配事实</p>
                    </Card>
                  ) : (
                    <div className="grid gap-3">
                      {facts.map((fact) => (
                        <Card key={fact.id} className="rounded-lg">
                          <CardHeader className="gap-3">
                            <div className="flex items-start justify-between gap-4">
                              <div className="min-w-0">
                                <CardTitle className="text-base leading-6">
                                  {fact.content}
                                </CardTitle>
                                <CardDescription className="mt-2 flex flex-wrap gap-2">
                                  <Badge variant="secondary">
                                    {fact.category}
                                  </Badge>
                                  <Badge variant="outline">
                                    置信度 {fact.confidence.toFixed(2)}
                                  </Badge>
                                  {fact.source ? (
                                    <Badge variant="outline">
                                      {fact.source}
                                    </Badge>
                                  ) : null}
                                </CardDescription>
                              </div>
                              <div className="flex shrink-0 gap-1">
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  onClick={() => openEdit(fact)}
                                >
                                  <PencilIcon className="h-4 w-4" />
                                </Button>
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  onClick={() => void handleDeleteFact(fact)}
                                >
                                  <Trash2Icon className="h-4 w-4" />
                                </Button>
                              </div>
                            </div>
                          </CardHeader>
                        </Card>
                      ))}
                    </div>
                  )}
                </section>

                <section className="flex flex-col gap-3">
                  <div>
                    <h2 className="font-semibold">上下文摘要</h2>
                    <p className="text-muted-foreground text-sm">
                      摘要由系统自动维护，当前以只读方式展示。
                    </p>
                  </div>
                  {summaries.length === 0 ? (
                    <Card className="text-muted-foreground rounded-lg p-8 text-center text-sm">
                      暂无摘要记忆。
                    </Card>
                  ) : (
                    summaries.map(([label, value]) => (
                      <Card key={label} className="rounded-lg">
                        <CardHeader>
                          <CardTitle className="text-sm">{label}</CardTitle>
                        </CardHeader>
                        <CardContent>
                          <p className="text-muted-foreground text-sm leading-6 whitespace-pre-wrap">
                            {value}
                          </p>
                        </CardContent>
                      </Card>
                    ))
                  )}
                  <Button
                    variant="destructive"
                    disabled={clearMemory.isPending}
                    onClick={() => void handleClear()}
                  >
                    清空全部记忆
                  </Button>
                </section>
              </div>
            </>
          )}
        </div>
      </main>

      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{form.id ? "编辑事实" : "添加事实"}</DialogTitle>
            <DialogDescription>
              用简短、稳定的事实描述希望 DeerFlow 长期记住的信息。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <label className="text-sm font-medium">内容</label>
              <Textarea
                value={form.content}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    content: event.target.value,
                  }))
                }
                className="min-h-28"
              />
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="grid gap-2">
                <label className="text-sm font-medium">类别</label>
                <Input
                  value={form.category}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      category: event.target.value,
                    }))
                  }
                />
              </div>
              <div className="grid gap-2">
                <label className="text-sm font-medium">置信度</label>
                <Input
                  value={form.confidence}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      confidence: event.target.value,
                    }))
                  }
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFormOpen(false)}>
              取消
            </Button>
            <Button
              disabled={createFact.isPending || updateFact.isPending}
              onClick={() => void handleSaveFact()}
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
