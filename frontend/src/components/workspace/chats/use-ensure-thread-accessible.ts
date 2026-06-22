"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { getAPIClient } from "@/core/api";
import { env } from "@/env";

import { useThreadChat } from "./use-thread-chat";

export function useEnsureThreadAccessible(redirectPath: string) {
  const router = useRouter();
  const { threadId, isNewThread, isMock } = useThreadChat();

  useEffect(() => {
    if (
      isNewThread ||
      isMock ||
      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
    ) {
      return;
    }

    let cancelled = false;
    void getAPIClient()
      .threads.get(threadId)
      .catch(() => {
        if (!cancelled) {
          router.replace(redirectPath);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [threadId, isNewThread, isMock, redirectPath, router]);
}
