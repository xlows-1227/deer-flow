import { expect, test } from "vitest";

import {
  convertWorkspaceDraftToServerDraft,
  isReadableSkillServerFile,
  mergeServerEntriesIntoDraft,
  workspaceDirectoryToServerPath,
  workspacePathToServerPath,
  type SkillLocalDraft,
} from "@/components/workspace/skills/ai-create/skill-local-draft";

test("workspacePathToServerPath maps unsupported nested folders under skills/", () => {
  expect(
    workspacePathToServerPath(
      "skills/github-repo-analyzer/custom/foo.md",
      "github-repo-analyzer",
    ),
  ).toBe("skills/custom/foo.md");
});

test("workspaceDirectoryToServerPath ignores workspace root folders", () => {
  expect(workspaceDirectoryToServerPath("skills", "github-repo-analyzer")).toBe(
    null,
  );
  expect(
    workspaceDirectoryToServerPath(
      "skills/github-repo-analyzer",
      "github-repo-analyzer",
    ),
  ).toBe(null);
  expect(
    workspaceDirectoryToServerPath(
      "skills/github-repo-analyzer/scripts",
      "github-repo-analyzer",
    ),
  ).toBe("scripts");
});

test("convertWorkspaceDraftToServerDraft skips invalid server directories", () => {
  const serverDraft = convertWorkspaceDraftToServerDraft(
    {
      skillName: "demo",
      directories: ["skills/demo/custom"],
      files: {
        "skills/demo/custom/foo.md": "x",
      },
    },
    "demo",
  );

  expect(serverDraft.directories).toEqual(["skills", "skills/custom"]);
  expect(serverDraft.files).toEqual({
    "skills/custom/foo.md": "x",
  });
});

test("isReadableSkillServerFile rejects binary skill archives", () => {
  expect(isReadableSkillServerFile("skills/github-repo-analyzer.skill")).toBe(
    false,
  );
  expect(isReadableSkillServerFile("references/guide.md")).toBe(true);
});

test("forced server merge refreshes existing file content", () => {
  const draft: SkillLocalDraft = {
    skillName: "demo",
    directories: ["skills"],
    files: {
      "skills/SKILL.md": "old",
    },
  };

  const next = mergeServerEntriesIntoDraft(
    draft,
    [{ path: "skills/SKILL.md", type: "file", size: 3 }],
    { "skills/SKILL.md": "new" },
    { replaceExisting: true },
  );

  expect(next.files["skills/SKILL.md"]).toBe("new");
});
