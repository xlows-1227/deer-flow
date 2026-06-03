import { beforeEach, expect, test, vi } from "vitest";

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: useQueryMock,
}));

beforeEach(() => {
  useQueryMock.mockReset();
});

test("useSandboxFiles does not poll the workspace file list", async () => {
  const { useSandboxFiles } = await import("@/core/sandbox/hooks");

  useSandboxFiles("thread-1");

  expect(useQueryMock).toHaveBeenCalledWith(
    expect.objectContaining({
      queryKey: ["sandbox-files", "thread-1"],
      staleTime: 1000,
    }),
  );
  expect(useQueryMock.mock.calls[0]?.[0]).not.toHaveProperty("refetchInterval");
});
