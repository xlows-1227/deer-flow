"use client";

import { useQuery } from "@tanstack/react-query";
import { FileX2Icon, LoaderCircleIcon } from "lucide-react";
import { useParams } from "next/navigation";

import { SHARED_HTML_IFRAME_SANDBOX, loadPublishedHtml } from "@/core/files";

export default function PublishedHtmlPage() {
  const { token } = useParams<{ token: string }>();
  const publication = useQuery({
    queryKey: ["files", "published-html", token],
    queryFn: () => loadPublishedHtml(token),
    retry: false,
    refetchOnWindowFocus: false,
  });

  if (publication.isLoading) {
    return (
      <main className="flex h-screen items-center justify-center bg-white text-gray-500">
        <div className="flex items-center gap-2 text-sm">
          <LoaderCircleIcon className="size-4 animate-spin" />
          正在加载发布页面…
        </div>
      </main>
    );
  }

  if (publication.error || !publication.data) {
    return (
      <main className="flex h-screen items-center justify-center bg-gray-50 px-6">
        <div className="max-w-sm text-center">
          <FileX2Icon className="mx-auto mb-4 size-10 text-gray-400" />
          <h1 className="text-base font-semibold text-gray-900">
            发布页面不存在或已停止发布
          </h1>
          <p className="mt-2 text-sm text-gray-500">
            请检查链接是否完整，或联系发布者确认页面状态。
          </p>
        </div>
      </main>
    );
  }

  return (
    <iframe
      className="block h-screen w-full border-0 bg-white"
      title={publication.data.name}
      sandbox={SHARED_HTML_IFRAME_SANDBOX}
      referrerPolicy="no-referrer"
      srcDoc={publication.data.html}
    />
  );
}
