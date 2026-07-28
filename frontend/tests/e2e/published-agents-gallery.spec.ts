import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const agents = [
  {
    id: "pa_research",
    slug: "researcher",
    display_name: "Research Desk",
    description: "Finds and verifies primary sources.",
    avatar_ref: null,
    status: "published",
    current_release_id: "rel_3",
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
  },
  {
    id: "pa_ops",
    slug: "ops-bot",
    display_name: "Ops Bot",
    description: "Keeps production healthy.",
    avatar_ref: null,
    status: "suspended",
    current_release_id: "rel_1",
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-19T00:00:00Z",
  },
  {
    id: "pa_draft",
    slug: "draft-bot",
    display_name: "Draft Bot",
    description: "Not published yet.",
    avatar_ref: null,
    status: "draft",
    current_release_id: null,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:00:00Z",
  },
];

async function mockPublishedAgentConsole(
  page: Parameters<typeof mockLangGraphAPI>[0],
) {
  mockLangGraphAPI(page);

  await page.route("**/api/published-agents", async (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          ...agents[0],
          id: "pa_new",
          slug: "release-notes",
          display_name: "Release Notes",
          description: null,
          status: "draft",
          current_release_id: null,
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(agents),
    });
  });

  await page.route("**/api/published-agents/*/releases", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "rel_3",
          agent_id: "pa_research",
          release_no: 3,
          agent_markdown: "# Research",
          soul_markdown: "",
          model_name: "deepseek",
          tool_groups: [],
          quota_overrides: {},
          manifest_checksum: "abc",
          created_by: "owner",
          created_at: "2026-07-20T08:00:00Z",
          skills: [],
          connector_grants: [],
        },
      ]),
    }),
  );
  await page.route("**/api/published-agents/*/keys", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "key_1",
          agent_id: "pa_research",
          name: "Production",
          key_prefix: "dfa_prod",
          last_four: "1234",
          status: "active",
          quota_overrides: {},
          created_at: "2026-07-20T08:00:00Z",
          last_used_at: null,
          expires_at: null,
          revoked_at: null,
          rotation_of: null,
        },
      ]),
    }),
  );
  await page.route("**/api/published-agents/*/channels", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    }),
  );
  await page.route("**/api/published-agents/*/usage?days=7", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        agent_id: "pa_research",
        days: [
          {
            date: "2026-07-19",
            runs: 2,
            input_tokens: 10,
            output_tokens: 20,
            total_tokens: 30,
            statuses: { success: 2 },
          },
          {
            date: "2026-07-20",
            runs: 5,
            input_tokens: 20,
            output_tokens: 40,
            total_tokens: 60,
            statuses: { success: 5 },
          },
        ],
        totals: {
          runs: 7,
          input_tokens: 30,
          output_tokens: 60,
          total_tokens: 90,
        },
      }),
    }),
  );
}

test.describe("Published Agent Gallery", () => {
  test("renders owner-only state, release and integration summaries", async ({
    page,
  }) => {
    await mockPublishedAgentConsole(page);

    await page.goto("/workspace/agents");

    await expect(page.getByRole("heading", { name: "Agent control" })).toBeVisible();
    await expect(page.getByText("Research Desk")).toBeVisible();
    const researchCard = page.getByTestId("published-agent-pa_research");
    await expect(
      researchCard.getByText("Published", { exact: true }).first(),
    ).toBeVisible();
    await expect(researchCard.getByText("Release 3")).toBeVisible();
    await expect(researchCard.getByText("1 API key")).toBeVisible();
    await expect(researchCard.getByText("7 runs")).toBeVisible();
    await expect(page.getByText("Marketplace")).toHaveCount(0);
    await expect(page.getByText("Share")).toHaveCount(0);
  });

  test("creates a stable draft identity and opens Studio", async ({ page }) => {
    await mockPublishedAgentConsole(page);

    await page.goto("/workspace/agents");
    await page.getByRole("button", { name: "New Agent" }).click();
    await page.getByLabel("Agent slug").fill("release-notes");
    await page.getByLabel("Display name").fill("Release Notes");
    await page.getByRole("button", { name: "Create draft" }).click();

    await expect(page).toHaveURL(/\/workspace\/agents\/pa_new$/);
  });

  test("does not offer suspend or resume for an unpublished draft", async ({
    page,
  }) => {
    await mockPublishedAgentConsole(page);
    await page.goto("/workspace/agents");

    const draftCard = page.getByTestId("published-agent-pa_draft");
    await draftCard.getByRole("button", { name: "Agent actions" }).click();

    await expect(page.getByRole("menuitem", { name: "Suspend" })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: "Resume" })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: "Archive" })).toBeVisible();
  });
});
