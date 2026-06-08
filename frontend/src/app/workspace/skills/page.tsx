"use client";

import {
  BookOpenIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  Code2Icon,
  Edit3Icon,
  Loader2Icon,
  MoreHorizontalIcon,
  PlusIcon,
  SearchIcon,
  SparklesIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SkillDetailDialog } from "@/components/workspace/skills/skill-detail-dialog";
import {
  useDeleteCustomSkill,
  useEnableSkill,
  useSkills,
} from "@/core/skills/hooks";
import type { Skill } from "@/core/skills/type";

const FILTERS = [
  { value: "all", label: "全部" },
  { value: "public", label: "公共" },
  { value: "custom", label: "自定义" },
] as const;

export default function WorkspaceSkillsPage() {
  const { skills, isLoading, error } = useSkills();
  const { mutate: enableSkill, isPending } = useEnableSkill();
  const { mutateAsync: deleteCustomSkill, isPending: isDeleting } =
    useDeleteCustomSkill();
  const [query, setQuery] = useState("");
  const [filter, setFilter] =
    useState<(typeof FILTERS)[number]["value"]>("all");
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);

  const filteredSkills = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return skills.filter((skill) => {
      const matchesFilter = filter === "all" || skill.category === filter;
      const matchesQuery =
        !normalizedQuery ||
        (skill.display_name ?? skill.name)
          .toLowerCase()
          .includes(normalizedQuery) ||
        skill.name.toLowerCase().includes(normalizedQuery) ||
        (skill.description_zh ?? skill.description)
          .toLowerCase()
          .includes(normalizedQuery);
      return matchesFilter && matchesQuery;
    });
  }, [filter, query, skills]);

  const enabledCount = skills.filter((skill) => skill.enabled).length;
  const customCount = skills.filter(
    (skill) => skill.category === "custom",
  ).length;

  async function handleDeleteSkill(skill: Skill) {
    if (skill.category !== "custom") return;
    if (
      !window.confirm(
        `确定删除自定义 Skill「${skill.display_name ?? skill.name}」吗？后端会保留历史记录，但当前 Skill 文件会被移除。`,
      )
    ) {
      return;
    }
    try {
      await deleteCustomSkill(skill.name);
      toast.success("Skill 已删除");
      if (selectedSkill?.name === skill.name) {
        setSelectedSkill(null);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除 Skill 失败");
    }
  }

  return (
    <div className="flex size-full flex-col bg-[#fafafa]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-gray-200 bg-white px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Skill 管理</h1>
          <p className="mt-1 text-sm text-gray-500">
            浏览、搜索并启用 DeerFlow 可调用的 Agent Skills。
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button>
              <PlusIcon className="h-4 w-4" />
              新建 Skill
              <ChevronDownIcon className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem asChild>
              <Link href="/workspace/skills/create">
                <PlusIcon className="h-4 w-4" />
                快速
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/workspace/skills/ai-create/new">
                <SparklesIcon className="h-4 w-4" />
                Chat 创建
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/workspace/skills/upload">
                <UploadIcon className="h-4 w-4" />
                上传 ZIP
              </Link>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
          <div className="grid gap-3 sm:grid-cols-3">
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <BookOpenIcon className="h-5 w-5 text-blue-600" />
                <div>
                  <div className="text-2xl font-semibold">{skills.length}</div>
                  <div className="text-xs text-gray-500">总 Skill</div>
                </div>
              </CardContent>
            </Card>
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <CheckCircleIcon className="h-5 w-5 text-emerald-600" />
                <div>
                  <div className="text-2xl font-semibold">{enabledCount}</div>
                  <div className="text-xs text-gray-500">已启用</div>
                </div>
              </CardContent>
            </Card>
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <Code2Icon className="h-5 w-5 text-gray-600" />
                <div>
                  <div className="text-2xl font-semibold">{customCount}</div>
                  <div className="text-xs text-gray-500">自定义</div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Tabs
              value={filter}
              onValueChange={(value) =>
                setFilter(value as (typeof FILTERS)[number]["value"])
              }
            >
              <TabsList className="rounded-lg bg-gray-100">
                {FILTERS.map((item) => (
                  <TabsTrigger key={item.value} value={item.value}>
                    {item.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <div className="relative ml-auto w-full sm:w-80">
              <SearchIcon className="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索 Skill 名称或说明"
                className="h-10 rounded-lg border-gray-200 bg-white pl-9"
              />
            </div>
          </div>

          {isLoading ? (
            <div className="flex h-40 items-center justify-center text-sm text-gray-500">
              加载 Skill 中...
            </div>
          ) : error ? (
            <Card className="rounded-lg border-red-200 bg-white p-6 text-sm text-red-600 shadow-none">
              加载失败：{error.message}
            </Card>
          ) : filteredSkills.length === 0 ? (
            <Card className="rounded-lg border-gray-200 bg-white p-10 text-center shadow-none">
              <p className="font-medium text-gray-900">没有找到匹配的 Skill</p>
              <p className="mt-1 text-sm text-gray-500">
                调整搜索条件，或从聊天页创建一个自定义 Skill。
              </p>
            </Card>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {filteredSkills.map((skill) => (
                <Card
                  key={skill.name}
                  role="button"
                  tabIndex={0}
                  className="flex h-full cursor-pointer flex-col rounded-lg border-gray-200 bg-white shadow-none transition-colors hover:border-gray-300 hover:bg-gray-50/70"
                  onClick={() => setSelectedSkill(skill)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedSkill(skill);
                    }
                  }}
                >
                  <CardHeader className="flex-1 gap-3">
                    <CardTitle className="min-w-0 truncate pr-2 text-base">
                      {skill.display_name ?? skill.name}
                    </CardTitle>
                    <CardDescription className="line-clamp-3 min-w-0">
                      {skill.description}
                    </CardDescription>
                    <CardAction>
                      <div onClick={(event) => event.stopPropagation()}>
                        <Switch
                          checked={skill.enabled}
                          disabled={isPending}
                          onCheckedChange={(enabled) =>
                            enableSkill({ skillName: skill.name, enabled })
                          }
                        />
                      </div>
                    </CardAction>
                  </CardHeader>
                  <CardFooter className="mt-auto flex items-center justify-between gap-2 pt-0">
                    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                      <Badge variant="secondary">{skill.category}</Badge>
                      {skill.license ? (
                        <Badge variant="outline">{skill.license}</Badge>
                      ) : null}
                    </div>
                    {skill.category === "custom" ? (
                      <div onClick={(event) => event.stopPropagation()}>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              className="size-8 text-gray-500 hover:bg-white hover:text-gray-900"
                              aria-label={`${skill.display_name ?? skill.name} 操作`}
                            >
                              {isDeleting ? (
                                <Loader2Icon className="size-4 animate-spin" />
                              ) : (
                                <MoreHorizontalIcon className="size-4" />
                              )}
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-36">
                            <DropdownMenuItem asChild>
                              <Link
                                href={`/workspace/skills/editor/${encodeURIComponent(skill.name)}`}
                              >
                                <Edit3Icon className="size-4" />
                                编辑
                              </Link>
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              variant="destructive"
                              disabled={isDeleting}
                              onClick={() => void handleDeleteSkill(skill)}
                            >
                              <Trash2Icon className="size-4" />
                              删除
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    ) : null}
                  </CardFooter>
                </Card>
              ))}
            </div>
          )}
        </div>
      </main>

      <SkillDetailDialog
        skill={selectedSkill}
        onClose={() => setSelectedSkill(null)}
      />
    </div>
  );
}
