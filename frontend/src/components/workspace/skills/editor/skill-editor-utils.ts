import type { SkillLocalDraft } from "@/components/workspace/skills/ai-create/skill-local-draft";

export type SkillDraftChangeType = "added" | "modified" | "deleted";

export interface SkillDraftChange {
  path: string;
  type: SkillDraftChangeType;
  before: string | null;
  after: string | null;
}

export interface SkillEditSessionSummary {
  threadId: string;
  title: string;
  skillName: string | null;
  published: boolean;
  updatedAt: number;
}

export function buildSkillDraftChanges(
  baseline: SkillLocalDraft,
  current: SkillLocalDraft,
): SkillDraftChange[] {
  const paths = new Set([
    ...Object.keys(baseline.files),
    ...Object.keys(current.files),
  ]);

  return [...paths]
    .sort((a, b) => {
      const aIsSkillMd = a.toLowerCase().endsWith("skill.md");
      const bIsSkillMd = b.toLowerCase().endsWith("skill.md");
      if (aIsSkillMd !== bIsSkillMd) return aIsSkillMd ? -1 : 1;
      return a.localeCompare(b);
    })
    .flatMap((path): SkillDraftChange[] => {
      const before = baseline.files[path];
      const after = current.files[path];

      if (before === undefined && after !== undefined) {
        return [{ path, type: "added", before: null, after }];
      }
      if (before !== undefined && after === undefined) {
        return [{ path, type: "deleted", before, after: null }];
      }
      if (before !== undefined && after !== undefined && before !== after) {
        return [{ path, type: "modified", before, after }];
      }
      return [];
    });
}

export function applySkillDraftChangeToBaseline(
  baseline: SkillLocalDraft,
  current: SkillLocalDraft,
  change: SkillDraftChange,
): SkillLocalDraft {
  const files = { ...baseline.files };
  if (change.type === "deleted") {
    delete files[change.path];
  } else {
    const content = current.files[change.path];
    if (content !== undefined) files[change.path] = content;
  }

  return {
    ...baseline,
    directories: [
      ...new Set([...baseline.directories, ...current.directories]),
    ],
    files,
  };
}

export function filterSkillEditSessions(
  sessions: SkillEditSessionSummary[],
  skillName: string,
) {
  return sessions
    .filter((session) => session.skillName === skillName)
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function normalizeDraftForSkillEditor(
  draft: SkillLocalDraft,
  skillName: string,
): SkillLocalDraft {
  const namedPrefix = `skills/${skillName}/`;
  const remap = (path: string) =>
    path.startsWith(namedPrefix)
      ? `skills/${path.slice(namedPrefix.length)}`
      : path;

  const directories = [
    ...new Set(
      draft.directories
        .map(remap)
        .filter((path) => path !== `skills/${skillName}`),
    ),
  ];
  const files = Object.fromEntries(
    Object.entries(draft.files).map(([path, content]) => [
      remap(path),
      content,
    ]),
  );

  return {
    ...draft,
    skillName,
    directories,
    files,
  };
}
