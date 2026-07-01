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

/** Remove YAML frontmatter block from skill markdown for preview rendering. */
export function stripSkillFrontmatter(content: string) {
  const trimmed = content.trimStart();
  const match = /^---\n[\s\S]*?\n---/.exec(trimmed);
  if (!match) {
    return content;
  }
  return trimmed.slice(match[0].length).trimStart();
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
  const trimmed = content.trim();
  const match = /^---\n[\s\S]*?\n---/.exec(trimmed);
  const body = match ? trimmed.slice(match[0].length).trimStart() : trimmed;
  return body ? `${frontmatter}\n\n${body}` : frontmatter;
}

export const SKILL_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Restrict skill name input to hyphen-case characters while typing. */
export function sanitizeSkillNameInput(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "")
    .slice(0, 64);
}

export type SkillMarkdownValidationResult =
  | { valid: true }
  | { valid: false; message: string };

/** Client-side SKILL.md validation aligned with backend rules. */
export function validateSkillMarkdownContent(
  content: string,
  expectedName?: string,
): SkillMarkdownValidationResult {
  const trimmed = content.trim();
  if (!trimmed.startsWith("---")) {
    return {
      valid: false,
      message:
        "SKILL.md 缺少 YAML 元数据（frontmatter）。请在文件开头添加 --- 包裹的元数据块，包含 name 和 description 字段；也可点击「重置模板」恢复默认格式。",
    };
  }

  const match = /^---\n([\s\S]*?)\n---/.exec(trimmed);
  if (!match?.[1]) {
    return {
      valid: false,
      message:
        "frontmatter 格式不正确。请确保文件以 --- 开头、以 --- 结尾，且 name、description 等字段写在两个 --- 之间。",
    };
  }

  const frontmatter = match[1];
  const name = readFrontmatterString(frontmatter, "name").trim();
  const description = readFrontmatterString(frontmatter, "description").trim();

  if (!name) {
    return {
      valid: false,
      message:
        "frontmatter 中缺少 name 字段。请填写技能标识（小写字母、数字和连字符），并与左侧「保存名称」一致。",
    };
  }

  if (!description) {
    return {
      valid: false,
      message:
        "frontmatter 中缺少 description 字段。请填写技能用途说明，或与左侧 Description 保持一致。",
    };
  }

  if (!SKILL_NAME_PATTERN.test(name)) {
    return {
      valid: false,
      message: `技能名称「${name}」格式无效。请使用小写字母、数字和连字符（例如 sql-check），不能以连字符开头或结尾。`,
    };
  }

  if (name.length > 64) {
    return {
      valid: false,
      message: `技能名称过长（${name.length} 个字符），最多 64 个字符。`,
    };
  }

  if (description.includes("<") || description.includes(">")) {
    return {
      valid: false,
      message: "description 不能包含尖括号（< 或 >）。",
    };
  }

  if (description.length > 1024) {
    return {
      valid: false,
      message: `description 过长（${description.length} 个字符），最多 1024 个字符。`,
    };
  }

  if (expectedName && name !== expectedName) {
    return {
      valid: false,
      message: `frontmatter 中的 name（${name}）必须与保存名称（${expectedName}）一致。`,
    };
  }

  return { valid: true };
}

/** Map backend validation errors to user-friendly Chinese messages. */
export function formatSkillValidationError(message: string): string {
  const trimmed = message.trim();
  if (!trimmed) {
    return "Skill 校验失败，请检查 SKILL.md 格式后重试。";
  }

  const rules: Array<{
    test: RegExp;
    format: (match: RegExpMatchArray) => string;
  }> = [
    {
      test: /^No YAML frontmatter found$/i,
      format: () =>
        "SKILL.md 缺少 YAML 元数据（frontmatter）。请在文件开头添加 --- 包裹的元数据块，包含 name 和 description 字段；也可点击「重置模板」恢复默认格式。",
    },
    {
      test: /^Invalid frontmatter format$/i,
      format: () =>
        "frontmatter 格式不正确。请确保文件以 --- 开头、以 --- 结尾，且 name、description 等字段写在两个 --- 之间。",
    },
    {
      test: /^Frontmatter must be a YAML dictionary$/i,
      format: () =>
        "frontmatter 必须是 YAML 键值对格式，不能是列表或其他类型。",
    },
    {
      test: /^Invalid YAML in frontmatter:(.+)$/i,
      format: (match) =>
        `frontmatter 中的 YAML 语法有误：${match[1]?.trim() ?? "请检查缩进、引号和冒号。"}`,
    },
    {
      test: /^Missing 'name' in frontmatter$/i,
      format: () =>
        "frontmatter 中缺少 name 字段。请填写技能标识，并与左侧「保存名称」一致。",
    },
    {
      test: /^Missing 'description' in frontmatter$/i,
      format: () => "frontmatter 中缺少 description 字段。请填写技能用途说明。",
    },
    {
      test: /^Name cannot be empty$/i,
      format: () => "name 不能为空，请填写有效的技能标识。",
    },
    {
      test: /^Name '(.+)' should be hyphen-case/i,
      format: (match) =>
        `技能名称「${match[1]}」格式无效。请使用小写字母、数字和连字符（例如 sql-check）。`,
    },
    {
      test: /^Name '(.+)' cannot start\/end with hyphen or contain consecutive hyphens$/i,
      format: (match) =>
        `技能名称「${match[1]}」不能以连字符开头或结尾，也不能包含连续连字符。`,
    },
    {
      test: /^Name is too long \((\d+) characters\)\. Maximum is 64 characters\.$/i,
      format: (match) => `技能名称过长（${match[1]} 个字符），最多 64 个字符。`,
    },
    {
      test: /^Description cannot contain angle brackets/i,
      format: () => "description 不能包含尖括号（< 或 >）。",
    },
    {
      test: /^Description is too long \((\d+) characters\)\. Maximum is 1024 characters\.$/i,
      format: (match) =>
        `description 过长（${match[1]} 个字符），最多 1024 个字符。`,
    },
    {
      test: /^Frontmatter name '(.+)' must match requested skill name '(.+)'\.$/i,
      format: (match) =>
        `frontmatter 中的 name（${match[1]}）必须与保存名称（${match[2]}）一致。`,
    },
    {
      test: /^Unexpected key\(s\) in SKILL\.md frontmatter: (.+)\. Allowed properties are: (.+)$/i,
      format: (match) =>
        `frontmatter 包含不支持的字段：${match[1]}。允许的字段有：${match[2]}。`,
    },
    {
      test: /^Security scan blocked the create: (.+)$/i,
      format: (match) =>
        `安全扫描未通过：${match[1]?.trim() ?? "请检查脚本内容后重试。"}`,
    },
  ];

  for (const rule of rules) {
    const match = rule.test.exec(trimmed);
    if (match) {
      return rule.format(match);
    }
  }

  if (/frontmatter/i.test(trimmed)) {
    return `Skill 校验失败：${trimmed}`;
  }

  return trimmed;
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
