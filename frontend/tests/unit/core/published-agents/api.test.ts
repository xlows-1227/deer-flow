import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.fn();

vi.mock("@/core/api/fetcher", () => ({
  fetch: fetchWithAuth,
}));

beforeEach(() => {
  fetchWithAuth.mockReset();
});

describe("published agents API", () => {
  it("uses the owner control-plane URL when listing agents", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: "pa_1",
          slug: "researcher",
          display_name: "Researcher",
          status: "published",
        },
      ],
    });

    const { listPublishedAgents } = await import(
      "@/core/published-agents/api"
    );

    await expect(listPublishedAgents()).resolves.toHaveLength(1);
    expect(fetchWithAuth).toHaveBeenCalledWith("/api/published-agents", {
      method: "GET",
    });
  });

  it("PATCHes a revisioned draft bundle", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({
        agent_id: "pa_1",
        revision: 5,
        agent_markdown: "# Agent",
        soul_markdown: "",
        model_name: null,
        tool_groups: [],
        quota_overrides: {},
        skills: [],
        connector_grants: [],
      }),
    });

    const { updateAgentDraft } = await import("@/core/published-agents/api");
    await updateAgentDraft("pa_1", {
      revision: 4,
      agent_markdown: "# Agent",
    });

    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/published-agents/pa_1/draft",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          revision: 4,
          agent_markdown: "# Agent",
        }),
      }),
    );
  });

  it("starts a dedicated non-billable draft sandbox run", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: true,
      json: async () => ({
        agent_id: "pa_1",
        thread_id: "thread_1",
        run_id: "run_1",
        status: "pending",
        draft_revision: 5,
        billable: false,
      }),
    });

    const { createDraftSandboxRun } = await import(
      "@/core/published-agents/api"
    );
    await expect(
      createDraftSandboxRun("pa_1", "Use the saved draft."),
    ).resolves.toMatchObject({
      draft_revision: 5,
      billable: false,
    });
    expect(fetchWithAuth).toHaveBeenCalledWith(
      "/api/published-agents/pa_1/draft/sandbox-runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ message: "Use the saved draft." }),
      }),
    );
  });

  it("maps a 409 revision conflict to a dedicated error", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: false,
      status: 409,
      statusText: "Conflict",
      json: async () => ({
        detail: {
          code: "revision_conflict",
          message: "Draft revision is stale",
        },
      }),
    });

    const { DraftRevisionConflictError, updateAgentDraft } = await import(
      "@/core/published-agents/api"
    );

    await expect(
      updateAgentDraft("pa_1", { revision: 1, soul_markdown: "# Soul" }),
    ).rejects.toBeInstanceOf(DraftRevisionConflictError);
  });

  it("maps a 429 response to a dedicated quota error", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: false,
      status: 429,
      statusText: "Too Many Requests",
      json: async () => ({
        detail: { code: "daily_runs_exceeded", message: "Daily run limit" },
      }),
    });

    const { QuotaExceededError, getAgentUsage } = await import(
      "@/core/published-agents/api"
    );

    await expect(getAgentUsage("pa_1")).rejects.toBeInstanceOf(
      QuotaExceededError,
    );
  });

  it("preserves publish violations for field-level rendering", async () => {
    fetchWithAuth.mockResolvedValue({
      ok: false,
      status: 422,
      statusText: "Unprocessable Entity",
      json: async () => ({
        detail: {
          code: "publish_validation_failed",
          violations: [
            {
              code: "MISSING_INSTRUCTIONS",
              message: "Add AGENT.md or SOUL.md",
              field: "agent_markdown",
            },
          ],
        },
      }),
    });

    const { PublishValidationError, publishAgent } = await import(
      "@/core/published-agents/api"
    );

    try {
      await publishAgent("pa_1");
      throw new Error("Expected publishAgent to reject");
    } catch (error) {
      expect(error).toBeInstanceOf(PublishValidationError);
      expect((error as InstanceType<typeof PublishValidationError>).violations)
        .toEqual([
          {
            code: "MISSING_INSTRUCTIONS",
            message: "Add AGENT.md or SOUL.md",
            field: "agent_markdown",
          },
        ]);
    }
  });
});
