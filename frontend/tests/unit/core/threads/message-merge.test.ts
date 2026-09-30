import type { Message, Run } from "@langchain/langgraph-sdk";
import { expect, test } from "vitest";

import {
  getVisibleOptimisticMessagesForServerMessages,
  getVisibleOptimisticMessages,
  mergeMessages,
} from "@/core/threads/merge";
import { mergeLoadedRunMessages } from "@/core/threads/query";

test("mergeMessages removes duplicate messages already present in history", () => {
  const human = {
    id: "human-1",
    type: "human",
    content: "Design an agent",
  } as Message;
  const ai = {
    id: "ai-1",
    type: "ai",
    content: "Let's design it.",
  } as Message;

  expect(mergeMessages([human, ai, human, ai], [], [])).toEqual([human, ai]);
});

test("mergeMessages lets live thread messages replace overlapping history", () => {
  const oldHuman = {
    id: "human-1",
    type: "human",
    content: "old",
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "live",
  } as Message;
  const oldAi = {
    id: "ai-1",
    type: "ai",
    content: "old",
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live",
  } as Message;

  expect(mergeMessages([oldHuman, oldAi], [liveHuman, liveAi], [])).toEqual([
    liveHuman,
    liveAi,
  ]);
});

test("mergeMessages preserves history timestamps when live messages replace history", () => {
  const historyAi = {
    id: "ai-1",
    type: "ai",
    content: "old",
    additional_kwargs: { timestamp: "2026-05-27T01:23:45+08:00" },
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live",
  } as Message;

  expect(mergeMessages([historyAi], [liveAi], [])).toEqual([
    {
      ...liveAi,
      additional_kwargs: {
        timestamp: "2026-05-27T01:23:45+08:00",
      },
    },
  ]);
});

test("mergeMessages keeps live timestamps when they already exist", () => {
  const historyAi = {
    id: "ai-1",
    type: "ai",
    content: "old",
    additional_kwargs: { timestamp: "2026-05-27T01:23:45+08:00" },
  } as Message;
  const liveAi = {
    id: "ai-1",
    type: "ai",
    content: "live",
    additional_kwargs: { timestamp: "2026-05-27T02:00:00+08:00" },
  } as Message;

  expect(mergeMessages([historyAi], [liveAi], [])).toEqual([liveAi]);
});

test("mergeLoadedRunMessages keeps newer runs after older history", () => {
  const olderRun = {
    run_id: "run-old",
    created_at: "2026-06-26T08:34:04.000Z",
  } as Run;
  const newerRun = {
    run_id: "run-new",
    created_at: "2026-06-26T08:34:44.000Z",
  } as Run;
  const olderHuman = {
    id: "human-old",
    type: "human",
    content: "hello",
  } as Message;
  const olderAi = {
    id: "ai-old",
    type: "ai",
    content: "Hello! I'm Friday.",
  } as Message;
  const newerHuman = {
    id: "human-new",
    type: "human",
    content: "你能做什么",
  } as Message;

  expect(
    mergeLoadedRunMessages(
      [newerRun, olderRun],
      new Map([
        [
          olderRun.run_id,
          [
            { seq: 1, message: olderHuman },
            { seq: 2, message: olderAi },
          ],
        ],
        [newerRun.run_id, [{ seq: 3, message: newerHuman }]],
      ]),
    ),
  ).toEqual([olderHuman, olderAi, newerHuman]);
});

test("mergeLoadedRunMessages orders by global seq when run created_at disagrees", () => {
  const runA = {
    run_id: "run-a",
    created_at: "2026-06-30T06:46:44.000Z",
  } as Run;
  const runB = {
    run_id: "run-b",
    created_at: "2026-06-30T06:39:32.000Z",
  } as Run;
  const firstHuman = {
    id: "human-1",
    type: "human",
    content: "暂时不用了",
    additional_kwargs: { timestamp: "2026-06-30T06:39:32+08:00" },
  } as Message;
  const helloHuman = {
    id: "human-2",
    type: "human",
    content: "hello",
    additional_kwargs: { timestamp: "2026-06-30T06:40:03+08:00" },
  } as Message;
  const whatCanYouDoHuman = {
    id: "human-3",
    type: "human",
    content: "你能做什么",
    additional_kwargs: { timestamp: "2026-06-30T06:40:03+08:00" },
  } as Message;
  const secondHuman = {
    id: "human-4",
    type: "human",
    content: "暂时不用了",
    additional_kwargs: { timestamp: "2026-06-30T06:46:44+08:00" },
  } as Message;
  const assistantReply = {
    id: "ai-1",
    type: "ai",
    content: "Hello! I'm Friday, your AI assistant. How can I help you today?",
    additional_kwargs: { timestamp: "2026-06-30T06:47:13+08:00" },
  } as Message;

  expect(
    mergeLoadedRunMessages(
      [runA, runB],
      new Map([
        [
          runB.run_id,
          [
            { seq: 1, message: firstHuman },
            { seq: 2, message: helloHuman },
            { seq: 3, message: whatCanYouDoHuman },
          ],
        ],
        [
          runA.run_id,
          [
            { seq: 4, message: secondHuman },
            { seq: 5, message: assistantReply },
          ],
        ],
      ]),
    ),
  ).toEqual([
    firstHuman,
    helloHuman,
    whatCanYouDoHuman,
    secondHuman,
    assistantReply,
  ]);
});

