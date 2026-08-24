"use client";

import { ChevronLeftIcon, ChevronRightIcon, Loader2Icon, Users2Icon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { useAllUsers, useSkillShares, useUpdateSkillShares } from "@/core/skills/hooks";
import type { SkillSharedUser, UserInfo } from "@/core/skills/type";
import { cn } from "@/lib/utils";

type ShareUserRow = SkillSharedUser & { selected?: boolean };

function useSortedShareLists(args: {
  skillName: string | null;
  ownerUserId: string | null;
  allUsers: UserInfo[];
  shareStateSharees: SkillSharedUser[] | null;
}) {
  const { skillName, ownerUserId, allUsers, shareStateSharees } = args;
  return useMemo(() => {
    if (!skillName) return { candidates: [] as ShareUserRow[], sharees: [] as ShareUserRow[] };

    const sharedById = new Map<string, SkillSharedUser>();
    (shareStateSharees ?? []).forEach((s) => sharedById.set(s.id.toLowerCase(), s));

    const candidates: ShareUserRow[] = [];
    const sharees: ShareUserRow[] = [];
    for (const u of allUsers) {
      if (ownerUserId && u.id === ownerUserId) continue;

      const shared = sharedById.get(u.id.toLowerCase());
      const row: ShareUserRow = {
        id: u.id,
        email: u.email,
        system_role: u.system_role,
      };
      if (shared) {
        sharees.push(row);
      } else {
        candidates.push(row);
      }
    }
    console.log("[SkillShareDialog] useSortedShareLists:", {
      skillName,
      ownerUserId,
      allUsersCount: allUsers.length,
      shareStateShareesCount: shareStateSharees?.length ?? 0,
      shareStateShareesIds: shareStateSharees?.map((s) => s.id) ?? [],
      allUsersIds: allUsers.map((u) => u.id),
      candidatesCount: candidates.length,
      shareesCount: sharees.length,
      candidateEmails: candidates.map((c) => c.email),
      shareeEmails: sharees.map((s) => s.email),
    });
    const byEmail = (a: { email: string }, b: { email: string }) =>
      a.email.localeCompare(b.email);
    candidates.sort(byEmail);
    sharees.sort(byEmail);
    return { candidates, sharees };
  }, [allUsers, ownerUserId, shareStateSharees, skillName]);
}

export function SkillShareDialog({
  skillName,
  skillDisplayName,
  ownerUserId,
  open,
  onClose,
}: {
  skillName: string | null;
  skillDisplayName: string | null;
  ownerUserId: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const { users: allUsers, isLoading: loadingUsers } = useAllUsers();
  const { shares, isLoading: loadingShares, error: sharesError } = useSkillShares(open ? skillName : null);
  const updateShares = useUpdateSkillShares();

  useEffect(() => {
    if (open) {
      console.log("[SkillShareDialog] state dump:", {
        skillName,
        skillNameLower: skillName?.toLowerCase(),
        ownerUserId,
        ownerUserIdLower: ownerUserId?.toLowerCase(),
        shares: shares ? {
          skill_name: shares.skill_name,
          owner_user_id: shares.owner_user_id,
          owner_email: shares.owner_email,
          count: shares.sharees.length,
          sharees: shares.sharees.map((s) => ({ id: s.id, email: s.email })),
        } : null,
        allUsersCount: allUsers.length,
        allUsers: allUsers.map((u) => ({ id: u.id, email: u.email })),
        error: sharesError?.message ?? null,
        loading: { loadingUsers, loadingShares },
      });
    }
  }, [open, shares, allUsers, sharesError, skillName, ownerUserId, loadingUsers, loadingShares]);

  const initialShareeIds = useMemo(
    () => new Set<string>(shares?.sharees?.map((s) => s.id.toLowerCase()) ?? []),
    [shares],
  );

  // Local working set — only persisted on "确认".  Stored as a Set for easy
  // right-list membership testing; the UI reads a derived sorted list.
  const [workingIds, setWorkingIds] = useState<Set<string>>(new Set());
  const [candidateFilter, setCandidateFilter] = useState("");
  const [shareeFilter, setShareeFilter] = useState("");
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<Set<string>>(new Set());
  const [selectedShareeIds, setSelectedShareeIds] = useState<Set<string>>(new Set());

  // Seed the working copy when the dialog opens *or* when the server payload
  // arrives for the first time (avoids flashing stale state while the share
  // list query is in flight).
  useEffect(() => {
    if (!open) return;
    if (!initialShareeIds.size && !shares) return;
    // Use the original-case IDs from shares for the working set
    const originalIds = new Set<string>(
      shares?.sharees?.map((s) => s.id) ?? []
    );
    setWorkingIds(originalIds);
    setSelectedCandidateIds(new Set());
    setSelectedShareeIds(new Set());
    setCandidateFilter("");
    setShareeFilter("");
  }, [open, initialShareeIds, shares]);

  const { candidates, sharees } = useSortedShareLists({
    skillName,
    ownerUserId,
    allUsers,
    shareStateSharees: shares?.sharees ?? null,
  });

  // Re-split users through the working set instead of server state so that
  // the middle-column buttons reflect local changes immediately.
  const { leftList, rightList } = useMemo(() => {
    const left: ShareUserRow[] = [];
    const right: ShareUserRow[] = [];
    for (const c of candidates) {
      if (workingIds.has(c.id)) continue;
      if (candidateFilter && !c.email.toLowerCase().includes(candidateFilter.toLowerCase())) {
        continue;
      }
      left.push({ ...c, selected: selectedCandidateIds.has(c.id) });
    }
    for (const row of [...candidates, ...sharees]) {
      if (!workingIds.has(row.id)) continue;
      if (shareeFilter && !row.email.toLowerCase().includes(shareeFilter.toLowerCase())) {
        continue;
      }
      right.push({ ...row, selected: selectedShareeIds.has(row.id) });
    }
    const byEmail = (a: { email: string }, b: { email: string }) =>
      a.email.localeCompare(b.email);
    left.sort(byEmail);
    right.sort(byEmail);
    return { leftList: left, rightList: right };
  }, [
    candidates,
    sharees,
    workingIds,
    candidateFilter,
    shareeFilter,
    selectedCandidateIds,
    selectedShareeIds,
  ]);

  const moveSelectedToSharees = () => {
    if (!selectedCandidateIds.size) return;
    setWorkingIds((prev) => {
      const next = new Set(prev);
      selectedCandidateIds.forEach((id) => next.add(id));
      return next;
    });
    setSelectedCandidateIds(new Set());
  };

  const moveSelectedToCandidates = () => {
    if (!selectedShareeIds.size) return;
    setWorkingIds((prev) => {
      const next = new Set(prev);
      selectedShareeIds.forEach((id) => next.delete(id));
      return next;
    });
    setSelectedShareeIds(new Set());
  };

  const dirty =
    workingIds.size !== initialShareeIds.size ||
    [...workingIds].some((id) => !initialShareeIds.has(id));

  async function handleConfirm() {
    if (!skillName) return;
    try {
      await updateShares.mutateAsync({
        skillName,
        sharedWithUserIds: [...workingIds],
      });
      toast.success("共享列表已保存");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    }
  }

  const loading = loadingUsers || loadingShares;
  const title = `管理共享 — ${skillDisplayName ?? skillName ?? "自定义 Skill"}`;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="flex h-[82vh] max-h-[900px] w-[calc(100vw-2rem)] max-w-none flex-col overflow-hidden p-0 sm:max-w-4xl">
        <DialogHeader className="shrink-0 border-b border-gray-100 px-5 py-3.5 sm:px-6">
          <DialogTitle className="flex items-center gap-2 text-base font-semibold">
            <Users2Icon className="size-4 text-gray-500" />
            {title}
          </DialogTitle>
          <DialogDescription className="sr-only">
            将系统中的用户添加为共享接收者，或从共享列表中移除已添加的用户。
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 p-4 sm:p-6">
          {sharesError ? (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              加载共享列表失败：{sharesError.message}
            </div>
          ) : null}
          <div className="grid min-h-0 flex-1 grid-cols-1 items-stretch gap-3 md:grid-cols-[1fr_auto_1fr]">
            {/* Left column: candidates */}
            <div className="flex min-h-[260px] min-w-0 flex-col rounded-lg border border-gray-200 bg-white">
              <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                  可共享用户
                </h3>
                <Badge variant="secondary" className="text-[10px]">
                  {leftList.length}
                </Badge>
              </div>
              <div className="border-b border-gray-100 p-2">
                <Input
                  className="h-8"
                  placeholder="搜索邮箱..."
                  value={candidateFilter}
                  onChange={(e) => setCandidateFilter(e.target.value)}
                />
              </div>
              <ScrollArea className="min-h-0 flex-1">
                {loading ? (
                  <div className="flex h-40 items-center justify-center text-sm text-gray-400">
                    <Loader2Icon className="mr-2 size-4 animate-spin" />
                    加载用户列表...
                  </div>
                ) : !leftList.length ? (
                  <div className="flex h-40 items-center justify-center text-sm text-gray-400">
                    没有可共享的用户
                  </div>
                ) : (
                  <ul className="divide-y divide-gray-50 p-1">
                    {leftList.map((u) => (
                      <li key={u.id}>
                        <label
                          className={cn(
                            "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                            u.selected ? "bg-blue-50" : "hover:bg-gray-50",
                          )}
                        >
                          <input
                            type="checkbox"
                            className="size-3.5 accent-blue-600"
                            checked={!!u.selected}
                            onChange={(e) => {
                              setSelectedCandidateIds((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) next.add(u.id);
                                else next.delete(u.id);
                                return next;
                              });
                            }}
                          />
                          <span className="min-w-0 flex-1 truncate text-gray-800">
                            {u.email}
                          </span>
                          {u.system_role === "admin" ? (
                            <Badge variant="outline" className="text-[10px]">
                              admin
                            </Badge>
                          ) : null}
                        </label>
                      </li>
                    ))}
                  </ul>
                )}
              </ScrollArea>
            </div>

            {/* Middle column: add / remove buttons */}
            <div className="flex items-center justify-center gap-2 md:flex-col">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={moveSelectedToSharees}
                disabled={!selectedCandidateIds.size || loading}
                title="把选中的用户加入共享列表"
                className="h-9 min-w-0 px-3 md:h-10 md:px-3"
              >
                <ChevronRightIcon className="size-4 md:mr-0" />
                <span className="md:hidden">共享</span>
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={moveSelectedToCandidates}
                disabled={!selectedShareeIds.size || loading}
                title="把选中的用户移出共享列表"
                className="h-9 min-w-0 px-3 md:h-10 md:px-3"
              >
                <ChevronLeftIcon className="size-4 md:mr-0" />
                <span className="md:hidden">取消</span>
              </Button>
            </div>

            {/* Right column: sharees */}
            <div className="flex min-h-[260px] min-w-0 flex-col rounded-lg border border-gray-200 bg-white">
              <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">
                  已共享用户
                </h3>
                <Badge variant="secondary" className="text-[10px]">
                  {rightList.length}
                </Badge>
              </div>
              <div className="border-b border-gray-100 p-2">
                <Input
                  className="h-8"
                  placeholder="搜索邮箱..."
                  value={shareeFilter}
                  onChange={(e) => setShareeFilter(e.target.value)}
                />
              </div>
              <ScrollArea className="min-h-0 flex-1">
                {!rightList.length ? (
                  <div className="flex h-40 items-center justify-center text-sm text-gray-400">
                    尚未共享给任何用户
                  </div>
                ) : (
                  <ul className="divide-y divide-gray-50 p-1">
                    {rightList.map((u) => (
                      <li key={u.id}>
                        <label
                          className={cn(
                            "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                            u.selected ? "bg-blue-50" : "hover:bg-gray-50",
                          )}
                        >
                          <input
                            type="checkbox"
                            className="size-3.5 accent-blue-600"
                            checked={!!u.selected}
                            onChange={(e) => {
                              setSelectedShareeIds((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) next.add(u.id);
                                else next.delete(u.id);
                                return next;
                              });
                            }}
                          />
                          <span className="min-w-0 flex-1 truncate text-gray-800">
                            {u.email}
                          </span>
                          {u.system_role === "admin" ? (
                            <Badge variant="outline" className="text-[10px]">
                              admin
                            </Badge>
                          ) : null}
                        </label>
                      </li>
                    ))}
                  </ul>
                )}
              </ScrollArea>
            </div>
          </div>

          <div className="text-xs text-gray-400">
            提示：技能创建者不会出现在任何一侧（创建者已有全部访问权限）。共享后的用户可以查看该 Skill，但无法进行编辑。
          </div>
        </div>

        <DialogFooter className="shrink-0 border-t border-gray-100 px-5 py-3 sm:px-6">
          <Button
            type="button"
            variant="outline"
            onClick={onClose}
            disabled={updateShares.isPending}
          >
            取消
          </Button>
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={updateShares.isPending || loading}
          >
            {updateShares.isPending ? (
              <>
                <Loader2Icon className="mr-2 size-4 animate-spin" />
                保存中...
              </>
            ) : (
              "确认"
            )}
            {dirty && !updateShares.isPending ? (
              <Badge variant="secondary" className="ml-2 text-[10px]">
                未保存
              </Badge>
            ) : null}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
