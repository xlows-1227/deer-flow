"use client";

import {
  BookOpenIcon,
  CheckCircleIcon,
  Code2Icon,
  SearchIcon,
  SparklesIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useEnableSkill, useSkills } from "@/core/skills/hooks";

const FILTERS = [
  { value: "all", label: "全部" },
  { value: "public", label: "公共" },
  { value: "custom", label: "自定义" },
] as const;

export default function WorkspaceSkillsPage() {
  const { skills, isLoading, error } = useSkills();
  const { mutate: enableSkill, isPending } = useEnableSkill();
  const [query, setQuery] = useState("");
  const [filter, setFilter] =
    useState<(typeof FILTERS)[number]["value"]>("all");

  const filteredSkills = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return skills.filter((skill) => {
      const matchesFilter = filter === "all" || skill.category === filter;
      const matchesQuery =
        !normalizedQuery ||
        skill.name.toLowerCase().includes(normalizedQuery) ||
        skill.description.toLowerCase().includes(normalizedQuery);
      return matchesFilter && matchesQuery;
    });
  }, [filter, query, skills]);

  const enabledCount = skills.filter((skill) => skill.enabled).length;
  const customCount = skills.filter(
    (skill) => skill.category === "custom",
  ).length;

  return (
    <div className="flex size-full flex-col bg-[#fafafa]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-gray-200 bg-white px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Skill 管理</h1>
          <p className="mt-1 text-sm text-gray-500">
            浏览、搜索并启用 DeerFlow 可调用的 Agent Skills。
          </p>
        </div>
        <Button asChild>
          <Link href="/workspace/chats/new?mode=skill">
            <SparklesIcon className="h-4 w-4" />
            新建 Skill
          </Link>
        </Button>
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
                  className="rounded-lg border-gray-200 bg-white shadow-none"
                >
                  <CardHeader className="gap-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <CardTitle className="truncate text-base">
                          {skill.name}
                        </CardTitle>
                        <CardDescription className="mt-2 line-clamp-3">
                          {skill.description}
                        </CardDescription>
                      </div>
                      <Switch
                        checked={skill.enabled}
                        disabled={isPending}
                        onCheckedChange={(enabled) =>
                          enableSkill({ skillName: skill.name, enabled })
                        }
                      />
                    </div>
                  </CardHeader>
                  <CardContent className="flex flex-wrap items-center gap-2">
                    <Badge variant="secondary">{skill.category}</Badge>
                    {skill.license ? (
                      <Badge variant="outline">{skill.license}</Badge>
                    ) : null}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
