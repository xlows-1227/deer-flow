"use client";

import { useEffect } from "react";

const CHUNK_RELOAD_KEY = "deer-flow-chunk-reload";

function isChunkLoadFailure(reason: unknown): boolean {
  if (!reason) return false;
  if (reason instanceof Error) {
    return (
      reason.name === "ChunkLoadError" ||
      reason.message.includes("Loading chunk")
    );
  }
  if (typeof reason === "string") {
    return reason.includes("Loading chunk");
  }
  return false;
}

function retryOnceOnChunkFailure() {
  if (sessionStorage.getItem(CHUNK_RELOAD_KEY)) {
    sessionStorage.removeItem(CHUNK_RELOAD_KEY);
    return;
  }
  sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
  window.location.reload();
}

export function ChunkLoadRecovery() {
  useEffect(() => {
    const handleError = (event: ErrorEvent) => {
      if (!isChunkLoadFailure(event.error ?? event.message)) return;
      event.preventDefault();
      retryOnceOnChunkFailure();
    };

    const handleRejection = (event: PromiseRejectionEvent) => {
      if (!isChunkLoadFailure(event.reason)) return;
      event.preventDefault();
      retryOnceOnChunkFailure();
    };

    window.addEventListener("error", handleError);
    window.addEventListener("unhandledrejection", handleRejection);
    return () => {
      window.removeEventListener("error", handleError);
      window.removeEventListener("unhandledrejection", handleRejection);
    };
  }, []);

  useEffect(() => {
    sessionStorage.removeItem(CHUNK_RELOAD_KEY);
  }, []);

  return null;
}
