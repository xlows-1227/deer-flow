import {
  listCustomSkillFiles,
  listCustomSkillVersionFiles,
  readCustomSkillFile,
  readCustomSkillVersionFile,
} from "@/core/skills/api";
import type { SkillFileEntry } from "@/core/skills/type";

import {
  isReadableSkillServerFile,
  mergeServerEntriesIntoDraft,
  serverPathToWorkspacePath,
  type SkillLocalDraft,
} from "./skill-local-draft";

export async function loadCustomSkillFileSnapshot(skillName: string) {
  const entries = await listCustomSkillFiles(skillName);
  const contents: Record<string, string> = {};

  await Promise.all(
    entries
      .filter(
        (entry) =>
          entry.type === "file" && isReadableSkillServerFile(entry.path),
      )
      .map(async (entry) => {
        try {
          const response = await readCustomSkillFile(skillName, entry.path);
          contents[entry.path] = response.content;
        } catch {
          // Ignore unreadable files during import.
        }
      }),
  );

  return { entries, contents };
}

export async function loadCustomSkillVersionSnapshot(
  skillName: string,
  seq: number,
) {
  const entries = await listCustomSkillVersionFiles(skillName, seq);
  const contents: Record<string, string> = {};

  await Promise.all(
    entries
      .filter(
        (entry) =>
          entry.type === "file" && isReadableSkillServerFile(entry.path),
      )
      .map(async (entry) => {
        try {
          const response = await readCustomSkillVersionFile(
            skillName,
            seq,
            entry.path,
          );
          contents[entry.path] = response.content;
        } catch {
          // Ignore unreadable files during import.
        }
      }),
  );

  return { entries, contents };
}

export function mergeCustomSkillSnapshotIntoDraft(
  draft: SkillLocalDraft,
  skillName: string,
  entries: SkillFileEntry[],
  contents: Record<string, string>,
  options: { replaceExisting?: boolean } = {},
): {
  draft: SkillLocalDraft;
  workspacePaths: string[];
} {
  const next = mergeServerEntriesIntoDraft(
    { ...draft, skillName },
    entries,
    contents,
    options,
  );
  const workspacePaths = entries.map((entry) =>
    serverPathToWorkspacePath(entry.path),
  );
  return { draft: next, workspacePaths };
}
