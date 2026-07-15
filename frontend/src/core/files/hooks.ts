"use client";

import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { getAPIClient } from "@/core/api";
import { useThreads } from "@/core/threads/hooks";
import type { AgentThreadState } from "@/core/threads/types";
import { listUploadedFiles } from "@/core/uploads/api";

import {
  getUserFileUploadConfig,
  listFiles,
  listUserFolders,
  threadUploadToFileItem,
  type ListFilesParams,
} from "./api";
import { threadArtifactToFileItem } from "./conversation";
import { listReceivedFileShares } from "./sharing";
import type { ConversationFileSource, FileItem } from "./type";

interface FilesQueryOptions {
  enabled?: boolean;
}

interface AllUserFilesOptions extends FilesQueryOptions {
  conversationSource?: ConversationFileSource | null;
}

export function useSharedFiles({ enabled = true }: FilesQueryOptions = {}) {
  const query = useQuery<FileItem[]>({
    queryKey: ["files", "shared-with-me"],
    queryFn: listReceivedFileShares,
    enabled,
  });
  return {
    files: query.data ?? [],
    isLoading: enabled ? query.isLoading : false,
    isFetching: enabled && query.isFetching,
    error: query.error,
    refetch: query.refetch,
  };
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

const threadStateQueryKey = (threadId: string) =>
  ["threads", "state", threadId] as const;

/**
 * Data source for the file-management page. The normal view returns library
 * files only. Selecting a locked conversation folder switches the hook to
 * either per-thread uploads or generated artifacts, both normalized to the
 * shared {@link FileItem} shape.
 *
 * Upload discovery intentionally stays client-side: it fans out N small
 * `listUploadedFiles` calls via `useQueries`. Thread search only returns the
 * display title in `values`, so generated artifacts are loaded from each
 * thread's latest checkpoint state with a second bounded fan-out.
 */
export function useAllUserFiles(
  params: ListFilesParams = {},
  { enabled = true, conversationSource = null }: AllUserFilesOptions = {},
) {
  const queryClient = useQueryClient();
  const showingConversationFiles = conversationSource !== null;
  const library = useFiles(params, {
    enabled: enabled && !showingConversationFiles,
  });
  const threads = useThreads(
    {
      limit: MAX_THREADS_TO_SCAN,
      sortBy: "updated_at",
      sortOrder: "desc",
      select: ["thread_id", "updated_at", "values", "metadata"],
    },
    { enabled: enabled && showingConversationFiles },
  );

  const threadUploads = useQueries({
    queries:
      conversationSource === "uploaded"
        ? (threads.data ?? []).map((thread) => ({
            queryKey: threadUploadsQueryKey(thread.thread_id),
            queryFn: () => listUploadedFiles(thread.thread_id),
            // A single inaccessible thread must not hide the remaining files.
            enabled: enabled && !!threads.data,
            retry: false,
          }))
        : [],
  });

  const threadStates = useQueries({
    queries:
      conversationSource === "generated"
        ? (threads.data ?? []).map((thread) => ({
            queryKey: threadStateQueryKey(thread.thread_id),
            queryFn: () =>
              getAPIClient().threads.getState<AgentThreadState>(
                thread.thread_id,
              ),
            // A deleted or inaccessible thread must not hide other artifacts.
            enabled: enabled && !!threads.data,
            retry: false,
          }))
        : [],
  });

  const files = useMemo<FileItem[]>(() => {
    if (!showingConversationFiles) {
      return library.files;
    }

    const threadTitleById = new Map<string, string | undefined>();
    for (const thread of threads.data ?? []) {
      // Thread titles live in `values.title` (set by TitleMiddleware).
      const title = (thread.values as { title?: string } | undefined)?.title;
      threadTitleById.set(thread.thread_id, title);
    }

    if (conversationSource === "generated") {
      const generatedItems: FileItem[] = [];
      (threads.data ?? []).forEach((thread, index) => {
        const state = threadStates[index]?.data;
        const title = threadTitleById.get(thread.thread_id);
        for (const artifact of state?.values?.artifacts ?? []) {
          generatedItems.push(
            threadArtifactToFileItem(
              artifact,
              thread.thread_id,
              title,
              thread.updated_at,
            ),
          );
        }
      });
      return generatedItems;
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

    return threadItems;
  }, [
    conversationSource,
    library.files,
    showingConversationFiles,
    threads.data,
    threadStates,
    threadUploads,
  ]);

  const threadUploadsLoading =
    conversationSource === "uploaded" &&
    threadUploads.some((query) => query.isLoading);
  const threadStatesLoading =
    conversationSource === "generated" &&
    threadStates.some((query) => query.isLoading);

  return {
    files,
    isLoading: enabled
      ? showingConversationFiles
        ? threads.isLoading || threadUploadsLoading || threadStatesLoading
        : library.isLoading
      : false,
    isFetching:
      enabled &&
      (showingConversationFiles
        ? threads.isFetching ||
          threadUploads.some((query) => query.isFetching) ||
          threadStates.some((query) => query.isFetching)
        : library.isFetching),
    error: showingConversationFiles ? threads.error : library.error,
    /**
     * Manual refetch after explicit user actions. Conversation uploads have
     * their own per-thread cache entries, which must be invalidated together.
     */
    refetch: async () => {
      if (!showingConversationFiles) {
        await library.refetch();
        return;
      }

      await threads.refetch();
      if (conversationSource === "uploaded") {
        await queryClient.invalidateQueries({
          queryKey: ["uploads", "list"],
        });
      } else if (conversationSource === "generated") {
        await queryClient.invalidateQueries({
          queryKey: ["threads", "state"],
        });
      }
    },
  };
}
