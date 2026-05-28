import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Sidebar navigation", () => {
  test("sidebar contains Chats and Agents nav links", async ({ page }) => {
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new");

    // Sidebar uses data-sidebar="menu-button" with asChild rendering on <Link>
    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar.locator("a[href='/workspace/chats']")).toBeVisible({
      timeout: 15_000,
    });
    await expect(sidebar.locator("a[href='/workspace/agents']")).toBeVisible();
  });

  test("keeps navigation fixed while recent chats and task records scroll", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: Array.from({ length: 40 }, (_, index) => ({
        thread_id: `00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
        title: `Thread ${index + 1}`,
      })),
      scheduledTasks: [
        {
          id: "daily-report",
          name: "Daily report",
          last_run_status: "success",
          last_run_at: "2025-01-01T09:00:00Z",
          last_run_thread_id: "00000000-0000-0000-0000-000000000001",
          last_run_id: "run-daily-report",
        },
      ],
    });

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    const agentsLink = sidebar.locator("a[href='/workspace/agents']");
    const scrollRegion = sidebar.locator(".overflow-y-auto").first();
    await expect(agentsLink).toBeVisible({ timeout: 15_000 });

    const before = await agentsLink.boundingBox();
    await scrollRegion.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });

    await expect(sidebar.getByText("Task records")).toBeVisible();
    await expect(sidebar.getByText("Daily report")).toBeVisible();

    const after = await agentsLink.boundingBox();
    expect(before).not.toBeNull();
    expect(after).not.toBeNull();
    expect(after!.y).toBeCloseTo(before!.y, 1);
  });

  test("shows only four recent chats and opens full history", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: Array.from({ length: 8 }, (_, index) => ({
        thread_id: `00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
        title: `Thread ${index + 1}`,
      })),
    });

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    await expect(sidebar.getByText("Thread 1")).toBeVisible({
      timeout: 15_000,
    });
    await expect(sidebar.getByText("Thread 4")).toBeVisible();
    await expect(sidebar.getByText("Thread 5")).toBeHidden();

    await sidebar.getByRole("link", { name: "View all" }).click();
    await page.waitForURL("**/workspace/chats");
    await expect(page).toHaveURL(/\/workspace\/chats$/);
  });

  test("Agents link navigates to agents page", async ({ page }) => {
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    const agentsLink = sidebar.locator("a[href='/workspace/agents']");
    await expect(agentsLink).toBeVisible({ timeout: 15_000 });
    await agentsLink.click();

    await page.waitForURL("**/workspace/agents");
    await expect(page).toHaveURL(/\/workspace\/agents/);
  });
});
