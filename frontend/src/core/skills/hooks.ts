import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { enableSkill, loadCustomSkill, loadPublicSkill } from "./api";
import type { Skill } from "./type";

import { loadSkills } from ".";

export function useSkills() {
  const { data, isLoading, error } = useQuery<Skill[]>({
    queryKey: ["skills"],
    queryFn: () => loadSkills(),
  });
  return { skills: data ?? [], isLoading, error };
}

export function useCustomSkill(skillName: string | null) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills", "custom", skillName],
    queryFn: () => loadCustomSkill(skillName!),
    enabled: !!skillName,
  });
  return { skill: data ?? null, isLoading, error };
}

export function usePublicSkill(skillName: string | null) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills", "public", skillName],
    queryFn: () => loadPublicSkill(skillName!),
    enabled: !!skillName,
  });
  return { skill: data ?? null, isLoading, error };
}

export function useEnableSkill() {
  const queryClient = useQueryClient();
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
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}
