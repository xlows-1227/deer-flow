import { expect, test, type Page } from "@playwright/test";

const TOKEN = "public-token";

async function mockPublishedHtml(page: Page) {
  await page.route("**/api/public-files/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === `/api/public-files/${TOKEN}/content`) {
      return route.fulfill({
        status: 200,
        contentType: "text/plain; charset=utf-8",
        body: `<!doctype html>
          <html>
            <body>
              <h1>公开交互报告</h1>
              <button id="run" onclick="this.textContent='已运行'">运行</button>
            </body>
          </html>`,
      });
    }
    if (pathname === `/api/public-files/${TOKEN}`) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          name: "report.html",
          content_url: `/api/public-files/${TOKEN}/content`,
        }),
      });
    }
    return route.fulfill({ status: 404 });
  });
}

test("renders published HTML in an interactive isolated iframe", async ({
  page,
}) => {
  await mockPublishedHtml(page);
  await page.goto(`/published/${TOKEN}`);

  const iframe = page.getByTitle("report.html");
  await expect(iframe).toHaveAttribute("sandbox", "allow-scripts allow-forms");
  await expect(iframe).not.toHaveAttribute("sandbox", /allow-same-origin/);

  const publishedPage = page.frameLocator('iframe[title="report.html"]');
  await expect(
    publishedPage.getByRole("heading", { name: "公开交互报告" }),
  ).toBeVisible();
  await publishedPage.getByRole("button", { name: "运行" }).click();
  await expect(
    publishedPage.getByRole("button", { name: "已运行" }),
  ).toBeVisible();
});

test("shows a standalone error when the publication is unavailable", async ({
  page,
}) => {
  await page.route("**/api/public-files/**", (route) =>
    route.fulfill({ status: 404 }),
  );
  await page.goto("/published/missing-token");

  await expect(
    page.getByText("发布页面不存在或已停止发布", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(0);
});
