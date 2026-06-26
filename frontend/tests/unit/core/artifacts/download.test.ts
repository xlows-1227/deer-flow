import { describe, expect, it, vi, afterEach } from "vitest";

import { downloadArtifactFile } from "@/core/artifacts/download";

describe("downloadArtifactFile", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("requests the artifact download URL with credentials", async () => {
    const click = vi.fn();
    vi.stubGlobal("document", {
      body: {
        appendChild: vi.fn(),
        removeChild: vi.fn(),
      },
      createElement: () => ({
        click,
        style: {},
      }),
    });
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:mock"),
      revokeObjectURL: vi.fn(),
    });

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        headers: {
          get: (name: string) =>
            name === "Content-Disposition"
              ? "attachment; filename*=UTF-8''deerflow-layered-architecture.drawio"
              : null,
        },
        blob: async () =>
          new Blob(["<mxfile></mxfile>"], { type: "application/xml" }),
      }),
    );

    await downloadArtifactFile({
      filepath: "/mnt/user-data/outputs/deerflow-layered-architecture.drawio",
      threadId: "thread-1",
    });

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/threads/thread-1/artifacts/mnt/user-data/outputs/deerflow-layered-architecture.drawio?download=true",
      ),
      { credentials: "include" },
    );
    expect(click).toHaveBeenCalled();
  });

  it("throws when the response is not ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
      }),
    );

    await expect(
      downloadArtifactFile({
        filepath: "/mnt/user-data/outputs/missing.drawio",
        threadId: "thread-1",
      }),
    ).rejects.toThrow("Download failed (404)");
  });
});
