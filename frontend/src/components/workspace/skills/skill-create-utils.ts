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
    return { name: "", displayName: "", description: "" };
  }
  const name = readFrontmatterString(frontmatter, "name");
  const displayName = readFrontmatterString(frontmatter, "display_name");
  const description = readFrontmatterString(frontmatter, "description");
  return {
    name,
    displayName: displayName ? extractDisplayLabel(displayName, name) : "",
    description,
  };
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
  const match = new RegExp(`^${key}:\\s*(.+?)\\s*$`, "m").exec(frontmatter);
  return match?.[1]?.replace(/^["']|["']$/g, "") ?? "";
}
