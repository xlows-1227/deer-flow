import type { Message } from "@langchain/langgraph-sdk";
import { expect, test } from "vitest";

import { extractMessageChoiceOptions } from "@/core/messages/choice-options";
import {
  getAssistantTurnCopyData,
  getAssistantTurnUsageMessages,
  getMessageGroups,
  getStreamingMessageLookup,
  isAssistantMessageGroupStreaming,
} from "@/core/messages/utils";

test("extracts trailing numbered clarification choices", () => {
  const parsed = extractMessageChoiceOptions(
    [
      "需要你的协助",
      "",
      "你希望这期科技简报覆盖哪些方向？",
      "",
      "1. AI/大模型行业动态（周报，中等篇幅）",
      "2. 综合科技要闻（周报，覆盖 AI、芯片、互联网等）",
      "3. 国内科技动态为主（周报）",
      "4. 深度分析型简报（聚焦 1-2 个热点话题）",
    ].join("\n"),
  );

  expect(parsed?.prompt).toContain("你希望这期科技简报覆盖哪些方向？");
  expect(parsed?.options).toEqual([
    { index: 1, value: "AI/大模型行业动态（周报，中等篇幅）" },
    { index: 2, value: "综合科技要闻（周报，覆盖 AI、芯片、互联网等）" },
    { index: 3, value: "国内科技动态为主（周报）" },
    { index: 4, value: "深度分析型简报（聚焦 1-2 个热点话题）" },
  ]);
});

test("does not extract non-terminal numbered steps as choices", () => {
  expect(
    extractMessageChoiceOptions(
      [
        "Follow these steps:",
        "",
        "1. Install deps",
        "2. Run tests",
        "",
        "Done.",
      ].join("\n"),
    ),
  ).toBeNull();
});

test("aggregates token usage messages once per assistant turn", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Plan a trip",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "tool-1", name: "web_search", args: {} }],
      usage_metadata: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
    },
    {
      id: "tool-1-result",
      type: "tool",
      name: "web_search",
      tool_call_id: "tool-1",
      content: "[]",
    },
    {
      id: "ai-2",
      type: "ai",
      content: "Here is the itinerary",
      usage_metadata: { input_tokens: 2, output_tokens: 8, total_tokens: 10 },
    },
    {
      id: "human-2",
      type: "human",
      content: "Make it shorter",
    },
    {
      id: "ai-3",
      type: "ai",
      content: "Short version",
      usage_metadata: { input_tokens: 1, output_tokens: 1, total_tokens: 2 },
    },
  ] as Message[];

  const groups = getMessageGroups(messages);
  const usageMessagesByGroupIndex = getAssistantTurnUsageMessages(groups);

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant",
    "human",
    "assistant",
  ]);

  expect(
    usageMessagesByGroupIndex.map(
      (groupMessages) => groupMessages?.map((message) => message.id) ?? null,
    ),
  ).toEqual([null, null, ["ai-1", "ai-2"], null, ["ai-3"]]);
});

test("hides internal todo reminder messages from message groups", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Audit the middleware",
    },
    {
      id: "todo-reminder-1",
      type: "human",
      name: "todo_completion_reminder",
      content: "<system_reminder>finish todos</system_reminder>",
    },
    {
      id: "todo-reminder-2",
      type: "human",
      name: "todo_reminder",
      content: "<system_reminder>remember todos</system_reminder>",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Done",
    },
  ] as Message[];

  const groups = getMessageGroups(messages);

  expect(groups.map((group) => group.type)).toEqual(["human", "assistant"]);
  expect(
    groups.flatMap((group) => group.messages).map((message) => message.id),
  ).toEqual(["human-1", "ai-1"]);
});

test("hides assistant copy data while that turn is streaming", () => {
  const messages = [
    {
      id: "ai-1",
      type: "ai",
      content: "Partial answer",
    },
  ] as Message[];

  expect(getAssistantTurnCopyData(messages)).toBe("Partial answer");
  expect(getAssistantTurnCopyData(messages, { isStreaming: true })).toBeNull();
});

test("marks the latest assistant message as streaming", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Still generating",
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true, () => ({
        streamMetadata: { langgraph_node: "agent" },
      })),
    ),
  ).toBe(true);
  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, false, () => ({
        streamMetadata: { langgraph_node: "agent" },
      })),
    ),
  ).toBe(false);
});

test("keeps previous assistant copyable while waiting for a new visible answer", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
    },
    {
      id: "opt-human-1",
      type: "human",
      content: "Continue",
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true),
    ),
  ).toBe(false);
});

test("keeps previous assistant copyable while a hidden send is starting", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true),
    ),
  ).toBe(false);
});

test("keeps previous assistant copyable after a hidden send is appended", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
    },
    {
      id: "human-hidden",
      type: "human",
      content: "Save this agent",
      additional_kwargs: { hide_from_ui: true },
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true),
    ),
  ).toBe(false);
});

test("uses stream metadata to identify an assistant before optimistic input", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Completed answer",
    },
    {
      id: "ai-2",
      type: "ai",
      content: "Still generating",
    },
    {
      id: "opt-human-1",
      type: "human",
      content: "Continue",
    },
  ] as Message[];
  const assistantGroups = getMessageGroups(messages).filter(
    (group) => group.type === "assistant",
  );
  const groups = getMessageGroups(messages);
  const assistantGroupIndexes = groups
    .map((group, index) => (group.type === "assistant" ? index : -1))
    .filter((index) => index >= 0);

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndexes[0] ?? -1]?.messages ?? [],
      getStreamingMessageLookup(messages, true, (message) =>
        message.id === "ai-2"
          ? { streamMetadata: { langgraph_node: "agent" } }
          : undefined,
      ),
    ),
  ).toBe(false);
  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndexes[1] ?? -1]?.messages ?? [],
      getStreamingMessageLookup(messages, true, (message) =>
        message.id === "ai-2"
          ? { streamMetadata: { langgraph_node: "agent" } }
          : undefined,
      ),
    ),
  ).toBe(true);
  expect(assistantGroups.map((group) => group.id)).toEqual(["ai-1", "ai-2"]);
});

