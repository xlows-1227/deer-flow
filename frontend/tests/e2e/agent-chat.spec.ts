import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const MOCK_AGENTS = [
  {
    name: "test-agent",
    description: "A test agent for E2E tests",
    system_prompt: "You are a test agent.",
  },
];

test.describe("Agent chat", () => {
  test("agent gallery page loads and shows agents", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents");

    // The agent card should appear with the agent name
    await expect(
      page.getByRole("heading", { name: "test-agent", exact: true }),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("agent chat page loads with input box", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/test-agent/chats/new");

    // The prompt input textarea should be visible
    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await expect(
      page.locator("header").getByRole("button", { name: "New chat" }),
    ).toHaveCount(0);
  });

  test("agent chat page shows agent badge", async ({ page }) => {
    mockLangGraphAPI(page, { agents: MOCK_AGENTS });

    await page.goto("/workspace/agents/test-agent/chats/new");

    // The agent badge should display in the header (scoped to header to avoid
    // matching the welcome area which also shows the agent name)
    await expect(
      page.locator("header span", { hasText: "test-agent" }),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("draft sandbox chat only exposes its frozen skills and connectors", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      agents: [
        {
          name: "scope-agent",
          description: "Sandbox-scoped agent",
          system_prompt: "Use only the selected capabilities.",
        },
      ],
      threads: [
        {
          thread_id: "thread-sandbox",
          agent_name: "scope-agent",
          title: "Sandbox test",
          messages: [],
        },
      ],
    });
    await page.route("**/api/langgraph/threads/thread-sandbox", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          thread_id: "thread-sandbox",
          status: "idle",
          created_at: "2026-07-26T00:00:00Z",
          updated_at: "2026-07-26T00:00:00Z",
          metadata: {
            draft_sandbox: true,
            draft_sandbox_agent_id: "pa-scope",
            draft_sandbox_revision: 3,
            draft_sandbox_billable: false,
          },
          values: {},
        }),
      }),
    );
    await page.route(
      "**/api/published-agents/draft/sandbox-threads/thread-sandbox",
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            agent_id: "pa-scope",
            agent_slug: "scope-agent",
            thread_id: "thread-sandbox",
            draft_revision: 3,
            skill_names: ["selected-skill"],
            connector_ids: [],
            billable: false,
          }),
        }),
    );
    await page.route("**/api/skills", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          skills: [
            {
              name: "selected-skill",
              display_name: "Selected Skill",
              description: "Allowed by the draft",
              description_zh: null,
              category: "public",
              license: null,
              enabled: true,
            },
            {
              name: "unselected-skill",
              display_name: "Unselected Skill",
              description: "Not allowed by the draft",
              description_zh: null,
              category: "public",
              license: null,
              enabled: true,
            },
          ],
        }),
      }),
    );
    await page.route("**/api/connectors", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          connectors: [
            {
              id: "unselected-connector",
              name: "unselected-connector",
              display_name: "Unselected Connector",
              type: "mysql",
              status: "active",
              config: {},
              default_policy: {},
              health: {},
            },
          ],
        }),
      }),
    );

    await page.goto("/workspace/agents/scope-agent/chats/thread-sandbox");

    const skillButton = page.getByRole("button", { name: "Skill" });
    await expect(skillButton).toBeVisible({ timeout: 15_000 });
    await skillButton.click();
    await expect(
      page.getByText("Selected Skill", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Unselected Skill", { exact: true }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Connector" })).toHaveCount(
      0,
    );
  });
});
