import { useQuery } from "@tanstack/react-query";

import { listSandboxFiles } from "./api";

export function sandboxFilesQueryKey(threadId: string) {
  return ["sandbox-files", threadId] as const;
}

export function useSandboxFiles(threadId: string, enabled = true) {
  return useQuery({
    queryKey: sandboxFilesQueryKey(threadId),
    queryFn: () => listSandboxFiles(threadId),
    enabled: enabled && Boolean(threadId),
    staleTime: 1000,
    retry: false,
  });
}
