import { expect, test } from "vitest";

import { getToolDisplayPolicy } from "@/core/tools/display-policy";

test("hides raw Skill paths while retaining the safe Skill name", () => {
  const policy = getToolDisplayPolicy("read_file", {
    description: "Load data analysis skill",
    path: "/mnt/skills/public/data-analysis/SKILL.md",
  });

  expect(policy.isProtected).toBe(true);
  expect(policy.displayPath).toBe("data-analysis");
  expect(policy.safeArgs).toEqual({
    description: "Load data analysis skill",
    skill_name: "data-analysis",
    redacted: true,
  });
  expect(JSON.stringify(policy)).not.toContain("/mnt/skills");
});

test("never enables raw bash command rendering", () => {
  const policy = getToolDisplayPolicy("bash", {
    description: "Inspect files",
    command: "cat /mnt/skills/custom/private/SKILL.md",
  });

  expect(policy.showRawCommand).toBe(false);
  expect(policy.safeArgs).toBeNull();
  expect(JSON.stringify(policy)).not.toContain("cat /mnt/skills");
});