test("mergeMessages deduplicates tool messages by tool_call_id", () => {
  const oldTool = {
    id: "tool-message-old",
    type: "tool",
    tool_call_id: "call-1",
    content: "old",
  } as Message;
  const liveTool = {
    id: "tool-message-live",
    type: "tool",
    tool_call_id: "call-1",
    content: "live",
  } as Message;

  expect(mergeMessages([oldTool], [liveTool], [])).toEqual([liveTool]);
});

test("getVisibleOptimisticMessages hides optimistic user input after server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 0, 1)).toEqual([]);
});

test("mergeMessages shows server human instead of optimistic duplicate after first response", () => {
  const serverHuman = {
    id: "server-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;
  const visibleOptimistic = getVisibleOptimisticMessages(
    [optimisticHuman],
    0,
    1,
  );

  expect(mergeMessages([], [serverHuman], visibleOptimistic)).toEqual([
    serverHuman,
  ]);
});

test("mergeMessages places optimistic user input before streaming assistant output", () => {
  const previousHuman = {
    id: "human-1",
    type: "human",
    content: "hello",
  } as Message;
  const previousAi = {
    id: "ai-1",
    type: "ai",
    content: "Hello! I'm Friday.",
  } as Message;
  const streamingAi = {
    id: "ai-2",
    type: "ai",
    content: "周报需要这些信息",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-2",
    type: "human",
    content: "帮我写一份周报需要什么信息",
  } as Message;

  expect(
    mergeMessages(
      [previousHuman, previousAi],
      [streamingAi],
      [optimisticHuman],
    ),
  ).toEqual([previousHuman, previousAi, optimisticHuman, streamingAi]);
});

test("mergeMessages keeps checkpoint-only clarification card after its AI message", () => {
  // Regression: ClarificationMiddleware injects the ask_clarification
  // ToolMessage via Command(goto=END); on_tool_end never fires for it, so
  // run-event history has no copy while the checkpoint does. Strict
  // history-suffix/thread-prefix alignment failed on the extra message and
  // the fallback merge appended the card (plus duplicate question copies)
  // to the very end of the conversation.
  const q1History = {
    id: null,
    type: "human",
    content: "搜索点新闻",
    additional_kwargs: { timestamp: "2026-09-29T10:00:00+08:00" },
  } as unknown as Message;
  const q1Thread = {
    id: "q1-orig__user",
    type: "human",
    content: "搜索点新闻",
  } as Message;
  const hiddenReminder = {
    id: "q1-orig",
    type: "human",
    content: "<system-reminder>memory</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as unknown as Message;
  const clarifyAi = {
    id: "ai-clar",
    type: "ai",
    content: "您的需求「搜索点新闻」比较宽泛，我需要确认一下方向：",
  } as Message;
  const clarificationCard = {
    id: "clarification:call-1",
    type: "tool",
    name: "ask_clarification",
    tool_call_id: "call-1",
    content: "您想搜索哪方面的新闻？",
  } as unknown as Message;
  const q2History = {
    id: null,
    type: "human",
    content: "今日国内外综合头条新闻",
    additional_kwargs: { timestamp: "2026-09-29T10:01:00+08:00" },
  } as unknown as Message;
  const q2Thread = {
    id: "q2-orig",
    type: "human",
    content: "今日国内外综合头条新闻",
  } as Message;
  const searchAi = {
    id: "ai-search",
    type: "ai",
    content: "I'll search for today's top news.",
    tool_calls: [{ id: "call-web-1", name: "web_search", args: {} }],
  } as unknown as Message;
  const searchTool = {
    id: "tool-live",
    type: "tool",
    tool_call_id: "call-web-1",
    name: "web_search",
    content: '{"results": []}',
  } as unknown as Message;
  const finalAi = {
    id: "ai-final",
    type: "ai",
    content: "Here is the news summary.",
  } as Message;

  const history = [
    q1History,
    clarifyAi,
    q2History,
    searchAi,
    searchTool,
    finalAi,
  ];
  const thread = [
    hiddenReminder,
    q1Thread,
    clarifyAi,
    clarificationCard,
    q2Thread,
    searchAi,
    searchTool,
    finalAi,
  ];

  const merged = mergeMessages(history, thread, []);

  // Timestamps are backfilled from history copies onto the thread copies.
  const q1ThreadWithTs = {
    ...q1Thread,
    additional_kwargs: { timestamp: "2026-09-29T10:00:00+08:00" },
  } as Message;
  const q2ThreadWithTs = {
    ...q2Thread,
    additional_kwargs: { timestamp: "2026-09-29T10:01:00+08:00" },
  } as Message;

  expect(merged).toEqual([
    q1ThreadWithTs,
    clarifyAi,
    clarificationCard,
    q2ThreadWithTs,
    searchAi,
    searchTool,
    finalAi,
  ]);
  // The card must sit between the clarification AI message and the second
  // question, not after the final answer. (Compare by id — timestamp
  // backfilling creates new message objects.)
  expect(merged[2]).toBe(clarificationCard);
  expect(merged.findIndex((m) => m.id === q2Thread.id)).toBe(3);
  expect(
    merged.findIndex((m) => m.id === clarificationCard.id),
  ).toBeLessThan(merged.findIndex((m) => m.id === q2Thread.id));
  expect(
    merged.findIndex((m) => m.id === clarificationCard.id),
  ).toBeLessThan(merged.findIndex((m) => m.id === finalAi.id));
});

test("mergeMessages keeps clarification card in place when run-event tool order diverges from checkpoint", () => {
  // Regression (real thread 8126cdd4): after "load more" loads the
  // clarification run, history (run events) and thread (checkpoint) diverge
  // in two ways:
  //   1. web_search ToolMessages are journaled in COMPLETION order
  //      (call_00, call_02, call_01) but stored in the checkpoint in CALL
  //      order (call_00, call_01, call_02) — strict subsequence alignment
  //      fails on the first swapped pair;
  //   2. the ask_clarification ToolMessage only exists in the checkpoint
  //      (injected via Command(goto=END), never journaled as a run event).
  // The suffix-merge fallback used to append checkpoint-only messages after
  // the whole history, displacing the option card to the very bottom.
  const run1CreatedAt = "2026-09-29T08:47:50+00:00";
  const run2CreatedAt = "2026-09-29T08:52:00+00:00";
  const q1History = {
    id: null,
    type: "human",
    content: "搜索点新闻",
    additional_kwargs: { timestamp: run1CreatedAt },
  } as unknown as Message;
  const clarifyAiHistory = {
    id: "lc_run--01a0ec59-9588-7790-b207-de5e1eca2427",
    type: "ai",
    content:
      "您的需求「搜索点新闻」比较宽泛，为了给您更精准的结果，我需要确认一下方向：[工具调用: ask_clarification]",
    tool_calls: [
      {
        name: "ask_clarification",
        id: "call_00_6fHlswEXwi38BfZ9ZxWf1712",
        args: { question: "您想搜索哪方面的新闻？" },
      },
    ],
    additional_kwargs: { timestamp: "2026-09-29T08:48:13+00:00" },
  } as unknown as Message;
  const q2History = {
    id: null,
    type: "human",
    content: "今日国内外综合头条新闻",
    additional_kwargs: { timestamp: run2CreatedAt },
  } as unknown as Message;
  const searchAi = {
    id: "lc_run--01a0ec59-fcdb-71c2-9f9d-d605717dc988",
    type: "ai",
    content:
      "I'll search for today's top domestic and international news.[工具调用: web_search][工具调用: web_search][工具调用: web_search]",
    tool_calls: [
      { name: "web_search", id: "call_00_CjcQVvzeG6", args: {} },
      { name: "web_search", id: "call_01_oaRe0UAeRMs", args: {} },
      { name: "web_search", id: "call_02_qCkm391B9", args: {} },
    ],
    additional_kwargs: { timestamp: "2026-09-29T08:52:11+00:00" },
  } as unknown as Message;
  const searchToolEvent =
    (toolCallId: string, query: string) =>
    ({
      id: null,
      type: "tool",
      name: "web_search",
      tool_call_id: toolCallId,
      content: `{"query": "${query}", "results": []}`,
    }) as unknown as Message;
  const finalAi = {
    id: "lc_run--01a0ec5c-57f8-7291-b711-cc3fe0b1e046",
    type: "ai",
    content: "Here is the news summary.",
  } as Message;

  // useThreadHistory composes: [run.kwargs.input.messages(seq -1), ...run
  // events]. Web-search results are journaled in completion order:
  // call_00, call_02, call_01.
  const history = [
    q1History,
    clarifyAiHistory,
    q2History,
    searchAi,
    searchToolEvent("call_00_CjcQVvzeG6", "今日头条新闻"),
    searchToolEvent("call_02_qCkm391B9", "国内新闻"),
    searchToolEvent("call_01_oaRe0UAeRMs", "国际新闻"),
    finalAi,
  ];

  const hiddenReminder = {
    id: "97a3acf8-2dbd-4be6-bd9b-15c94ca20c38",
    type: "human",
    content: "<system-reminder>memory</system-reminder>",
    additional_kwargs: { hide_from_ui: true },
  } as unknown as Message;
  const q1Thread = {
    id: "97a3acf8-2dbd-4be6-bd9b-15c94ca20c38__user",
    type: "human",
    name: "user-input",
    content: "搜索点新闻",
  } as Message;
  // Checkpoint copy of the clarification AI message carries the marker at the
  // FRONT (middleware re-serialization) — same id, different text.
  const clarifyAiThread = {
    ...clarifyAiHistory,
    content:
      "[工具调用: ask_clarification]\n您的需求「搜索点新闻」比较宽泛，为了给您更精准的结果，我需要确认一下方向：",
  } as Message;
  const clarificationCard = {
    id: "clarification:call_00_6fHlswEXwi38BfZ9ZxWf1712",
    type: "tool",
    name: "ask_clarification",
    tool_call_id: "call_00_6fHlswEXwi38BfZ9ZxWf1712",
    content:
      "🤔 「搜索点新闻」没有指明主题、地区或时间范围，不同方向结果差异很大。\n\n您想搜索哪方面的新闻？\n\n  1. 今日国内外综合头条新闻\n  2. 科技/AI 领域新闻",
  } as unknown as Message;
  const q2Thread = {
    id: "bb9c2605-4205-4119-ba05-720499f8e992",
    type: "human",
    name: "user-input",
    content: "今日国内外综合头条新闻",
  } as Message;
  const searchToolCheckpoint =
    (id: string, toolCallId: string, query: string) =>
    ({
      id,
      type: "tool",
      name: "web_search",
      tool_call_id: toolCallId,
      content: `{"query": "${query}", "results": []}`,
    }) as unknown as Message;

  // Checkpoint stores tool results in CALL order: call_00, call_01, call_02.
  const thread = [
    hiddenReminder,
    q1Thread,
    clarifyAiThread,
    clarificationCard,
    q2Thread,
    searchAi,
    searchToolCheckpoint("231c7ce3", "call_00_CjcQVvzeG6", "今日头条新闻"),
    searchToolCheckpoint("11924881", "call_01_oaRe0UAeRMs", "国际新闻"),
    searchToolCheckpoint("85a8d5e3", "call_02_qCkm391B9", "国内新闻"),
    finalAi,
  ];

  const merged = mergeMessages(history, thread, []);

  expect(merged.map((message) => message.id)).toEqual([
    q1Thread.id,
    clarifyAiThread.id,
    clarificationCard.id,
    q2Thread.id,
    searchAi.id,
    "231c7ce3",
    "11924881",
    "85a8d5e3",
    finalAi.id,
  ]);
  // The card must stay between the clarification AI message and the answer
  // to the clarification (Q2), not after the final answer.
  expect(merged.findIndex((m) => m.id === clarificationCard.id)).toBe(2);
  expect(
    merged.findIndex((m) => m.id === clarificationCard.id),
  ).toBeLessThan(merged.findIndex((m) => m.id === q2Thread.id));
  expect(
    merged.findIndex((m) => m.id === clarificationCard.id),
  ).toBeLessThan(merged.findIndex((m) => m.id === finalAi.id));
});

test("mergeMessages keeps clarification card before the answer even when the card is journaled after the run input", () => {
  // Future shape once the backend journals the clarification ToolMessage as
  // a run event: the card event belongs to the RESUMED run, so it lands
  // after that run's input message (Q2) in composed history, while the
  // checkpoint stores it BEFORE Q2 (tool result must precede the next human
  // message). History order alone would show the option card one turn too
  // low; the thread (checkpoint) order must win.
  const clarifyAi = {
    id: "ai-clar",
    type: "ai",
    content: "需要确认一下方向：[工具调用: ask_clarification]",
    tool_calls: [
      { name: "ask_clarification", id: "call-1", args: {} },
    ],
  } as unknown as Message;
  const q1History = {
    id: null,
    type: "human",
    content: "搜索点新闻",
    additional_kwargs: { timestamp: "2026-09-29T10:00:00+08:00" },
  } as unknown as Message;
  const clarificationCardEvent = {
    id: "clarification:call-1",
    type: "tool",
    name: "ask_clarification",
    tool_call_id: "call-1",
    content: "您想搜索哪方面的新闻？\n\n  1. 头条\n  2. 科技",
  } as unknown as Message;
  const q2History = {
    id: null,
    type: "human",
    content: "今日国内外综合头条新闻",
    additional_kwargs: { timestamp: "2026-09-29T10:05:00+08:00" },
  } as unknown as Message;
  const finalAi = {
    id: "ai-final",
    type: "ai",
    content: "Here is the news summary.",
  } as Message;

  const history = [q1History, clarifyAi, q2History, clarificationCardEvent, finalAi];
  const thread = [
    {
      id: "q1-orig__user",
      type: "human",
      content: "搜索点新闻",
    },
    clarifyAi,
    clarificationCardEvent,
    {
      id: "q2-orig",
      type: "human",
      content: "今日国内外综合头条新闻",
    },
    finalAi,
  ] as Message[];

  const merged = mergeMessages(history, thread, []);

  expect(merged.map((message) => message.id)).toEqual([
    "q1-orig__user",
    "ai-clar",
    "clarification:call-1",
    "q2-orig",
    "ai-final",
  ]);
});

test("mergeMessages appends optimistic follow-up after prior turn when history is empty", () => {
  const previousHuman = {
    id: "human-1",
    type: "human",
    content: "hello",
  } as Message;
  const previousAi = {
    id: "ai-1",
    type: "ai",
    content: "Hello! I'm Friday.",
  } as Message;
  const streamingAi = {
    id: "ai-2",
    type: "ai",
    content: "今天天气不错",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-2",
    type: "human",
    content: "今天天气怎么样",
  } as Message;

  expect(
    mergeMessages(
      [],
      [previousHuman, previousAi, streamingAi],
      [optimisticHuman],
    ),
  ).toEqual([previousHuman, previousAi, optimisticHuman, streamingAi]);
});

test("mergeMessages appends optimistic follow-up before streaming when prior turn only exists in thread", () => {
  const previousHuman = {
    id: "human-1",
    type: "human",
    content: "hello",
  } as Message;
  const previousAi = {
    id: "ai-1",
    type: "ai",
    content: "Hello! I'm Friday.",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-2",
    type: "human",
    content: "今天天气怎么样",
  } as Message;

  expect(
    mergeMessages([], [previousHuman, previousAi], [optimisticHuman]),
  ).toEqual([previousHuman, previousAi, optimisticHuman]);
});

test("mergeMessages keeps server human before streaming assistant output after optimistic cleared", () => {
  const previousHuman = {
    id: "human-1",
    type: "human",
    content: "hello",
  } as Message;
  const previousAi = {
    id: "ai-1",
    type: "ai",
    content: "Hello! I'm Friday.",
  } as Message;
  // Backend streams this turn as [AI output, human input] and the optimistic
  // message has already been cleared (third arg empty).
  const streamingAi = {
    id: "ai-2",
    type: "ai",
    content: "周报需要这些信息",
  } as Message;
  const serverHuman = {
    id: "server-human-2",
    type: "human",
    content: "帮我写一份周报需要什么信息",
  } as Message;

  expect(
    mergeMessages([previousHuman, previousAi], [streamingAi, serverHuman], []),
  ).toEqual([previousHuman, previousAi, serverHuman, streamingAi]);
});

test("mergeMessages does not reorder historical slices with multiple human messages", () => {
  const firstHuman = {
    id: "human-1",
    type: "human",
    content: "今天天气怎么样",
  } as Message;
  const secondHuman = {
    id: "human-2",
    type: "human",
    content: "南京天气",
  } as Message;
  const clarificationAi = {
    id: "ai-1",
    type: "ai",
    content: "我需要先确认一下您想查询哪个城市的天气。",
  } as Message;
  const laterHuman = {
    id: "human-3",
    type: "human",
    content: "南京天气",
  } as Message;

  expect(
    mergeMessages(
      [],
      [firstHuman, secondHuman, clarificationAi, laterHuman],
      [],
    ),
  ).toEqual([firstHuman, secondHuman, clarificationAi, laterHuman]);
});

test("mergeMessages keeps replaced history before optimistic user input", () => {
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "old",
  } as Message;
  const liveHuman = {
    id: "human-1",
    type: "human",
    content: "live",
  } as Message;
  const streamingAi = {
    id: "ai-2",
    type: "ai",
    content: "streaming",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-2",
    type: "human",
    content: "follow up",
  } as Message;

  expect(
    mergeMessages([historyHuman], [liveHuman, streamingAi], [optimisticHuman]),
  ).toEqual([liveHuman, optimisticHuman, streamingAi]);
});

test("getVisibleOptimisticMessages keeps optimistic user input until server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "hello",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 0, 0)).toEqual([
    optimisticHuman,
  ]);
});

