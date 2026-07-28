import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

type DraftState = {
  agent_id: string;
  agent_markdown: string;
  soul_markdown: string;
  model_name: string | null;
  tool_groups: string[];
  quota_overrides: Record<string, number>;
  revision: number;
  skills: { skill_name: string; source: "public" | "private" }[];
  connector_grants: {
    connector_instance_id: string;
    capability: string;
  }[];
};

type ReleaseState = {
  id: string;
  agent_id: string;
  release_no: number;
  agent_markdown: string;
  soul_markdown: string;
  model_name: string;
  tool_groups: string[];
  quota_overrides: Record<string, number>;
  manifest_checksum: string;
  created_by: string;
  created_at: string;
  skills: { skill_revision_id: string; skill_name: string }[];
  connector_grants: {
    connector_instance_id: string;
    capability: string;
  }[];
};

type StudioMock = {
  getCurrentReleaseId: () => string | null;
  getCurrentRelease: () => ReleaseState | null;
  getDraft: () => DraftState;
  getPublishCount: () => number;
  getCreatedKeyCount: () => number;
  getCreatedChannelCount: () => number;
};

type SoulPresetId = "professional" | "warm" | "concise" | "coach";

function presetSoulMarkdown(presetId: SoulPresetId): string {
  return `<!-- deer-flow:soul-preset:${presetId}:v1 -->\n# Personality\nManaged ${presetId} personality.`;
}

function presetMarker(presetId: SoulPresetId): string {
  return `deer-flow:soul-preset:${presetId}:v1`;
}

function releaseFromDraft(draft: DraftState, releaseNo: number): ReleaseState {
  return {
    id: `rel_${draft.agent_id}_${releaseNo}`,
    agent_id: draft.agent_id,
    release_no: releaseNo,
    agent_markdown: draft.agent_markdown,
    soul_markdown: draft.soul_markdown,
    model_name: draft.model_name ?? "model-a",
    tool_groups: draft.tool_groups,
    quota_overrides: draft.quota_overrides,
    manifest_checksum: `sha256:${draft.agent_id}:${releaseNo}`,
    created_by: "owner-acceptance",
    created_at: `2026-07-2${releaseNo}T08:00:00Z`,
    skills: [],
    connector_grants: draft.connector_grants,
  };
}

