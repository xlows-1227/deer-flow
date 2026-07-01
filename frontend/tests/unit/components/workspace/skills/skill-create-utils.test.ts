import { describe, expect, test } from "vitest";

import {
  sanitizeSkillNameInput,
  SKILL_NAME_PATTERN,
} from "@/components/workspace/skills/skill-create-utils";

describe("sanitizeSkillNameInput", () => {
  test("removes special characters and uppercases", () => {
    expect(sanitizeSkillNameInput("Research@Brief!")).toBe("researchbrief");
    expect(sanitizeSkillNameInput("sql_check")).toBe("sqlcheck");
    expect(sanitizeSkillNameInput("研究-brief")).toBe("-brief");
  });

  test("keeps allowed hyphen-case characters", () => {
    expect(sanitizeSkillNameInput("research-brief")).toBe("research-brief");
    expect(sanitizeSkillNameInput("sql-check-2")).toBe("sql-check-2");
  });

  test("truncates to 64 characters", () => {
    expect(sanitizeSkillNameInput("a".repeat(80))).toHaveLength(64);
  });
});

describe("SKILL_NAME_PATTERN", () => {
  test("accepts valid hyphen-case names", () => {
    expect(SKILL_NAME_PATTERN.test("research-brief")).toBe(true);
    expect(SKILL_NAME_PATTERN.test("sql-check-2")).toBe(true);
  });

  test("rejects invalid hyphen-case names", () => {
    expect(SKILL_NAME_PATTERN.test("-research")).toBe(false);
    expect(SKILL_NAME_PATTERN.test("research-")).toBe(false);
    expect(SKILL_NAME_PATTERN.test("research--brief")).toBe(false);
  });
});
