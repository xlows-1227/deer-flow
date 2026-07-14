export type ToolDisplayPolicy = {
  safeArgs: Record<string, unknown> | null;
  resultKind: "skill" | "file" | "hidden";
  isProtected: boolean;
  skillName: string | null;
  displayPath: string | null;
  showRawCommand: boolean;
};

function getStringArg(
  args: Record<string, unknown>,
  key: string,
): string | null {
  const value = args[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function getSkillNameFromPath(path: string): string | null {
  const segments = path.replaceAll("\\", "/").split("/").filter(Boolean);
  const skillsIndex = segments.findIndex(
    (segment) => segment.toLowerCase() === "skills",
  );
  if (skillsIndex < 0) return null;
  const category = segments[skillsIndex + 1]?.toLowerCase();
  if (category !== "public" && category !== "custom") return null;
  return segments[skillsIndex + 2] ?? null;
}

export function getToolDisplayPolicy(
  name: string,
  args: Record<string, unknown>,
  options: { visibilityRedacted?: boolean } = {},
): ToolDisplayPolicy {
  const description = getStringArg(args, "description");
  const path = getStringArg(args, "path");
  const serverSkillName = getStringArg(args, "skill_name");
  const inferredSkillName = path ? getSkillNameFromPath(path) : null;
  const skillName = serverSkillName ?? inferredSkillName;
  const isProtected =
    options.visibilityRedacted === true ||
    args.redacted === true ||
    skillName !== null;

  if (name === "read_file") {
    if (isProtected) {
      return {
        safeArgs: {
          ...(description ? { description } : {}),
          ...(skillName ? { skill_name: skillName } : {}),
          redacted: true,
        },
        resultKind: "skill",
        isProtected: true,
        skillName,
        displayPath: skillName,
        showRawCommand: false,
      };
    }
    return {
      safeArgs: {
        ...(description ? { description } : {}),
        ...(path ? { path } : {}),
      },
      resultKind: "file",
      isProtected: false,
      skillName: null,
      displayPath: path,
      showRawCommand: false,
    };
  }

  if (name === "write_file" || name === "str_replace" || name === "ls") {
    return {
      safeArgs: {
        ...(description ? { description } : {}),
        ...(!isProtected && path ? { path } : {}),
        ...(isProtected && skillName ? { skill_name: skillName } : {}),
        ...(isProtected ? { redacted: true } : {}),
      },
      resultKind: "hidden",
      isProtected,
      skillName,
      displayPath: isProtected ? skillName : path,
      showRawCommand: false,
    };
  }

  return {
    safeArgs: null,
    resultKind: "hidden",
    isProtected,
    skillName,
    displayPath: null,
    showRawCommand: false,
  };
}
