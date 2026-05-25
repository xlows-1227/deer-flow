import { useQuery } from "@tanstack/react-query";

import { listSandboxFiles } from "./api";

export function useSandboxFiles(threadId: string, enabled = true) {
  return useQuery({
    queryKey: ["sandbox-files", threadId],
    queryFn: () => listSandboxFiles(threadId),
    enabled: enabled && Boolean(threadId),
    refetchInterval: 3000,
    staleTime: 1000,
  });
}
