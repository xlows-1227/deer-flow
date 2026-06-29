"use client";

import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { useThreads } from "@/core/threads/hooks";
import { listUploadedFiles } from "@/core/uploads/api";

import {
  getUserFileUploadConfig,
  listFiles,
  listUserFolders,
  threadUploadToFileItem,
  type ListFilesParams,
} from "./api";
import type { FileItem } from "./type";

interface FilesQueryOptions {
  enabled?: boolean;
}

/**
 * TanStack Query wrapper around {@link listFiles}. Used by the @-mention
 * picker in the chat input to populate the file suggestion list.
 *
 * The picker is "live" — it should not block the user from typing, so we
 * `enabled: true` always and let the picker show a loading skeleton while
 * the request is in flight.
 */
export function useFiles(
  params: ListFilesParams = {},
  { enabled = true }: FilesQueryOptions = {},
) {
  // Stabilize the query key on the actual field values. We deliberately
  // omit `limit` from the key — the same set of files, just trimmed.
  // The leading underscore tells the linter we intentionally read-and-discard.
  const stableParams: ListFilesParams = (() => {
    const { limit: _limit, ...rest } = params;
    void _limit;
    return rest;
  })();
  const query = useQuery<FileItem[]>({
    queryKey: ["files", "list", stableParams],
    queryFn: () => listFiles(params),
    enabled,
    // Pickers like this one need the latest data fresh; the library is
    // small and changes infrequently. 30s is a good balance between
    // "show me the file I just uploaded" and "don't refetch on every
    // keystroke" — we re-fetch explicitly after uploads via invalidation
    // in the future.
    staleTime: 30_000,
  });
  return {
    files: query.data ?? [],
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    error: query.error,
    refetch: query.refetch,
  };
}

export function useUserFolders() {
  const query = useQuery<string[]>({
    queryKey: ["files", "folders"],
    queryFn: listUserFolders,
  });
  return {
    folders: query.data ?? [],
    isLoading: query.isLoading,
    refetch: query.refetch,
  };
}

export function useUserFileUploadConfig() {
  const query = useQuery({
    queryKey: ["files", "upload-config"],
    queryFn: getUserFileUploadConfig,
    staleTime: Number.POSITIVE_INFINITY,
  });
  return {
    config: query.data ?? null,
    isLoading: query.isLoading,
  };
}

/**
 * How many recent threads to scan for chat-uploaded files when the
 * file-management page stitches a unified view together. Tuned for the
 * common case (a handful of recent threads) — beyond this we'd want a
 * proper backend "list everything" endpoint rather than N+1 fan-out.
 */
const MAX_THREADS_TO_SCAN = 50;

const threadUploadsQueryKey = (threadId: string) =>
  ["uploads", "list", threadId] as const;

/**
 * Unified file list for the file-management page: library files + chat
 * thread uploads, normalized to the same {@link FileItem} shape.
 *
 * The two storage locations historically have separate UIs:
 *
 * - Library files live in the user document store (`/api/files`) and
 *   are uploaded via this page.
 * - Thread uploads are dropped into the per-thread sandbox uploads
 *   directory when the user attaches a file to a chat turn.
 *
 * From the user's point of view they're all "my files", so this hook
 * glues them together in one query so the management page can render
 * a single list. Each merged record carries `source_thread_id` when
 * the record came from a thread; the page uses that field to render
 * the source label and to route open/delete calls to the right
 * endpoint.
 *
 * The hook intentionally stays client-side: we fan out N small
 * `listUploadedFiles` calls in parallel via `useQueries`, which is
 * cheap for the typical "few recent threads" case. If a user routinely
 * has hundreds of active threads, swap this for a real backend
 * "list-all-files" endpoint.
 */
export function useAllUserFiles(
  params: ListFilesParams = {},
  { enabled = true }: FilesQueryOptions = {},
) {
  const queryClient = useQueryClient();
  const library = useFiles(params, { enabled });
  const inLibraryFolder = !!params.folder_path;
  const threads = useThreads(
    {
      limit: MAX_THREADS_TO_SCAN,
      sortBy: "updated_at",
      sortOrder: "desc",
    },
    { enabled: enabled && !inLibraryFolder },
  );

  const threadUploads = useQueries({
    queries: (threads.data ?? []).map((thread) => ({
      queryKey: threadUploadsQueryKey(thread.thread_id),
      queryFn: () => listUploadedFiles(thread.thread_id),
      // Only fire after the thread list arrives. Errors are non-fatal —
      // a single thread's uploads failing shouldn't break the whole
      // page; we filter them out of the merged result.
      enabled: enabled && !!threads.data && !inLibraryFolder,
      retry: false,
    })),
  });

  const files = useMemo<FileItem[]>(() => {
    const threadTitleById = new Map<string, string | undefined>();
    for (const thread of threads.data ?? []) {
      // Thread titles live in `values.title` (set by TitleMiddleware).
      const title = (thread.values as { title?: string } | undefined)?.title;
      threadTitleById.set(thread.thread_id, title);
    }

    if (inLibraryFolder) {
      return library.files;
    }

    const threadItems: FileItem[] = [];
    (threads.data ?? []).forEach((thread, index) => {
      const query = threadUploads[index];
      if (!query?.data) {
        return;
      }
      const title = threadTitleById.get(thread.thread_id);
      for (const file of query.data.files) {
        threadItems.push(threadUploadToFileItem(file, thread.thread_id, title));
      }
    });

    return [...library.files, ...threadItems];
  }, [inLibraryFolder, library.files, threads.data, threadUploads]);

  return {
    files,
    isLoading:
      enabled && (library.isLoading || (!inLibraryFolder && threads.isLoading)),
    isFetching:
      enabled &&
      (library.isFetching || (!inLibraryFolder && threads.isFetching)),
    error: library.error,
    /**
     * Manual refetch — useful after explicit user actions (delete, upload, or
     * refresh). The merged list includes per-thread upload queries, so those
     * caches must be invalidated too; otherwise deleted chat uploads linger
     * until a full page reload.
     */
    refetch: async () => {
      await Promise.all([
        library.refetch(),
        threads.refetch(),
        inLibraryFolder
          ? Promise.resolve()
          : queryClient.invalidateQueries({ queryKey: ["uploads", "list"] }),
      ]);
    },
  };
}
