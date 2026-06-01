import { describe, expect, test } from "vitest";

import {
  detectSlashCommand,
  nextPickerIndex,
} from "@/core/skills/slash-picker";

describe("detectSlashCommand", () => {
  test("returns inactive for plain text", () => {
    expect(detectSlashCommand("hello world")).toEqual({
      active: false,
      query: "",
      start: -1,
    });
  });

  test("detects a bare slash", () => {
    expect(detectSlashCommand("/")).toEqual({
      active: true,
      query: "",
      start: 0,
    });
  });

  test("captures the query after the slash", () => {
    expect(detectSlashCommand("/re")).toEqual({
      active: true,
      query: "re",
      start: 0,
    });
  });

  test("starts at the trailing token when slash appears mid-sentence", () => {
    expect(detectSlashCommand("hello /re")).toEqual({
      active: true,
      query: "re",
      start: 6,
    });
  });

  test("inactivates when the trailing token is not a slash command", () => {
    expect(detectSlashCommand("hello /re world")).toEqual({
      active: false,
      query: "",
      start: -1,
    });
  });

  test("handles newlines as token separators", () => {
    expect(detectSlashCommand("first line\n/cod")).toEqual({
      active: true,
      query: "cod",
      start: 11,
    });
  });
});

describe("nextPickerIndex", () => {
  test("moves down by one and wraps at the end", () => {
    expect(nextPickerIndex(0, 3, "down")).toBe(1);
    expect(nextPickerIndex(2, 3, "down")).toBe(0);
  });

  test("moves up by one and wraps at the start", () => {
    expect(nextPickerIndex(1, 3, "up")).toBe(0);
    expect(nextPickerIndex(0, 3, "up")).toBe(2);
  });

  test("starts at 0 when going down from -1", () => {
    expect(nextPickerIndex(-1, 3, "down")).toBe(0);
  });

  test("starts at the last row when going up from -1", () => {
    expect(nextPickerIndex(-1, 3, "up")).toBe(2);
  });

  test("returns -1 when total is 0", () => {
    expect(nextPickerIndex(0, 0, "down")).toBe(-1);
    expect(nextPickerIndex(0, 0, "up")).toBe(-1);
  });
});
