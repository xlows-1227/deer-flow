import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  clearMemory,
  createMemoryFact,
  deleteDailyMemory,
  deleteMemoryFact,
  importMemory,
  loadDailyMemory,
  loadMemory,
  loadMemoryProfile,
  rollupDailyMemory,
  rollupThreadMemory,
  updateMemoryFact,
} from "./api";
import type {
  MemoryFactInput,
  MemoryFactPatchInput,
  UserMemory,
} from "./types";

export function useMemory() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["memory"],
    queryFn: () => loadMemory(),
  });
  return { memory: data ?? null, isLoading, error };
}

export function useMemoryProfile() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["memory", "profile"],
    queryFn: () => loadMemoryProfile(),
  });
  return { profile: data ?? null, isLoading, error };
}

export function useDailyMemory(limit = 30) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["memory", "daily", limit],
    queryFn: () => loadDailyMemory(limit),
  });
  return { dailyMemory: data ?? [], isLoading, error };
}

export function useClearMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => clearMemory(),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(["memory"], memory);
      queryClient.setQueryData(["memory", "profile"], null);
      queryClient.setQueriesData({ queryKey: ["memory", "daily"] }, []);
      void queryClient.invalidateQueries({ queryKey: ["memory", "profile"] });
      void queryClient.invalidateQueries({ queryKey: ["memory", "daily"] });
    },
  });
}

export function useRollupDailyMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input?: {
      date?: string;
      threadId?: string;
      force?: boolean;
    }) => rollupDailyMemory(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });
}

export function useRollupThreadMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (threadId: string) => rollupThreadMemory(threadId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });
}

export function useDeleteDailyMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (date: string) => deleteDailyMemory(date),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });
}

export function useDeleteMemoryFact() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (factId: string) => deleteMemoryFact(factId),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(["memory"], memory);
      void queryClient.invalidateQueries({ queryKey: ["memory", "profile"] });
    },
  });
}

export function useImportMemory() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (memory: UserMemory) => importMemory(memory),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(["memory"], memory);
      void queryClient.invalidateQueries({ queryKey: ["memory", "profile"] });
      void queryClient.invalidateQueries({ queryKey: ["memory", "daily"] });
    },
  });
}

export function useCreateMemoryFact() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: MemoryFactInput) => createMemoryFact(input),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(["memory"], memory);
      void queryClient.invalidateQueries({ queryKey: ["memory", "profile"] });
    },
  });
}

export function useUpdateMemoryFact() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      factId,
      input,
    }: {
      factId: string;
      input: MemoryFactPatchInput;
    }) => updateMemoryFact(factId, input),
    onSuccess: (memory) => {
      queryClient.setQueryData<UserMemory>(["memory"], memory);
      void queryClient.invalidateQueries({ queryKey: ["memory", "profile"] });
    },
  });
}
