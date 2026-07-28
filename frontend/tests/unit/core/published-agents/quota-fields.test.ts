import { describe, expect, it } from "vitest";

import {
  createEmptyQuotaInput,
  parseQuotaInput,
  quotaOverridesToInput,
} from "@/components/workspace/published-agents/quota-fields";

describe("published Agent quota field schema", () => {
  it("uses one parser for inherited, invalid, and bounded values", () => {
    const input = createEmptyQuotaInput();
    input.daily_runs = "25";
    input.daily_tokens = "invalid";
    input.max_concurrent_runs = "9";

    const parsed = parseQuotaInput(input, {
      max_concurrent_runs: 8,
      daily_runs: 100,
      daily_tokens: 10_000,
      max_run_seconds: 60,
      max_tokens_per_run: 1_000,
      max_input_bytes: 256_000,
      inbound_rps: 20,
    });

    expect(parsed.overrides).toEqual({ daily_runs: 25 });
    expect(parsed.errors).toEqual({
      max_concurrent_runs: "maximum",
      daily_tokens: "invalid",
    });
  });

  it("round-trips sparse overrides into a complete editor value", () => {
    expect(
      quotaOverridesToInput({
        daily_runs: 5,
        max_run_seconds: 30,
      }),
    ).toEqual({
      max_concurrent_runs: "",
      daily_runs: "5",
      daily_tokens: "",
      max_run_seconds: "30",
      max_tokens_per_run: "",
      max_input_bytes: "",
      inbound_rps: "",
    });
  });
});
