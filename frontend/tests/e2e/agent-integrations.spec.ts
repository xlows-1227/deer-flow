import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const detail = {
  id: "pa_integrations",
  slug: "ops-assistant",
  display_name: "Ops Assistant",
  description: "Handles owner operations.",
  avatar_ref: null,
  status: "published",
  current_release_id: "rel_3",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-24T00:00:00Z",
  draft: {
    agent_id: "pa_integrations",
    agent_markdown: "# Ops",
    soul_markdown: "",
    model_name: null,
    tool_groups: [],
    quota_overrides: {},
    revision: 3,
    skills: [],
    connector_grants: [],
  },
};

const safeKey = {
  id: "key_1",
  agent_id: "pa_integrations",
  name: "Production",
  key_prefix: "dfa_live_",
  last_four: "9X2Q",
  status: "revoked",
  quota_overrides: {},
  created_at: "2026-07-20T00:00:00Z",
  last_used_at: null,
  expires_at: null,
  revoked_at: "2026-07-24T00:00:00Z" as string | null,
  rotation_of: null,
};

async function mockIntegrationStudio(
  page: Page,
  channelStatus: "inactive" | "active" | "deleting" = "active",
) {
  mockLangGraphAPI(page);
  let keys = [safeKey];
  let channelHealth = "healthy";
  let channelDetail = "WebSocket connected";

  await page.route("**/api/published-agents/pa_integrations", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(detail),
    }),
  );
  await page.route(
    "**/api/published-agents/pa_integrations/draft/options",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ skills: [] }),
      }),
  );
  await page.route("**/api/published-agents/pa_integrations/keys", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(keys),
      });
    }
    const createdKey = {
      ...safeKey,
      id: "key_2",
      name: route.request().postDataJSON().name,
      key_prefix: "dfa_created_",
      last_four: "cret",
      status: "active",
      revoked_at: null,
    };
    keys = [createdKey, ...keys];
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        ...createdKey,
        api_key: "dfa_live_once_only_secret",
        warning: "This API key will not be shown again.",
      }),
    });
  });
  await page.route(
    "**/api/published-agents/pa_integrations/keys/key_1",
    (route) => {
      if (route.request().method() !== "DELETE") {
        return route.fallback();
      }
      keys = keys.filter((key) => key.id !== "key_1");
      return route.fulfill({ status: 204 });
    },
  );
  await page.route(
    "**/api/published-agents/pa_integrations/channels",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "ach_1",
            agent_id: "pa_integrations",
            channel_type: "feishu",
            app_id: "cli_test",
            connection_mode: "websocket",
            status: channelStatus,
            health: channelHealth,
            health_detail: channelDetail,
            secret_configured: true,
            created_at: "2026-07-20T00:00:00Z",
            updated_at: "2026-07-24T00:00:00Z",
            last_started_at: "2026-07-24T00:00:00Z",
          },
        ]),
      }),
  );
  await page.route(
    "**/api/published-agents/pa_integrations/channels/ach_1/test",
    (route) => {
      channelHealth = "unhealthy";
      channelDetail = "Verification token rejected";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          health: channelHealth,
          detail: channelDetail,
        }),
      });
    },
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

test("creates with a name, copies the key from its row, and deletes a revoked key", async ({
  page,
}) => {
  await mockIntegrationStudio(page);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/workspace/agents/pa_integrations");
  await page.getByRole("tab", { name: "Integrations" }).click();

  await page.getByRole("button", { name: "Create API key" }).click();
  await expect(page.getByLabel("Concurrent runs")).toHaveCount(0);
  await expect(page.getByLabel("Daily runs")).toHaveCount(0);
  await page.getByLabel("Key name").fill("Automation");
  await page.getByRole("button", { name: "Create key" }).click();
  await expect(page.getByText("dfa_live_once_only_secret")).toBeVisible();
  await expect(page.getByText("This secret is displayed once")).toBeVisible();
  await page.getByRole("button", { name: "Copy API key" }).click();
  await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("dfa_live_once_only_secret");
  await page.getByRole("button", { name: "I stored this key" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("dfa_live_once_only_secret")).toHaveCount(0);

  await expect(page.getByText("Automation", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Edit Automation" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Edit Production" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Copy Production" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Copy Automation" }),
  ).toBeEnabled();
  await page.evaluate(() => navigator.clipboard.writeText(""));
  await page.getByRole("button", { name: "Copy Automation" }).click();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("dfa_live_once_only_secret");

  await expect(
    page.getByRole("button", { name: "Rotate Production" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Revoke Production" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Delete Production" }).click();
  await page.getByRole("button", { name: "Delete API key" }).click();
  await expect(page.getByText("Production", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Automation", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByText("Needs attention")).toBeVisible();
  await expect(
    page
      .getByRole("tabpanel", { name: "Integrations" })
      .getByText("Verification token rejected"),
  ).toBeVisible();
});

test("localizes deleting channel state and blocks conflicting actions", async ({
  page,
}) => {
  await mockIntegrationStudio(page, "deleting");
  await page.goto("/workspace/agents/pa_integrations");
  await page.getByRole("tab", { name: "Integrations" }).click();

  await expect(page.getByText("Deleting", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Test connection" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Start", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Restart", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Rotate credentials" }),
  ).toBeDisabled();
});