test("keeps optimistic user input when only old server history arrives", () => {
  const oldServerHuman = {
    id: "human-old",
    type: "human",
    content: "hello",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-new",
    type: "human",
    content: "帮我写一份周报需要什么信息",
  } as Message;

  expect(
    getVisibleOptimisticMessagesForServerMessages(
      [optimisticHuman],
      new Set(),
      [oldServerHuman],
    ),
  ).toEqual([optimisticHuman]);
});

test("hides optimistic user input only after matching server human arrives", () => {
  const oldServerHuman = {
    id: "human-old",
    type: "human",
    content: "hello",
  } as Message;
  const serverHuman = {
    id: "human-new",
    type: "human",
    content: "帮我写一份周报需要什么信息",
  } as Message;
  const optimisticHuman = {
    id: "opt-human-new",
    type: "human",
    content: "帮我写一份周报需要什么信息",
  } as Message;

  expect(
    getVisibleOptimisticMessagesForServerMessages(
      [optimisticHuman],
      new Set(["message:human-old"]),
      [oldServerHuman, serverHuman],
    ),
  ).toEqual([]);
});

test("getVisibleOptimisticMessages keeps non-human optimistic status messages", () => {
  const optimisticAi = {
    id: "opt-ai-1",
    type: "ai",
    content: "Uploading files...",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticAi], 0, 1)).toEqual([
    optimisticAi,
  ]);
});

