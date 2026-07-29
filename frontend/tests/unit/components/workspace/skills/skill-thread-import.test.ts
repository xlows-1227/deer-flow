import { describe, expect, test } from "vitest";

import {
  collectThreadOutputSandboxPaths,
  sandboxPathToWorkspacePath,
  scrubPollutedMountPathsFromDraft,
} from "@/components/workspace/skills/ai-create/skill-thread-import";

describe("sandboxPathToWorkspacePath", () => {
  test("maps user-data workspace and outputs into skills/", () => {
    expect(
      sandboxPathToWorkspacePath("/mnt/user-data/workspace/notes.md"),
    ).toBe("skills/notes.md");
    expect(sandboxPathToWorkspacePath("/mnt/user-data/outputs/report.md")).toBe(
      "skills/report.md",
    );
    expect(
      sandboxPathToWorkspacePath(
        "/mnt/user-data/workspace/skills/demo/SKILL.md",
      ),
    ).toBe("skills/demo/SKILL.md");
  });

  test("skips uploads", () => {
    expect(
      sandboxPathToWorkspacePath("/mnt/user-data/uploads/secret.pdf"),
    ).toBe("");
  });

  test("rejects skills and other non-user-data mounts", () => {
    expect(sandboxPathToWorkspacePath("/mnt/skills/custom/demo/SKILL.md")).toBe(
      "",
    );
    expect(
      sandboxPathToWorkspacePath(
        "/mnt/skills/public/skill-creator/scripts/init_skill.py",
      ),
    ).toBe("");
    expect(sandboxPathToWorkspacePath("/mnt/acp-workspace/README.md")).toBe("");
    expect(sandboxPathToWorkspacePath("mnt/skills/custom/demo/SKILL.md")).toBe(
      "",
    );
  });
});

describe("scrubPollutedMountPathsFromDraft", () => {
  test("removes skills/.../mnt/skills pollution left by bad imports", () => {
    const cleaned = scrubPollutedMountPathsFromDraft({
      skillName: "demo",
      directories: [
        "skills",
        "skills/demo",
        "skills/demo/mnt",
        "skills/demo/mnt/skills",
        "skills/demo/scripts",
      ],
      files: {
        "skills/demo/SKILL.md": "ok",
        "skills/demo/mnt/skills/custom/demo/SKILL.md": "polluted",
        "skills/demo/scripts/run.sh": "run",
      },
    });

    expect(cleaned.files).toEqual({
      "skills/demo/SKILL.md": "ok",
      "skills/demo/scripts/run.sh": "run",
    });
    expect(cleaned.directories).toEqual([
      "skills",
      "skills/demo",
      "skills/demo/scripts",
    ]);
  });
});

describe("collectThreadOutputSandboxPaths", () => {
  test("drops /mnt/skills paths from tool calls and artifacts", () => {
    const paths = collectThreadOutputSandboxPaths({
      messages: [
        {
          type: "ai",
          id: "1",
          content: "",
          tool_calls: [
            {
              id: "c1",
              name: "write_file",
              args: { path: "/mnt/skills/custom/demo/SKILL.md" },
              type: "tool_call",
            },
            {
              id: "c2",
              name: "write_file",
              args: { path: "/mnt/user-data/outputs/notes.md" },
              type: "tool_call",
            },
          ],
        },
      ],
      artifacts: [
        "/mnt/skills/public/skill-creator/SKILL.md",
        "/mnt/user-data/workspace/helpers.py",
      ],
      sandboxFiles: [
        {
          path: "/mnt/user-data/outputs/notes.md",
          name: "notes.md",
          size: 10,
          modified_at: 1,
          source: "outputs",
        },
      ],
    });

    expect(paths).toEqual(
      expect.arrayContaining([
        "/mnt/user-data/outputs/notes.md",
        "/mnt/user-data/workspace/helpers.py",
      ]),
    );
    expect(paths.some((path) => path.includes("/mnt/skills"))).toBe(false);
  });
});
