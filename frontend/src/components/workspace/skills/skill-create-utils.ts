export function slugifySkillName(value: string) {
  const slug = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 64)
    .replace(/-$/g, "");
  return slug || "custom-skill";
}

export function formatSkillDisplayName(displayName: string, name: string) {
  const label = displayName.trim();
  if (!label) return "";
  return `${label}（${name}）`;
}

export function extractDisplayLabel(displayName: string, name?: string) {
  const suffix = name ? `（${name}）` : "";
  if (suffix && displayName.endsWith(suffix)) {
    return displayName.slice(0, -suffix.length).trim();
  }
  return displayName.replace(/（[^）]+）$/, "").trim();
}

export function buildSkillMarkdown({
  name,
  displayName,
  description,
}: {
  name: string;
  displayName?: string;
  description: string;
}) {
  const formattedDisplayName = formatSkillDisplayName(displayName ?? "", name);
  const displayNameLine = formattedDisplayName
    ? `display_name: ${formattedDisplayName}\n`
    : "";

  return `---
name: ${name}
${displayNameLine}description: ${description || "Custom DeerFlow skill"}
---

Use this skill when the user asks for ${description || "this custom workflow"}.

## Workflow
1. Clarify the target outcome when the request is ambiguous.
2. Gather only the context needed for the task.
3. Execute the task using DeerFlow's existing runtime and conventions.
4. Verify the result before reporting completion.

## Output
- Keep the final answer concise.
- Include files, commands, or artifacts that are useful for review.
`;
}

export function parseSkillMarkdown(content: string) {
  const frontmatter = /^---\n([\s\S]*?)\n---/.exec(content.trimStart())?.[1];
  if (!frontmatter) {
    return {
      name: "",
      displayName: "",
      description: "",
      descriptionZh: "",
    };
  }
  const name = readFrontmatterString(frontmatter, "name");
  const displayNameRaw = readFrontmatterString(frontmatter, "display_name");
  const description = readFrontmatterString(frontmatter, "description");
  const descriptionZh = readFrontmatterString(frontmatter, "description_zh");
  return {
    name,
    displayName: displayNameRaw
      ? extractDisplayLabel(displayNameRaw, name)
      : "",
    description,
    descriptionZh,
  };
}

export function resolveSkillDisplayName(
  parsed: ReturnType<typeof parseSkillMarkdown>,
  settingsName?: string,
  draftDisplayName?: string,
) {
  const explicit = settingsName?.trim();
  if (explicit) return explicit;
  if (parsed.displayName.trim()) return parsed.displayName.trim();
  if (draftDisplayName?.trim()) return draftDisplayName.trim();
  return parsed.name.trim() || "未命名技能";
}

export function resolveSkillDisplayDescription(
  parsed: ReturnType<typeof parseSkillMarkdown>,
  settingsDescription?: string,
) {
  const explicit = settingsDescription?.trim();
  if (explicit) return explicit;
  if (parsed.descriptionZh.trim()) return parsed.descriptionZh.trim();
  return parsed.description.trim();
}

/** Update only display_name / description_zh; leaves name and description unchanged. */
export function syncSkillDisplayFrontmatter({
  content,
  displayName,
  descriptionZh,
}: {
  content: string;
  displayName: string;
  descriptionZh: string;
}) {
  const match = /^---\n([\s\S]*?)\n---/.exec(content.trimStart());
  const frontmatterBody = match?.[1];
  if (!match || !frontmatterBody) {
    return content;
  }

  const name =
    readFrontmatterString(frontmatterBody, "name").trim() || "custom-skill";
  const formattedDisplayName = displayName.trim()
    ? formatSkillDisplayName(displayName.trim(), name)
    : "";

  let frontmatter = frontmatterBody;
  frontmatter = upsertFrontmatterField(
    frontmatter,
    "display_name",
    formattedDisplayName,
  );
  frontmatter = upsertFrontmatterField(
    frontmatter,
    "description_zh",
    descriptionZh.trim(),
  );

  return content.replace(match[0], `---\n${frontmatter}\n---`);
}

export function syncSkillFrontmatter({
  content,
  name,
  displayName,
  description,
}: {
  content: string;
  name: string;
  displayName?: string;
  description: string;
}) {
  const formattedDisplayName = formatSkillDisplayName(displayName ?? "", name);
  const displayNameLine = formattedDisplayName
    ? `display_name: ${formattedDisplayName}\n`
    : "";
  const frontmatter = `---
name: ${name}
${displayNameLine}description: ${description || "Custom DeerFlow skill"}
---`;
  const match = /^---\n[\s\S]*?\n---/.exec(content.trimStart());
  if (!match) {
    return `${frontmatter}\n\n${content.trimStart()}`;
  }
  return content.replace(match[0], frontmatter);
}

function readFrontmatterString(frontmatter: string, key: string) {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`^${escaped}:\\s*(.+?)\\s*$`, "m").exec(frontmatter);
  return match?.[1]?.replace(/^["']|["']$/g, "") ?? "";
}

function upsertFrontmatterField(
  frontmatter: string,
  key: string,
  value: string,
) {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`^${escaped}:\\s*.+?\\s*$\\n?`, "m");
  if (!value) {
    return frontmatter.replace(pattern, "");
  }
  const line = `${key}: ${value}\n`;
  if (pattern.test(frontmatter)) {
    return frontmatter.replace(pattern, line);
  }
  return `${frontmatter.trimEnd()}\n${line}`;
}
