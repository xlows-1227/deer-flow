import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  convertWorkspaceDraftToServerDraft,
  deleteLocalDraft,
  isReadableSkillServerFile,
  loadLocalDraft,
  mergeServerEntriesIntoDraft,
  saveLocalDraft,
  workspaceDirectoryToServerPath,
  workspacePathToServerPath,
  type SkillLocalDraft,
} from "@/components/workspace/skills/ai-create/skill-local-draft";

const DRAFT_STORAGE_PREFIX = "skill-ai-create-draft:";

function createSessionStorageMock() {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    key: vi.fn((index: number) => [...data.keys()][index] ?? null),
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      data.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      data.delete(key);
    }),
    data,
  };
}

beforeEach(() => {
  vi.stubGlobal("window", {});
  vi.stubGlobal("sessionStorage", createSessionStorageMock());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

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

test("saveLocalDraft persists and loads a local draft", () => {
  const draft: SkillLocalDraft = {
    skillName: "demo",
    directories: ["skills"],
    files: {
      "skills/SKILL.md": "content",
    },
  };

  expect(saveLocalDraft("thread-1", draft)).toBe(true);

  expect(loadLocalDraft("thread-1")).toEqual({
    ...draft,
    displayName: "",
    descriptionZh: "",
  });
});

test("saveLocalDraft evicts other skill drafts and retries after storage failure", () => {
  const storage = createSessionStorageMock();
  storage.data.set(`${DRAFT_STORAGE_PREFIX}old-thread`, "{}");
  storage.data.set("unrelated", "keep");
  let shouldThrow = true;
  storage.setItem.mockImplementation((key: string, value: string) => {
    if (shouldThrow) {
      shouldThrow = false;
      throw new Error("quota exceeded");
    }
    storage.data.set(key, value);
  });
  vi.stubGlobal("sessionStorage", storage);

  expect(
    saveLocalDraft("thread-1", {
      skillName: "demo",
      directories: [],
      files: { "skills/SKILL.md": "content" },
    }),
  ).toBe(true);

  expect(storage.data.has(`${DRAFT_STORAGE_PREFIX}old-thread`)).toBe(false);
  expect(storage.data.has("unrelated")).toBe(true);
  expect(storage.data.has(`${DRAFT_STORAGE_PREFIX}thread-1`)).toBe(true);
});

test("saveLocalDraft does not throw when the current draft exceeds storage quota", () => {
  const storage = createSessionStorageMock();
  storage.setItem.mockImplementation(() => {
    throw new Error("quota exceeded");
  });
  vi.stubGlobal("sessionStorage", storage);

  expect(() =>
    saveLocalDraft("thread-1", {
      skillName: "demo",
      directories: [],
      files: { "skills/SKILL.md": "very large content" },
    }),
  ).not.toThrow();
  expect(saveLocalDraft("thread-1", createEmptyDraftForTest())).toBe(false);
  expect(storage.removeItem).toHaveBeenCalledWith(
    `${DRAFT_STORAGE_PREFIX}thread-1`,
  );
});

test("deleteLocalDraft ignores storage removal failures", () => {
  const storage = createSessionStorageMock();
  storage.removeItem.mockImplementation(() => {
    throw new Error("storage blocked");
  });
  vi.stubGlobal("sessionStorage", storage);

  expect(() => deleteLocalDraft("thread-1")).not.toThrow();
});

function createEmptyDraftForTest(): SkillLocalDraft {
  return {
    skillName: null,
    directories: [],
    files: {},
  };
}
