import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { fetchWithAuth } = vi.hoisted(() => ({
  fetchWithAuth: vi.fn(),
}));

vi.mock("@/core/api/fetcher", () => ({
  fetch: fetchWithAuth,
}));

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://localhost:8001",
}));

import { useFilePublications } from "@/core/files/hooks";

type PublicationsResult = ReturnType<typeof useFilePublications>;

function PublicationsProbe({
  onRender,
}: {
  onRender: (result: PublicationsResult) => void;
}) {
  onRender(useFilePublications({ enabled: true }));
  return null;
}

beforeEach(() => {
  fetchWithAuth.mockReset();
  fetchWithAuth.mockResolvedValue({
    ok: true,
    json: async () => ({
      items: [
        {
          id: "publication-1",
          name: "report.html",
          thread_id: "thread-1",
          path: "/mnt/user-data/outputs/report.html",
          public_token: "public-token",
          public_url: "/published/public-token",
          created_at: "2026-07-15T08:00:00Z",
        },
      ],
      total: 1,
    }),
  });
  (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
  ).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => vi.restoreAllMocks());

test("loads the current user's file publications", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  let latest!: PublicationsResult;
  let renderer!: ReactTestRenderer;

  await act(async () => {
    renderer = create(
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(PublicationsProbe, {
          onRender: (result: PublicationsResult) => {
            latest = result;
          },
        }),
      ),
    );
  });
  await act(async () => {
    await vi.waitFor(() => {
      expect(latest.publications).toMatchObject([
        { id: "publication-1", public_token: "public-token" },
      ]);
    });
  });

  await act(async () => renderer.unmount());
  queryClient.clear();
});
