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
