import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const MOCK_THREAD_ID = "00000000-0000-0000-0000-000000000042";

const threadWithToolCalls = {
  thread_id: MOCK_THREAD_ID,
  title: "Tool call banner test",
  updated_at: "2025-01-01T00:00:00Z",
  messages: [
    {
      type: "human",
      id: "msg-human-1",
      content: [{ type: "text", text: "Research this topic" }],
    },
    {
      type: "ai",
      id: "msg-ai-1",
      content: "[工具调用: search_web, read_file]\nHere is a summary.",
      tool_calls: [
        { name: "search_web", args: {}, id: "tc-1" },
        { name: "read_file", args: {}, id: "tc-2" },
      ],
    },
  ],
};

const threadWithTaskCalls = {
  thread_id: "00000000-0000-0000-0000-000000000043",
  title: "Task tool banner test",
  updated_at: "2025-01-01T00:00:00Z",
  messages: [
    {
      type: "human",
      id: "msg-human-2",
      content: [{ type: "text", text: "Plan the project" }],
    },
    {
      type: "ai",
      id: "msg-ai-2",
      content: "[工具调用已省略]\nPlan created.",
      tool_calls: [
        {
          name: "task",
          args: {
            description: "Break down tasks",
            subagent_type: "general-purpose",
            prompt: "Break down the project",
          },
          id: "tc-3",
        },
        {
          name: "task",
          args: {
            description: "Estimate effort",
            subagent_type: "general-purpose",
            prompt: "Estimate effort",
          },
          id: "tc-4",
        },
      ],
    },
  ],
};

test.describe("Tool call omission banner", () => {
  test("shows the actual tool name for each direct tool call", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [threadWithToolCalls] });
    await page.goto(`/workspace/chats/${threadWithToolCalls.thread_id}`);

    const banner = page.locator('[data-testid="tool-call-omission-banner"]').first();
    await expect(banner).toBeVisible({ timeout: 15_000 });
    await expect(banner).toContainText("2");

    await banner.locator("button").first().click();
    await expect(banner.getByText("search_web")).toBeVisible();
    await expect(banner.getByText("read_file")).toBeVisible();
  });

  test("shows task descriptions instead of generic numbers", async ({ page }) => {
    mockLangGraphAPI(page, { threads: [threadWithTaskCalls] });
    await page.goto(`/workspace/chats/${threadWithTaskCalls.thread_id}`);

    const banner = page.locator('[data-testid="tool-call-omission-banner"]').first();
    await expect(banner).toBeVisible({ timeout: 15_000 });
    await expect(banner).toContainText("2");

    await banner.locator("button").first().click();
    await expect(banner.getByText("Break down tasks")).toBeVisible();
    await expect(banner.getByText("Estimate effort")).toBeVisible();
  });
});
