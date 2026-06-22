import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/core/auth/AuthProvider";

import {
  createCustomSkillVersionSnapshot,
  deleteCustomSkill,
  enableSkill,
  listCustomSkillFiles,
  listCustomSkillVersionFiles,
  listCustomSkillVersions,
  loadCustomSkill,
  loadCustomSkills,
  loadPublicSkill,
  readCustomSkillFile,
  readCustomSkillVersionFile,
  restoreCustomSkillVersion,
  updateCustomSkill,
} from "./api";
import type { Skill, SkillFileEntry, SkillVersion } from "./type";

import { loadSkills } from ".";

function useSkillQueryRoot() {
  const { user } = useAuth();
  return ["skills", user?.id ?? "anonymous"] as const;
}

export function useSkills() {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error } = useQuery<Skill[]>({
    queryKey: queryRoot,
    queryFn: () => loadSkills(),
  });
  return { skills: data ?? [], isLoading, error };
}

export function useCustomSkill(skillName: string | null) {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...queryRoot, "custom", skillName],
    queryFn: () => loadCustomSkill(skillName!),
    enabled: !!skillName,
  });
  return { skill: data ?? null, isLoading, error, refetch };
}

export function usePublicSkill(skillName: string | null) {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error } = useQuery({
    queryKey: [...queryRoot, "public", skillName],
    queryFn: () => loadPublicSkill(skillName!),
    enabled: !!skillName,
  });
  return { skill: data ?? null, isLoading, error };
}

export function useEnableSkill() {
  const queryClient = useQueryClient();
  const queryRoot = useSkillQueryRoot();
  return useMutation({
    mutationFn: async ({
      skillName,
      enabled,
    }: {
      skillName: string;
      enabled: boolean;
    }) => {
      await enableSkill(skillName, enabled);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryRoot });
    },
  });
}

export function useDeleteCustomSkill() {
  const queryClient = useQueryClient();
  const queryRoot = useSkillQueryRoot();
  return useMutation({
    mutationFn: (skillName: string) => deleteCustomSkill(skillName),
    onSuccess: (_data, skillName) => {
      void queryClient.invalidateQueries({ queryKey: queryRoot });
      void queryClient.invalidateQueries({
        queryKey: [...queryRoot, "custom"],
      });
      void queryClient.removeQueries({
        queryKey: [...queryRoot, "custom", skillName],
      });
    },
  });
}

export function useCustomSkills(options?: {
  refetchInterval?: number | false;
}) {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error, refetch } = useQuery<Skill[]>({
    queryKey: [...queryRoot, "custom"],
    queryFn: () => loadCustomSkills(),
    refetchInterval: options?.refetchInterval,
  });
  return { skills: data ?? [], isLoading, error, refetch };
}

export function useCustomSkillFiles(
  skillName: string | null,
  options?: { refetchInterval?: number | false },
) {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error, refetch, isFetching } = useQuery<
    SkillFileEntry[]
  >({
    queryKey: [...queryRoot, "custom", skillName, "files"],
    queryFn: () => listCustomSkillFiles(skillName!),
    enabled: !!skillName,
    refetchInterval: options?.refetchInterval,
    placeholderData: (previous) => previous,
  });
  return {
    files: data ?? [],
    isLoading,
    isFetching,
    error,
    refetch,
  };
}

export function useCustomSkillFile(
  skillName: string | null,
  path: string | null,
) {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...queryRoot, "custom", skillName, "file", path],
    queryFn: () => readCustomSkillFile(skillName!, path!),
    enabled: !!skillName && !!path,
  });
  return { file: data ?? null, isLoading, error, refetch };
}

export function useUpdateCustomSkill() {
  const queryClient = useQueryClient();
  const queryRoot = useSkillQueryRoot();
  return useMutation({
    mutationFn: async ({
      skillName,
      content,
    }: {
      skillName: string;
      content: string;
    }) => updateCustomSkill(skillName, content),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryRoot });
      void queryClient.invalidateQueries({
        queryKey: [...queryRoot, "custom", variables.skillName],
      });
      void queryClient.invalidateQueries({
        queryKey: [...queryRoot, "custom", variables.skillName, "files"],
      });
    },
  });
}

export function useCustomSkillVersions(skillName: string | null) {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error, refetch } = useQuery<SkillVersion[]>({
    queryKey: [...queryRoot, "custom", skillName, "versions"],
    queryFn: () => listCustomSkillVersions(skillName!),
    enabled: !!skillName,
  });
  return { versions: data ?? [], isLoading, error, refetch };
}

export function useCreateCustomSkillVersionSnapshot() {
  const queryClient = useQueryClient();
  const queryRoot = useSkillQueryRoot();
  return useMutation({
    mutationFn: async ({
      skillName,
      action,
      message,
      thread_id,
    }: {
      skillName: string;
      action?: string;
      message?: string;
      thread_id?: string | null;
    }) =>
      createCustomSkillVersionSnapshot(skillName, {
        action,
        message,
        thread_id,
      }),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: [...queryRoot, "custom", variables.skillName, "versions"],
      });
    },
  });
}

export function useCustomSkillVersionFiles(
  skillName: string | null,
  seq: number | null,
) {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error, refetch } = useQuery<SkillFileEntry[]>({
    queryKey: [...queryRoot, "custom", skillName, "versions", seq, "files"],
    queryFn: () => listCustomSkillVersionFiles(skillName!, seq!),
    enabled: !!skillName && typeof seq === "number",
  });
  return { files: data ?? [], isLoading, error, refetch };
}

export function useCustomSkillVersionFile(
  skillName: string | null,
  seq: number | null,
  path: string | null,
) {
  const queryRoot = useSkillQueryRoot();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [
      ...queryRoot,
      "custom",
      skillName,
      "versions",
      seq,
      "file",
      path,
    ],
    queryFn: () => readCustomSkillVersionFile(skillName!, seq!, path!),
    enabled: !!skillName && typeof seq === "number" && !!path,
  });
  return { file: data ?? null, isLoading, error, refetch };
}

export function useRestoreCustomSkillVersion() {
  const queryClient = useQueryClient();
  const queryRoot = useSkillQueryRoot();
  return useMutation({
    mutationFn: async ({
      skillName,
      seq,
    }: {
      skillName: string;
      seq: number;
    }) => restoreCustomSkillVersion(skillName, seq),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryRoot });
      void queryClient.invalidateQueries({
        queryKey: [...queryRoot, "custom", variables.skillName],
      });
      void queryClient.invalidateQueries({
        queryKey: [...queryRoot, "custom", variables.skillName, "files"],
      });
      void queryClient.invalidateQueries({
        queryKey: [...queryRoot, "custom", variables.skillName, "versions"],
      });
    },
  });
}
