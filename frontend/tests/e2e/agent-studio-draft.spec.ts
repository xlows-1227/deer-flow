import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const detail = {
  id: "pa_studio",
  slug: "research-desk",
  display_name: "Research Desk",
  description: "Verifies primary sources.",
  avatar_ref: null,
  status: "published",
  current_release_id: "rel_4",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
  draft: {
    agent_id: "pa_studio",
    agent_markdown: "# Research desk",
    soul_markdown: "Be precise.",
    model_name: "deepseek",
    tool_groups: ["web"],
    quota_overrides: {},
    revision: 4,
    skills: [],
    connector_grants: [],
  },
};

async function mockStudio(page: Page, draft = detail.draft) {
  mockLangGraphAPI(page);

  await page.route("**/api/published-agents/pa_studio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...detail, draft }),
    }),
  );
  await page.route("**/api/published-agents/pa_studio/draft/options", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        skills: [
          {
            skill_name: "web-search",
            source: "public",
            display_name: "公开资料检索",
            description: "Search public sources",
            description_zh: "搜索公开资料并汇总可信来源。",
            declared_connector_caps: [],
          },
          {
            skill_name: "warehouse-reporting",
            source: "private",
            display_name: "仓库经营报表",
            description: "Run owner warehouse reports",
            description_zh: "查询仓库数据并生成经营报表。",
            declared_connector_caps: ["database.query"],
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
            id: "conn_1",
            name: "warehouse",
            display_name: "Warehouse",
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
  await page.route("**/api/connector-types", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connector_types: [
          {
            type: "mysql",
            category: "database",
            display_name: "MySQL",
            auth_modes: [],
            capabilities: ["database.query", "database.table.sample"],
            config_schema: {},
            credential_schema: {},
            default_policy: {},
          },
        ],
      }),
    }),
  );
  await page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        models: [
          {
            id: "deepseek",
            name: "deepseek",
            model: "deepseek-chat",
            display_name: "DeepSeek",
          },
        ],
        token_usage: { enabled: true },
      }),
    }),
  );
}

