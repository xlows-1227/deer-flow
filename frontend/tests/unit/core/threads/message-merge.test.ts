import type { Message, Run } from "@langchain/langgraph-sdk";
import { expect, test } from "vitest";

import {
  getVisibleOptimisticMessagesForServerMessages,
  getVisibleOptimisticMessages,
  mergeLoadedRunMessages,
  mergeMessages,
} from "@/core/threads/hooks";

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
        [olderRun.run_id, [olderHuman, olderAi]],
        [newerRun.run_id, [newerHuman]],
      ]),
    ),
  ).toEqual([olderHuman, olderAi, newerHuman]);
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

  expect(
    mergeMessages(
      [],
      [reminder, clarificationAi, secondHuman, firstHumanCopy],
      [],
    ).map((message) => message.id),
  ).toEqual(["turn-1", "turn-1__user", "ai-1", "turn-2"]);
});