test("does not mark a completed assistant group streaming from a later processing group", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Visible answer",
    },
    {
      id: "ai-2",
      type: "ai",
      content: "",
      tool_calls: [{ id: "tool-1", name: "web_search", args: {} }],
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant",
    "assistant:processing",
  ]);
  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true, (message) =>
        message.id === "ai-2"
          ? { streamMetadata: { langgraph_node: "agent" } }
          : undefined,
      ),
    ),
  ).toBe(false);
});

test("keeps streaming assistant hidden when a hidden control message follows it", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Still generating",
    },
    {
      id: "human-hidden",
      type: "human",
      content: "Save this agent",
      additional_kwargs: { hide_from_ui: true },
    },
  ] as Message[];
  const groups = getMessageGroups(messages);
  const assistantGroupIndex = groups.findIndex(
    (group) => group.type === "assistant",
  );

  expect(
    isAssistantMessageGroupStreaming(
      groups[assistantGroupIndex]?.messages ?? [],
      getStreamingMessageLookup(messages, true, (message) =>
        message.id === "ai-1"
          ? { streamMetadata: { langgraph_node: "agent" } }
          : undefined,
      ),
    ),
  ).toBe(true);
});

test("routes tool results to processing group after clarification group", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Research AI news",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [
        { id: "tool-search", name: "web_search", args: {} },
        {
          id: "tool-clarify",
          name: "ask_clarification",
          args: { question: "Which region?" },
        },
      ],
    },
    {
      id: "tool-clarify-result",
      type: "tool",
      name: "ask_clarification",
      tool_call_id: "tool-clarify",
      content: "Which region?",
    },
    {
      id: "tool-search-result",
      type: "tool",
      name: "web_search",
      tool_call_id: "tool-search",
      content: "[]",
    },
  ] as Message[];

  const groups = getMessageGroups(messages);
  const processingGroup = groups.find(
    (group) => group.type === "assistant:processing",
  );

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant:clarification",
  ]);
  expect(
    processingGroup?.messages
      .filter((message) => message.type === "tool")
      .map((message) => message.id),
  ).toEqual(["tool-clarify-result", "tool-search-result"]);
});

test("routes late tool results to the group that owns the tool call", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "tool-1", name: "web_search", args: {} }],
    },
    {
      id: "ai-2",
      type: "ai",
      content: "Visible answer",
    },
    {
      id: "tool-1-result",
      type: "tool",
      name: "web_search",
      tool_call_id: "tool-1",
      content: "[]",
    },
  ] as Message[];

  const groups = getMessageGroups(messages);
  const processingGroup = groups.find(
    (group) => group.type === "assistant:processing",
  );

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant",
  ]);
  expect(
    processingGroup?.messages
      .filter((message) => message.type === "tool")
      .map((message) => message.id),
  ).toEqual(["tool-1-result"]);
});

test("ignores incomplete streaming tool placeholders", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Done",
    },
    {
      type: "tool",
    },
  ] as Message[];

  expect(getMessageGroups(messages).map((group) => group.type)).toEqual([
    "human",
    "assistant",
  ]);
});

test("routes tool results to processing group after clarification group", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Research AI news",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [
        { id: "tool-search", name: "web_search", args: {} },
        {
          id: "tool-clarify",
          name: "ask_clarification",
          args: { question: "Which region?" },
        },
      ],
    },
    {
      id: "tool-clarify-result",
      type: "tool",
      name: "ask_clarification",
      tool_call_id: "tool-clarify",
      content: "Which region?",
    },
    {
      id: "tool-search-result",
      type: "tool",
      name: "web_search",
      tool_call_id: "tool-search",
      content: "[]",
    },
  ] as Message[];

  const groups = getMessageGroups(messages);
  const processingGroup = groups.find(
    (group) => group.type === "assistant:processing",
  );

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant:clarification",
  ]);
  expect(
    processingGroup?.messages
      .filter((message) => message.type === "tool")
      .map((message) => message.id),
  ).toEqual(["tool-clarify-result", "tool-search-result"]);
});

test("routes late tool results to the group that owns the tool call", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "tool-1", name: "web_search", args: {} }],
    },
    {
      id: "ai-2",
      type: "ai",
      content: "Visible answer",
    },
    {
      id: "tool-1-result",
      type: "tool",
      name: "web_search",
      tool_call_id: "tool-1",
      content: "[]",
    },
  ] as Message[];

  const groups = getMessageGroups(messages);
  const processingGroup = groups.find(
    (group) => group.type === "assistant:processing",
  );

  expect(groups.map((group) => group.type)).toEqual([
    "human",
    "assistant:processing",
    "assistant",
  ]);
  expect(
    processingGroup?.messages
      .filter((message) => message.type === "tool")
      .map((message) => message.id),
  ).toEqual(["tool-1-result"]);
});

test("ignores incomplete streaming tool placeholders", () => {
  const messages = [
    {
      id: "human-1",
      type: "human",
      content: "Hello",
    },
    {
      id: "ai-1",
      type: "ai",
      content: "Done",
    },
    {
      type: "tool",
    },
  ] as Message[];

  expect(getMessageGroups(messages).map((group) => group.type)).toEqual([
    "human",
    "assistant",
  ]);
});
