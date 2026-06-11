"use client";

import {
  DownloadIcon,
  PenLineIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";
import { Streamdown } from "streamdown";

import { Button } from "@/components/ui/button";
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
import { useI18n } from "@/core/i18n/hooks";
import { exportMemory } from "@/core/memory/api";
import {
  useClearMemory,
  useCreateMemoryFact,
  useDeleteDailyMemory,
  useDeleteMemoryFact,
  useDailyMemory,
  useImportMemory,
  useMemory,
  useMemoryProfile,
  useRollupDailyMemory,
  useUpdateMemoryFact,
} from "@/core/memory/hooks";
import type {
  DailyPersonSummary,
  MemoryFactInput,
  MemoryFactPatchInput,
  MemoryProfile,
  MemoryProfileItem,
  UserMemory,
} from "@/core/memory/types";
import { streamdownPlugins } from "@/core/streamdown/plugins";
import { formatTimeAgo } from "@/core/utils/datetime";

import { SettingsSection } from "./settings-section";

type MemoryFact = UserMemory["facts"][number];

type PendingImport = {
  fileName: string;
  memory: UserMemory;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isMemorySection(value: unknown): value is {
  summary: string;
  updatedAt: string;
} {
  return (
    isRecord(value) &&
    typeof value.summary === "string" &&
    typeof value.updatedAt === "string"
  );
}

function isMemoryFact(value: unknown): value is UserMemory["facts"][number] {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.content === "string" &&
    typeof value.category === "string" &&
    typeof value.confidence === "number" &&
    Number.isFinite(value.confidence) &&
    typeof value.createdAt === "string" &&
    typeof value.source === "string"
  );
}

function isImportedMemory(value: unknown): value is UserMemory {
  if (!isRecord(value)) {
    return false;
  }

  if (
    typeof value.version !== "string" ||
    typeof value.lastUpdated !== "string" ||
    !isRecord(value.user) ||
    !isRecord(value.history) ||
    !Array.isArray(value.facts)
  ) {
    return false;
  }

  return (
    isMemorySection(value.user.workContext) &&
    isMemorySection(value.user.personalContext) &&
    isMemorySection(value.user.topOfMind) &&
    isMemorySection(value.history.recentMonths) &&
    isMemorySection(value.history.earlierContext) &&
    isMemorySection(value.history.longTermBackground) &&
    value.facts.every(isMemoryFact)
  );
}

type FactFormState = {
  content: string;
  category: string;
  confidence: string;
};

const DEFAULT_FACT_FORM_STATE: FactFormState = {
  content: "",
  category: "context",
  confidence: "0.8",
};

function confidenceToLevelKey(confidence: unknown): {
  key: "veryHigh" | "high" | "normal" | "unknown";
  value?: number;
} {
  if (typeof confidence !== "number" || !Number.isFinite(confidence)) {
    return { key: "unknown" };
  }

  const value = Math.min(1, Math.max(0, confidence));
  if (value >= 0.85) return { key: "veryHigh", value };
  if (value >= 0.65) return { key: "high", value };
  return { key: "normal", value };
}

function profileItemsToMarkdown(title: string, items: MemoryProfileItem[]) {
  const activeItems = items.filter((item) => item.status === "active");
  if (activeItems.length === 0) return "";
  return [
    `### ${title}`,
    ...activeItems.map((item) => `- ${item.content}`),
    "",
  ].join("\n");
}

function memoryProfileToMarkdown(profile: MemoryProfile | null) {
  if (!profile) return "";
  const sections = [
    profile.overview || "",
    profileItemsToMarkdown("偏好", profile.preferences),
    profileItemsToMarkdown("沟通风格", profile.communicationStyle),
    profileItemsToMarkdown("Skill 与工具使用习惯", profile.skillUsagePatterns),
    profileItemsToMarkdown("兴趣与画像", profile.interests),
    profileItemsToMarkdown("近期关注", profile.topOfMind),
    profileItemsToMarkdown("纠正与避免", profile.corrections),
  ].filter((part) => part.trim().length > 0);
  if (sections.length === 0) return "";
  return ["## 长期画像", ...sections].join("\n");
}

function dailyMemoryToMarkdown(dailyMemory: DailyPersonSummary[]) {
  if (dailyMemory.length === 0) return "";
  const parts = ["## 每日总结"];
  for (const daily of dailyMemory.slice(0, 7)) {
    parts.push(`### ${daily.date}`);
    if (daily.summary.trim()) parts.push(daily.summary);
    const lines = [
      ...daily.preferences.map((item) => `- 偏好: ${item}`),
      ...daily.recentFocus.map((item) => `- 近期关注: ${item}`),
      ...daily.skillUsagePatterns.map((item) => `- 使用习惯: ${item}`),
      ...daily.interests.map((item) => `- 兴趣/画像: ${item}`),
      ...daily.corrections.map((item) => `- 纠正: ${item}`),
    ];
    if (lines.length > 0) parts.push(lines.join("\n"));
    if (daily.updatedAt)
      parts.push(`> 更新于: \`${formatTimeAgo(daily.updatedAt)}\``);
  }
  return parts.join("\n\n");
}

function truncateFactPreview(content: string, maxLength = 140) {
  const normalized = content.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) {
    return normalized;
  }
  const ellipsis = "...";
  if (maxLength <= ellipsis.length) {
    return normalized.slice(0, maxLength);
  }
  return `${normalized.slice(0, maxLength - ellipsis.length)}${ellipsis}`;
}

