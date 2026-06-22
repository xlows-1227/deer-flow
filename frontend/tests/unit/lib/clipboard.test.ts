import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const globalKeys = ["window", "navigator", "document"] as const;
type GlobalKey = (typeof globalKeys)[number];

describe("clipboard fallback", () => {
  const originalDescriptors = new Map<
    GlobalKey,
    PropertyDescriptor | undefined
  >();

  beforeEach(() => {
    vi.resetModules();
    for (const key of globalKeys) {
      originalDescriptors.set(
        key,
        Object.getOwnPropertyDescriptor(globalThis, key),
      );
    }
  });

  afterEach(() => {
    for (const key of globalKeys) {
      const descriptor = originalDescriptors.get(key);
      if (descriptor) {
        Object.defineProperty(globalThis, key, descriptor);
      } else {
        Reflect.deleteProperty(globalThis, key);
      }
    }
  });

  test("patched clipboard uses the legacy fallback without recursing", async () => {
    const execCommand = vi.fn(() => true);
    const textarea = {
      value: "",
      style: { position: "", left: "", top: "" },
      setAttribute: vi.fn(),
      select: vi.fn(),
      setSelectionRange: vi.fn(),
    };

    Object.defineProperties(globalThis, {
      window: { value: {}, configurable: true },
      navigator: { value: {}, configurable: true },
      document: {
        value: {
          body: {
            appendChild: vi.fn(),
            removeChild: vi.fn(),
          },
          createElement: vi.fn(() => textarea),
          execCommand,
        },
        configurable: true,
      },
    });

    const { copyTextToClipboard, ensureClipboardApi } =
      await import("@/lib/clipboard");

    ensureClipboardApi();

    await expect(copyTextToClipboard("hello")).resolves.toBe(true);
    expect(execCommand).toHaveBeenCalledWith("copy");
  });
});
