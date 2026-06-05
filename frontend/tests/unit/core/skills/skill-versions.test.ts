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

describe("skill version api", () => {
  test("listCustomSkillVersions returns version list", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({
        versions: [
          {
            seq: 2,
            created_at: "2026-06-05T00:00:00Z",
            author: "human",
            action: "edit",
            message: null,
            label: null,
            thread_id: null,
            file_count: 1,
            size_bytes: 10,
          },
        ],
      }),
    });

    const { listCustomSkillVersions } = await import("@/core/skills/api");

    await expect(listCustomSkillVersions("demo-skill")).resolves.toEqual([
      expect.objectContaining({ seq: 2, action: "edit" }),
    ]);

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "http://localhost:8001/api/skills/custom/demo-skill/versions",
    );
  });

  test("createCustomSkillVersionSnapshot posts snapshot metadata", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({
        seq: 3,
        created_at: "2026-06-05T00:00:00Z",
        author: "human",
        action: "edit",
        message: "applied changes",
        label: null,
        thread_id: null,
        file_count: 2,
        size_bytes: 20,
      }),
    });

    const { createCustomSkillVersionSnapshot } =
      await import("@/core/skills/api");

    await expect(
      createCustomSkillVersionSnapshot("demo-skill", {
        action: "edit",
        message: "applied changes",
      }),
    ).resolves.toMatchObject({ seq: 3, action: "edit" });

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "http://localhost:8001/api/skills/custom/demo-skill/versions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "edit",
          message: "applied changes",
        }),
      }),
    );
  });

  test("restoreCustomSkillVersion posts restore request", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({
        version: {
          seq: 4,
          created_at: "2026-06-05T00:00:00Z",
          author: "human",
          action: "restore",
          message: "restored from 1",
          label: null,
          thread_id: null,
          restored_from: 1,
          file_count: 2,
          size_bytes: 20,
        },
      }),
    });

    const { restoreCustomSkillVersion } = await import("@/core/skills/api");

    await expect(restoreCustomSkillVersion("demo-skill", 1)).resolves.toEqual({
      version: expect.objectContaining({ restored_from: 1 }),
    });

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "http://localhost:8001/api/skills/custom/demo-skill/versions/1/restore",
      { method: "POST" },
    );
  });
});
