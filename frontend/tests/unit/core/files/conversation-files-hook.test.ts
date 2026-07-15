import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { getStateMock } = vi.hoisted(() => ({
  getStateMock: vi.fn(),
}));

vi.mock("@/core/api", () => ({
  getAPIClient: () => ({
    threads: { getState: getStateMock },
  }),
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreads: () => ({
    data: [
      {
        thread_id: "thread-generated",
        updated_at: "2026-07-15T08:00:00Z",
        values: { title: "Generated report" },
        metadata: {},
      },
    ],
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

import { useAllUserFiles } from "@/core/files/hooks";

type FilesResult = ReturnType<typeof useAllUserFiles>;

function FilesProbe({ onRender }: { onRender: (result: FilesResult) => void }) {
  onRender(
    useAllUserFiles({}, { conversationSource: "generated", enabled: true }),
  );
  return null;
}

beforeEach(() => {
  getStateMock.mockReset();
  getStateMock.mockResolvedValue({
    values: {
      artifacts: ["/mnt/user-data/outputs/interactive-report.html"],
    },
  });
  (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  vi.restoreAllMocks();
});

test("loads generated artifacts from each thread's latest state", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  let latest!: FilesResult;
  let renderer!: ReactTestRenderer;

  await act(async () => {
    renderer = create(
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(FilesProbe, {
          onRender: (result: FilesResult) => {
            latest = result;
          },
        }),
      ),
    );
  });

  await act(async () => {
    await vi.waitFor(() => {
      expect(latest.files).toMatchObject([
        {
          name: "interactive-report.html",
          source_thread_id: "thread-generated",
          source_thread_title: "Generated report",
          conversation_source: "generated",
        },
      ]);
    });
  });
  expect(getStateMock).toHaveBeenCalledWith("thread-generated");

  await act(async () => renderer.unmount());
  queryClient.clear();
});
