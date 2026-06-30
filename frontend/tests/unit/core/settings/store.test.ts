import { beforeEach, expect, test, vi } from "vitest";

function installStorageMock() {
  const data = new Map<string, string>();
  const storage = {
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      data.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      data.delete(key);
    }),
    clear: vi.fn(() => {
      data.clear();
    }),
  };
  vi.stubGlobal("localStorage", storage);
  vi.stubGlobal("window", {
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  });
  return storage;
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
  installStorageMock();
});

test("copyThreadContext migrates temporary thread settings to created thread", async () => {
  const { copyThreadContext, getThreadContextSnapshot, updateThreadSettings } =
    await import("@/core/settings/store");

  updateThreadSettings("temp-thread", "context", {
    model_name: "fast-model",
    mode: "flash",
    reasoning_effort: "minimal",
    skill_name: "sales-report",
    connector_ids: ["connector-1"],
  });
  updateThreadSettings("created-thread", "context", {
    model_name: "old-model",
    mode: "thinking",
  });

  copyThreadContext("temp-thread", "created-thread");

  expect(getThreadContextSnapshot("created-thread")).toEqual({
    model_name: "fast-model",
    mode: "flash",
    reasoning_effort: "minimal",
    skill_name: "sales-report",
    connector_ids: ["connector-1"],
  });
});
