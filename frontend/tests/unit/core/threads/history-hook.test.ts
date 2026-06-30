import type { Message, Run } from "@langchain/langgraph-sdk";
import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { fetchMock, runsByThread, toastErrorMock } = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  runsByThread: new Map<string, Run[]>(),
  toastErrorMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: vi.fn(),
  useQuery: vi.fn(({ queryKey }: { queryKey: unknown[] }) => ({
    data: runsByThread.get(String(queryKey[1])),
  })),
  useQueryClient: vi.fn(),
}));

vi.mock("@/core/api", () => ({
  getAPIClient: () => ({ runs: { list: vi.fn() } }),
}));

vi.mock("@/core/api/fetcher", () => ({
  fetch: fetchMock,
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

vi.mock("sonner", () => ({
  toast: { error: toastErrorMock },
}));

import { useThreadHistory } from "@/core/threads/hooks";

type HistoryResult = ReturnType<typeof useThreadHistory>;

type DeferredResponse = {
  promise: Promise<{ ok: boolean; json: () => Promise<unknown> }>;
  resolve: (value: { ok: boolean; json: () => Promise<unknown> }) => void;
};

function deferredResponse(): DeferredResponse {
  let resolve!: DeferredResponse["resolve"];
  const promise = new Promise<{ ok: boolean; json: () => Promise<unknown> }>(
    (resolvePromise) => {
      resolve = resolvePromise;
    },
  );
  return { promise, resolve };
}

function run(runId: string): Run {
  return { run_id: runId } as Run;
}

function response(messageId: string, content: string) {
  const message = {
    id: messageId,
    type: "human",
    content,
  } as Message;
  return {
    ok: true,
    json: async () => ({
      data: [{ content: message, metadata: {}, created_at: null }],
      hasMore: false,
    }),
  };
}

function HistoryProbe({
  threadId,
  onRender,
}: {
  threadId: string;
  revision: number;
  onRender: (result: HistoryResult) => void;
}) {
  const result = useThreadHistory(threadId);
  onRender(result);
  return null;
}

async function renderProbe(
  threadId: string,
  onRender: (result: HistoryResult) => void,
) {
  let renderer!: ReactTestRenderer;
  await act(async () => {
    renderer = create(
      createElement(HistoryProbe, { threadId, revision: 0, onRender }),
    );
  });
  return renderer;
}

beforeEach(() => {
  runsByThread.clear();
  fetchMock.mockReset();
  toastErrorMock.mockReset();
  (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  vi.restoreAllMocks();
});

test("ignores a stale A response after switching A -> B -> A", async () => {
  const oldA = deferredResponse();
  const threadB = deferredResponse();
  const newA = deferredResponse();
  fetchMock
    .mockReturnValueOnce(oldA.promise)
    .mockReturnValueOnce(threadB.promise)
    .mockReturnValueOnce(newA.promise);
  runsByThread.set("thread-a", [run("run-a-old")]);
  runsByThread.set("thread-b", [run("run-b")]);

  let latest!: HistoryResult;
  const onRender = (result: HistoryResult) => {
    latest = result;
  };
  const renderer = await renderProbe("thread-a", onRender);

  await act(async () => {
    renderer.update(
      createElement(HistoryProbe, {
        threadId: "thread-b",
        revision: 1,
        onRender,
      }),
    );
  });
  runsByThread.set("thread-a", [run("run-a-new")]);
  await act(async () => {
    renderer.update(
      createElement(HistoryProbe, {
        threadId: "thread-a",
        revision: 2,
        onRender,
      }),
    );
  });

  await act(async () => {
    newA.resolve(response("new-a", "new A"));
    await newA.promise;
  });
  expect(latest.messages.map((message) => message.content)).toEqual(["new A"]);

  await act(async () => {
    oldA.resolve(response("old-a", "old A"));
    await oldA.promise;
  });
  expect(latest.messages.map((message) => message.content)).toEqual(["new A"]);

  await act(async () => {
    renderer.unmount();
    threadB.resolve(response("b", "B"));
    await threadB.promise;
  });
});

test("aborts and invalidates an in-flight request on unmount", async () => {
  const pending = deferredResponse();
  fetchMock.mockReturnValueOnce(pending.promise);
  runsByThread.set("thread-a", [run("run-a")]);

  let latest!: HistoryResult;
  const renderer = await renderProbe("thread-a", (result) => {
    latest = result;
  });
  const signal = fetchMock.mock.calls[0]?.[1]?.signal as AbortSignal;
  expect(latest.loading).toBe(true);
  expect(signal.aborted).toBe(false);

  await act(async () => {
    renderer.unmount();
  });
  expect(signal.aborted).toBe(true);

  await act(async () => {
    pending.resolve(response("late", "late result"));
    await pending.promise;
  });
  expect(toastErrorMock).not.toHaveBeenCalled();
});

test("loads a new run discovered while another history request is pending", async () => {
  const first = deferredResponse();
  const second = deferredResponse();
  fetchMock
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  runsByThread.set("thread-a", [run("run-1")]);

  let latest!: HistoryResult;
  const onRender = (result: HistoryResult) => {
    latest = result;
  };
  const renderer = await renderProbe("thread-a", onRender);

  runsByThread.set("thread-a", [run("run-1"), run("run-2")]);
  await act(async () => {
    renderer.update(
      createElement(HistoryProbe, {
        threadId: "thread-a",
        revision: 1,
        onRender,
      }),
    );
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);

  await act(async () => {
    first.resolve(response("first", "first"));
    await first.promise;
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);

  await act(async () => {
    second.resolve(response("second", "second"));
    await second.promise;
  });
  expect(latest.messages.map((message) => message.content)).toEqual([
    "first",
    "second",
  ]);

  await act(async () => {
    renderer.unmount();
  });
});
