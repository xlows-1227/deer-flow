import { describe, expect, test } from "vitest";

import {
  CONVERSATION_SYSTEM_FOLDERS,
  SYSTEM_FOLDERS,
  isReservedSystemFolderPath,
  threadArtifactToFileItem,
  threadGeneratedFileUrl,
} from "@/core/files/conversation";

describe("conversation file collections", () => {
  test("exposes uploads and generated artifacts as locked system folders", () => {
    expect(CONVERSATION_SYSTEM_FOLDERS).toMatchObject([
      {
        name: "对话上传",
        kind: "folder",
        source: "uploaded",
        system_folder: "uploaded",
      },
      {
        name: "对话生成",
        kind: "folder",
        source: "generated",
        system_folder: "generated",
      },
    ]);
    expect(SYSTEM_FOLDERS[2]).toMatchObject({
      name: "他人分享",
      kind: "folder",
      system_folder: "shared",
    });
  });

  test("reserves the system folder roots as manual upload destinations", () => {
    expect(isReservedSystemFolderPath("@conversation/uploaded")).toBe(true);
    expect(isReservedSystemFolderPath("@shared")).toBe(true);
    expect(isReservedSystemFolderPath("对话上传")).toBe(false);
    expect(isReservedSystemFolderPath("项目/对话上传")).toBe(false);
  });

  test("normalizes generated artifacts with their source conversation", () => {
    const item = threadArtifactToFileItem(
      "/mnt/user-data/outputs/Report.PDF",
      "thread-abc",
      "季度报告",
      "2026-07-01T08:30:00Z",
    );

    expect(item).toMatchObject({
      id: "thread:thread-abc:artifact:/mnt/user-data/outputs/Report.PDF",
      name: "Report.PDF",
      path: "/mnt/user-data/outputs/Report.PDF",
      source: "generated",
      extension: ".pdf",
      modified_at: "2026-07-01T08:30:00.000Z",
      source_thread_id: "thread-abc",
      source_thread_title: "季度报告",
      conversation_source: "generated",
    });
    expect(item.preview_url).toBe(
      threadGeneratedFileUrl("thread-abc", "/mnt/user-data/outputs/Report.PDF"),
    );
    expect(item.download_url).toBe(`${item.preview_url}?download=true`);
  });
});
