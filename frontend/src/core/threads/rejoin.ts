type RunLike = {
  run_id: string;
  status: string;
};

/**
 * Pick an in-flight run to re-attach to after an SSE drop.
 * Returns undefined when the user intentionally stopped, so stop cannot race
 * with the auto-rejoin timer and resume a still-running tool call (e.g. SQL).
 */
export function findRunToRejoin<T extends RunLike>(
  runs: T[],
  options: { suppressRejoin: boolean },
): T | undefined {
  if (options.suppressRejoin) {
    return undefined;
  }
  return runs.find(
    (run) => run.status === "pending" || run.status === "running",
  );
}
