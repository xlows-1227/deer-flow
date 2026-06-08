import { describe, expect, test } from "vitest";

import {
  convertWorkspaceDraftToServerDraft,
  findWorkspaceSkillMdPath,
  reorganizeSkillsIntoNamedFolder,
} from "@/components/workspace/skills/ai-create/skill-local-draft";
import {
  applySkillDraftChangeToBaseline,
  buildSkillDraftChanges,
  filterSkillEditSessions,
  normalizeDraftForSkillEditor,
  type SkillEditSessionSummary,
} from "@/components/workspace/skills/editor/skill-editor-utils";

describe("buildSkillDraftChanges", () => {
  test("returns added, modified, and deleted file changes", () => {
    const changes = buildSkillDraftChanges(
      {
        skillName: "demo",
        directories: ["skills"],
        files: {
          "skills/SKILL.md": "old skill",
          "skills/keep.md": "same",
          "skills/remove.md": "gone",
        },
      },
      {
        skillName: "demo",
        directories: ["skills"],
        files: {
          "skills/SKILL.md": "new skill",
          "skills/keep.md": "same",
          "skills/add.md": "added",
        },
      },
    );

    expect(changes.map((change) => [change.path, change.type])).toEqual([
      ["skills/SKILL.md", "modified"],
      ["skills/add.md", "added"],
      ["skills/remove.md", "deleted"],
    ]);
    expect(changes[0]?.before).toBe("old skill");
    expect(changes[0]?.after).toBe("new skill");
  });
});

describe("applySkillDraftChangeToBaseline", () => {
  test("updates only the confirmed file in the baseline draft", () => {
    const baseline = {
      skillName: "demo",
      directories: ["skills"],
      files: {
        "skills/SKILL.md": "old skill",
        "skills/other.md": "old other",
      },
    };
    const current = {
      skillName: "demo",
      directories: ["skills"],
      files: {
        "skills/SKILL.md": "new skill",
        "skills/other.md": "new other",
      },
    };

    const nextBaseline = applySkillDraftChangeToBaseline(baseline, current, {
      path: "skills/SKILL.md",
      type: "modified",
      before: "old skill",
      after: "new skill",
    });

    expect(nextBaseline.files).toEqual({
      "skills/SKILL.md": "new skill",
      "skills/other.md": "old other",
    });
  });

  test("removes a confirmed deleted file from the baseline draft", () => {
    const nextBaseline = applySkillDraftChangeToBaseline(
      {
        skillName: "demo",
        directories: ["skills"],
        files: {
          "skills/remove.md": "gone",
          "skills/keep.md": "same",
        },
      },
      {
        skillName: "demo",
        directories: ["skills"],
        files: {
          "skills/keep.md": "same",
        },
      },
      {
        path: "skills/remove.md",
        type: "deleted",
        before: "gone",
        after: null,
      },
    );

    expect(nextBaseline.files).toEqual({
      "skills/keep.md": "same",
    });
  });
});

describe("reorganizeSkillsIntoNamedFolder", () => {
  test("moves files directly under skills into a named skill directory", () => {
    const reorganized = reorganizeSkillsIntoNamedFolder(
      {
        skillName: "demo",
        directories: ["skills", "skills/scripts"],
        files: {
          "skills/SKILL.md": "name: demo",
          "skills/scripts/run.sh": "run",
        },
      },
      "demo",
    );

    expect(reorganized.directories).toContain("skills");
    expect(reorganized.directories).toContain("skills/demo");
    expect(reorganized.directories).toContain("skills/demo/scripts");
    expect(reorganized.files).toEqual({
      "skills/demo/SKILL.md": "name: demo",
      "skills/demo/scripts/run.sh": "run",
    });
  });

  test("moves a previous skill directory when the skill name changes", () => {
    const reorganized = reorganizeSkillsIntoNamedFolder(
      {
        skillName: "old-demo",
        directories: ["skills", "skills/old-demo", "skills/old-demo/scripts"],
        files: {
          "skills/old-demo/SKILL.md": "name: new-demo",
          "skills/old-demo/scripts/run.sh": "run",
        },
      },
      "new-demo",
    );

    expect(reorganized.directories).toContain("skills/new-demo");
    expect(reorganized.directories).toContain("skills/new-demo/scripts");
    expect(reorganized.directories).not.toContain("skills/old-demo");
    expect(reorganized.files).toEqual({
      "skills/new-demo/SKILL.md": "name: new-demo",
      "skills/new-demo/scripts/run.sh": "run",
    });
  });
});

describe("findWorkspaceSkillMdPath", () => {
  test("finds SKILL.md inside a named skill directory", () => {
    expect(
      findWorkspaceSkillMdPath({
        skillName: "demo",
        directories: ["skills", "skills/demo"],
        files: {
          "skills/demo/SKILL.md": "name: demo",
        },
      }),
    ).toBe("skills/demo/SKILL.md");
  });
});

describe("convertWorkspaceDraftToServerDraft", () => {
  test("publishes only the contents inside the named skill directory", () => {
    const serverDraft = convertWorkspaceDraftToServerDraft(
      {
        skillName: "github-repo-analyzer",
        directories: [
          "skills",
          "skills/github-repo-analyzer",
          "skills/github-repo-analyzer/scripts",
          "skills/github-repo-analyzer/skills",
          "skills/github-repo-analyzer/skills/evals",
        ],
        files: {
          "skills/github-repo-analyzer/SKILL.md": "name: github-repo-analyzer",
          "skills/github-repo-analyzer/test_root.html": "<root />",
          "skills/github-repo-analyzer/scripts/repo_analyzer.py": "print('x')",
          "skills/github-repo-analyzer/skills/evals/test.html": "<html />",
        },
      },
      "github-repo-analyzer",
    );

    expect(serverDraft.files).toEqual({
      "SKILL.md": "name: github-repo-analyzer",
      "skills/test_root.html": "<root />",
      "scripts/repo_analyzer.py": "print('x')",
      "skills/evals/test.html": "<html />",
    });
    expect(serverDraft.directories).toEqual([
      "scripts",
      "skills",
      "skills/evals",
    ]);
  });
});

describe("normalizeDraftForSkillEditor", () => {
  test("flattens named AI-create skill folders into the editor workspace shape", () => {
    const normalized = normalizeDraftForSkillEditor(
      {
        skillName: "demo",
        directories: ["skills", "skills/demo", "skills/demo/scripts"],
        files: {
          "skills/demo/SKILL.md": "skill",
          "skills/demo/scripts/run.sh": "run",
        },
      },
      "demo",
    );

    expect(normalized.directories).toEqual(["skills", "skills/scripts"]);
    expect(normalized.files).toEqual({
      "skills/SKILL.md": "skill",
      "skills/scripts/run.sh": "run",
    });
  });
});

describe("filterSkillEditSessions", () => {
  test("returns sessions linked to the requested skill newest first", () => {
    const sessions: SkillEditSessionSummary[] = [
      {
        threadId: "older",
        title: "Older edit",
        skillName: "demo",
        published: false,
        updatedAt: 10,
      },
      {
        threadId: "other",
        title: "Other skill",
        skillName: "other",
        published: false,
        updatedAt: 30,
      },
      {
        threadId: "newer",
        title: "Newer edit",
        skillName: "demo",
        published: true,
        updatedAt: 20,
      },
    ];

    expect(filterSkillEditSessions(sessions, "demo")).toEqual([
      sessions[2],
      sessions[0],
    ]);
  });
});
