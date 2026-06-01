import { useMutation, useQuery } from "@tanstack/react-query";

import { createThreadShare, fetchSharedThread } from "./api";

export function useCreateThreadShare() {
  return useMutation({
    mutationFn: async (threadId: string) => {
      return createThreadShare(threadId);
    },
  });
}

export function useSharedThread(token: string) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["shared-thread", token],
    queryFn: () => fetchSharedThread(token),
    enabled: !!token,
  });
  return { thread: data, isLoading, error };
}
