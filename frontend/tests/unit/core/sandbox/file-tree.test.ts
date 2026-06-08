import { expect, test } from "vitest";

import type { SandboxFileInfo } from "@/core/sandbox";
import { buildSandboxFileTree } from "@/core/sandbox/file-tree";

const baseFile = (path: string, source: SandboxFileInfo["source"]) => ({
  path,
  name: path.split("/").pop() ?? path,
  size: 1024,
  modified_at: 0,
  source,
  extension: path.split(".").pop() ?? "",
  mime_type: null,
});

test("buildSandboxFileTree keeps real /mnt/user-data path hierarchy", () => {
  const tree = buildSandboxFileTree([
    baseFile("/mnt/user-data/outputs/reports/final.md", "outputs"),
    baseFile("/mnt/user-data/workspace/src/app.ts", "workspace"),
    baseFile("/mnt/user-data/uploads/raw/data.csv", "uploads"),
    baseFile("/mnt/user-data/workspace/README.md", "workspace"),
  ]);

  expect(tree.map((node) => node.name)).toEqual([
    "workspace",
    "uploads",
    "outputs",
  ]);

  const workspace = tree[0]!;
  expect(workspace.type).toBe("directory");
  expect(workspace.children.map((node) => node.name)).toEqual([
    "src",
    "README.md",
  ]);

  const src = workspace.children[0]!;
  expect(src.type).toBe("directory");
  expect(src.children[0]?.path).toBe("/mnt/user-data/workspace/src/app.ts");

  const uploads = tree[1]!;
  expect(uploads.children[0]?.name).toBe("raw");
  expect(uploads.children[0]?.children[0]?.path).toBe(
    "/mnt/user-data/uploads/raw/data.csv",
  );
});
