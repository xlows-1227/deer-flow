import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const detail = {
  id: "pa_ops",
  slug: "ops-desk",
  display_name: "Ops Desk",
  description: "Operations console fixture.",
  avatar_ref: null,
  status: "published",
  current_release_id: "rel_2",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-24T00:00:00Z",
  draft: {
    agent_id: "pa_ops",
    agent_markdown: "# Ops",
    // A managed Soul preset keeps the Studio editor clean, so quota saves are
    // not blocked by the client-side instruction initialization.
    soul_markdown:
      "<!-- deer-flow:soul-preset:professional:v1 -->\n# Personality\nManaged professional personality.",
    model_name: null,
    tool_groups: [],
    quota_overrides: {},
    revision: 5,
    skills: [],
    connector_grants: [],
  },
};

async function mockOpsStudio(page: Page) {
  mockLangGraphAPI(page);
  let ownerOverrides: Record<string, number> = {};

  await page.route("**/api/published-agents/pa_ops", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...detail,
        draft: {
          ...detail.draft,
          quota_overrides: ownerOverrides,
        },
      }),
    }),
  );
  await page.route("**/api/published-agents/pa_ops/draft/options", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ skills: [] }),
    }),
  );
  await page.route("**/api/published-agents/pa_ops/quota", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        agent_id: "pa_ops",
        platform_defaults: {
          max_concurrent_runs: 8,
          daily_runs: 1000,
          daily_tokens: 2000000,
          max_run_seconds: 600,
          max_tokens_per_run: 200000,
          max_input_bytes: 262144,
          inbound_rps: 20,
        },
        owner_overrides: ownerOverrides,
        effective: {
          max_concurrent_runs: 8,
          daily_runs: ownerOverrides.daily_runs ?? 1000,
          daily_tokens: 2000000,
          max_run_seconds: 600,
          max_tokens_per_run: 200000,
          max_input_bytes: 262144,
          inbound_rps: 20,
        },
      }),
    }),
  );
  await page.route("**/api/published-agents/pa_ops/draft", (route) => {
    const body = route.request().postDataJSON();
    expect(body.revision).toBe(5);
    expect(body.quota_overrides).toEqual({ daily_runs: 250 });
    ownerOverrides = body.quota_overrides;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...detail.draft,
        revision: 6,
        quota_overrides: ownerOverrides,
      }),
    });
  });
  await page.route("**/api/published-agents/pa_ops/usage?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        agent_id: "pa_ops",
        days: [
          {
            date: "2026-07-23",
            runs: 2,
            input_tokens: 20,
            output_tokens: 30,
            total_tokens: 50,
            cost_microusd: 125000,
            statuses: { success: 1, failed: 1 },
          },
        ],
        totals: {
          runs: 2,
          input_tokens: 20,
          output_tokens: 30,
          total_tokens: 50,
          cost_microusd: 125000,
        },
        operations: {
          agent_status: "published",
          agent_active: true,
          active_bindings: 1,
          unhealthy_bindings: 1,
          quota_rejections: 2,
          concurrency_saturation: 1,
          feishu_event_latency_ms: { average: 210, p95: 420 },
          connector_failures: 1,
          connector_denials: 2,
          current_release_id: "rel_5",
          current_release_runs: 2,
          current_release_errors: 1,
          current_release_error_rate: 0.5,
        },
      }),
    }),
  );
  await page.route("**/api/published-agents/pa_ops/audit?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "audit_1",
          request_id: "req_12345678",
          source: "api",
          credential_id: "key_1",
          category: "quota",
          action: "post:create_agent_run",
          resource_type: "agent",
          resource_id: "pa_ops",
          skill_name: null,
          method: "POST",
          path_template:
            "/api/v1/agents/{agent_id}/conversations/{conversation_id}/runs",
          status_code: 429,
          duration_ms: 4,
          created_at: "2026-07-24T08:00:00Z",
        },
      ]),
    }),
  );
  await page.route("**/api/published-agents/pa_ops/keys", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    }),
  );
  await page.route("**/api/connectors", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connectors: [] }),
    }),
  );
  await page.route("**/api/connector-types", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connector_types: [] }),
    }),
  );
}

test("shows inherited quota limits and saves a bounded owner override", async ({
  page,
}) => {
  await mockOpsStudio(page);
  await page.goto("/workspace/agents/pa_ops");
  await page.getByRole("tab", { name: "Operations" }).click();

  await expect(
    page.getByText(
      "Blank means inherit the platform default — never unlimited.",
    ),
  ).toBeVisible();
  await expect(page.getByText("50.0%").first()).toBeVisible();
  await expect(page.getByText("Estimated cost")).toBeVisible();
  await expect(page.getByText("$0.125")).toBeVisible();
  await expect(page.getByText("Current Release errors")).toBeVisible();
  const quotaMetric = page.getByText("Quota rejections").locator("..");
  await expect(quotaMetric.locator("p").nth(1)).toContainText("2");
  await expect(quotaMetric.locator("p").nth(1)).toContainText("1 saturation");
  await expect(page.getByText("420 ms")).toBeVisible();
  const connectorMetric = page.getByText("Connector failures").locator("..");
  await expect(connectorMetric.locator("p").nth(1)).toContainText("1");
  await expect(connectorMetric.locator("p").nth(1)).toContainText("2 denied");
  const bindingMetric = page.getByText("Active bindings").locator("..");
  await expect(bindingMetric.locator("p").nth(1)).toContainText("1");
  await expect(bindingMetric.locator("p").nth(1)).toContainText("1 unhealthy");
  await expect(
    page.getByText("Quota rejection", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("raw external user prompt")).toHaveCount(0);

  const dailyRuns = page.getByLabel("Daily runs override");
  await dailyRuns.fill("1001");
  await expect(page.getByText("Must not exceed 1,000.")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Save quota draft" }),
  ).toBeDisabled();

  await dailyRuns.fill("250");
  await page.getByRole("button", { name: "Save quota draft" }).click();
  await expect(page.getByText("Quota draft saved")).toBeVisible();
});
