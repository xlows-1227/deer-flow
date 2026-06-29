import { describe, expect, it } from "vitest";

import {
  buildApiKeyUpdateValue,
  MASKED_API_KEY,
} from "@/core/models/config-api";

describe("buildApiKeyUpdateValue", () => {
  it("returns undefined for empty draft", () => {
    expect(buildApiKeyUpdateValue("", false)).toBeUndefined();
    expect(buildApiKeyUpdateValue("   ", true)).toBeUndefined();
  });

  it("returns masked sentinel when keeping existing key", () => {
    expect(buildApiKeyUpdateValue(MASKED_API_KEY, true)).toBe(MASKED_API_KEY);
  });

  it("returns trimmed new key when provided", () => {
    expect(buildApiKeyUpdateValue("  sk-new-key  ", true)).toBe("sk-new-key");
  });
});
