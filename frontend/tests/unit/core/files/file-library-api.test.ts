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

describe("file library api", () => {
  test("listFiles forwards folder_path to the backend", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({
        folder_path: "Reports/2026",
        items: [],
        total: 0,
      }),
    });

    const { listFiles } = await import("@/core/files/api");

    await expect(
      listFiles({
        folder_path: "Reports/2026",
        source: "uploaded",
        q: "summary",
      }),
    ).resolves.toEqual([]);

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "http://localhost:8001/api/files?folder_path=Reports%2F2026&source=uploaded&q=summary",
      { method: "GET" },
    );
  });

  test("loads folder destinations and upload size configuration", async () => {
    fetchWithAuth
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ folders: ["Reports", "Reports/2026"] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          max_upload_bytes: 50 * 1024 * 1024,
          max_upload_label: "50 MiB",
        }),
      });

    const { getUserFileUploadConfig, listUserFolders } =
      await import("@/core/files/api");

    await expect(listUserFolders()).resolves.toEqual([
      "Reports",
      "Reports/2026",
    ]);
    await expect(getUserFileUploadConfig()).resolves.toEqual({
      max_upload_bytes: 50 * 1024 * 1024,
      max_upload_label: "50 MiB",
    });
  });
});