function upperFirst(str: string) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}

export function MemorySettingsPage() {
  const { t } = useI18n();
  const { memory, isLoading, error } = useMemory();
  const { profile } = useMemoryProfile();
  const { dailyMemory } = useDailyMemory(30);
  const clearMemory = useClearMemory();
  const createMemoryFact = useCreateMemoryFact();
  const deleteDailyMemory = useDeleteDailyMemory();
  const deleteMemoryFact = useDeleteMemoryFact();
  const importMemoryMutation = useImportMemory();
  const rollupDailyMemory = useRollupDailyMemory();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const updateMemoryFact = useUpdateMemoryFact();
  const [clearDialogOpen, setClearDialogOpen] = useState(false);
  const [dailyToDelete, setDailyToDelete] = useState<DailyPersonSummary | null>(
    null,
  );
  const [factToDelete, setFactToDelete] = useState<MemoryFact | null>(null);
  const [factToEdit, setFactToEdit] = useState<MemoryFact | null>(null);
  const [factEditorOpen, setFactEditorOpen] = useState(false);
  const [factForm, setFactForm] = useState<FactFormState>(
    DEFAULT_FACT_FORM_STATE,
  );
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(
    null,
  );
  const [isExporting, setIsExporting] = useState(false);
  const factContentInputId = useId();
  const factCategoryInputId = useId();
  const factConfidenceInputId = useId();
  const factConfidenceHintId = useId();

  const clearAllLabel = t.settings.memory.clearAll ?? "Clear all memory";
  const clearAllConfirmTitle =
    t.settings.memory.clearAllConfirmTitle ?? "Clear all memory?";
  const clearAllConfirmDescription =
    t.settings.memory.clearAllConfirmDescription ??
    "This will remove all saved summaries and facts. This action cannot be undone.";
  const clearAllSuccess =
    t.settings.memory.clearAllSuccess ?? "All memory cleared";
  const factDeleteConfirmTitle =
    t.settings.memory.factDeleteConfirmTitle ?? "Delete this fact?";
  const factDeleteConfirmDescription =
    t.settings.memory.factDeleteConfirmDescription ??
    "This fact will be removed from memory immediately. This action cannot be undone.";
  const factDeleteSuccess =
    t.settings.memory.factDeleteSuccess ?? "Fact deleted";
  const addFactLabel = t.settings.memory.addFact;
  const addFactTitle = t.settings.memory.addFactTitle;
  const editFactTitle = t.settings.memory.editFactTitle;
  const addFactSuccess = t.settings.memory.addFactSuccess;
  const editFactSuccess = t.settings.memory.editFactSuccess;
  const factContentLabel = t.settings.memory.factContentLabel;
  const factCategoryLabel = t.settings.memory.factCategoryLabel;
  const factConfidenceLabel = t.settings.memory.factConfidenceLabel;
  const factContentPlaceholder = t.settings.memory.factContentPlaceholder;
  const factCategoryPlaceholder = t.settings.memory.factCategoryPlaceholder;
  const factConfidenceHint = t.settings.memory.factConfidenceHint;
  const factSave = t.settings.memory.factSave;
  const factValidationContent = t.settings.memory.factValidationContent;
  const factValidationConfidence = t.settings.memory.factValidationConfidence;
  const noFacts = t.settings.memory.noFacts ?? "No saved facts yet.";
  const memoryFullyEmpty =
    t.settings.memory.memoryFullyEmpty ?? "No memory saved yet.";
  const factPreviewLabel =
    t.settings.memory.factPreviewLabel ?? "Fact to delete";
  const rollupDailyLabel = t.settings.memory.rollupDaily ?? "Roll up now";
  const rollupDailySuccess =
    t.settings.memory.rollupDailySuccess ?? "Daily summary updated";
  const rollupDailyEmpty =
    t.settings.memory.rollupDailyEmpty ??
    "No session content is ready to summarize";
  const dailyDeleteConfirmTitle =
    t.settings.memory.dailyDeleteConfirmTitle ?? "Delete this daily summary?";
  const dailyDeleteConfirmDescription =
    t.settings.memory.dailyDeleteConfirmDescription ??
    "This soft-deletes the daily summary immediately.";
  const dailyDeleteSuccess =
    t.settings.memory.dailyDeleteSuccess ?? "Daily summary deleted";
  const dailyDeletePreviewLabel =
    t.settings.memory.dailyDeletePreviewLabel ?? "Date to delete";
  const exportButton = t.settings.memory.exportButton ?? t.common.export;
  const exportSuccess =
    t.settings.memory.exportSuccess ?? t.common.exportSuccess;
  const importButton = t.settings.memory.importButton ?? t.common.import;
  const importSuccess = t.settings.memory.importSuccess ?? "Memory imported";

  const manualFacts = memory
    ? memory.facts.filter((fact) => fact.source === "manual")
    : [];
  const profileMarkdown = memoryProfileToMarkdown(profile);
  const dailyMarkdown = dailyMemoryToMarkdown(dailyMemory);
  const isMemoryEmpty =
    !profileMarkdown && !dailyMarkdown && manualFacts.length === 0;

  async function handleExportMemory() {
    try {
      setIsExporting(true);
      const exportedMemory = await exportMemory();
      const fileName = `deerflow-memory-${(exportedMemory.lastUpdated || new Date().toISOString()).replace(/[:.]/g, "-")}.json`;
      const blob = new Blob([JSON.stringify(exportedMemory, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success(exportSuccess);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setIsExporting(false);
    }
  }

  async function handleImportFileSelection(event: {
    target: HTMLInputElement;
  }) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }

    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!isImportedMemory(parsed)) {
        toast.error(t.settings.memory.importInvalidFile);
        return;
      }
      setPendingImport({
        fileName: file.name,
        memory: parsed,
      });
    } catch {
      toast.error(t.settings.memory.importInvalidFile);
    }
  }

  async function handleConfirmImport() {
    if (!pendingImport) {
      return;
    }

    try {
      await importMemoryMutation.mutateAsync(pendingImport.memory);
      toast.success(importSuccess);
      setPendingImport(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleClearMemory() {
    try {
      await clearMemory.mutateAsync();
      toast.success(clearAllSuccess);
      setClearDialogOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleRollupDailyMemory() {
    try {
      const summary = await rollupDailyMemory.mutateAsync({});
      toast.success(summary ? rollupDailySuccess : rollupDailyEmpty);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDeleteDailyMemory() {
    if (!dailyToDelete) return;

    try {
      await deleteDailyMemory.mutateAsync(dailyToDelete.date);
      toast.success(dailyDeleteSuccess);
      setDailyToDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDeleteFact() {
    if (!factToDelete) return;

    try {
      await deleteMemoryFact.mutateAsync(factToDelete.id);
      toast.success(factDeleteSuccess);
      setFactToDelete(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  function openCreateFactDialog() {
    setFactToEdit(null);
    setFactForm(DEFAULT_FACT_FORM_STATE);
    setFactEditorOpen(true);
  }

  function openEditFactDialog(fact: MemoryFact) {
    setFactToEdit(fact);
    setFactForm({
      content: fact.content,
      category: fact.category,
      confidence: String(fact.confidence),
    });
    setFactEditorOpen(true);
  }

  async function handleSaveFact() {
    const trimmedContent = factForm.content.trim();
    if (!trimmedContent) {
      toast.error(factValidationContent);
      return;
    }

    const confidence = Number(factForm.confidence);
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      toast.error(factValidationConfidence);
      return;
    }

    const input: MemoryFactInput = {
      content: trimmedContent,
      category: factForm.category.trim() || "context",
      confidence,
    };

    try {
      if (factToEdit) {
        const patchInput: MemoryFactPatchInput = {
          content: input.content,
          category: input.category,
          confidence: input.confidence,
        };
        await updateMemoryFact.mutateAsync({
          factId: factToEdit.id,
          input: patchInput,
        });
        toast.success(editFactSuccess);
      } else {
        await createMemoryFact.mutateAsync(input);
        toast.success(addFactSuccess);
      }
      setFactEditorOpen(false);
      setFactToEdit(null);
      setFactForm(DEFAULT_FACT_FORM_STATE);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  const isFactFormPending =
    createMemoryFact.isPending || updateMemoryFact.isPending;

  return (
    <>
      <SettingsSection
        title={t.settings.memory.title}
        description={t.settings.memory.description}
      >
        {isLoading ? (
          <div className="text-muted-foreground text-sm">
            {t.common.loading}
          </div>
        ) : error ? (
          <div>Error: {error.message}</div>
        ) : !memory ? (
          <div className="text-muted-foreground text-sm">
            {t.settings.memory.empty}
          </div>
        ) : (
          <div className="space-y-4">
            {isMemoryEmpty ? (
              <div className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">
                {memoryFullyEmpty}
              </div>
            ) : null}

            <div className="flex min-w-0 flex-wrap gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept=".json,application/json"
                className="hidden"
                onChange={(event) => void handleImportFileSelection(event)}
              />
              <Button
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
                disabled={importMemoryMutation.isPending}
              >
                <UploadIcon className="mr-2 h-4 w-4" />
                {importButton}
              </Button>
              <Button
                variant="outline"
                onClick={() => void handleExportMemory()}
                disabled={isExporting}
              >
                <DownloadIcon className="mr-2 h-4 w-4" />
                {isExporting ? t.common.loading : exportButton}
              </Button>
              <Button
                variant="outline"
                onClick={() => void handleRollupDailyMemory()}
                disabled={rollupDailyMemory.isPending}
              >
                <RefreshCwIcon className="mr-2 h-4 w-4" />
                {rollupDailyMemory.isPending
                  ? t.common.loading
                  : rollupDailyLabel}
              </Button>
              <Button variant="outline" onClick={openCreateFactDialog}>
                <PlusIcon className="mr-2 h-4 w-4" />
                {addFactLabel}
              </Button>
              <Button
                variant="destructive"
                onClick={() => setClearDialogOpen(true)}
                disabled={clearMemory.isPending}
              >
                {clearMemory.isPending ? t.common.loading : clearAllLabel}
              </Button>
            </div>

            {profileMarkdown ? (
              <div className="min-w-0 rounded-lg border p-4">
                <Streamdown
                  className="size-full min-w-0 [overflow-wrap:anywhere] [&>*:first-child]:mt-0 [&>*:last-child]:mb-0"
                  {...streamdownPlugins}
                >
                  {profileMarkdown}
                </Streamdown>
              </div>
            ) : null}

            {dailyMarkdown ? (
              <div className="min-w-0 rounded-lg border p-4">
                <Streamdown
                  className="size-full min-w-0 [overflow-wrap:anywhere] [&>*:first-child]:mt-0 [&>*:last-child]:mb-0"
                  {...streamdownPlugins}
                >
                  {dailyMarkdown}
                </Streamdown>
                {dailyMemory.length > 0 ? (
                  <div className="mt-4 space-y-2 border-t pt-4">
                    {dailyMemory.slice(0, 7).map((daily) => (
                      <div
                        key={daily.id}
                        className="flex min-h-10 items-center justify-between gap-3 rounded-md border px-3 py-2"
                      >
                        <span className="text-sm font-medium">
                          {daily.date}
                        </span>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="text-destructive hover:text-destructive shrink-0"
                          onClick={() => setDailyToDelete(daily)}
                          disabled={deleteDailyMemory.isPending}
                          title={t.common.delete}
                          aria-label={t.common.delete}
                        >
                          <Trash2Icon className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className="min-w-0 rounded-lg border p-4">
              <div className="mb-4">
                <h3 className="text-base font-medium">
                  {t.settings.memory.markdown.facts}
                </h3>
              </div>

              {manualFacts.length === 0 ? (
                <div className="text-muted-foreground text-sm">{noFacts}</div>
              ) : (
                <div className="space-y-3">
                  {manualFacts.map((fact) => {
                    const { key } = confidenceToLevelKey(fact.confidence);
                    const confidenceText =
                      t.settings.memory.markdown.table.confidenceLevel[key];

                    return (
                      <div
                        key={fact.id}
                        className="flex flex-col gap-3 rounded-md border p-3 sm:flex-row sm:items-start sm:justify-between"
                      >
                        <div className="min-w-0 space-y-2 [overflow-wrap:anywhere]">
                          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                            <span>
                              <span className="text-muted-foreground">
                                {t.settings.memory.markdown.table.category}:
                              </span>{" "}
                              {upperFirst(fact.category)}
                            </span>
                            <span>
                              <span className="text-muted-foreground">
                                {t.settings.memory.markdown.table.confidence}:
                              </span>{" "}
                              {confidenceText}
                            </span>
                            <span>
                              <span className="text-muted-foreground">
                                {t.settings.memory.markdown.table.createdAt}:
                              </span>{" "}
                              {formatTimeAgo(fact.createdAt)}
                            </span>
                            <span>
                              <span className="text-muted-foreground">
                                {t.settings.memory.markdown.table.source}:
                              </span>{" "}
                              {t.settings.memory.manualFactSource}
                            </span>
                          </div>
                          <p className="text-sm [overflow-wrap:anywhere]">
                            {fact.content}
                          </p>
                        </div>

                        <div className="flex shrink-0 items-center gap-1 self-start sm:ml-3">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="shrink-0"
                            onClick={() => openEditFactDialog(fact)}
                            disabled={deleteMemoryFact.isPending}
                            title={t.common.edit}
                            aria-label={t.common.edit}
                          >
                            <PenLineIcon className="h-4 w-4" />
                          </Button>

                          <Button
                            variant="ghost"
                            size="icon"
                            className="text-destructive hover:text-destructive shrink-0"
                            onClick={() => setFactToDelete(fact)}
                            disabled={deleteMemoryFact.isPending}
                            title={t.common.delete}
                            aria-label={t.common.delete}
                          >
                            <Trash2Icon className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </SettingsSection>

      <Dialog open={clearDialogOpen} onOpenChange={setClearDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{clearAllConfirmTitle}</DialogTitle>
            <DialogDescription>{clearAllConfirmDescription}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setClearDialogOpen(false)}
              disabled={clearMemory.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleClearMemory()}
              disabled={clearMemory.isPending}
            >
              {clearMemory.isPending ? t.common.loading : clearAllLabel}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={factEditorOpen}
        onOpenChange={(open) => {
          setFactEditorOpen(open);
          if (!open) {
            setFactToEdit(null);
            setFactForm(DEFAULT_FACT_FORM_STATE);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {factToEdit ? editFactTitle : addFactTitle}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label
                className="text-sm font-medium"
                htmlFor={factContentInputId}
              >
                {factContentLabel}
              </label>
              <Textarea
                id={factContentInputId}
                value={factForm.content}
                onChange={(event) =>
                  setFactForm((current) => ({
                    ...current,
                    content: event.target.value,
                  }))
                }
                placeholder={factContentPlaceholder}
                rows={4}
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <label
                  className="text-sm font-medium"
                  htmlFor={factCategoryInputId}
                >
                  {factCategoryLabel}
                </label>
                <Input
                  id={factCategoryInputId}
                  value={factForm.category}
                  onChange={(event) =>
                    setFactForm((current) => ({
                      ...current,
                      category: event.target.value,
                    }))
                  }
                  placeholder={factCategoryPlaceholder}
                />
              </div>

              <div className="space-y-2">
                <label
                  className="text-sm font-medium"
                  htmlFor={factConfidenceInputId}
                >
                  {factConfidenceLabel}
                </label>
                <Input
                  id={factConfidenceInputId}
                  aria-describedby={factConfidenceHintId}
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                  value={factForm.confidence}
                  onChange={(event) =>
                    setFactForm((current) => ({
                      ...current,
                      confidence: event.target.value,
                    }))
                  }
                />
                <div
                  className="text-muted-foreground text-xs"
                  id={factConfidenceHintId}
                >
                  {factConfidenceHint}
                </div>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setFactEditorOpen(false);
                setFactToEdit(null);
                setFactForm(DEFAULT_FACT_FORM_STATE);
              }}
              disabled={isFactFormPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => void handleSaveFact()}
              disabled={isFactFormPending}
            >
              {isFactFormPending ? t.common.loading : factSave}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={factToDelete !== null}
        onOpenChange={(open) => {
          if (!open) {
            setFactToDelete(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{factDeleteConfirmTitle}</DialogTitle>
            <DialogDescription>
              {factDeleteConfirmDescription}
            </DialogDescription>
          </DialogHeader>
          {factToDelete ? (
            <div className="bg-muted rounded-md border p-3 text-sm">
              <div className="text-muted-foreground mb-1 font-medium">
                {factPreviewLabel}
              </div>
              <p className="break-words">
                {truncateFactPreview(factToDelete.content)}
              </p>
            </div>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setFactToDelete(null)}
              disabled={deleteMemoryFact.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDeleteFact()}
              disabled={deleteMemoryFact.isPending}
            >
              {deleteMemoryFact.isPending ? t.common.loading : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={dailyToDelete !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDailyToDelete(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{dailyDeleteConfirmTitle}</DialogTitle>
            <DialogDescription>
              {dailyDeleteConfirmDescription}
            </DialogDescription>
          </DialogHeader>
          {dailyToDelete ? (
            <div className="bg-muted rounded-md border p-3 text-sm">
              <div className="text-muted-foreground mb-1 font-medium">
                {dailyDeletePreviewLabel}
              </div>
              <p className="break-words">{dailyToDelete.date}</p>
            </div>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDailyToDelete(null)}
              disabled={deleteDailyMemory.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDeleteDailyMemory()}
              disabled={deleteDailyMemory.isPending}
            >
              {deleteDailyMemory.isPending ? t.common.loading : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={pendingImport !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingImport(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.settings.memory.importConfirmTitle}</DialogTitle>
            <DialogDescription>
              {t.settings.memory.importConfirmDescription}
            </DialogDescription>
          </DialogHeader>
          {pendingImport ? (
            <div className="bg-muted rounded-md border p-3 text-sm">
              <div>
                <span className="text-muted-foreground">
                  {t.settings.memory.importFileLabel}:
                </span>{" "}
                {pendingImport.fileName}
              </div>
              <div>
                <span className="text-muted-foreground">
                  {t.settings.memory.markdown.facts}:
                </span>{" "}
                {pendingImport.memory.facts.length}
              </div>
              <div>
                <span className="text-muted-foreground">
                  {t.common.lastUpdated}:
                </span>{" "}
                {pendingImport.memory.lastUpdated
                  ? formatTimeAgo(pendingImport.memory.lastUpdated)
                  : "-"}
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPendingImport(null)}
              disabled={importMemoryMutation.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              onClick={() => void handleConfirmImport()}
              disabled={importMemoryMutation.isPending}
            >
              {importMemoryMutation.isPending
                ? t.common.loading
                : t.common.import}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
