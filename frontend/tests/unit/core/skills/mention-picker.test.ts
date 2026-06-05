import { describe, expect, test } from "vitest";

import { detectMention } from "@/core/skills/mention-picker";

describe("detectMention", () => {
  test("returns inactive for plain text", () => {
    expect(detectMention("hello world")).toEqual({
      active: false,
      query: "",
      start: -1,
    });
  });

  test("detects a bare @", () => {
    expect(detectMention("@")).toEqual({
      active: true,
      query: "",
      start: 0,
    });
  });

  test("captures the query after the @", () => {
    expect(detectMention("@rep")).toEqual({
      active: true,
      query: "rep",
      start: 0,
    });
  });

  test("starts at the trailing token when @ appears mid-sentence", () => {
    expect(detectMention("hello @rep")).toEqual({
      active: true,
      query: "rep",
      start: 6,
    });
  });

  test("inactivates when the trailing token is not a mention", () => {
    expect(detectMention("hello @rep world")).toEqual({
      active: false,
      query: "",
      start: -1,
    });
  });

  test("handles newlines as token separators", () => {
    expect(detectMention("first line\n@readme")).toEqual({
      active: true,
      query: "readme",
      start: 11,
    });
  });

  test("tolerates email-like substrings mid-line — they don't trigger the picker", () => {
    // `user@example` doesn't start with `@`, so it isn't an in-progress
    // mention — the picker stays closed. The user would have to backspace
    // to the `u` and type `@` again to re-summon the file picker.
    expect(detectMention("ping user@example")).toEqual({
      active: false,
      query: "",
      start: -1,
    });
  });

  test("inactivates when the trailing token starts with a non-@ char", () => {
    expect(detectMention("hello foo@bar")).toEqual({
      active: false,
      query: "",
      start: -1,
    });
  });
});
