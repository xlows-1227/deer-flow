import type { Message } from "@langchain/langgraph-sdk";
import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: { thinking: "thinking" },
      toolCalls: {
        searchFor: (query: string) => `search ${query}`,
        viewWebPage: "view page",
        presentFiles: "present files",
        writeTodos: "write todos",
        useTool: (name: string) => `use ${name}`,
      },
    },
  }),
}));

import { WorkspaceToolExecutionPanel } from "@/components/workspace/chats/workspace-tool-execution-panel";
import { ThreadContext } from "@/components/workspace/messages/context";

const SECRET_MARKER = "SECRET_SKILL_MARKER_123_DO_NOT_EXPOSE";

function makeToolMessages({
  name,
  args,
  result,
  visibility,
}: {
  name: string;
  args: Record<string, unknown>;
  result: string;
  visibility?: string;
}): Message[] {
  return [
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [
        {
          id: "call-1",
          name,
          type: "tool_call",
          args,
        },
      ],
    },
    {
      id: "tool-1",
      type: "tool",
      name,
      tool_call_id: "call-1",
      content: result,
      additional_kwargs: visibility ? { visibility } : {},
    },
  ] as Message[];
}

async function renderExpanded(messages: Message[]): Promise<string> {
  const thread = { messages, isLoading: false };
  let renderer!: ReactTestRenderer;

  await act(async () => {
    renderer = create(
      createElement(
        ThreadContext.Provider,
        { value: { thread: thread as never } },
        createElement(WorkspaceToolExecutionPanel),
      ),
    );
  });

  await act(async () => {
    renderer.root.findByType("button").props.onClick();
  });

  return JSON.stringify(renderer.toJSON());
}

beforeEach(() => {
  (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
});

test("hides SKILL.md paths and contents in execution history", async () => {
  const rendered = await renderExpanded(
    makeToolMessages({
      name: "read_file",
      args: {
        description: "Load data analysis skill",
        path: "/mnt/skills/public/data-analysis/SKILL.md",
      },
      result: `---\nname: data-analysis\n---\n${SECRET_MARKER}`,
    }),
  );

  expect(rendered).not.toContain(SECRET_MARKER);
  expect(rendered).not.toContain("/mnt/skills");
  expect(rendered).toContain("已加载 Skill 指令，内容已隐藏");
});

test("does not expose raw arguments or results for unknown tools", async () => {
  const rendered = await renderExpanded(
    makeToolMessages({
      name: "third_party_tool",
      args: { token: `arg-${SECRET_MARKER}` },
      result: `result-${SECRET_MARKER}`,
    }),
  );

  expect(rendered).not.toContain(SECRET_MARKER);
  expect(rendered).toContain("参数与结果已隐藏");
});

test("uses server-safe skill metadata when visibility is redacted", async () => {
  const rendered = await renderExpanded(
    makeToolMessages({
      name: "read_file",
      args: {
        description: "Load data analysis skill",
        skill_name: "data-analysis",
        redacted: true,
      },
      result: "Skill instructions loaded.",
      visibility: "redacted",
    }),
  );

  expect(rendered).toContain("data-analysis");
  expect(rendered).toContain("已加载 Skill 指令，内容已隐藏");
  expect(rendered).not.toContain("Skill instructions loaded.");
});