test("getVisibleOptimisticMessages hides the upload optimistic pair after server human arrives", () => {
  const optimisticHuman = {
    id: "opt-human-1",
    type: "human",
    content: "upload this",
  } as Message;
  const optimisticUploadingAi = {
    id: "opt-ai-uploading",
    type: "ai",
    content: "Uploading files...",
  } as Message;

  expect(
    getVisibleOptimisticMessages(
      [optimisticHuman, optimisticUploadingAi],
      0,
      1,
    ),
  ).toEqual([]);
});

test("getVisibleOptimisticMessages hides optimistic user input after later server turns", () => {
  const optimisticHuman = {
    id: "opt-human-2",
    type: "human",
    content: "follow up",
  } as Message;

  expect(getVisibleOptimisticMessages([optimisticHuman], 3, 4)).toEqual([]);
  expect(getVisibleOptimisticMessages([optimisticHuman], 3, 3)).toEqual([
    optimisticHuman,
  ]);
});

test("mergeMessages does not duplicate humans when history and thread ids differ but content overlaps", () => {
  const historyH1 = {
    id: "hist-h1",
    type: "human",
    content: "今天天气怎么样",
    additional_kwargs: { timestamp: "2026-06-29T15:46:29+08:00" },
  } as Message;
  const historyH2 = {
    id: "hist-h2",
    type: "human",
    content: "南京天气",
    additional_kwargs: { timestamp: "2026-06-29T15:54:20+08:00" },
  } as Message;
  const threadH1 = {
    id: "thread-h1",
    type: "human",
    content: "今天天气怎么样",
    additional_kwargs: { timestamp: "2026-06-29T15:46:29+08:00" },
  } as Message;
  const threadH2 = {
    id: "thread-h2",
    type: "human",
    content: "南京天气",
    additional_kwargs: { timestamp: "2026-06-29T15:54:09+08:00" },
  } as Message;
  const ai = {
    id: "ai-1",
    type: "ai",
    content: "我需要先确认一下您想查询哪个城市的天气。",
  } as Message;

  expect(
    mergeMessages([historyH1, historyH2], [threadH1, threadH2, ai], []),
  ).toEqual([threadH1, threadH2, ai]);
});

