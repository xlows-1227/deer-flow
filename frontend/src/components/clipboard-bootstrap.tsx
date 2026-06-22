"use client";

import { ensureClipboardApi } from "@/lib/clipboard";

if (typeof window !== "undefined") {
  ensureClipboardApi();
}

export function ClipboardBootstrap() {
  return null;
}
