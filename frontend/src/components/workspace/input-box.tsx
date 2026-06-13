"use client";

import type { ChatStatus } from "ai";
import {
  AtSignIcon,
  CheckIcon,
  CommandIcon,
  CpuIcon,
  DatabaseIcon,
  EraserIcon,
  FileIcon,
  FileTextIcon,
  GraduationCapIcon,
  HelpCircleIcon,
  ImageIcon,
  LightbulbIcon,
  Loader2Icon,
  PaperclipIcon,
  PlusIcon,
  SparklesIcon,
  RocketIcon,
  WrenchIcon,
  XIcon,
  ZapIcon,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";

import {
  PromptInput,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInputAttachment,
  PromptInputAttachments,
  PromptInputBody,
  PromptInputButton,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputAttachments,
  usePromptInputController,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { ConfettiButton } from "@/components/ui/confetti-button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import { useConnectors } from "@/core/connectors/hooks";
import { useFiles } from "@/core/files/hooks";
import type { FileItem, ReferencedFile } from "@/core/files/type";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { useSkills } from "@/core/skills/hooks";
import { detectMention } from "@/core/skills/mention-picker";
import {
  detectSlashCommand,
  nextPickerIndex,
} from "@/core/skills/slash-picker";
import {
  filterSlashCommands,
  getBuiltinSlashCommands,
  getSlashCommands,
  type SlashCommand,
} from "@/core/slash-commands";
import type { AgentThreadContext } from "@/core/threads";
import { textOfMessage } from "@/core/threads/utils";
import { cn } from "@/lib/utils";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorName,
  ModelSelectorTrigger,
} from "../ai-elements/model-selector";
import { Suggestion, Suggestions } from "../ai-elements/suggestion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

import { useThread } from "./messages/context";
import { ModeHoverGuide } from "./mode-hover-guide";
import { Tooltip } from "./tooltip";

type InputMode = "flash" | "thinking" | "pro" | "ultra";

function getResolvedMode(
  mode: InputMode | undefined,
  supportsThinking: boolean,
): InputMode {
  if (!supportsThinking && mode !== "flash") {
    return "flash";
  }
  if (mode) {
    return mode;
  }
  return supportsThinking ? "pro" : "flash";
}

export function InputBox({
  className,
  disabled,
  autoFocus,
  status = "ready",
  context,
  extraHeader,
  isWelcomeMode,
  threadId,
  initialValue,
  onContextChange,
  onFollowupsVisibilityChange,
  onSubmit,
  onStop,
  lockedSkillName,
  showWelcomeSuggestions = true,
  footerExtensionClassName,
  ...props
}: Omit<ComponentProps<typeof PromptInput>, "onSubmit"> & {
  assistantId?: string | null;
  status?: ChatStatus;
  disabled?: boolean;
  context: Omit<
    AgentThreadContext,
    "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
  > & {
    mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
    reasoning_effort?: "minimal" | "low" | "medium" | "high";
    skill_name?: string;
  };
  extraHeader?: React.ReactNode;
  /**
   * Whether to render the input in welcome layout (vertically centered,
   * with hero + quick action suggestions).  This is purely a visual flag,
   * decoupled from "the backend has created the thread" — see issue #2746.
   */
  isWelcomeMode?: boolean;
  threadId: string;
  initialValue?: string;
  onContextChange?: (
    context: Omit<
      AgentThreadContext,
      "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
    > & {
      mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
      reasoning_effort?: "minimal" | "low" | "medium" | "high";
      skill_name?: string;
    },
  ) => void;
  onFollowupsVisibilityChange?: (visible: boolean) => void;
  onSubmit?: (message: PromptInputMessage) => void | Promise<void>;
  onStop?: () => void;
  /** When set, skill is fixed and cannot be changed or cleared. */
  lockedSkillName?: string;
  /** Whether to show welcome quick suggestion chips under the input. */
  showWelcomeSuggestions?: boolean;
  /** Background for the footer extension strip under the input (defaults to theme background). */
  footerExtensionClassName?: string;
}) {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);
  const [connectorMenuOpen, setConnectorMenuOpen] = useState(false);
  const [helpDialogOpen, setHelpDialogOpen] = useState(false);
  const { models } = useModels();
  const { skills } = useSkills();
  const { connectors, isLoading: connectorsLoading } = useConnectors();
  const { thread, isMock } = useThread();
  const { textInput } = usePromptInputController();
  const appliedInitialValueRef = useRef<string | undefined>(undefined);
  const promptRootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!initialValue) return;
    if (appliedInitialValueRef.current === initialValue) return;
    appliedInitialValueRef.current = initialValue;
    textInput.setInput(initialValue);
  }, [initialValue, textInput]);

  const [followups, setFollowups] = useState<string[]>([]);
  const [followupsHidden, setFollowupsHidden] = useState(false);
  const [followupsLoading, setFollowupsLoading] = useState(false);
  const lastGeneratedForAiIdRef = useRef<string | null>(null);
  const wasStreamingRef = useRef(false);
  const messagesRef = useRef(thread.messages);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingSuggestion, setPendingSuggestion] = useState<string | null>(
    null,
  );

  // ---- Slash command picker --------------------------------------------------
  // Tracks the live state of an in-progress `/` token at the trailing caret
  // position. The picker overlay reads from this to decide whether to show.
  //
  // The candidate list is sourced from the global slash-command registry
  // (built-in commands + any team-registered custom commands) filtered by
  // the trailing query. The registry is module-scoped, so we just call
  // `getSlashCommands()` to snapshot it on every render.
  const [slashActive, setSlashActive] = useState(false);
  const [slashQuery, setSlashQuery] = useState("");
  const [slashIndex, setSlashIndex] = useState(0);

  // ---- @-mention (file) picker ---------------------------------------------
  // Mirrors the slash picker, but the trigger is `@` and the candidate list
  // is the user's file library. When a row is selected, the trailing `@query`
  // token is removed from the input and the file is added to `referencedFiles`
  // (rendered as a removable chip above the input, and shipped to the backend
  // in the message's `referencedFiles` on submit).
  const [mentionActive, setMentionActive] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionStart, setMentionStart] = useState(-1);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [referencedFiles, setReferencedFiles] = useState<ReferencedFile[]>([]);
  // Persist the last non-empty mention query so the picker doesn't immediately
  // flash "no files" while the user is still in the middle of typing.
  const lastMentionQueryRef = useRef("");

  // We cap the picker at 50 to keep the dropdown snappy. The backend
  // already supports `q=` for server-side filtering, but client-side
  // filtering is more responsive for typical library sizes.
  const {
    files: libraryFiles,
    isLoading: libraryLoading,
    error: libraryError,
  } = useFiles({ limit: 50 });
  const mentionCandidates = useMemo<FileItem[]>(() => {
    if (!mentionActive) {
      return [];
    }
    const needle = mentionQuery.trim().toLowerCase();
    if (!needle) {
      return libraryFiles;
    }
    return libraryFiles.filter((file) =>
      file.name.toLowerCase().includes(needle),
    );
  }, [mentionActive, mentionQuery, libraryFiles]);
  const mentionTotalRows = mentionActive ? mentionCandidates.length : 0;
  const mentionHasNone = mentionActive && mentionCandidates.length === 0;
  // Empty library state: the picker is up, no query, and the fetch returned
  // no files. We use this to surface a friendlier "no files yet" hint.
  const mentionLibraryEmpty =
    mentionActive &&
    !mentionQuery &&
    !libraryLoading &&
    !libraryError &&
    libraryFiles.length === 0;
  // Track which file is currently in flight so we can avoid double-adding on
  // rapid Enter presses. The id is the library `path` — unique per file.
  const mentionPickingRef = useRef(false);

  // Combined command set: built-in + third-party, deduplicated by id
  // (built-ins win since they are registered first and registry is
  // idempotent on id).
  const slashCommands = useMemo<SlashCommand[]>(() => {
    if (!slashActive) {
      return [];
    }
    const builtins = getBuiltinSlashCommands({
      skills,
      modeLabels: {
        flash: t.inputBox.flashMode,
        thinking: t.inputBox.reasoningMode,
        pro: t.inputBox.proMode,
        ultra: t.inputBox.ultraMode,
      },
      noSkillLabel: t.inputBox.noSkill,
      noSkillDescription: t.inputBox.noSkillDescription,
      modelLabel: t.inputBox.slashCommandModel,
      modelDescription: t.inputBox.slashCommandModelDescription,
      clearLabel: t.inputBox.slashCommandClear,
      clearDescription: t.inputBox.slashCommandClearDescription,
      helpLabel: t.inputBox.slashCommandHelp,
      helpDescription: t.inputBox.slashCommandHelpDescription,
    });
    const custom = getSlashCommands().filter(
      (c) => !builtins.some((b) => b.id === c.id),
    );
    return [...builtins, ...custom];
    // We intentionally re-derive when the user types or when skills/i18n
    // change, so any of these in deps is correct.
  }, [slashActive, skills, t.inputBox]);
  // Filtered view used by the picker rows.
  const slashCandidates = useMemo(
    () => filterSlashCommands(slashQuery, slashCommands),
    [slashCommands, slashQuery],
  );
  const slashTotalRows = slashActive ? slashCandidates.length : 0;
  const slashHasNone = slashActive && slashCandidates.length === 0;

  const contextRef = useRef(context);
  contextRef.current = context;
  const onContextChangeRef = useRef(onContextChange);
  onContextChangeRef.current = onContextChange;

  useEffect(() => {
    if (models.length === 0) {
      return;
    }
    const currentModel = models.find((m) => m.name === contextRef.current.model_name);
    const fallbackModel = currentModel ?? models[0]!;
    const supportsThinking = fallbackModel.supports_thinking ?? false;
    const nextModelName = fallbackModel.name;
    const nextMode = getResolvedMode(contextRef.current.mode, supportsThinking);

    if (
      contextRef.current.model_name === nextModelName &&
      contextRef.current.mode === nextMode
    ) {
      return;
    }

    onContextChangeRef.current?.({
      ...contextRef.current,
      model_name: nextModelName,
      mode: nextMode,
    });
  }, [context.model_name, context.mode, models]);

  const selectedModel = useMemo(() => {
    if (models.length === 0) {
      return undefined;
    }
    return models.find((m) => m.name === context.model_name) ?? models[0];
  }, [context.model_name, models]);

  const resolvedModelName = selectedModel?.name;

  const supportThinking = useMemo(
    () => selectedModel?.supports_thinking ?? false,
    [selectedModel],
  );

  const supportReasoningEffort = useMemo(
    () => selectedModel?.supports_reasoning_effort ?? false,
    [selectedModel],
  );

  const activeConnectors = useMemo(
    () => connectors.filter((connector) => connector.status === "active"),
    [connectors],
  );
  const selectedConnectorId = context.connector_ids?.[0];
  const selectedConnector = useMemo(
    () =>
      selectedConnectorId
        ? activeConnectors.find(
            (connector) => connector.id === selectedConnectorId,
          )
        : undefined,
    [activeConnectors, selectedConnectorId],
  );

  useEffect(() => {
    if (connectorsLoading || !selectedConnectorId || selectedConnector) {
      return;
    }
    onContextChange?.({
      ...context,
      connector_ids: undefined,
    });
  }, [
    connectorsLoading,
    context,
    onContextChange,
    selectedConnector,
    selectedConnectorId,
  ]);

  const handleModelSelect = useCallback(
    (model_name: string) => {
      const model = models.find((m) => m.name === model_name);
      if (!model) {
        return;
      }
      onContextChange?.({
        ...context,
        model_name,
        mode: getResolvedMode(context.mode, model.supports_thinking ?? false),
        reasoning_effort: context.reasoning_effort,
      });
      setModelDialogOpen(false);
    },
    [onContextChange, context, models],
  );

  const handleModeSelect = useCallback(
    (mode: InputMode) => {
      onContextChange?.({
        ...context,
        mode: getResolvedMode(mode, supportThinking),
        reasoning_effort:
          mode === "ultra"
            ? "high"
            : mode === "pro"
              ? "medium"
              : mode === "thinking"
                ? "low"
                : "minimal",
      });
    },
    [onContextChange, context, supportThinking],
  );

  const handleReasoningEffortSelect = useCallback(
    (effort: "minimal" | "low" | "medium" | "high") => {
      onContextChange?.({
        ...context,
        reasoning_effort: effort,
      });
    },
    [onContextChange, context],
  );

  useEffect(() => {
    if (!lockedSkillName || !onContextChange) return;
    if (context.skill_name === lockedSkillName) return;
    onContextChange({
      ...context,
      skill_name: lockedSkillName,
    });
  }, [context, lockedSkillName, onContextChange]);

  const handleSkillSelect = useCallback(
    (skillName: string | undefined) => {
      if (lockedSkillName) {
        if (skillName !== lockedSkillName) return;
      }
      onContextChange?.({
        ...context,
        skill_name: skillName,
      });
      setSkillMenuOpen(false);
      // Focus back to textarea so user can keep typing
      setTimeout(() => {
        const ta = document.querySelector<HTMLTextAreaElement>(
          "textarea[name='message']",
        );
        ta?.focus();
      }, 0);
    },
    [lockedSkillName, onContextChange, context],
  );

  const handleConnectorSelect = useCallback(
    (connectorId: string | undefined) => {
      onContextChange?.({
        ...context,
        connector_ids: connectorId ? [connectorId] : undefined,
      });
      setConnectorMenuOpen(false);
      setTimeout(() => {
        const ta = document.querySelector<HTMLTextAreaElement>(
          "textarea[name='message']",
        );
        ta?.focus();
      }, 0);
    },
    [onContextChange, context],
  );

  const lockedSkillLabel = useMemo(() => {
    if (!lockedSkillName) return lockedSkillName;
    const skill = skills.find((item) => item.name === lockedSkillName);
    return skill?.display_name ?? lockedSkillName;
  }, [lockedSkillName, skills]);

  // Apply the slash command at `slashIndex`. Routes by `kind`:
  //   - skill:   set/clear the active skill via `handleSkillSelect`
  //   - mode:    change the agent mode via `handleModeSelect`
  //   - model:   open the model picker dialog
  //   - clear:   input is already being cleared below
  //   - help:    surface a help dialog (we toast a summary here; richer
  //              help screen can be a follow-up)
  //   - custom:  invoke `command.run(query)`; the command decides what
  //              happens to the input
  const selectSlashCandidateByIndex = useCallback(
    (index: number) => {
      if (!slashActive) {
        return;
      }
      const command = slashCandidates[index];
      if (!command) {
        return;
      }
      // Run the command's effect first; some commands may want to keep
      // the query (e.g. /standup with a date suffix). The default
      // behavior is to clear the input.
      let nextInput: string | null | undefined = "";
      switch (command.kind) {
        case "skill":
          if (!lockedSkillName) {
            handleSkillSelect(command.value ?? undefined);
          }
          break;
        case "mode":
          if (
            command.value === "flash" ||
            command.value === "thinking" ||
            command.value === "pro" ||
            command.value === "ultra"
          ) {
            handleModeSelect(command.value);
          }
          break;
        case "model":
          setModelDialogOpen(true);
          break;
        case "clear":
          // No-op: input is cleared below.
          break;
        case "help":
          setHelpDialogOpen(true);
          break;
        case "custom": {
          // For custom commands, the handler may return the next input.
          if (command.run) {
            const result = command.run(slashQuery);
            if (typeof result === "string") {
              nextInput = result;
            }
          }
          break;
        }
        default: {
          // Exhaustive check — if a new kind is added, this will fail
          // to compile until we handle it.
          const _exhaustive: never = command.kind;
          void _exhaustive;
        }
      }
      if (nextInput === undefined || nextInput === null) {
        textInput.setInput("");
      } else {
        textInput.setInput(nextInput);
      }
      setSlashActive(false);
      setSlashQuery("");
      setSlashIndex(0);
    },
    [
      handleSkillSelect,
      handleModeSelect,
      slashActive,
      slashCandidates,
      slashQuery,
      textInput,
    ],
  );

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      if (status === "streaming") {
        onStop?.();
        return;
      }
      // Merge in the @-picked files. The form-level `message` only knows
      // about the textarea text and the *uploaded* attachments; the
      // mention-picker state lives here, so we tack it on at submit time.
      // The recipient (`useThreadStream`) reads `referencedFiles` and
      // forwards them as `additionalKwargs.referenced_files`.
      //
      // IMPORTANT: clear the chip strip *eagerly*, before the message even
      // goes out. The message-list will surface the same picks in the
      // human-message bubble (the optimistic message copies
      // `additional_kwargs.referenced_files` straight through), so a
      // delayed clear here causes a 1-frame "chip above input + chip in
      // bubble" duplicate that the eye picks up on a new chat.
      const submittedReferencedFiles = referencedFiles;
      if (submittedReferencedFiles.length > 0) {
        setReferencedFiles([]);
      }
      const enrichedMessage: PromptInputMessage = {
        ...message,
        referencedFiles:
          submittedReferencedFiles.length > 0
            ? submittedReferencedFiles
            : undefined,
      };
      if (
        !enrichedMessage.text.trim() &&
        enrichedMessage.files.length === 0 &&
        (enrichedMessage.referencedFiles?.length ?? 0) === 0
      ) {
        return;
      }
      setFollowups([]);
      setFollowupsHidden(false);
      setFollowupsLoading(false);

      // Guard against submitting before the initial model auto-selection
      // effect has flushed thread settings to storage/state.
      if (resolvedModelName && context.model_name !== resolvedModelName) {
        onContextChange?.({
          ...context,
          model_name: resolvedModelName,
          mode: getResolvedMode(
            context.mode,
            selectedModel?.supports_thinking ?? false,
          ),
        });
        return new Promise<void>((resolve, reject) => {
          setTimeout(() => {
            Promise.resolve(onSubmit?.(enrichedMessage))
              .then(() => {
                resolve();
              })
              .catch((err: unknown) => {
                // Restore the chip strip on failure so the user can
                // retry without re-@ing the files.
                if (submittedReferencedFiles.length > 0) {
                  setReferencedFiles(submittedReferencedFiles);
                }
                reject(err instanceof Error ? err : new Error(String(err)));
              });
          }, 0);
        });
      }

      const result = onSubmit?.(enrichedMessage);
      if (result instanceof Promise) {
        return result.catch((err: unknown) => {
          // Restore the chip strip on failure so the user can retry
          // without re-@ing the files.
          if (submittedReferencedFiles.length > 0) {
            setReferencedFiles(submittedReferencedFiles);
          }
          throw err instanceof Error ? err : new Error(String(err));
        });
      }
      return result;
    },
    [
      context,
      onContextChange,
      onSubmit,
      onStop,
      referencedFiles,
      resolvedModelName,
      selectedModel?.supports_thinking,
      status,
    ],
  );

  const requestFormSubmit = useCallback(() => {
    const form = promptRootRef.current?.querySelector("form");
    form?.requestSubmit();
  }, []);

  const handleFollowupClick = useCallback(
    (suggestion: string) => {
      if (status === "streaming") {
        return;
      }
      const current = (textInput.value ?? "").trim();
      if (current) {
        setPendingSuggestion(suggestion);
        setConfirmOpen(true);
        return;
      }
      textInput.setInput(suggestion);
      setFollowupsHidden(true);
      setTimeout(() => requestFormSubmit(), 0);
    },
    [requestFormSubmit, status, textInput],
  );

  const confirmReplaceAndSend = useCallback(() => {
    if (!pendingSuggestion) {
      setConfirmOpen(false);
      return;
    }
    textInput.setInput(pendingSuggestion);
    setFollowupsHidden(true);
    setConfirmOpen(false);
    setPendingSuggestion(null);
    setTimeout(() => requestFormSubmit(), 0);
  }, [pendingSuggestion, requestFormSubmit, textInput]);

  const confirmAppendAndSend = useCallback(() => {
    if (!pendingSuggestion) {
      setConfirmOpen(false);
      return;
    }
    const current = (textInput.value ?? "").trim();
    const next = current
      ? `${current}\n${pendingSuggestion}`
      : pendingSuggestion;
    textInput.setInput(next);
    setFollowupsHidden(true);
    setConfirmOpen(false);
    setPendingSuggestion(null);
    setTimeout(() => requestFormSubmit(), 0);
  }, [pendingSuggestion, requestFormSubmit, textInput]);

  const showFollowups =
    !disabled &&
    !isWelcomeMode &&
    !followupsHidden &&
    (followupsLoading || followups.length > 0);

  useEffect(() => {
    onFollowupsVisibilityChange?.(showFollowups);
  }, [onFollowupsVisibilityChange, showFollowups]);

  useEffect(() => {
    return () => onFollowupsVisibilityChange?.(false);
  }, [onFollowupsVisibilityChange]);

  useEffect(() => {
    messagesRef.current = thread.messages;
  }, [thread.messages]);

  // Keep the slash picker in sync with the current text input value.
  useEffect(() => {
    const match = detectSlashCommand(textInput.value ?? "");
    if (!match.active) {
      if (slashActive) {
        setSlashActive(false);
        setSlashQuery("");
        setSlashIndex(0);
      }
      return;
    }
    if (!slashActive) {
      setSlashActive(true);
    }
    if (match.query !== slashQuery) {
      setSlashQuery(match.query);
      // Reset highlight to first match when the query changes.
      setSlashIndex(0);
    }
  }, [textInput.value, slashActive, slashQuery]);

  // Keep the @-mention picker in sync with the current text input value.
  // We deliberately give `/` priority over `@` — if the user is typing a
  // slash command the mention picker stays closed to avoid double overlays.
  useEffect(() => {
    const match = detectMention(textInput.value ?? "");
    if (!match.active || slashActive) {
      if (mentionActive) {
        setMentionActive(false);
        setMentionQuery("");
        setMentionStart(-1);
        setMentionIndex(0);
      }
      return;
    }
    if (!mentionActive) {
      setMentionActive(true);
    }
    if (match.query !== mentionQuery) {
      setMentionQuery(match.query);
      lastMentionQueryRef.current = match.query;
      // Reset highlight to first match when the query changes.
      setMentionIndex(0);
    }
    if (match.start !== mentionStart) {
      setMentionStart(match.start);
    }
  }, [textInput.value, mentionActive, mentionQuery, mentionStart, slashActive]);

  // When the candidate list shrinks (e.g. user types more), clamp the
  // highlighted mention index so it always points at a valid item.
  useEffect(() => {
    if (!mentionActive) {
      return;
    }
    if (mentionIndex >= mentionTotalRows) {
      setMentionIndex(mentionTotalRows > 0 ? 0 : -1);
    }
  }, [mentionActive, mentionTotalRows, mentionIndex]);

  // Remove a referenced file by id (the file's library `path`).
  const removeReferencedFile = useCallback((id: string) => {
    setReferencedFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  // Apply the mention candidate at `mentionIndex`. Removes the typed
  // `@<query>` token from the text input (replacing with a single trailing
  // space so the user can keep typing their sentence), and adds the
  // picked file to the referenced-files chip strip.
  const selectMentionCandidateByIndex = useCallback(
    (index: number) => {
      if (!mentionActive || mentionPickingRef.current) {
        return;
      }
      const file = mentionCandidates[index];
      if (!file) {
        return;
      }
      mentionPickingRef.current = true;
      try {
        const current = textInput.value ?? "";
        // Drop the `@<query>` token, keep everything before and after it.
        // We always append a single space after the deletion so the user's
        // cursor doesn't sit glued to whatever the previous character was.
        const before = current.slice(0, mentionStart);
        const after = current.slice(mentionStart + 1 + mentionQuery.length);
        // If the char right after our token is a space, don't add another
        // one — keeps double-spaces out of the input.
        const needsSpace = after.length > 0 && !/^\s/.test(after);
        const trailing = needsSpace ? " " : "";
        const newValue = before + trailing + after;
        textInput.setInput(newValue);
        // Focus the textarea and put the caret at the end of the trailing
        // space we just inserted, so the user can keep typing naturally.
        requestAnimationFrame(() => {
          const ta = document.querySelector<HTMLTextAreaElement>(
            "textarea[name='message']",
          );
          if (ta) {
            const caret = (before + trailing).length;
            ta.focus();
            ta.setSelectionRange(caret, caret);
          }
        });
        setReferencedFiles((prev) => {
          if (prev.some((f) => f.id === file.id)) {
            return prev;
          }
          const ref: ReferencedFile = {
            id: file.id,
            name: file.name,
            path: file.path,
            mime_type: file.mime_type,
            extension: file.extension,
            size: file.size,
          };
          return [...prev, ref];
        });
        // Close the picker; the `@<query>` is gone so it would auto-close
        // on the next effect tick anyway, but doing it eagerly avoids a
        // 1-frame flash of the overlay.
        setMentionActive(false);
        setMentionQuery("");
        setMentionStart(-1);
        setMentionIndex(0);
      } finally {
        // Release the lock on the next tick so rapid Enter presses after a
        // pick don't get dropped.
        requestAnimationFrame(() => {
          mentionPickingRef.current = false;
        });
      }
    },
    [mentionActive, mentionCandidates, mentionQuery, mentionStart, textInput],
  );

  // When the candidate list shrinks (e.g. user types more), clamp the
  // highlighted index so it always points at a valid item.
  useEffect(() => {
    if (!slashActive) {
      return;
    }
    // slashTotalRows includes the reserved "Auto" row at index 0.
    if (slashIndex >= slashTotalRows) {
      setSlashIndex(slashTotalRows > 0 ? 0 : -1);
    }
  }, [slashActive, slashTotalRows, slashIndex]);

  // Capture-phase keydown handler. The textarea has its own onKeyDown that
  // calls `preventDefault()` on Enter, so we need capture to run first and
  // decide whether the keypress should belong to the slash picker instead.
  useEffect(() => {
    const root = promptRootRef.current;
    if (!root) {
      return;
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (!slashActive) {
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        e.stopPropagation();
        setSlashIndex((prev) => nextPickerIndex(prev, slashTotalRows, "down"));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        e.stopPropagation();
        setSlashIndex((prev) => nextPickerIndex(prev, slashTotalRows, "up"));
      } else if (e.key === "Enter") {
        // Always intercept Enter while the picker is open — if there are
        // zero matches the user almost certainly doesn't want to send `/foo`
        // as a literal message.
        e.preventDefault();
        e.stopPropagation();
        const target = slashIndex === -1 ? 0 : slashIndex;
        // If everything is filtered out, fall back to "Auto" (index 0).
        if (slashHasNone) {
          selectSlashCandidateByIndex(0);
        } else {
          selectSlashCandidateByIndex(target);
        }
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        textInput.setInput("");
        setSlashActive(false);
        setSlashQuery("");
        setSlashIndex(0);
      }
    };
    root.addEventListener("keydown", onKeyDown, true);
    return () => {
      root.removeEventListener("keydown", onKeyDown, true);
    };
  }, [
    promptRootRef,
    selectSlashCandidateByIndex,
    slashActive,
    slashHasNone,
    slashIndex,
    slashTotalRows,
    textInput,
  ]);

  // Capture-phase keydown handler for the @-mention picker. Same pattern as
  // the slash picker: run before the textarea's own Enter handler so the
  // picker can claim the keystroke.
  useEffect(() => {
    const root = promptRootRef.current;
    if (!root) {
      return;
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (!mentionActive) {
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        e.stopPropagation();
        setMentionIndex((prev) =>
          nextPickerIndex(prev, mentionTotalRows, "down"),
        );
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        e.stopPropagation();
        setMentionIndex((prev) =>
          nextPickerIndex(prev, mentionTotalRows, "up"),
        );
      } else if (e.key === "Enter") {
        // Intercept Enter only when there's something to pick; if the
        // library is empty or filtered to zero, let the textarea submit
        // the message as usual.
        if (mentionHasNone) {
          // Close the picker so the user can submit their message.
          e.preventDefault();
          e.stopPropagation();
          setMentionActive(false);
          setMentionQuery("");
          setMentionStart(-1);
          setMentionIndex(0);
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        const target = mentionIndex === -1 ? 0 : mentionIndex;
        selectMentionCandidateByIndex(target);
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        // Escape removes the typed `@<query>` so the user lands back on
        // their message draft, same as it works for the slash picker.
        const current = textInput.value ?? "";
        const before = current.slice(0, mentionStart);
        const after = current.slice(mentionStart + 1 + mentionQuery.length);
        textInput.setInput(before + after);
        setMentionActive(false);
        setMentionQuery("");
        setMentionStart(-1);
        setMentionIndex(0);
      }
    };
    root.addEventListener("keydown", onKeyDown, true);
    return () => {
      root.removeEventListener("keydown", onKeyDown, true);
    };
  }, [
    promptRootRef,
    selectMentionCandidateByIndex,
    mentionActive,
    mentionHasNone,
    mentionIndex,
    mentionTotalRows,
    mentionQuery,
    mentionStart,
    textInput,
  ]);

  useEffect(() => {
    const streaming = status === "streaming";
    const wasStreaming = wasStreamingRef.current;
    wasStreamingRef.current = streaming;
    if (!wasStreaming || streaming) {
      return;
    }

    if (disabled || isMock) {
      return;
    }

    const lastAi = [...messagesRef.current]
      .reverse()
      .find((m) => m.type === "ai");
    const lastAiId = lastAi?.id ?? null;
    if (!lastAiId || lastAiId === lastGeneratedForAiIdRef.current) {
      return;
    }
    lastGeneratedForAiIdRef.current = lastAiId;

    const recent = messagesRef.current
      .filter((m) => m.type === "human" || m.type === "ai")
      .map((m) => {
        const role = m.type === "human" ? "user" : "assistant";
        const content = textOfMessage(m) ?? "";
        return { role, content };
      })
      .filter((m) => m.content.trim().length > 0)
      .slice(-6);

    if (recent.length === 0) {
      return;
    }

    const controller = new AbortController();
    setFollowupsHidden(false);
    setFollowupsLoading(true);
    setFollowups([]);

    fetch(`${getBackendBaseURL()}/api/threads/${threadId}/suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: recent,
        n: 3,
        model_name: context.model_name ?? undefined,
      }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          return { suggestions: [] as string[] };
        }
        return (await res.json()) as { suggestions?: string[] };
      })
      .then((data) => {
        const suggestions = (data.suggestions ?? [])
          .map((s) => (typeof s === "string" ? s.trim() : ""))
          .filter((s) => s.length > 0)
          .slice(0, 5);
        setFollowups(suggestions);
      })
      .catch(() => {
        setFollowups([]);
      })
      .finally(() => {
        setFollowupsLoading(false);
      });

    return () => controller.abort();
  }, [context.model_name, disabled, isMock, status, threadId]);

  return (
    <div
      ref={promptRootRef}
      className={cn(
        "relative flex flex-col",
        isWelcomeMode ? "gap-4" : "gap-2",
      )}
    >
      {showFollowups && (
        <div className="flex items-center justify-center pb-1">
          <div className="flex items-center gap-2">
            {followupsLoading ? (
              <div className="text-muted-foreground bg-background/80 rounded-full border px-4 py-1.5 text-xs backdrop-blur-sm">
                {t.inputBox.followupLoading}
              </div>
            ) : (
              <Suggestions className="w-fit items-center">
                {followups.map((s) => (
                  <Suggestion
                    key={s}
                    className="py-1.5"
                    suggestion={s}
                    onClick={() => handleFollowupClick(s)}
                  />
                ))}
                <Button
                  aria-label={t.common.close}
                  className="text-muted-foreground h-auto cursor-pointer rounded-full px-2.5 py-1.5 text-xs font-normal"
                  variant="outline"
                  size="sm"
                  type="button"
                  onClick={() => setFollowupsHidden(true)}
                >
                  <XIcon className="size-4" />
                </Button>
              </Suggestions>
            )}
          </div>
        </div>
      )}
      {slashActive && (
        <div
          aria-label={t.inputBox.slashSkillPickerTitle}
          className="bg-popover text-popover-foreground absolute right-0 bottom-full left-0 z-50 mb-2 max-h-72 overflow-y-auto rounded-lg border p-1 shadow-md"
          data-testid="slash-skill-picker"
          role="listbox"
        >
          <div className="text-muted-foreground flex items-center px-2 py-1 text-[11px] font-medium">
            <span>{t.inputBox.slashSkillPickerTitle}</span>
            <span className="ml-2 font-normal">
              {slashQuery ? `/ ${slashQuery}` : "/"}
            </span>
          </div>
          {slashCandidates.length === 0 ? (
            <div className="text-muted-foreground px-2 py-2 text-xs">
              {t.inputBox.slashSkillPickerEmpty}
            </div>
          ) : (
            slashCandidates.map((command, i) => (
              <SlashPickerRow
                key={command.id}
                active={slashIndex === i}
                description={command.description}
                icon={slashCommandIcon(command.kind)}
                label={command.label}
                onHover={() => setSlashIndex(i)}
                onSelect={() => selectSlashCandidateByIndex(i)}
                testId={`slash-cmd-row-${command.id}`}
              />
            ))
          )}
          <div className="text-muted-foreground border-t px-2 pt-1 pb-0.5 text-[10px]">
            {t.inputBox.slashSkillPickerHint}
          </div>
        </div>
      )}
      {mentionActive && (
        <div
          aria-label={t.inputBox.mentionFilePickerTitle}
          className="bg-popover text-popover-foreground absolute right-0 bottom-full left-0 z-50 mb-2 max-h-72 overflow-y-auto rounded-lg border p-1 shadow-md"
          data-testid="mention-file-picker"
          role="listbox"
        >
          <div className="text-muted-foreground flex items-center px-2 py-1 text-[11px] font-medium">
            <AtSignIcon className="mr-1 size-3" />
            <span>{t.inputBox.mentionFilePickerTitle}</span>
            <span className="ml-2 font-normal">
              {mentionQuery ? `@ ${mentionQuery}` : "@"}
            </span>
          </div>
          {libraryLoading ? (
            <div className="text-muted-foreground flex items-center gap-2 px-2 py-2 text-xs">
              <Loader2Icon className="size-3 animate-spin" />
              <span>{t.inputBox.mentionFilePickerLoading}</span>
            </div>
          ) : libraryError ? (
            <div className="text-muted-foreground px-2 py-2 text-xs">
              {t.inputBox.mentionFilePickerError}
            </div>
          ) : mentionLibraryEmpty ? (
            <div className="text-muted-foreground px-2 py-2 text-xs">
              {t.inputBox.mentionFilePickerNoFiles}
            </div>
          ) : mentionCandidates.length === 0 ? (
            <div className="text-muted-foreground px-2 py-2 text-xs">
              {t.inputBox.mentionFilePickerEmpty}
            </div>
          ) : (
            mentionCandidates.map((file, i) => (
              <MentionFilePickerRow
                key={file.id}
                active={mentionIndex === i}
                file={file}
                onHover={() => setMentionIndex(i)}
                onSelect={() => selectMentionCandidateByIndex(i)}
                testId={`mention-file-row-${file.id}`}
              />
            ))
          )}
          <div className="text-muted-foreground border-t px-2 pt-1 pb-0.5 text-[10px]">
            {t.inputBox.mentionFilePickerHint}
          </div>
        </div>
      )}
      <PromptInput
        className={cn(
          "bg-background/85 rounded-2xl backdrop-blur-sm transition-all duration-300 ease-out *:data-[slot='input-group']:rounded-2xl",
          className,
        )}
        disabled={disabled}
        globalDrop
        multiple
        onSubmit={handleSubmit}
        {...props}
      >
        {extraHeader && (
          <div className="absolute top-0 right-0 left-0 z-10">
            <div className="absolute right-0 bottom-0 left-0 flex items-center justify-center">
              {extraHeader}
            </div>
          </div>
        )}
        <PromptInputAttachments>
          {(attachment) => <PromptInputAttachment data={attachment} />}
        </PromptInputAttachments>
        {referencedFiles.length > 0 && (
          <div
            aria-label={t.inputBox.mentionFilePickerTitle}
            className="flex w-full flex-wrap items-center gap-1.5 px-3 pt-2"
            data-testid="referenced-file-chips"
          >
            {referencedFiles.map((file) => (
              <ReferencedFileChip
                key={file.id}
                file={file}
                onRemove={() => removeReferencedFile(file.id)}
                removeLabel={t.inputBox.referencedFileChipRemove}
              />
            ))}
          </div>
        )}
        <PromptInputBody className="absolute top-0 right-0 left-0 z-3">
          <PromptInputTextarea
            className={cn("size-full")}
            disabled={disabled}
            placeholder={t.inputBox.placeholder}
            autoFocus={autoFocus}
          />
        </PromptInputBody>
        <PromptInputFooter className="flex">
          <PromptInputTools>
            {/* TODO: Add more connectors here
          <PromptInputActionMenu>
            <PromptInputActionMenuTrigger className="px-2!" />
            <PromptInputActionMenuContent>
              <PromptInputActionAddAttachments
                label={t.inputBox.addAttachments}
              />
            </PromptInputActionMenuContent>
          </PromptInputActionMenu> */}
            <AddAttachmentsButton className="px-2!" />
            <PromptInputActionMenu>
              <ModeHoverGuide
                mode={
                  context.mode === "flash" ||
                  context.mode === "thinking" ||
                  context.mode === "pro" ||
                  context.mode === "ultra"
                    ? context.mode
                    : "flash"
                }
              >
                <PromptInputActionMenuTrigger className="gap-1! px-2!">
                  <div>
                    {context.mode === "flash" && <ZapIcon className="size-3" />}
                    {context.mode === "thinking" && (
                      <LightbulbIcon className="size-3" />
                    )}
                    {context.mode === "pro" && (
                      <GraduationCapIcon className="size-3" />
                    )}
                    {context.mode === "ultra" && (
                      <RocketIcon className="size-3 text-[#dabb5e]" />
                    )}
                  </div>
                  <div
                    className={cn(
                      "text-xs font-normal",
                      context.mode === "ultra" ? "golden-text" : "",
                    )}
                  >
                    {(context.mode === "flash" && t.inputBox.flashMode) ||
                      (context.mode === "thinking" &&
                        t.inputBox.reasoningMode) ||
                      (context.mode === "pro" && t.inputBox.proMode) ||
                      (context.mode === "ultra" && t.inputBox.ultraMode)}
                  </div>
                </PromptInputActionMenuTrigger>
              </ModeHoverGuide>
              <PromptInputActionMenuContent className="w-80">
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="text-muted-foreground text-xs">
                    {t.inputBox.mode}
                  </DropdownMenuLabel>
                  <PromptInputActionMenu>
                    <PromptInputActionMenuItem
                      className={cn(
                        context.mode === "flash"
                          ? "text-accent-foreground"
                          : "text-muted-foreground/65",
                      )}
                      onSelect={() => handleModeSelect("flash")}
                    >
                      <div className="flex flex-col gap-2">
                        <div className="flex items-center gap-1 font-bold">
                          <ZapIcon
                            className={cn(
                              "mr-2 size-4",
                              context.mode === "flash" &&
                                "text-accent-foreground",
                            )}
                          />
                          {t.inputBox.flashMode}
                        </div>
                        <div className="pl-7 text-xs">
                          {t.inputBox.flashModeDescription}
                        </div>
                      </div>
                      {context.mode === "flash" ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </PromptInputActionMenuItem>
                    {supportThinking && (
                      <PromptInputActionMenuItem
                        className={cn(
                          context.mode === "thinking"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleModeSelect("thinking")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            <LightbulbIcon
                              className={cn(
                                "mr-2 size-4",
                                context.mode === "thinking" &&
                                  "text-accent-foreground",
                              )}
                            />
                            {t.inputBox.reasoningMode}
                          </div>
                          <div className="pl-7 text-xs">
                            {t.inputBox.reasoningModeDescription}
                          </div>
                        </div>
                        {context.mode === "thinking" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                    )}
                    <PromptInputActionMenuItem
                      className={cn(
                        context.mode === "pro"
                          ? "text-accent-foreground"
                          : "text-muted-foreground/65",
                      )}
                      onSelect={() => handleModeSelect("pro")}
                    >
                      <div className="flex flex-col gap-2">
                        <div className="flex items-center gap-1 font-bold">
                          <GraduationCapIcon
                            className={cn(
                              "mr-2 size-4",
                              context.mode === "pro" &&
                                "text-accent-foreground",
                            )}
                          />
                          {t.inputBox.proMode}
                        </div>
                        <div className="pl-7 text-xs">
                          {t.inputBox.proModeDescription}
                        </div>
                      </div>
                      {context.mode === "pro" ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </PromptInputActionMenuItem>
                    <PromptInputActionMenuItem
                      className={cn(
                        context.mode === "ultra"
                          ? "text-accent-foreground"
                          : "text-muted-foreground/65",
                      )}
                      onSelect={() => handleModeSelect("ultra")}
                    >
                      <div className="flex flex-col gap-2">
                        <div className="flex items-center gap-1 font-bold">
                          <RocketIcon
                            className={cn(
                              "mr-2 size-4",
                              context.mode === "ultra" && "text-[#dabb5e]",
                            )}
                          />
                          <div
                            className={cn(
                              context.mode === "ultra" && "golden-text",
                            )}
                          >
                            {t.inputBox.ultraMode}
                          </div>
                        </div>
                        <div className="pl-7 text-xs">
                          {t.inputBox.ultraModeDescription}
                        </div>
                      </div>
                      {context.mode === "ultra" ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </PromptInputActionMenuItem>
                  </PromptInputActionMenu>
                </DropdownMenuGroup>
              </PromptInputActionMenuContent>
            </PromptInputActionMenu>
            {supportReasoningEffort && context.mode !== "flash" && (
              <PromptInputActionMenu>
                <PromptInputActionMenuTrigger className="gap-1! px-2!">
                  <div className="text-xs font-normal">
                    {t.inputBox.reasoningEffort}:
                    {" " +
                      (context.reasoning_effort === "minimal"
                        ? t.inputBox.reasoningEffortMinimal
                        : context.reasoning_effort === "low"
                          ? t.inputBox.reasoningEffortLow
                          : context.reasoning_effort === "high"
                            ? t.inputBox.reasoningEffortHigh
                            : t.inputBox.reasoningEffortMedium)}
                  </div>
                </PromptInputActionMenuTrigger>
                <PromptInputActionMenuContent className="w-48">
                  <DropdownMenuGroup>
                    <DropdownMenuLabel className="text-muted-foreground text-xs">
                      {t.inputBox.reasoningEffort}
                    </DropdownMenuLabel>
                    <PromptInputActionMenu>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "minimal"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("minimal")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortMinimal}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortMinimalDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "minimal" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "low"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("low")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortLow}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortLowDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "low" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "medium" ||
                            !context.reasoning_effort
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("medium")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortMedium}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortMediumDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "medium" ||
                        !context.reasoning_effort ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      <PromptInputActionMenuItem
                        className={cn(
                          context.reasoning_effort === "high"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleReasoningEffortSelect("high")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            {t.inputBox.reasoningEffortHigh}
                          </div>
                          <div className="pl-2 text-xs">
                            {t.inputBox.reasoningEffortHighDescription}
                          </div>
                        </div>
                        {context.reasoning_effort === "high" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                    </PromptInputActionMenu>
                  </DropdownMenuGroup>
                </PromptInputActionMenuContent>
              </PromptInputActionMenu>
            )}
            {activeConnectors.length > 0 && (
              <PromptInputActionMenu
                open={connectorMenuOpen}
                onOpenChange={setConnectorMenuOpen}
              >
                <PromptInputActionMenuTrigger className="gap-1! px-2!">
                  <DatabaseIcon className="size-3" />
                  <div className="max-w-[100px] truncate text-xs font-normal">
                    {selectedConnector
                      ? (selectedConnector.display_name ??
                        selectedConnector.name)
                      : t.inputBox.connector}
                  </div>
                  {selectedConnectorId && (
                    <span
                      className="ml-1 inline-flex cursor-pointer items-center"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleConnectorSelect(undefined);
                      }}
                      onPointerDown={(e) => e.stopPropagation()}
                    >
                      <XIcon className="text-muted-foreground hover:text-foreground size-3" />
                    </span>
                  )}
                </PromptInputActionMenuTrigger>
                <PromptInputActionMenuContent className="w-72">
                  <DropdownMenuGroup>
                    <DropdownMenuLabel className="text-muted-foreground text-xs">
                      {t.inputBox.connector}
                    </DropdownMenuLabel>
                    <PromptInputActionMenu>
                      <PromptInputActionMenuItem
                        className={cn(
                          !selectedConnectorId
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleConnectorSelect(undefined)}
                      >
                        <div className="flex flex-col gap-1">
                          <div className="text-xs">
                            {t.inputBox.noConnector}
                          </div>
                          <div className="text-muted-foreground text-[11px]">
                            {t.inputBox.noConnectorDescription}
                          </div>
                        </div>
                        {!selectedConnectorId ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                      {activeConnectors.map((connector) => (
                        <PromptInputActionMenuItem
                          key={connector.id}
                          className={cn(
                            selectedConnectorId === connector.id
                              ? "text-accent-foreground"
                              : "text-muted-foreground/65",
                          )}
                          onSelect={() => handleConnectorSelect(connector.id)}
                        >
                          <div className="flex min-w-0 flex-col gap-1">
                            <div className="truncate text-xs">
                              {connector.display_name ?? connector.name}
                            </div>
                            <div className="text-muted-foreground truncate text-[11px]">
                              {connector.type}
                              {typeof connector.config.database === "string"
                                ? ` / ${connector.config.database}`
                                : ""}
                            </div>
                          </div>
                          {selectedConnectorId === connector.id ? (
                            <CheckIcon className="ml-auto size-4" />
                          ) : (
                            <div className="ml-auto size-4" />
                          )}
                        </PromptInputActionMenuItem>
                      ))}
                    </PromptInputActionMenu>
                  </DropdownMenuGroup>
                </PromptInputActionMenuContent>
              </PromptInputActionMenu>
            )}
            {lockedSkillName ? (
              <div
                className="inline-flex h-8 max-w-[200px] items-center gap-1.5 rounded-md border border-violet-200/80 bg-violet-50 px-2.5 text-xs font-normal text-violet-900"
                title={lockedSkillName}
              >
                <WrenchIcon className="size-3 shrink-0" />
                <span className="truncate">{lockedSkillLabel}</span>
              </div>
            ) : (
              skills.length > 0 && (
                <PromptInputActionMenu
                  open={skillMenuOpen}
                  onOpenChange={setSkillMenuOpen}
                >
                  <PromptInputActionMenuTrigger className="gap-1! px-2!">
                    <WrenchIcon className="size-3" />
                    <div className="max-w-[100px] truncate text-xs font-normal">
                      {context.skill_name
                        ? ((
                            skills.find(
                              (s) => s.name === context.skill_name,
                            ) as
                              | { name: string; display_name: string | null }
                              | undefined
                          )?.display_name ??
                          (
                            skills.find(
                              (s) => s.name === context.skill_name,
                            ) as { name: string } | undefined
                          )?.name ??
                          context.skill_name)
                        : t.inputBox.skill}
                    </div>
                    {context.skill_name && (
                      <span
                        className="ml-1 inline-flex cursor-pointer items-center"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleSkillSelect(undefined);
                        }}
                        onPointerDown={(e) => e.stopPropagation()}
                      >
                        <XIcon className="text-muted-foreground hover:text-foreground size-3" />
                      </span>
                    )}
                  </PromptInputActionMenuTrigger>
                  <PromptInputActionMenuContent className="w-70">
                    <DropdownMenuGroup>
                      <DropdownMenuLabel className="text-muted-foreground text-xs">
                        {t.inputBox.skill}
                      </DropdownMenuLabel>
                      <PromptInputActionMenu>
                        <PromptInputActionMenuItem
                          className={cn(
                            !context.skill_name
                              ? "text-accent-foreground"
                              : "text-muted-foreground/65",
                          )}
                          onSelect={() => handleSkillSelect(undefined)}
                        >
                          <div className="flex items-center gap-1 text-xs">
                            {t.inputBox.noSkill}
                          </div>
                          {!context.skill_name ? (
                            <CheckIcon className="ml-auto size-4" />
                          ) : (
                            <div className="ml-auto size-4" />
                          )}
                        </PromptInputActionMenuItem>
                        {skills
                          .filter((s) => s.enabled)
                          .map((skill) => (
                            <PromptInputActionMenuItem
                              key={skill.name}
                              className={cn(
                                context.skill_name === skill.name
                                  ? "text-accent-foreground"
                                  : "text-muted-foreground/65",
                              )}
                              onSelect={() => handleSkillSelect(skill.name)}
                            >
                              <div className="flex items-center gap-1 text-xs">
                                {skill.display_name ?? skill.name}
                              </div>
                              {context.skill_name === skill.name ? (
                                <CheckIcon className="ml-auto size-4" />
                              ) : (
                                <div className="ml-auto size-4" />
                              )}
                            </PromptInputActionMenuItem>
                          ))}
                      </PromptInputActionMenu>
                    </DropdownMenuGroup>
                  </PromptInputActionMenuContent>
                </PromptInputActionMenu>
              )
            )}
          </PromptInputTools>
          <PromptInputTools className="gap-2">
            <ModelSelector
              open={modelDialogOpen}
              onOpenChange={setModelDialogOpen}
            >
              <ModelSelectorTrigger asChild>
                <PromptInputButton className="max-w-[120px]">
                  <div className="flex min-w-0 flex-col items-start text-left">
                    <ModelSelectorName className="text-xs font-normal">
                      {selectedModel?.display_name}
                    </ModelSelectorName>
                  </div>
                </PromptInputButton>
              </ModelSelectorTrigger>
              <ModelSelectorContent>
                <ModelSelectorInput placeholder={t.inputBox.searchModels} />
                <ModelSelectorList>
                  {models.map((m) => (
                    <ModelSelectorItem
                      key={m.name}
                      value={m.name}
                      onSelect={() => handleModelSelect(m.name)}
                    >
                      <div className="flex min-w-0 flex-1 flex-col">
                        <ModelSelectorName>{m.display_name}</ModelSelectorName>
                        <span className="text-muted-foreground truncate text-[10px]">
                          {m.model}
                        </span>
                      </div>
                      {m.name === context.model_name ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </ModelSelectorItem>
                  ))}
                </ModelSelectorList>
              </ModelSelectorContent>
            </ModelSelector>
            <PromptInputSubmit
              className="rounded-full"
              disabled={disabled}
              variant="outline"
              status={status}
            />
          </PromptInputTools>
        </PromptInputFooter>
        {!isWelcomeMode && (
          <div
            className={cn(
              "bg-background absolute right-0 -bottom-[17px] left-0 z-0 h-4",
              footerExtensionClassName,
            )}
          />
        )}
      </PromptInput>

      {isWelcomeMode &&
        showWelcomeSuggestions &&
        searchParams.get("mode") !== "skill" && (
          <div className="flex items-center justify-center pt-2">
            <SuggestionList />
          </div>
        )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.inputBox.followupConfirmTitle}</DialogTitle>
            <DialogDescription>
              {t.inputBox.followupConfirmDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button variant="secondary" onClick={confirmAppendAndSend}>
              {t.inputBox.followupConfirmAppend}
            </Button>
            <Button onClick={confirmReplaceAndSend}>
              {t.inputBox.followupConfirmReplace}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={helpDialogOpen} onOpenChange={setHelpDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.inputBox.slashCommandHelpTitle}</DialogTitle>
            <DialogDescription>
              {t.inputBox.slashCommandHelpIntro}
            </DialogDescription>
          </DialogHeader>
          <ul className="text-muted-foreground space-y-1 text-xs">
            <li>{t.inputBox.slashCommandHelpSkillRow}</li>
            <li>{t.inputBox.slashCommandHelpModeRow}</li>
            <li>{t.inputBox.slashCommandHelpModelRow}</li>
            <li>{t.inputBox.slashCommandHelpClearRow}</li>
          </ul>
          <DialogFooter>
            <Button onClick={() => setHelpDialogOpen(false)}>
              {t.common.close}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SuggestionList() {
  const { t } = useI18n();
  const { textInput } = usePromptInputController();
  const handleSuggestionClick = useCallback(
    (prompt: string | undefined) => {
      if (!prompt) return;
      textInput.setInput(prompt);
      setTimeout(() => {
        const textarea = document.querySelector<HTMLTextAreaElement>(
          "textarea[name='message']",
        );
        if (textarea) {
          const selStart = prompt.indexOf("[");
          const selEnd = prompt.indexOf("]");
          if (selStart !== -1 && selEnd !== -1) {
            textarea.setSelectionRange(selStart, selEnd + 1);
            textarea.focus();
          }
        }
      }, 500);
    },
    [textInput],
  );
  return (
    <Suggestions className="min-h-16 w-fit items-start">
      <ConfettiButton
        className="text-muted-foreground cursor-pointer rounded-full px-4 text-xs font-normal"
        variant="outline"
        size="sm"
        onClick={() => handleSuggestionClick(t.inputBox.surpriseMePrompt)}
      >
        <SparklesIcon className="size-4" /> {t.inputBox.surpriseMe}
      </ConfettiButton>
      {t.inputBox.suggestions.map((suggestion) => (
        <Suggestion
          key={suggestion.suggestion}
          icon={suggestion.icon}
          suggestion={suggestion.suggestion}
          onClick={() => handleSuggestionClick(suggestion.prompt)}
        />
      ))}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Suggestion icon={PlusIcon} suggestion={t.common.create} />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuGroup>
            {t.inputBox.suggestionsCreate.map((suggestion, index) =>
              "type" in suggestion && suggestion.type === "separator" ? (
                <DropdownMenuSeparator key={index} />
              ) : (
                !("type" in suggestion) && (
                  <DropdownMenuItem
                    key={suggestion.suggestion}
                    onClick={() => handleSuggestionClick(suggestion.prompt)}
                  >
                    {suggestion.icon && <suggestion.icon className="size-4" />}
                    {suggestion.suggestion}
                  </DropdownMenuItem>
                )
              ),
            )}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </Suggestions>
  );
}

function AddAttachmentsButton({ className }: { className?: string }) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  return (
    <Tooltip content={t.inputBox.addAttachments}>
      <PromptInputButton
        className={cn("px-2!", className)}
        onClick={() => attachments.openFileDialog()}
      >
        <PaperclipIcon className="size-3" />
      </PromptInputButton>
    </Tooltip>
  );
}

type SlashPickerRowProps = {
  label: string;
  description?: string;
  icon: ReactNode;
  active: boolean;
  testId: string;
  onSelect: () => void;
  onHover: () => void;
};

function SlashPickerRow({
  label,
  description,
  icon,
  active,
  testId,
  onSelect,
  onHover,
}: SlashPickerRowProps) {
  return (
    <button
      className={cn(
        "flex w-full cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : "text-popover-foreground hover:bg-accent/60",
      )}
      data-active={active ? "true" : "false"}
      data-testid={testId}
      onClick={onSelect}
      onMouseEnter={onHover}
      type="button"
    >
      <div className="mt-0.5 shrink-0">{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{label}</div>
        {description && (
          <div className="text-muted-foreground mt-0.5 line-clamp-2 text-[11px] leading-tight">
            {description}
          </div>
        )}
      </div>
      {active && <CheckIcon className="mt-0.5 size-4 shrink-0" />}
    </button>
  );
}

/**
 * Pick a Lucide icon for a slash command based on its `kind`. We keep this
 * in the view layer (rather than letting each command carry its own
 * icon component) so the icon set stays visually consistent — built-in
 * and team-registered commands end up looking the same.
 */
function slashCommandIcon(kind: SlashCommand["kind"]): ReactNode {
  const cls = "size-4 opacity-60";
  switch (kind) {
    case "skill":
      return <WrenchIcon className={cls} />;
    case "mode":
      return <SparklesIcon className={cls} />;
    case "model":
      return <CpuIcon className={cls} />;
    case "clear":
      return <EraserIcon className={cls} />;
    case "help":
      return <HelpCircleIcon className={cls} />;
    case "custom":
      return <CommandIcon className={cls} />;
    default: {
      const _exhaustive: never = kind;
      void _exhaustive;
      return null;
    }
  }
}

// ----------------------------------------------------------------------------
// @-mention (file) picker UI
// ----------------------------------------------------------------------------

/**
 * Pick a Lucide icon for a file based on its MIME type / extension. We use
 * this in the picker row so the user can scan the list by shape, not just
 * filename. Order: image -> audio -> pdf/markdown/text -> other.
 */
function mentionFileIcon(file: FileItem): ReactNode {
  const mime = file.mime_type ?? "";
  if (mime.startsWith("image/")) {
    return <ImageIcon className="size-4 opacity-60" />;
  }
  if (mime.startsWith("audio/")) {
    return <FileIcon className="size-4 opacity-60" />;
  }
  if (
    mime.startsWith("text/") ||
    file.extension === ".md" ||
    file.extension === ".txt"
  ) {
    return <FileTextIcon className="size-4 opacity-60" />;
  }
  return <FileIcon className="size-4 opacity-60" />;
}

type MentionFilePickerRowProps = {
  file: FileItem;
  active: boolean;
  testId: string;
  onSelect: () => void;
  onHover: () => void;
};

function MentionFilePickerRow({
  file,
  active,
  testId,
  onSelect,
  onHover,
}: MentionFilePickerRowProps) {
  return (
    <button
      className={cn(
        "flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
        active
          ? "bg-accent text-accent-foreground"
          : "text-popover-foreground hover:bg-accent/60",
      )}
      data-active={active ? "true" : "false"}
      data-testid={testId}
      onClick={onSelect}
      onMouseEnter={onHover}
      type="button"
    >
      <div className="mt-0.5 shrink-0">{mentionFileIcon(file)}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{file.name}</div>
        <div className="text-muted-foreground mt-0.5 line-clamp-1 font-mono text-[10px]">
          {file.path}
        </div>
      </div>
      {active && <CheckIcon className="mt-0.5 size-4 shrink-0" />}
    </button>
  );
}

type ReferencedFileChipProps = {
  file: ReferencedFile;
  onRemove: () => void;
  removeLabel: string;
};

function ReferencedFileChip({
  file,
  onRemove,
  removeLabel,
}: ReferencedFileChipProps) {
  return (
    <span
      className="group bg-accent/40 text-accent-foreground inline-flex max-w-full items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium"
      data-testid={`referenced-file-chip-${file.id}`}
    >
      <FileTextIcon className="size-3 shrink-0" />
      <span className="truncate">{file.name}</span>
      <button
        aria-label={removeLabel}
        className="text-muted-foreground hover:text-foreground -mr-0.5 ml-0.5 inline-flex shrink-0 items-center rounded-sm p-0.5"
        onClick={onRemove}
        type="button"
      >
        <XIcon className="size-3" />
      </button>
    </span>
  );
}
