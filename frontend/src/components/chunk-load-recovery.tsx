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

/**
 * Detect a 409 Conflict thrown by the run-cancel endpoint.
 *
 * The LangGraph React SDK's ``stop()`` calls ``client.runs.cancel()`` as a
 * fire-and-forget promise (no ``await``).  When the run has already finished
 * (status: success/error), the backend responds with 409 "Run ... is not
 * cancellable".  Because the SDK doesn't catch it, the rejection surfaces as
 * an unhandled promise rejection and crashes the page's error overlay in dev.
 * Silently swallowing it is safe — there is nothing to cancel, and the run
 * result is already reflected in the thread state.
 */
function isRunCancelConflict(reason: unknown): boolean {
  if (!reason) return false;
  const status =
    (reason as { status?: number })?.status ??
    (reason as { response?: { status?: number } })?.response?.status;
  if (status === 409) return true;
  if (reason instanceof Error) {
    return (
      reason.message.includes("not cancellable") ||
      reason.message.includes("409")
    );
  }
  if (typeof reason === "string") {
    return reason.includes("not cancellable") || reason.includes("409");
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
      if (isRunCancelConflict(event.reason)) {
        // 409 from the cancel endpoint — nothing to cancel, suppress the
        // unhandled rejection so the dev error overlay doesn't fire.
        event.preventDefault();
        return;
      }
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
