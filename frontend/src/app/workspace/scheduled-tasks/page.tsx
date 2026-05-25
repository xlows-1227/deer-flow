"use client";

import {
  CalendarClockIcon,
  CheckCircleIcon,
  ClockIcon,
  PlusIcon,
  RotateCwIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const taskTemplates = [
  {
    title: "每日数据巡检",
    description: "按固定时间读取表格、生成摘要并输出异常项。",
    status: "模板",
  },
  {
    title: "周报生成",
    description: "汇总聊天、文件和任务结果，生成结构化周报。",
    status: "模板",
  },
  {
    title: "定时提醒",
    description: "在指定时间唤起 Agent，继续某个业务流程。",
    status: "模板",
  },
];

export default function WorkspaceScheduledTasksPage() {
  return (
    <div className="flex size-full flex-col bg-[#fafafa]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-gray-200 bg-white px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">定时任务</h1>
          <p className="mt-1 text-sm text-gray-500">
            参考 Work-Agent
            的定时任务入口，展示任务模板、执行状态和后续接入位置。
          </p>
        </div>
        <Button disabled>
          <PlusIcon className="h-4 w-4" />
          新建任务
        </Button>
      </header>

      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
          <div className="grid gap-3 sm:grid-cols-3">
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <CalendarClockIcon className="h-5 w-5 text-blue-600" />
                <div>
                  <div className="text-2xl font-semibold">0</div>
                  <div className="text-xs text-gray-500">运行中任务</div>
                </div>
              </CardContent>
            </Card>
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <CheckCircleIcon className="h-5 w-5 text-emerald-600" />
                <div>
                  <div className="text-2xl font-semibold">0</div>
                  <div className="text-xs text-gray-500">今日完成</div>
                </div>
              </CardContent>
            </Card>
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <RotateCwIcon className="h-5 w-5 text-amber-600" />
                <div>
                  <div className="text-2xl font-semibold">3</div>
                  <div className="text-xs text-gray-500">可用模板</div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            {taskTemplates.map((item) => (
              <Card
                key={item.title}
                className="rounded-lg border-gray-200 bg-white shadow-none"
              >
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <CardTitle className="text-base">{item.title}</CardTitle>
                      <CardDescription className="mt-2 line-clamp-2">
                        {item.description}
                      </CardDescription>
                    </div>
                    <Badge variant="outline">{item.status}</Badge>
                  </div>
                </CardHeader>
              </Card>
            ))}
          </div>

          <Card className="rounded-lg border-dashed border-gray-300 bg-white shadow-none">
            <CardContent className="flex flex-col items-center justify-center px-6 py-12 text-center">
              <ClockIcon className="h-10 w-10 text-gray-300" />
              <p className="mt-4 font-medium text-gray-900">
                DeerFlow 当前还没有 Work-Agent 的任务调度接口
              </p>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-500">
                页面入口已接好。真正创建、暂停、重跑和查看执行记录，需要后端提供
                scheduler API；在此之前可以用 Codex
                自动化或系统计划任务触发本地脚本。
              </p>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}