test("mergeMessages keeps repeated human text when positions align across history and thread", () => {
  const history = [
    {
      id: "hist-h1",
      type: "human",
      content: "今天天气怎么样",
    },
    {
      id: "hist-h2",
      type: "human",
      content: "南京天气",
    },
    {
      id: "hist-ai",
      type: "ai",
      content: "clarification",
    },
    {
      id: "hist-h3",
      type: "human",
      content: "南京天气",
    },
  ] as Message[];
  const thread = [
    {
      id: "thread-h1",
      type: "human",
      content: "今天天气怎么样",
    },
    {
      id: "thread-h2",
      type: "human",
      content: "南京天气",
    },
    {
      id: "thread-ai",
      type: "ai",
      content: "clarification",
    },
    {
      id: "thread-h3",
      type: "human",
      content: "南京天气",
    },
  ] as Message[];

  expect(mergeMessages(history, thread, [])).toEqual(thread);
});

test("mergeMessages keeps history position when alignment fails and thread re-sends old messages", () => {
  // Regression: after a failed run + resume, history/thread alignment can fail
  // and the whole checkpoint message list is appended after history. The old
  // dedupe kept the LAST occurrence, relocating early messages to the bottom.
  const oldHuman = {
    id: "human-1",
    type: "human",
    content: "帮我分析一下这个数据",
    additional_kwargs: { timestamp: "2026-07-09T15:29:04+08:00" },
  } as Message;
  const oldAi = {
    id: "ai-1",
    type: "ai",
    content: "好的，我来分析。",
  } as Message;
  const continueHuman = {
    id: "human-2",
    type: "human",
    content: "继续",
  } as Message;
  // Thread state diverges from history (e.g. summarization rewrote it), so no
  // suffix/prefix overlap can be found; it still contains the old human.
  const threadOldHumanCopy = {
    id: "human-1",
    type: "human",
    content: "帮我分析一下这个数据",
  } as Message;
  const streamingAi = {
    id: "ai-2",
    type: "ai",
    content: "继续分析中",
  } as Message;

  const merged = mergeMessages(
    [oldHuman, oldAi, continueHuman],
    [threadOldHumanCopy, streamingAi],
    [],
  );

  expect(merged.map((message) => message.id)).toEqual([
    "human-1",
    "ai-1",
    "human-2",
    "ai-2",
  ]);
  // The old human stays at its history position with its timestamp preserved.
  expect(merged[0]!.additional_kwargs?.timestamp).toBe(
    "2026-07-09T15:29:04+08:00",
  );
});

