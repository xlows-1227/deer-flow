"use client";

import {
  DatabaseIcon,
  FileSpreadsheetIcon,
  FileTextIcon,
  PlusIcon,
  SearchIcon,
  UploadIcon,
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
import { Input } from "@/components/ui/input";

const collections = [
  {
    name: "项目文档",
    description: "需求、设计文档、会议纪要等长期资料。",
    count: 0,
    status: "待接入",
  },
  {
    name: "数据表格",
    description: "Excel、CSV 等可被 Agent 分析的结构化文件。",
    count: 0,
    status: "可通过聊天上传",
  },
  {
    name: "团队知识",
    description: "常见流程、术语、规范和复用经验。",
    count: 0,
    status: "待接入",
  },
];

export default function WorkspaceKnowledgePage() {
  return (
    <div className="flex size-full flex-col bg-[#fafafa]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-4 border-b border-gray-200 bg-white px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">知识库</h1>
          <p className="mt-1 text-sm text-gray-500">
            参考 Work-Agent 的资料管理入口，先把页面结构接入 DeerFlow 工作区。
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" disabled>
            <UploadIcon className="h-4 w-4" />
            上传文件
          </Button>
          <Button disabled>
            <PlusIcon className="h-4 w-4" />
            新建知识库
          </Button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
          <div className="grid gap-3 sm:grid-cols-3">
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <DatabaseIcon className="h-5 w-5 text-blue-600" />
                <div>
                  <div className="text-2xl font-semibold">3</div>
                  <div className="text-xs text-gray-500">知识分组</div>
                </div>
              </CardContent>
            </Card>
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <FileTextIcon className="h-5 w-5 text-gray-600" />
                <div>
                  <div className="text-2xl font-semibold">0</div>
                  <div className="text-xs text-gray-500">已索引文档</div>
                </div>
              </CardContent>
            </Card>
            <Card className="rounded-lg border-gray-200 bg-white py-4 shadow-none">
              <CardContent className="flex items-center gap-3 px-4">
                <FileSpreadsheetIcon className="h-5 w-5 text-emerald-600" />
                <div>
                  <div className="text-2xl font-semibold">Excel</div>
                  <div className="text-xs text-gray-500">当前推荐任务入口</div>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="relative">
            <SearchIcon className="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <Input
              disabled
              placeholder="搜索知识库、文件名或标签"
              className="h-10 rounded-lg border-gray-200 bg-white pl-9"
            />
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            {collections.map((item) => (
              <Card
                key={item.name}
                className="rounded-lg border-gray-200 bg-white shadow-none"
              >
                <CardHeader className="gap-2">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <CardTitle className="text-base">{item.name}</CardTitle>
                      <CardDescription className="mt-2 line-clamp-2">
                        {item.description}
                      </CardDescription>
                    </div>
                    <Badge variant="outline">{item.status}</Badge>
                  </div>
                </CardHeader>
                <CardContent className="text-sm text-gray-500">
                  {item.count} 个文件
                </CardContent>
              </Card>
            ))}
          </div>

          <Card className="rounded-lg border-dashed border-gray-300 bg-white shadow-none">
            <CardContent className="flex flex-col items-center justify-center px-6 py-12 text-center">
              <UploadIcon className="h-10 w-10 text-gray-300" />
              <p className="mt-4 font-medium text-gray-900">
                DeerFlow 当前还没有 Work-Agent 的知识库后端接口
              </p>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-500">
                现在已经把管理入口和页面结构放进工作区。Excel
                处理可以先在新建对话里上传文件并发起任务，后续接入文件索引接口后，这里可以直接承载上传、检索和知识库选择。
              </p>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}
