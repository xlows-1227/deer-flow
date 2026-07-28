"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  changePublishedAgentStatus,
  createAgentChannel,
  createDraftSandboxRun,
  createAgentKey,
  createPublishedAgent,
  deleteAgentKey,
  deleteAgentChannel,
  getAgentDraftOptions,
  getAgentQuotaPolicy,
  getAgentRelease,
  getAgentUsage,
  getDraftSandboxThread,
  getPublishedAgent,
  listAgentChannels,
  listAgentAuditEvents,
  listAgentKeys,
  listAgentReleases,
  listPublishedAgents,
  publishAgent,
  rollbackAgent,
  runAgentChannelAction,
  updateAgentChannel,
  updateAgentDraft,
  updateAgentKey,
} from "./api";
import type {
  AgentChannelCredentials,
  CreateAgentKeyInput,
  CreatePublishedAgentInput,
  UpdateAgentChannelCredentials,
  UpdateAgentDraftInput,
  UpdateAgentKeyInput,
} from "./types";

export const publishedAgentQueryKeys = {
  all: ["published-agents"] as const,
  list: () => [...publishedAgentQueryKeys.all, "list"] as const,
  detail: (agentId: string) =>
    [...publishedAgentQueryKeys.all, agentId] as const,
  draftOptions: (agentId: string) =>
    [...publishedAgentQueryKeys.detail(agentId), "draft-options"] as const,
  sandboxThread: (threadId: string) =>
    [...publishedAgentQueryKeys.all, "sandbox-thread", threadId] as const,
  releases: (agentId: string) =>
    [...publishedAgentQueryKeys.detail(agentId), "releases"] as const,
  release: (agentId: string, releaseNo: number) =>
    [...publishedAgentQueryKeys.releases(agentId), releaseNo] as const,
  keys: (agentId: string) =>
    [...publishedAgentQueryKeys.detail(agentId), "keys"] as const,
  channels: (agentId: string) =>
    [...publishedAgentQueryKeys.detail(agentId), "channels"] as const,
  usage: (
    agentId: string,
    days: number,
    source?: "api" | "feishu",
    keyId?: string,
  ) =>
    [
      ...publishedAgentQueryKeys.detail(agentId),
      "usage",
      days,
      source ?? "all",
      keyId ?? "all",
    ] as const,
  quota: (agentId: string) =>
    [...publishedAgentQueryKeys.detail(agentId), "quota"] as const,
  audit: (agentId: string, limit: number) =>
    [...publishedAgentQueryKeys.detail(agentId), "audit", limit] as const,
};

export function usePublishedAgents(enabled = true) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.list(),
    queryFn: listPublishedAgents,
    enabled,
  });
  return {
    ...query,
    agents: query.data ?? [],
  };
}

export function usePublishedAgent(agentId: string | null | undefined) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.detail(agentId ?? ""),
    queryFn: () => getPublishedAgent(agentId!),
    enabled: Boolean(agentId),
  });
  return {
    ...query,
    agent: query.data ?? null,
  };
}

export function useAgentDraft(agentId: string | null | undefined) {
  const query = usePublishedAgent(agentId);
  return {
    ...query,
    draft: query.agent?.draft ?? null,
  };
}

export function useAgentDraftOptions(agentId: string | null | undefined) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.draftOptions(agentId ?? ""),
    queryFn: () => getAgentDraftOptions(agentId!),
    enabled: Boolean(agentId),
  });
  return {
    ...query,
    skills: query.data?.skills ?? [],
  };
}

export function useCreatePublishedAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreatePublishedAgentInput) =>
      createPublishedAgent(input),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: publishedAgentQueryKeys.list(),
      }),
  });
}

export function useUpdateAgentDraft(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: UpdateAgentDraftInput) =>
      updateAgentDraft(agentId, input),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.detail(agentId),
        }),
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.quota(agentId),
        }),
      ]);
    },
  });
}

export function useDraftSandboxRun(agentId: string) {
  return useMutation({
    mutationFn: (message: string) => createDraftSandboxRun(agentId, message),
  });
}

export function useDraftSandboxThread(threadId: string | null | undefined) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.sandboxThread(threadId ?? ""),
    queryFn: () => getDraftSandboxThread(threadId!),
    enabled: Boolean(threadId),
    retry: false,
  });
  return {
    ...query,
    sandbox: query.data ?? null,
  };
}

export function usePublishedAgentLifecycle(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: "archive" | "suspend" | "resume") =>
      changePublishedAgentStatus(agentId, action),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.list(),
        }),
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.detail(agentId),
        }),
      ]);
    },
  });
}

