import { expect, test, type Page } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

const HTML_PATH = "/mnt/user-data/outputs/report.html";
const PDF_PATH = "/mnt/user-data/outputs/report.pdf";
const PUBLIC_TOKEN = "public-token";

async function mockFilesPage(page: Page) {
  let publication:
    | {
        id: string;
        name: string;
        thread_id: string;
        path: string;
        public_token: string;
        public_url: string;
        created_at: string;
      }
    | undefined;

  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Published report conversation",
        artifacts: [HTML_PATH, PDF_PATH],
      },
    ],
  });

  await page.route("**/api/files/folders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ folders: [] }),
    }),
  );
  await page.route("**/api/files/upload-config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        max_upload_bytes: 10_000_000,
        max_upload_label: "10 MB",
      }),
    }),
  );
  await page.route(/\/api\/files(?:\?.*)?$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ folder_path: "", items: [], total: 0 }),
    }),
  );
  await page.route("**/api/file-publications", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: publication ? [publication] : [],
          total: publication ? 1 : 0,
        }),
      });
    }
    if (route.request().method() === "POST") {
      publication = {
        id: "publication-1",
        name: "report.html",
        thread_id: MOCK_THREAD_ID,
        path: HTML_PATH,
        public_token: PUBLIC_TOKEN,
        public_url: `/published/${PUBLIC_TOKEN}`,
        created_at: "2026-07-15T08:00:00Z",
      };
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(publication),
      });
    }
    return route.fallback();
  });
  await page.route("**/api/file-publications/*", (route) => {
    publication = undefined;
    return route.fulfill({ status: 204 });
  });
}

function fileRow(page: Page, name: string) {
  return page.getByText(name, { exact: true }).locator("..").locator("..");
}

test("only generated HTML exposes the publish-link action", async ({
  page,
}) => {
  await mockFilesPage(page);
  await page.goto("/workspace/files");

  await page.getByText("对话生成", { exact: true }).click();
  await expect(page.getByText("report.html", { exact: true })).toBeVisible();

  await fileRow(page, "report.html").getByRole("button").last().click();
  await expect(page.getByRole("menuitem", { name: "发布外链" })).toBeVisible();

  await page.keyboard.press("Escape");
  await fileRow(page, "report.pdf").getByRole("button").last().click();
  await expect(page.getByRole("menuitem", { name: "发布外链" })).toHaveCount(0);
});

test("publishes, copies, and revokes a generated HTML link", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await mockFilesPage(page);
  await page.goto("/workspace/files");
  await page.getByText("对话生成", { exact: true }).click();

  await fileRow(page, "report.html").getByRole("button").last().click();
  await page.getByRole("menuitem", { name: "发布外链" }).click();
  await expect(page.getByText("外链已发布", { exact: true })).toBeVisible();

  await fileRow(page, "report.html").getByRole("button").last().click();
  await expect(page.getByRole("menuitem", { name: "复制外链" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "取消发布" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "发布外链" })).toHaveCount(0);

  await page.getByRole("menuitem", { name: "复制外链" }).click();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe(`${new URL(page.url()).origin}/published/${PUBLIC_TOKEN}`);

  page.once("dialog", (dialog) => dialog.accept());
  await fileRow(page, "report.html").getByRole("button").last().click();
  await page.getByRole("menuitem", { name: "取消发布" }).click();
  await expect(page.getByText("已取消发布", { exact: true })).toBeVisible();

  await fileRow(page, "report.html").getByRole("button").last().click();
  await expect(page.getByRole("menuitem", { name: "发布外链" })).toBeVisible();
});
