import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const fetchWithAuth = vi.fn();
const publicFetch = vi.fn();

vi.mock("@/core/api/fetcher", () => ({
  fetch: fetchWithAuth,
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

const generatedHtml = {
  id: "artifact",
  name: "report.html",
  path: "/mnt/user-data/outputs/report.html",
  kind: "file" as const,
  source: "generated" as const,
  size: 12,
  mime_type: "text/html",
  extension: ".html",
  modified_at: "2026-07-15T08:00:00Z",
  preview_url: null,
  download_url: null,
  source_thread_id: "thread-1",
  conversation_source: "generated" as const,
};

beforeEach(() => {
  fetchWithAuth.mockReset();
  publicFetch.mockReset();
  vi.stubGlobal("fetch", publicFetch);
});

afterEach(() => vi.unstubAllGlobals());

describe("file publication api", () => {
  test("only conversation-generated HTML files are publishable", async () => {
    const { isPublishableGeneratedHtml, publishGeneratedHtml } =
      await import("@/core/files/publication");

    expect(isPublishableGeneratedHtml(generatedHtml)).toBe(true);
    const generatedPdf = {
      ...generatedHtml,
      name: "report.pdf",
      extension: ".pdf",
      mime_type: "application/pdf",
    };
    expect(isPublishableGeneratedHtml(generatedPdf)).toBe(false);
    await expect(publishGeneratedHtml(generatedPdf)).rejects.toThrow(
      "只能发布对话生成的 HTML 文件",
    );
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });

  test("publishes a conversation-generated HTML file", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({
        id: "publication-1",
        public_token: "public-token",
        public_url: "/published/public-token",
      }),
    });
    const { publishGeneratedHtml } = await import("@/core/files/publication");

    await publishGeneratedHtml(generatedHtml);

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "http://localhost:8001/api/file-publications",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          thread_id: "thread-1",
          path: "/mnt/user-data/outputs/report.html",
        }),
      }),
    );
  });

  test("lists and cancels the current user's publications", async () => {
    const publication = {
      id: "publication-1",
      name: "report.html",
      thread_id: "thread-1",
      path: "/mnt/user-data/outputs/report.html",
      public_token: "public-token",
      public_url: "/published/public-token",
      created_at: "2026-07-15T08:00:00Z",
    };
    fetchWithAuth
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [publication], total: 1 }),
      })
      .mockResolvedValueOnce({ ok: true });
    const { cancelFilePublication, listFilePublications } =
      await import("@/core/files/publication");

    await expect(listFilePublications()).resolves.toEqual([publication]);
    await cancelFilePublication("publication/id");

    expect(fetchWithAuth).toHaveBeenLastCalledWith(
      "http://localhost:8001/api/file-publications/publication%2Fid",
      { method: "DELETE" },
    );
  });

  test("loads public HTML without the authenticated fetch wrapper", async () => {
    publicFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          name: "report.html",
          content_url: "/api/public-files/public-token/content",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => "<button>run</button>",
      });
    const { loadPublishedHtml } = await import("@/core/files/publication");

    await expect(loadPublishedHtml("public-token")).resolves.toEqual({
      name: "report.html",
      html: "<button>run</button>",
    });
    expect(publicFetch).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8001/api/public-files/public-token",
      { cache: "no-store" },
    );
    expect(publicFetch).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8001/api/public-files/public-token/content",
      { cache: "no-store" },
    );
    expect(fetchWithAuth).not.toHaveBeenCalled();
  });
});
