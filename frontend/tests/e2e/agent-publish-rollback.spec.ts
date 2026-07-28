import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const draft = {
  agent_id: "pa_publish",
  agent_markdown: "# Research desk\nUse primary evidence.",
  soul_markdown: "Be precise.",
  model_name: "model-b",
  tool_groups: ["web", "filesystem"],
  quota_overrides: {},
  revision: 6,
  skills: [{ skill_name: "research", source: "public" }],
  connector_grants: [],
};

const releaseOne = {
  id: "rel_1",
  agent_id: "pa_publish",
  release_no: 1,
  agent_markdown: "# Research desk",
  soul_markdown: "Be precise.",
  model_name: "model-a",
  tool_groups: ["web"],
  quota_overrides: {},
  manifest_checksum: "sha256:one",
  created_by: "owner-1",
  created_at: "2026-07-20T08:00:00Z",
  skills: [{ skill_revision_id: "skr_search", skill_name: "search" }],
  connector_grants: [],
};

const releaseTwo = {
  ...releaseOne,
  id: "rel_2",
  release_no: 2,
  agent_markdown: draft.agent_markdown,
  model_name: draft.model_name,
  tool_groups: draft.tool_groups,
  manifest_checksum: "sha256:two",
  created_at: "2026-07-24T08:00:00Z",
  skills: [{ skill_revision_id: "skr_research", skill_name: "research" }],
};

async function mockPublishStudio(page: Page) {
  mockLangGraphAPI(page);
  let publishAttempts = 0;
  let currentReleaseId = "rel_1";
  let releases = [releaseOne];

  await page.route("**/api/published-agents/pa_publish", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "pa_publish",
        slug: "research-desk",
        display_name: "Research Desk",
        description: "Verifies primary sources.",
        avatar_ref: null,
        status: "published",
        current_release_id: currentReleaseId,
        created_at: "2026-07-01T00:00:00Z",
        updated_at: "2026-07-24T00:00:00Z",
        draft,
      }),
    }),
  );
  await page.route(
    "**/api/published-agents/pa_publish/draft/options",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ skills: [] }),
      }),
  );
  await page.route("**/api/published-agents/pa_publish/releases", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(releases),
      });
    }
    publishAttempts += 1;
    if (publishAttempts === 1) {
      return route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "publish_validation_failed",
            violations: [
              {
                code: "CONNECTOR_NOT_GRANTED",
                message: "backend detail should be localized",
                field: "connector_grants",
              },
            ],
          },
        }),
      });
    }
    currentReleaseId = "rel_2";
    releases = [releaseTwo, releaseOne];
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        release_id: "rel_2",
        release_no: 2,
        published_at: releaseTwo.created_at,
      }),
    });
  });
  await page.route(
    "**/api/published-agents/pa_publish/rollback",
    async (route) => {
      expect(route.request().postDataJSON()).toEqual({ release_no: 1 });
      currentReleaseId = "rel_1";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ release_id: "rel_1", release_no: 1 }),
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

test("publishes with localized validation, then rolls back a stable identity", async ({
  page,
}) => {
  await mockPublishStudio(page);
  await page.goto("/workspace/agents/pa_publish");
  await page.getByRole("tab", { name: "Publish" }).click();

  await expect(page.getByTestId("release-diff")).toContainText("research");
  await page.getByRole("button", { name: "Publish saved draft" }).click();
  await expect(page.getByTestId("publish-violations")).toContainText(
    "Grant the Connector capability required by the selected Skill.",
  );
  await expect(page.getByTestId("publish-violations")).not.toContainText(
    "backend detail should be localized",
  );

  await page.getByRole("button", { name: "Publish saved draft" }).click();
  await expect(page.getByText("Release 2 is live")).toBeVisible();
  await expect(
    page.getByText("Release 2", { exact: true }).first(),
  ).toBeVisible();

  await page
    .getByRole("button", { name: "Roll back" })
    .filter({ hasNot: page.locator("[disabled]") })
    .last()
    .click();
  await expect(
    page.getByText(
      /Agent ID, API path, API keys, Feishu bindings and conversation identity remain unchanged/,
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "Confirm rollback" }).click();
  await expect(
    page.getByText("Current pointer moved to Release 1"),
  ).toBeVisible();
});
