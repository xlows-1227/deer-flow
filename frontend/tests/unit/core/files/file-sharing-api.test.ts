import { beforeEach, describe, expect, test, vi } from "vitest";

const fetchWithAuth = vi.fn();

vi.mock("@/core/api/fetcher", () => ({
  fetch: fetchWithAuth,
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

beforeEach(() => {
  fetchWithAuth.mockReset();
});

describe("file sharing api", () => {
  test("allows shared HTML interactions without granting same-origin access", async () => {
    const { SHARED_HTML_IFRAME_SANDBOX } = await import("@/core/files/sharing");

    expect(SHARED_HTML_IFRAME_SANDBOX.split(" ")).toEqual(
      expect.arrayContaining(["allow-scripts", "allow-forms"]),
    );
    expect(SHARED_HTML_IFRAME_SANDBOX).not.toContain("allow-same-origin");
  });

  test("maps files shared by registered users into read-only file items", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({
        total: 1,
        items: [
          {
            id: "share-1",
            name: "notes.md",
            size: 20,
            mime_type: "text/markdown",
            extension: ".md",
            modified_at: "2026-07-13T08:00:00Z",
            shared_at: "2026-07-13T09:00:00Z",
            owner_email: "owner@example.com",
            source_type: "library",
            preview_url: "/api/file-shares/share-1/content",
            download_url: "/api/file-shares/share-1/content?download=true",
          },
        ],
      }),
    });

    const { listReceivedFileShares } = await import("@/core/files/sharing");
    await expect(listReceivedFileShares()).resolves.toMatchObject([
      {
        id: "share:share-1",
        name: "notes.md",
        path: "@shared/share-1",
        shared_file_id: "share-1",
        shared_by_email: "owner@example.com",
      },
    ]);
    expect(fetchWithAuth).toHaveBeenCalledWith(
      "http://localhost:8001/api/file-shares",
      { method: "GET" },
    );
  });

  test("shares a generated conversation file by recipient account email", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({ id: "share-2" }),
    });
    const { shareFileWithUser } = await import("@/core/files/sharing");

    await shareFileWithUser(
      {
        id: "artifact",
        name: "report.html",
        path: "/mnt/user-data/outputs/report.html",
        kind: "file",
        source: "generated",
        size: 12,
        mime_type: "text/html",
        extension: ".html",
        modified_at: "2026-07-13T08:00:00Z",
        preview_url: null,
        download_url: null,
        source_thread_id: "thread-1",
        conversation_source: "generated",
      },
      " recipient@example.com ",
    );

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "http://localhost:8001/api/file-shares",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          recipient_email: "recipient@example.com",
          source_type: "conversation_generated",
          path: "/mnt/user-data/outputs/report.html",
          thread_id: "thread-1",
        }),
      }),
    );
  });

  test("loads shared markdown and html through the authenticated content endpoint", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      text: async () => "# Shared",
    });
    const { loadSharedFileText } = await import("@/core/files/sharing");

    await expect(loadSharedFileText("share/id")).resolves.toBe("# Shared");
    expect(fetchWithAuth).toHaveBeenCalledWith(
      "http://localhost:8001/api/file-shares/share%2Fid/content",
      { method: "GET" },
    );
  });
});
