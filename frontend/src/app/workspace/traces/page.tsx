"use client";

import {
  ActivityIcon,
  CheckCircleIcon,
  ClockIcon,
  ExternalLinkIcon,
  ServerIcon,
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

const traceItems = [
  {
    title: "Frontend",
    description: "Next.js 页面、聊天交互和工具配置请求。",
    status: "已运行",
  },
  {
    title: "Gateway",
    description: "8001 端口代理 API 请求，负责连接后端工作流。",
    status: "本地服务",
  },
  {
    title: "LangGraph",
    description: "Agent 执行链路、工具调用和流式消息。",
    status: "后端链路",
  },
];

export default function WorkspaceTracesPage() {
  return (
    <div className="flex size-full flex-col bg-[#fafafa]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-gray-200 bg-white px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">日志链路</h1>
          <p className="mt-1 text-sm text-gray-500">
            按 Work-Agent 的入口方式聚合本地服务、Agent 执行和观测配置。
          </p>
        </div>
        <Button variant="outline" disabled>
          <ExternalLinkIcon className="h-4 w-4" />
          打开观测平台
        </Button>
      </header>

      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
          <div className="grid gap-3 sm:grid-cols-3">
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <ActivityIcon className="h-5 w-5 text-blue-600" />
                <div>
                  <div className="text-2xl font-semibold">3</div>
                  <div className="text-xs text-gray-500">链路节点</div>
                </div>
              </CardContent>
            </Card>
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <CheckCircleIcon className="h-5 w-5 text-emerald-600" />
                <div>
                  <div className="text-2xl font-semibold">Ready</div>
                  <div className="text-xs text-gray-500">页面入口</div>
                </div>
              </CardContent>
            </Card>
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <ClockIcon className="h-5 w-5 text-amber-600" />
                <div>
                  <div className="text-2xl font-semibold">Live</div>
                  <div className="text-xs text-gray-500">本地运行态</div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            {traceItems.map((item) => (
              <Card
                key={item.title}
                className="rounded-lg border-gray-200 bg-white shadow-none"
              >
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <CardTitle className="text-base">{item.title}</CardTitle>
                      <CardDescription className="mt-2">
                        {item.description}
                      </CardDescription>
                    </div>
                    <Badge variant="secondary">{item.status}</Badge>
                  </div>
                </CardHeader>
              </Card>
            ))}
          </div>

          <Card className="rounded-lg border-gray-200 bg-white shadow-none">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ServerIcon className="h-4 w-4 text-gray-500" />
                本地排查建议
              </CardTitle>
              <CardDescription>
                这个页面先承载 Work-Agent 的日志链路入口。实际 trace
                数据要等后端接入 LangSmith、Langfuse
                或自定义日志查询接口后展示。
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 text-sm text-gray-600">
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3">
                Gateway 端口：8001
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3">
                Frontend 端口：3000
              </div>
              <div className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3">
                Nginx 可选入口：2026，Windows 本地可以使用 --no-nginx 跳过。
              </div>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}