test.describe("Agent Studio draft", () => {
  test("saves Agent rules and a selected Soul preset with revision protection", async ({
    page,
  }) => {
    await mockStudio(page);
    let saves = 0;
    await page.route(
      "**/api/published-agents/pa_studio/draft",
      async (route) => {
        saves += 1;
        if (saves === 2) {
          return route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify({
              detail: {
                code: "revision_conflict",
                message: "Draft changed elsewhere",
              },
            }),
          });
        }
        const request = route.request().postDataJSON();
        expect(request.revision).toBe(4);
        expect(request.agent_markdown).toContain("primary evidence");
        expect(request.soul_markdown).toContain(
          "deer-flow:soul-preset:warm:v1",
        );
        expect(request).not.toHaveProperty("tool_groups");
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ...detail.draft,
            ...request,
            revision: 5,
          }),
        });
      },
    );

    await page.goto("/workspace/agents/pa_studio");
    await expect(page.getByLabel("Tool groups")).toHaveCount(0);
    await page.getByRole("tab", { name: "Instructions" }).click();
    await page.getByRole("radio", { name: /Warm & patient/ }).click();
    await page
      .getByLabel(/AGENT\.md/)
      .fill("# Research desk\nAlways cite primary evidence.");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText("Draft saved")).toBeVisible();

    await page
      .getByLabel(/AGENT\.md/)
      .fill("# Research desk\nBe rigorous and concise.");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "Draft changed elsewhere" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Reload draft" }),
    ).toBeVisible();
  });

  test("keeps legacy custom Soul read-only until a preset is selected", async ({
    page,
  }) => {
    await mockStudio(page);
    await page.context().addCookies([
      {
        name: "locale",
        value: "zh-CN",
        domain: "localhost",
        path: "/",
      },
    ]);

    await page.goto("/workspace/agents/pa_studio");
    await page.getByRole("tab", { name: "指令" }).click();

    await expect(page.getByRole("heading", { name: "工作规则" })).toBeVisible();
    await expect(
      page.getByRole("alert").filter({ hasText: "当前使用旧版自定义 SOUL.md" }),
    ).toBeVisible();
    await expect(page.getByLabel(/AGENT\.md/)).toHaveValue(/# Research desk/);
    await expect(page.getByLabel(/AGENT\.md/)).not.toHaveValue(/Be precise\./);
    await expect(page.getByLabel("当前只读 SOUL.md")).toHaveText("Be precise.");
    await expect(page.getByRole("textbox", { name: /SOUL\.md/ })).toHaveCount(
      0,
    );
    await page.getByRole("radio", { name: /专业严谨/ }).click();
    await expect(page.getByRole("radio", { name: /专业严谨/ })).toBeChecked();
    await expect(
      page.getByRole("alert").filter({ hasText: "当前使用旧版自定义 SOUL.md" }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: "保存草稿" })).toBeEnabled();
  });

  test("prefills an empty draft with the Agent template and default Soul preset", async ({
    page,
  }) => {
    await mockStudio(page, {
      ...detail.draft,
      agent_markdown: "",
      soul_markdown: "",
    });
    await page.context().addCookies([
      {
        name: "locale",
        value: "zh-CN",
        domain: "localhost",
        path: "/",
      },
    ]);

    await page.goto("/workspace/agents/pa_studio");
    await page.getByRole("tab", { name: "指令" }).click();

    const editor = page.getByLabel(/AGENT\.md/);
    await expect(editor).toHaveValue(/# 角色与目标/);
    await expect(editor).toHaveValue(/# 工作职责/);
    await expect(editor).toHaveValue(/# 工作流程/);
    await expect(editor).toHaveValue(/# 行为边界/);
    await expect(editor).toHaveValue(/# 输出要求/);
    await expect(editor).not.toHaveValue(/# 性格与表达/);
    await expect(page.getByRole("radio", { name: /专业严谨/ })).toBeChecked();
    await expect(page.getByRole("textbox", { name: /SOUL\.md/ })).toHaveCount(
      0,
    );
    await expect(page.getByRole("button", { name: "保存草稿" })).toBeEnabled();
  });

  test("links private Skill requirements to Connector grants", async ({
    page,
  }) => {
    await mockStudio(page);

    await page.goto("/workspace/agents/pa_studio");
    await page.getByRole("tab", { name: "Skills" }).click();
    await page.getByRole("checkbox", { name: /warehouse-reporting/i }).click();

    await expect(page.getByText("database.query")).toBeVisible();
    await expect(
      page.getByText("Missing Connector grant", { exact: true }),
    ).toBeVisible();

    await page.getByRole("tab", { name: "Sandbox" }).click();
    await expect(page.getByText("Not live", { exact: true })).toBeVisible();
  });

  test("shows localized Skill metadata and filters the catalog", async ({
    page,
  }) => {
    await mockStudio(page);
    await page.context().addCookies([
      {
        name: "locale",
        value: "zh-CN",
        domain: "localhost",
        path: "/",
      },
    ]);

    await page.goto("/workspace/agents/pa_studio");
    await page.getByRole("tab", { name: "Skills" }).click();

    await expect(page.getByText("公开资料检索", { exact: true })).toBeVisible();
    await expect(
      page.getByText("搜索公开资料并汇总可信来源。", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("web-search", { exact: true })).toBeVisible();

    const search = page.getByPlaceholder("搜索 Skill 名称或说明...");
    await search.fill("仓库");
    await expect(page.getByText("仓库经营报表", { exact: true })).toBeVisible();
    await expect(page.getByText("公开资料检索", { exact: true })).toHaveCount(
      0,
    );

    await search.fill("不存在的 Skill");
    await expect(page.getByText("没有匹配的 Skill。")).toBeVisible();
  });

  test("starts the saved draft through the non-billable sandbox endpoint", async ({
    page,
  }) => {
    await mockStudio(page);
    await page.route(
      "**/api/published-agents/pa_studio/draft/sandbox-runs",
      async (route) => {
        expect(route.request().method()).toBe("POST");
        expect(route.request().postDataJSON()).toEqual({
          message: "Apply the unpublished instruction.",
        });
        return route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            agent_id: "pa_studio",
            thread_id: "thread_sandbox",
            run_id: "run_sandbox",
            status: "pending",
            draft_revision: 4,
            billable: false,
          }),
        });
      },
    );

    await page.goto("/workspace/agents/pa_studio");
    await page.getByRole("tab", { name: "Sandbox" }).click();
    await page
      .getByLabel("Sandbox message")
      .fill("Apply the unpublished instruction.");
    await page.getByRole("button", { name: "Run saved draft" }).click();

    await expect(
      page.getByText("Draft revision 4 · Not billable"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Open sandbox conversation" }),
    ).toBeVisible();
  });
});
