import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Root page", () => {
  test("redirects an authenticated user to the workspace", async ({ page }) => {
    mockLangGraphAPI(page);

    await page.goto("/");

    await expect(page).toHaveURL(/\/workspace\/chats\/new/);
  });

  test("shows the new-chat composer after redirect", async ({ page }) => {
    mockLangGraphAPI(page);

    await page.goto("/");

    await expect(
      page.getByPlaceholder(/how can i assist you today/i),
    ).toBeVisible();
  });
});
