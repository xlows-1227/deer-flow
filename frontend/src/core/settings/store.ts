import {
  DEFAULT_LOCAL_SETTINGS,
  LOCAL_SETTINGS_KEY,
  THREAD_CONTEXT_KEY_PREFIX,
  THREAD_MODEL_KEY_PREFIX,
  getThreadContext,
  getLocalSettings,
  saveLocalSettings,
  saveThreadContext,
  type LocalSettings,
  type ThreadContextSettings,
} from "./local";

type Listener = () => void;

export type LocalSettingsSetter = <K extends keyof LocalSettings>(
  key: K,
  value: Partial<LocalSettings[K]>,
) => void;

const listeners = new Set<Listener>();
const threadContexts = new Map<string, ThreadContextSettings | undefined>();

let baseSettings: LocalSettings = DEFAULT_LOCAL_SETTINGS;
let baseSettingsLoaded = false;
let storageListenerRegistered = false;

function emitChange() {
  for (const listener of listeners) {
    listener();
  }
}

function ensureBaseSettingsLoaded() {
  if (baseSettingsLoaded || typeof window === "undefined") {
    return;
  }

  baseSettings = getLocalSettings();
  baseSettingsLoaded = true;
}

function ensureStorageListenerRegistered() {
  if (storageListenerRegistered || typeof window === "undefined") {
    return;
  }

  window.addEventListener("storage", handleStorage);
  storageListenerRegistered = true;
}

function mergeSettingsSection<K extends keyof LocalSettings>(
  settings: LocalSettings,
  key: K,
  value: Partial<LocalSettings[K]>,
): LocalSettings {
  return {
    ...settings,
    [key]: {
      ...settings[key],
      ...value,
    },
  } as LocalSettings;
}

function handleStorage(event: StorageEvent) {
  if (event.storageArea && event.storageArea !== localStorage) {
    return;
  }

  ensureBaseSettingsLoaded();

  if (event.key === null) {
    baseSettings = getLocalSettings();
    threadContexts.clear();
    emitChange();
    return;
  }

  if (event.key === LOCAL_SETTINGS_KEY) {
    baseSettings = getLocalSettings();
    emitChange();
    return;
  }

  if (
    !event.key.startsWith(THREAD_CONTEXT_KEY_PREFIX) &&
    !event.key.startsWith(THREAD_MODEL_KEY_PREFIX)
  ) {
    return;
  }

  const threadId = event.key.startsWith(THREAD_CONTEXT_KEY_PREFIX)
    ? event.key.slice(THREAD_CONTEXT_KEY_PREFIX.length)
    : event.key.slice(THREAD_MODEL_KEY_PREFIX.length);
  threadContexts.set(threadId, getThreadContext(threadId));
  emitChange();
}

export function subscribe(listener: Listener): () => void {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();
  listeners.add(listener);

  return () => {
    listeners.delete(listener);
  };
}

export function getBaseSettingsSnapshot(): LocalSettings {
  ensureBaseSettingsLoaded();
  return baseSettings;
}

export function getThreadContextSnapshot(
  threadId: string,
): ThreadContextSettings | undefined {
  ensureBaseSettingsLoaded();

  if (!threadContexts.has(threadId)) {
    threadContexts.set(threadId, getThreadContext(threadId));
  }

  return threadContexts.get(threadId);
}

export const updateLocalSettings: LocalSettingsSetter = (key, value) => {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();

  baseSettings = mergeSettingsSection(baseSettings, key, value);
  saveLocalSettings(baseSettings);
  emitChange();
};

export function updateThreadSettings<K extends keyof LocalSettings>(
  threadId: string,
  key: K,
  value: Partial<LocalSettings[K]>,
) {
  ensureBaseSettingsLoaded();
  ensureStorageListenerRegistered();

  if (key === "context") {
    const current = getThreadContextSnapshot(threadId) ?? {};
    const nextContext = {
      ...current,
      ...(value as ThreadContextSettings),
    };
    threadContexts.set(threadId, nextContext);
    saveThreadContext(threadId, nextContext);
    emitChange();
    return;
  }

  const nextBaseSettings = mergeSettingsSection(baseSettings, key, value);
  baseSettings = nextBaseSettings;
  saveLocalSettings(baseSettings);

  emitChange();
}