export function useAgentReleases(agentId: string | null | undefined) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.releases(agentId ?? ""),
    queryFn: () => listAgentReleases(agentId!),
    enabled: Boolean(agentId),
  });
  return { ...query, releases: query.data ?? [] };
}

export function useAgentRelease(
  agentId: string | null | undefined,
  releaseNo: number | null | undefined,
) {
  return useQuery({
    queryKey: publishedAgentQueryKeys.release(agentId ?? "", releaseNo ?? 0),
    queryFn: () => getAgentRelease(agentId!, releaseNo!),
    enabled: Boolean(agentId && releaseNo),
  });
}

export function usePublishAgent(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => publishAgent(agentId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.list(),
        }),
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.detail(agentId),
        }),
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.releases(agentId),
        }),
      ]);
    },
  });
}

export function useRollbackAgent(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (releaseNo: number) => rollbackAgent(agentId, releaseNo),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.list(),
        }),
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.detail(agentId),
        }),
        queryClient.invalidateQueries({
          queryKey: publishedAgentQueryKeys.releases(agentId),
        }),
      ]);
    },
  });
}

export function useAgentKeys(agentId: string | null | undefined) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.keys(agentId ?? ""),
    queryFn: () => listAgentKeys(agentId!),
    enabled: Boolean(agentId),
  });
  return { ...query, keys: query.data ?? [] };
}

export function useCreateAgentKey(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateAgentKeyInput) => createAgentKey(agentId, input),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: publishedAgentQueryKeys.keys(agentId),
      }),
  });
}

export function useUpdateAgentKey(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      keyId,
      input,
    }: {
      keyId: string;
      input: UpdateAgentKeyInput;
    }) => updateAgentKey(agentId, keyId, input),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: publishedAgentQueryKeys.keys(agentId),
      }),
  });
}

export function useDeleteAgentKey(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (keyId: string) => deleteAgentKey(agentId, keyId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: publishedAgentQueryKeys.keys(agentId),
      }),
  });
}

export function useAgentChannels(
  agentId: string | null | undefined,
  options: { polling?: boolean } = {},
) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.channels(agentId ?? ""),
    queryFn: () => listAgentChannels(agentId!),
    enabled: Boolean(agentId),
    refetchInterval: options.polling ? 5_000 : false,
  });
  return { ...query, channels: query.data ?? [] };
}

export function useCreateAgentChannel(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: AgentChannelCredentials) =>
      createAgentChannel(agentId, input),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: publishedAgentQueryKeys.channels(agentId),
      }),
  });
}

export function useUpdateAgentChannel(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      bindingId,
      input,
    }: {
      bindingId: string;
      input: UpdateAgentChannelCredentials;
    }) => updateAgentChannel(agentId, bindingId, input),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: publishedAgentQueryKeys.channels(agentId),
      }),
  });
}

export function useAgentChannelAction(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      bindingId,
      action,
    }: {
      bindingId: string;
      action: "test" | "start" | "stop" | "restart";
    }) => runAgentChannelAction(agentId, bindingId, action),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: publishedAgentQueryKeys.channels(agentId),
      }),
  });
}

export function useDeleteAgentChannel(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (bindingId: string) => deleteAgentChannel(agentId, bindingId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: publishedAgentQueryKeys.channels(agentId),
      }),
  });
}

export function useAgentUsage(
  agentId: string | null | undefined,
  days = 30,
  filters: {
    source?: "api" | "feishu";
    keyId?: string;
  } = {},
) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.usage(
      agentId ?? "",
      days,
      filters.source,
      filters.keyId,
    ),
    queryFn: () => getAgentUsage(agentId!, days, filters.source, filters.keyId),
    enabled: Boolean(agentId),
  });
  return { ...query, usage: query.data ?? null };
}

export function useAgentQuotaPolicy(agentId: string | null | undefined) {
  return useQuery({
    queryKey: publishedAgentQueryKeys.quota(agentId ?? ""),
    queryFn: () => getAgentQuotaPolicy(agentId!),
    enabled: Boolean(agentId),
  });
}

export function useAgentAuditEvents(
  agentId: string | null | undefined,
  limit = 20,
) {
  const query = useQuery({
    queryKey: publishedAgentQueryKeys.audit(agentId ?? "", limit),
    queryFn: () => listAgentAuditEvents(agentId!, limit),
    enabled: Boolean(agentId),
  });
  return { ...query, events: query.data ?? [] };
}