test("mergeMessages aligns history with thread state despite a leading summary message", () => {
  // Regression: summarization inserts HumanMessage(name="summary") at the head
  // of checkpoint state. Strict positional alignment used to fail on it,
  // causing the whole thread list to be re-appended after history.
  const historyHuman = {
    id: "human-1",
    type: "human",
    content: "hello",
    additional_kwargs: { timestamp: "2026-07-09T15:00:00+08:00" },
  } as Message;
  const historyAi = {
    id: "ai-1",
    type: "ai",
    content: "Hello! I'm Friday.",
  } as Message;
  const summaryMessage = {
    id: "summary-1",
    type: "human",
    name: "summary",
    content: "Here is a summary of the conversation to date:\n\n...",
  } as Message;
  const threadHuman = {
    id: "human-1",
    type: "human",
    content: "hello",
  } as Message;
  const threadAi = {
    id: "ai-1",
    type: "ai",
    content: "Hello! I'm Friday.",
  } as Message;
  const streamingAi = {
    id: "ai-2",
    type: "ai",
    content: "streaming",
  } as Message;

  const merged = mergeMessages(
    [historyHuman, historyAi],
    [summaryMessage, threadHuman, threadAi, streamingAi],
    [],
  );

  expect(merged.map((message) => message.id)).toEqual([
    "summary-1",
    "human-1",
    "ai-1",
    "ai-2",
  ]);
});

