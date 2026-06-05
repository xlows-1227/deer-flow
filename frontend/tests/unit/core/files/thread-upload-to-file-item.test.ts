/**
 * Tests for the file-management page's thread-upload normalization.
 *
 * The hook stitches library files (from `/api/files`) and per-thread
 * chat uploads (from `/api/threads/{id}/uploads/list`) into one list;
 * the `@`-mention picker only ever sees the library half, so the
 * normalization lives in `core/files/api.ts` rather than the picker.
 */

import { describe, expect, test } from "vitest";

import { threadUploadToFileItem } from "@/core/files/api";

describe("threadUploadToFileItem", () => {
  const baseUpload = {
    filename: "report.pdf",
    size: "12345",
    path: "/mnt/user-data/uploads/report.pdf",
    virtual_path: "/mnt/user-data/uploads/report.pdf",
    artifact_url: "/api/threads/thread-abc/uploads/report.pdf",
    extension: ".pdf",
    modified: 1_700_000_000,
  };

  test("normalizes a thread upload to the FileItem shape", () => {
    const item = threadUploadToFileItem(baseUpload, "thread-abc", "Q3 plan");
    expect(item).toMatchObject({
      id: "thread:thread-abc:report.pdf",
      name: "report.pdf",
      path: "report.pdf",
      kind: "file",
      source: "uploaded",
      extension: ".pdf",
      size: 12345,
      modified_at: new Date(1_700_000_000 * 1000).toISOString(),
      preview_url: baseUpload.artifact_url,
      download_url: baseUpload.artifact_url,
      source_thread_id: "thread-abc",
      source_thread_title: "Q3 plan",
    });
  });

  test("derives the extension from the filename when missing", () => {
    const item = threadUploadToFileItem(
      { ...baseUpload, extension: undefined, filename: "data.CSV" },
      "thread-abc",
    );
    expect(item.extension).toBe(".csv");
  });

  test("handles a string size of 0", () => {
    const item = threadUploadToFileItem(
      { ...baseUpload, size: "0" },
      "thread-abc",
    );
    expect(item.size).toBe(0);
  });

  test("falls back to the current timestamp when modified is missing", () => {
    const before = Date.now();
    const item = threadUploadToFileItem(
      { ...baseUpload, modified: undefined },
      "thread-abc",
    );
    const after = Date.now();
    // Modified_iso should be roughly now (within the test execution window).
    const ts = new Date(item.modified_at).getTime();
    expect(ts).toBeGreaterThanOrEqual(before);
    expect(ts).toBeLessThanOrEqual(after);
  });

  test("leaves source_thread_title undefined when not provided", () => {
    const item = threadUploadToFileItem(baseUpload, "thread-abc");
    expect(item.source_thread_title).toBeUndefined();
  });
});
