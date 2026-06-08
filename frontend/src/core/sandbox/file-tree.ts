import type { SandboxFileInfo } from "./api";

export interface SandboxFileTreeNode {
  type: "directory" | "file";
  name: string;
  path: string;
  source: SandboxFileInfo["source"];
  children: SandboxFileTreeNode[];
  file?: SandboxFileInfo;
}

const ROOT_ORDER = ["workspace", "uploads", "outputs", "user-data"];

function pathParts(path: string) {
  return path
    .replace(/^\/mnt\/user-data\/?/, "")
    .split("/")
    .filter(Boolean);
}

function compareTreeNodes(a: SandboxFileTreeNode, b: SandboxFileTreeNode) {
  if (a.type !== b.type) {
    return a.type === "directory" ? -1 : 1;
  }

  const aRootIndex = ROOT_ORDER.indexOf(a.name);
  const bRootIndex = ROOT_ORDER.indexOf(b.name);
  if (aRootIndex !== -1 || bRootIndex !== -1) {
    return (
      (aRootIndex === -1 ? ROOT_ORDER.length : aRootIndex) -
      (bRootIndex === -1 ? ROOT_ORDER.length : bRootIndex)
    );
  }

  return a.name.localeCompare(b.name);
}

function sortTree(nodes: SandboxFileTreeNode[]) {
  nodes.sort(compareTreeNodes);
  for (const node of nodes) {
    if (node.type === "directory") {
      sortTree(node.children);
    }
  }
}

export function buildSandboxFileTree(
  files: SandboxFileInfo[],
): SandboxFileTreeNode[] {
  const roots: SandboxFileTreeNode[] = [];
  const directories = new Map<string, SandboxFileTreeNode>();

  const ensureDirectory = (
    name: string,
    path: string,
    source: SandboxFileInfo["source"],
    siblings: SandboxFileTreeNode[],
  ) => {
    const existing = directories.get(path);
    if (existing) return existing;

    const directory: SandboxFileTreeNode = {
      type: "directory",
      name,
      path,
      source,
      children: [],
    };
    directories.set(path, directory);
    siblings.push(directory);
    return directory;
  };

  for (const file of files) {
    const parts = pathParts(file.path);
    if (parts.length === 0) continue;

    let siblings = roots;
    let currentPath = "/mnt/user-data";
    for (const [index, part] of parts.entries()) {
      currentPath = `${currentPath}/${part}`;
      const isFile = index === parts.length - 1;

      if (isFile) {
        siblings.push({
          type: "file",
          name: file.name || part,
          path: file.path,
          source: file.source,
          children: [],
          file,
        });
        continue;
      }

      const directory = ensureDirectory(
        part,
        currentPath,
        file.source,
        siblings,
      );
      siblings = directory.children;
    }
  }

  sortTree(roots);
  return roots;
}