test("mergeMessages repairs dynamic context user copy order from checkpoint state", () => {
  const reminder = {
    id: "turn-1",
    type: "human",
    content: "<system-reminder></system-reminder>",
    additional_kwargs: {
      hide_from_ui: true,
      dynamic_context_reminder: true,
    },
  } as Message;
  const clarificationAi = {
    id: "ai-1",
    type: "ai",
    content: "我需要先确认一下您想查询哪个城市的天气。",
  } as Message;
  const secondHuman = {
    id: "turn-2",
    type: "human",
    name: "user-input",
    content: [{ type: "text", text: "南京天气" }],
    additional_kwargs: { timestamp: "2026-06-29T07:54:20.150706+00:00" },
  } as Message;
  const firstHumanCopy = {
    id: "turn-1__user",
    type: "human",
    content: [{ type: "text", text: "今天天气怎么样" }],
  } as Message;

  // The hidden reminder (id turn-1) and the visible __user copy share an
  // identity after __user-stripping, so dedupe collapses them into the
  // (visible) __user copy at the reminder's position. The rendered order is
  // identical: 今天天气怎么样 → clarification → 南京天气.
  expect(
    mergeMessages(
      [],
      [reminder, clarificationAi, secondHuman, firstHumanCopy],
      [],
    ).map((message) => message.id),
  ).toEqual(["turn-1__user", "ai-1", "turn-2"]);
});

