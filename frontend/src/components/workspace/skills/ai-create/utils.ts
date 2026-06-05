import type { BaseStream } from "@langchain/langgraph-sdk/react";

import { extractTextFromMessage } from "@/core/messages/utils";
import type { SkillFileEntry } from "@/core/skills/type";
import type { ToolEndEvent } from "@/core/threads/hooks";
import type { AgentThreadState } from "@/core/threads/types";

export interface SkillFileNode {
  path: string;
  name: string;
  type: "file" | "directory";
  size?: number | null;
  children?: SkillFileNode[];
}

export function buildSkillFileTree(files: SkillFileEntry[]): SkillFileNode[] {
  const root: SkillFileNode[] = [];
  const dirMap = new Map<string, SkillFileNode>();

  const sorted = [...files].sort((a, b) => a.path.localeCompare(b.path));

  for (const file of sorted) {
    const parts = file.path.split("/");
    const name = parts.at(-1) ?? file.path;
    const node: SkillFileNode = {
      path: file.path,
      name,
      type: file.type,
      size: file.size,
      children: file.type === "directory" ? [] : undefined,
    };

    if (parts.length === 1) {
      root.push(node);
      if (file.type === "directory") {
        dirMap.set(file.path, node);
      }
      continue;
    }

    const parentPath = parts.slice(0, -1).join("/");
    const parent = dirMap.get(parentPath);
    if (parent?.children) {
      parent.children.push(node);
    } else {
      root.push(node);
    }
    if (file.type === "directory") {
      dirMap.set(file.path, node);
    }
  }

  return root;
}

function readSkillManagePayload(event: ToolEndEvent) {
  if (event.name !== "skill_manage") {
    return null;
  }
  const data = event.data as Record<string, unknown> | undefined;
  const input = data?.input as Record<string, unknown> | undefined;
  const output = typeof data?.output === "string" ? data.output : undefined;
  return { input, output };
}

export function truncateConversationTopic(text: string, maxLength = 48) {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength)}…`;
}

export function resolveSkillConversationTopic(
  thread: BaseStream<AgentThreadState>,
  fallback = "未命名话题",
) {
  const title = thread.values?.title?.trim();
  if (title) {
    return truncateConversationTopic(title) || fallback;
  }

  for (const message of thread.messages) {
    if (message.type !== "human") continue;
    const text = extractTextFromMessage(message);
    if (text) {
      return truncateConversationTopic(text) || fallback;
    }
  }

  return fallback;
}

export function extractSkillManageName(event: ToolEndEvent): string | null {
  const payload = readSkillManagePayload(event);
  if (!payload) return null;

  if (typeof payload.input?.name === "string" && payload.input.name.trim()) {
    return payload.input.name.trim();
  }

  if (payload.output) {
    const match =
      /custom skill '([^']+)'/i.exec(payload.output) ??
      /skill '([^']+)'/i.exec(payload.output);
    if (match?.[1]) {
      return match[1];
    }
  }

  return null;
}

export function extractSkillManageServerPath(
  event: ToolEndEvent,
): string | null {
  const payload = readSkillManagePayload(event);
  if (!payload?.input) return null;

  const action = payload.input.action;
  if (typeof action !== "string") return null;

  if (action === "write_file" || action === "remove_file") {
    const path = payload.input.path;
    return typeof path === "string" && path.trim() ? path.trim() : null;
  }

  if (action === "create" || action === "edit" || action === "patch") {
    return "SKILL.md";
  }

  return null;
}

export function isMarkdownPath(path: string) {
  return path.toLowerCase().endsWith(".md");
}

const SUPPORT_ROOTS = [
  "skills",
  "references",
  "templates",
  "scripts",
  "assets",
] as const;

export function getDefaultSupportDirectory() {
  return SUPPORT_ROOTS[0];
}

export function joinSkillRelativePath(parent: string, name: string) {
  const trimmedName = name.trim().replace(/^\/+|\/+$/g, "");
  if (!trimmedName) {
    return "";
  }
  const trimmedParent = parent.trim().replace(/^\/+|\/+$/g, "");
  if (!trimmedParent) {
    return trimmedName;
  }
  return `${trimmedParent}/${trimmedName}`;
}

export function resolveUploadRelativePath(file: File, baseDirectory: string) {
  const relative = file.webkitRelativePath;
  if (relative) {
    return joinSkillRelativePath(baseDirectory, relative);
  }
  return joinSkillRelativePath(baseDirectory, file.name);
}

export function getParentDirectory(path: string | null) {
  if (!path || path === "SKILL.md") {
    return getDefaultSupportDirectory();
  }
  if (!path.includes("/")) {
    return getDefaultSupportDirectory();
  }
  return path.split("/").slice(0, -1).join("/");
}

export function getDefaultExpandedPaths(files: SkillFileEntry[]) {
  const paths = new Set<string>();
  for (const file of files) {
    const parts = file.path.split("/");
    if (parts.length > 1) {
      paths.add(parts.slice(0, -1).join("/"));
    }
  }
  return paths;
}
