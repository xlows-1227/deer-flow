import { beforeEach, expect, test, vi } from "vitest";

const { invalidateQueriesMock, useMutationMock, useQueryClientMock } =
  vi.hoisted(() => {
    const invalidateQueriesMock = vi.fn();
    return {
      invalidateQueriesMock,
      useMutationMock: vi.fn((config) => config),
      useQueryClientMock: vi.fn(() => ({
        invalidateQueries: invalidateQueriesMock,
      })),
    };
  });

vi.mock("@tanstack/react-query", () => ({
  useMutation: useMutationMock,
  useQuery: vi.fn(),
  useQueryClient: useQueryClientMock,
}));

beforeEach(() => {
  invalidateQueriesMock.mockReset();
  useMutationMock.mockClear();
  useQueryClientMock.mockClear();
});

test("useUploadFiles refreshes uploaded and sandbox files after upload", async () => {
  const { useUploadFiles } = await import("@/core/uploads/hooks");

  const mutationConfig = useUploadFiles("thread-1") as unknown as {
    onSuccess: () => void;
  };
  mutationConfig.onSuccess();

  expect(invalidateQueriesMock).toHaveBeenCalledWith({
    queryKey: ["uploads", "list", "thread-1"],
  });
  expect(invalidateQueriesMock).toHaveBeenCalledWith({
    queryKey: ["sandbox-files", "thread-1"],
  });
});