test("mergeMessages keeps older checkpoint turns before a newest-run history suffix", () => {
  // Opening a historical thread loads the latest run first. That suffix must
  // not be prepended in front of the fuller checkpoint conversation.
  const olderHuman = {
    id: "human-1",
    type: "human",
    content: "先看一下项目列表",
    additional_kwargs: { timestamp: "2026-08-14T10:00:00+08:00" },
  } as Message;
  const olderAi = {
    id: "ai-1",
    type: "ai",
    content: "这是项目列表。",
    additional_kwargs: { timestamp: "2026-08-14T10:01:00+08:00" },
  } as Message;
  const latestHuman = {
    id: "human-2",
    type: "human",
    content: "查看项目面板",
    additional_kwargs: { timestamp: "2026-08-14T10:15:45+08:00" },
  } as Message;
  const latestAi = {
    id: "ai-2",
    type: "ai",
    content: "已生成交互式 HTML 看板。",
    additional_kwargs: { timestamp: "2026-08-14T10:42:35+08:00" },
  } as Message;

  expect(
    mergeMessages(
      [latestHuman, latestAi],
      [olderHuman, olderAi, latestHuman, latestAi],
      [],
    ).map((message) => message.id),
  ).toEqual(["human-1", "ai-1", "human-2", "ai-2"]);
});

test("mergeMessages uses history order when the checkpoint inverts the latest turn", () => {
  const olderHuman = {
    id: "human-1",
    type: "human",
    content: "先看一下项目列表",
  } as Message;
  const olderAi = {
    id: "ai-1",
    type: "ai",
    content: "这是项目列表。",
  } as Message;
  const latestHuman = {
    id: "human-2",
    type: "human",
    content: "查看项目面板",
    additional_kwargs: { timestamp: "2026-08-14T10:15:45+08:00" },
  } as Message;
  const latestAi = {
    id: "ai-2",
    type: "ai",
    content: "已生成交互式 HTML 看板。",
    additional_kwargs: { timestamp: "2026-08-14T10:42:35+08:00" },
  } as Message;

  expect(
    mergeMessages(
      [latestHuman, latestAi],
      [olderHuman, olderAi, latestAi, latestHuman],
      [],
    ).map((message) => message.id),
  ).toEqual(["human-1", "ai-1", "human-2", "ai-2"]);
});

test("mergeMessages aligns a newest-run suffix when checkpoint ids differ but content matches", () => {
  const olderHuman = {
    id: "ckpt-human-1",
    type: "human",
    content: "先看一下项目列表",
  } as Message;
  const olderAi = {
    id: "ckpt-ai-1",
    type: "ai",
    content: "这是项目列表。",
  } as Message;
  const historyHuman = {
    id: "event-human-2",
    type: "human",
    content: "查看项目面板",
    additional_kwargs: { timestamp: "2026-08-14T10:15:45+08:00" },
  } as Message;
  const historyAi = {
    id: "event-ai-2",
    type: "ai",
    content: "已生成交互式 HTML 看板。",
  } as Message;
  const checkpointHuman = {
    id: "ckpt-human-2",
    type: "human",
    content: "查看项目面板",
  } as Message;
  const checkpointAi = {
    id: "ckpt-ai-2",
    type: "ai",
    content: "已生成交互式 HTML 看板。",
  } as Message;

  expect(
    mergeMessages(
      [historyHuman, historyAi],
      [olderHuman, olderAi, checkpointHuman, checkpointAi],
      [],
    ).map((message) => message.id),
  ).toEqual(["ckpt-human-1", "ckpt-ai-1", "event-human-2", "event-ai-2"]);
});

test("mergeMessages repairs inverted last turn when only checkpoint state is available", () => {
  const olderHuman = {
    id: "human-1",
    type: "human",
    content: "先看一下项目列表",
  } as Message;
  const olderAi = {
    id: "ai-1",
    type: "ai",
    content: "这是项目列表。",
  } as Message;
  const latestHuman = {
    id: "human-2",
    type: "human",
    content: "查看项目面板",
  } as Message;
  const latestAi = {
    id: "ai-2",
    type: "ai",
    content: "已生成交互式 HTML 看板。",
  } as Message;

  expect(
    mergeMessages([], [olderHuman, olderAi, latestAi, latestHuman], []).map(
      (message) => message.id,
    ),
  ).toEqual(["human-1", "ai-1", "human-2", "ai-2"]);
});
