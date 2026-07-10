import type { TokenUsageInlineMode } from "../messages/usage-model";
import type { AgentThreadContext } from "../threads";

const LOCAL_SETTINGS_SCHEMA_VERSION = 2;

export const DEFAULT_LOCAL_SETTINGS: LocalSettings = {
  version: LOCAL_SETTINGS_SCHEMA_VERSION,
  notification: {
    enabled: true,
  },
  tokenUsage: {
    headerTotal: true,
    inlineMode: "per_turn",
  },
  context: {
    model_name: undefined,
    mode: "flash",
    // No default reasoning_effort: leave it undefined so per-mode defaults
    // apply at submit time. Advanced users can set a global override from
    // the Settings page.
    reasoning_effort: undefined,
  },
};

export const LOCAL_SETTINGS_KEY = "deerflow.local-settings";
export const THREAD_CONTEXT_KEY_PREFIX = "deerflow.thread-context.";
export const THREAD_MODEL_KEY_PREFIX = "deerflow.thread-model.";

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export interface LocalSettings {
  /**
   * Persisted schema version for browser-local preferences. Version 2 moves
   * reasoning effort from a per-message choice to an optional global override.
   */
  version: number;
  notification: {
    enabled: boolean;
  };
  tokenUsage: {
    headerTotal: boolean;
    inlineMode: TokenUsageInlineMode;
  };
  context: Omit<
    AgentThreadContext,
    | "thread_id"
    | "is_plan_mode"
    | "thinking_enabled"
    | "subagent_enabled"
    | "model_name"
    | "reasoning_effort"
  > & {
    model_name?: string | undefined;
    mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
    reasoning_effort?: "minimal" | "low" | "medium" | "high";
  };
}

export type ThreadContextSettings = Partial<LocalSettings["context"]>;

function mergeLocalSettings(settings?: Partial<LocalSettings>): LocalSettings {
  const savedVersion =
    typeof settings?.version === "number" ? settings.version : undefined;
  const preservesReasoningEffort =
    savedVersion !== undefined && savedVersion >= LOCAL_SETTINGS_SCHEMA_VERSION;
  const { reasoning_effort: savedReasoningEffort, ...savedContext } =
    settings?.context ?? {};

  return {
    ...DEFAULT_LOCAL_SETTINGS,
    version: preservesReasoningEffort
      ? savedVersion
      : LOCAL_SETTINGS_SCHEMA_VERSION,
    context: {
      ...DEFAULT_LOCAL_SETTINGS.context,
      ...savedContext,
      // Before version 2, this was the input-box's per-message value (and
      // defaulted to "minimal"). It must not become a global override after
      // upgrading, otherwise Pro/Thinking never reach their new low default.
      reasoning_effort: preservesReasoningEffort
        ? savedReasoningEffort
        : undefined,
    },
    tokenUsage: {
      ...DEFAULT_LOCAL_SETTINGS.tokenUsage,
      ...settings?.tokenUsage,
    },
    notification: {
      ...DEFAULT_LOCAL_SETTINGS.notification,
      ...settings?.notification,
    },
  };
}

function getThreadModelStorageKey(threadId: string): string {
  return `${THREAD_MODEL_KEY_PREFIX}${threadId}`;
}

function getThreadContextStorageKey(threadId: string): string {
  return `${THREAD_CONTEXT_KEY_PREFIX}${threadId}`;
}

export function getThreadModelName(threadId: string): string | undefined {
  if (!isBrowser()) {
    return undefined;
  }
  return localStorage.getItem(getThreadModelStorageKey(threadId)) ?? undefined;
}

export function saveThreadModelName(
  threadId: string,
  modelName: string | undefined,
) {
  if (!isBrowser()) {
    return;
  }
  const key = getThreadModelStorageKey(threadId);
  if (!modelName) {
    localStorage.removeItem(key);
    return;
  }
  localStorage.setItem(key, modelName);
}

export function getThreadContext(
  threadId: string,
): ThreadContextSettings | undefined {
  if (!isBrowser()) {
    return undefined;
  }

  const json = localStorage.getItem(getThreadContextStorageKey(threadId));
  if (json) {
    try {
      const context = JSON.parse(json) as ThreadContextSettings;
      // Drop legacy per-thread reasoning_effort written by the removed
      // input-box selector; the only override source now is the global
      // setting from the Settings page.
      delete context.reasoning_effort;
      return {
        ...context,
      };
    } catch {}
  }

  const modelName = getThreadModelName(threadId);
  return modelName ? { model_name: modelName } : undefined;
}

export function saveThreadContext(
  threadId: string,
  context: ThreadContextSettings | undefined,
) {
  if (!isBrowser()) {
    return;
  }

  const key = getThreadContextStorageKey(threadId);
  if (!context || Object.keys(context).length === 0) {
    localStorage.removeItem(key);
    saveThreadModelName(threadId, undefined);
    return;
  }

  localStorage.setItem(key, JSON.stringify(context));
  saveThreadModelName(threadId, context.model_name);
}

export function applyThreadContextOverride(
  settings: LocalSettings,
  threadContext: ThreadContextSettings | undefined,
): LocalSettings {
  return {
    ...settings,
    context: {
      ...DEFAULT_LOCAL_SETTINGS.context,
      // Thread context deliberately does not inherit the global chat context,
      // but the reasoning-effort override is a global advanced setting
      // (Settings → Models) and must apply to every thread.
      reasoning_effort: settings.context.reasoning_effort,
      ...threadContext,
    },
  };
}

export function getLocalSettings(): LocalSettings {
  if (!isBrowser()) {
    return DEFAULT_LOCAL_SETTINGS;
  }
  const json = localStorage.getItem(LOCAL_SETTINGS_KEY);
  try {
    if (json) {
      const settings = JSON.parse(json) as Partial<LocalSettings>;
      return mergeLocalSettings(settings);
    }
  } catch {}
  return DEFAULT_LOCAL_SETTINGS;
}

export function saveLocalSettings(settings: LocalSettings) {
  if (!isBrowser()) {
    return;
  }
  localStorage.setItem(LOCAL_SETTINGS_KEY, JSON.stringify(settings));
}