async function mockAcceptanceStudio(
  page: Page,
  {
    agentId,
    initialDraft,
    initialRelease = null,
  }: {
    agentId: string;
    initialDraft: DraftState;
    initialRelease?: ReleaseState | null;
  },
): Promise<StudioMock> {
  mockLangGraphAPI(page);
  let draft = structuredClone(initialDraft);
  let releases = initialRelease ? [structuredClone(initialRelease)] : [];
  let currentReleaseId = initialRelease?.id ?? null;
  let publishCount = 0;
  let keys: Record<string, unknown>[] = [];
  let channels: Record<string, unknown>[] = [];

  await page.route(`**/api/published-agents/${agentId}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: agentId,
        slug: `slug-${agentId}`,
        display_name: `Studio ${agentId}`,
        description: "Acceptance fixture",
        avatar_ref: null,
        status: currentReleaseId ? "published" : "draft",
        current_release_id: currentReleaseId,
        created_at: "2026-07-01T00:00:00Z",
        updated_at: "2026-07-24T00:00:00Z",
        draft,
      }),
    }),
  );
  await page.route(
    `**/api/published-agents/${agentId}/draft/options`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ skills: [] }),
      }),
  );
  await page.route(`**/api/published-agents/${agentId}/draft`, (route) => {
    const request = route.request().postDataJSON() as Partial<DraftState>;
    draft = {
      ...draft,
      ...request,
      revision: draft.revision + 1,
    };
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(draft),
    });
  });
  await page.route(`**/api/published-agents/${agentId}/releases`, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(releases),
      });
    }
    publishCount += 1;
    const release = releaseFromDraft(draft, releases.length + 1);
    releases = [release, ...releases];
    currentReleaseId = release.id;
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        release_id: release.id,
        release_no: release.release_no,
        published_at: release.created_at,
      }),
    });
  });
  await page.route(`**/api/published-agents/${agentId}/keys`, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(keys),
      });
    }
    const input = route.request().postDataJSON() as { name: string };
    const created = {
      id: `key_${keys.length + 1}`,
      agent_id: agentId,
      name: input.name,
      key_prefix: "dfa_accept_",
      last_four: "7A7A",
      status: "active",
      quota_overrides: {},
      created_at: "2026-07-24T00:00:00Z",
      last_used_at: null,
      expires_at: null,
      revoked_at: null,
      rotation_of: null,
    };
    keys = [...keys, created];
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        ...created,
        api_key: "dfa_acceptance_plaintext_once",
        warning: "This API key will not be shown again.",
      }),
    });
  });
  await page.route(`**/api/published-agents/${agentId}/channels`, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(channels),
      });
    }
    const input = route.request().postDataJSON() as { app_id: string };
    const created = {
      id: `ach_${channels.length + 1}`,
      agent_id: agentId,
      channel_type: "feishu",
      app_id: input.app_id,
      connection_mode: "websocket",
      status: "inactive",
      health: "unknown",
      health_detail: null,
      secret_configured: true,
      created_at: "2026-07-24T00:00:00Z",
      updated_at: "2026-07-24T00:00:00Z",
      last_started_at: null,
    };
    channels = [...channels, created];
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(created),
    });
  });
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
  await page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        models: [
          {
            id: "model-a",
            name: "model-a",
            model: "model-a",
            display_name: "Model A",
          },
        ],
        token_usage: { enabled: true },
      }),
    }),
  );

  return {
    getCurrentReleaseId: () => currentReleaseId,
    getCurrentRelease: () =>
      releases.find((release) => release.id === currentReleaseId) ?? null,
    getDraft: () => draft,
    getPublishCount: () => publishCount,
    getCreatedKeyCount: () => keys.length,
    getCreatedChannelCount: () => channels.length,
  };
}

function draftFixture(
  agentId: string,
  overrides: Partial<DraftState>,
): DraftState {
  return {
    agent_id: agentId,
    agent_markdown: "",
    soul_markdown: "",
    model_name: "model-a",
    tool_groups: [],
    quota_overrides: {},
    revision: 1,
    skills: [],
    connector_grants: [],
    ...overrides,
  };
}

/**
 * Studio authors AGENT.md freely and generates SOUL.md from a managed preset,
 * so the publishable instruction combinations are "rules only", "personality
 * only", and "both". SOUL.md has no free-text editor.
 */
const instructionCombinations = [
  {
    name: "edited AGENT.md rules with the default Soul preset",
    agentId: "pa_rules_with_default_preset",
    initialAgentMarkdown: "",
    initialSoulMarkdown: "",
    agentMarkdown: "# Agent rules acceptance",
    presetName: null,
    expectedPreset: "professional" as SoulPresetId,
  },
  {
    name: "a switched Soul preset only",
    agentId: "pa_preset_only",
    initialAgentMarkdown: "# Keep these rules",
    initialSoulMarkdown: presetSoulMarkdown("professional"),
    agentMarkdown: null,
    presetName: /Warm & patient/,
    expectedPreset: "warm" as SoulPresetId,
  },
  {
    name: "edited AGENT.md rules and a switched Soul preset",
    agentId: "pa_rules_and_preset",
    initialAgentMarkdown: "# Initial rules",
    initialSoulMarkdown: presetSoulMarkdown("professional"),
    agentMarkdown: "# Combined rules acceptance",
    presetName: /Guiding coach/,
    expectedPreset: "coach" as SoulPresetId,
  },
];

for (const combination of instructionCombinations) {
  test(`acceptance 2 publishes ${combination.name}`, async ({ page }) => {
    const mock = await mockAcceptanceStudio(page, {
      agentId: combination.agentId,
      initialDraft: draftFixture(combination.agentId, {
        agent_markdown: combination.initialAgentMarkdown,
        soul_markdown: combination.initialSoulMarkdown,
      }),
    });

    await page.goto(`/workspace/agents/${combination.agentId}`);
    await page.getByRole("tab", { name: "Instructions" }).click();
    await expect(page.getByRole("textbox", { name: /SOUL\.md/ })).toHaveCount(0);
    if (combination.agentMarkdown !== null) {
      await page.getByLabel(/AGENT\.md/).fill(combination.agentMarkdown);
    }
    if (combination.presetName) {
      await page.getByRole("radio", { name: combination.presetName }).click();
    }
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText("Draft saved")).toBeVisible();
    await page.getByRole("tab", { name: "Publish" }).click();
    await page.getByRole("button", { name: "Publish saved draft" }).click();

    await expect(page.getByText("Release 1 is live")).toBeVisible();
    const expectedAgentMarkdown =
      combination.agentMarkdown ?? combination.initialAgentMarkdown;
    const expectedMarker = presetMarker(combination.expectedPreset);
    expect(mock.getDraft().agent_markdown).toBe(expectedAgentMarkdown);
    expect(mock.getDraft().soul_markdown).toContain(expectedMarker);
    expect(mock.getCurrentRelease()?.agent_markdown).toBe(
      expectedAgentMarkdown,
    );
    expect(mock.getCurrentRelease()?.soul_markdown).toContain(expectedMarker);
    expect(mock.getPublishCount()).toBe(1);
  });
}

const legacyInstructionDrafts = [
  {
    name: "an AGENT.md-only legacy draft",
    agentId: "pa_legacy_agent_only",
    agentMarkdown: "# Legacy agent rules",
    soulMarkdown: "",
  },
  {
    name: "a SOUL.md-only legacy draft",
    agentId: "pa_legacy_soul_only",
    agentMarkdown: "",
    soulMarkdown: "Legacy custom voice.",
  },
];

for (const legacy of legacyInstructionDrafts) {
  test(`acceptance 2 publishes ${legacy.name} after Studio initialization`, async ({
    page,
  }) => {
    const mock = await mockAcceptanceStudio(page, {
      agentId: legacy.agentId,
      initialDraft: draftFixture(legacy.agentId, {
        agent_markdown: legacy.agentMarkdown,
        soul_markdown: legacy.soulMarkdown,
        revision: 3,
      }),
    });

    await page.goto(`/workspace/agents/${legacy.agentId}`);
    await page.getByRole("tab", { name: "Publish" }).click();
    await expect(
      page.getByText("Save the draft before publishing"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Publish saved draft" }),
    ).toBeDisabled();

    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText("Draft saved")).toBeVisible();
    await page.getByRole("button", { name: "Publish saved draft" }).click();
    await expect(page.getByText("Release 1 is live")).toBeVisible();

    const release = mock.getCurrentRelease();
    expect(release?.agent_markdown).not.toBe("");
    if (legacy.agentMarkdown) {
      expect(release?.agent_markdown).toBe(legacy.agentMarkdown);
      expect(release?.soul_markdown).toContain(presetMarker("professional"));
    } else {
      // Legacy custom personality stays byte-identical until the owner picks a preset.
      expect(release?.soul_markdown).toBe(legacy.soulMarkdown);
    }
    expect(mock.getPublishCount()).toBe(1);
  });
}

test("acceptance 3 saving a draft leaves the live Release unchanged", async ({
  page,
}) => {
  const agentId = "pa_draft_isolation";
  const initialDraft: DraftState = {
    agent_id: agentId,
    agent_markdown: "Published behavior",
    soul_markdown: "Published soul",
    model_name: "model-a",
    tool_groups: [],
    quota_overrides: {},
    revision: 4,
    skills: [],
    connector_grants: [],
  };
  const release = releaseFromDraft(initialDraft, 1);
  const mock = await mockAcceptanceStudio(page, {
    agentId,
    initialDraft,
    initialRelease: release,
  });

  await page.goto(`/workspace/agents/${agentId}`);
  await page.getByRole("tab", { name: "Instructions" }).click();
  await page.getByLabel("AGENT.md").fill("Draft-only behavior");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Draft saved")).toBeVisible();
  await page.getByRole("tab", { name: "Publish" }).click();

  await expect(page.getByTestId("release-diff")).toContainText(
    "Draft-only behavior",
  );
  await expect(page.getByTestId("release-diff")).toContainText(
    "Published behavior",
  );
  expect(mock.getCurrentReleaseId()).toBe(release.id);
  expect(mock.getPublishCount()).toBe(0);
});

test("acceptance 6 and 7 add API and Feishu integrations after publish", async ({
  page,
}) => {
  const agentId = "pa_late_integrations";
  const mock = await mockAcceptanceStudio(page, {
    agentId,
    initialDraft: draftFixture(agentId, {
      agent_markdown: "# Publish before integrations",
      soul_markdown: presetSoulMarkdown("professional"),
      revision: 2,
    }),
  });

  await page.goto(`/workspace/agents/${agentId}`);
  await page.getByRole("tab", { name: "Publish" }).click();
  await page.getByRole("button", { name: "Publish saved draft" }).click();
  await expect(page.getByText("Release 1 is live")).toBeVisible();

  await page.getByRole("tab", { name: "Integrations" }).click();
  const synchronousExample = page.locator('pre[aria-label="Synchronous"]');
  await expect(synchronousExample).toContainText(
    '"message":"Summarize today’s incidents"',
  );
  await expect(synchronousExample).not.toContainText('"input":{"messages"');
  await page.getByRole("button", { name: "Create API key" }).click();
  await page.getByLabel("Key name").fill("Post-publish client");
  await page.getByRole("button", { name: "Create key" }).click();
  await expect(page.getByText("dfa_acceptance_plaintext_once")).toBeVisible();
  await page.getByRole("button", { name: "I stored this key" }).click();

  await page.getByRole("button", { name: "Add Feishu binding" }).click();
  await page.getByLabel("App ID").fill("cli_late_binding");
  await page.getByLabel("App secret").fill("late-app-secret");
  await page.getByLabel("Verification token").fill("late-verification-token");
  await page.getByRole("button", { name: "Create binding" }).click();
  await expect(page.getByText("Feishu binding created")).toBeVisible();
  await expect(page.getByText("cli_late_binding")).toBeVisible();

  expect(mock.getCurrentReleaseId()).toBe(`rel_${agentId}_1`);
  expect(mock.getPublishCount()).toBe(1);
  expect(mock.getCreatedKeyCount()).toBe(1);
  expect(mock.getCreatedChannelCount()).toBe(1);
});
