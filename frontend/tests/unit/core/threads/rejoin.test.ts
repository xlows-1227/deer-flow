import { expect, test } from "vitest";

import { findRunToRejoin } from "@/core/threads/rejoin";

test("selects the first pending or running run when rejoin is allowed", () => {
  expect(
    findRunToRejoin(
      [
        { run_id: "done", status: "success" },
        { run_id: "active", status: "running" },
        { run_id: "queued", status: "pending" },
      ],
      { suppressRejoin: false },
    ),
  ).toEqual({ run_id: "active", status: "running" });
});

test("returns undefined when the user intentionally stopped", () => {
  expect(
    findRunToRejoin([{ run_id: "active", status: "running" }], {
      suppressRejoin: true,
    }),
  ).toBeUndefined();
});

test("returns undefined when no run is still active", () => {
  expect(
    findRunToRejoin(
      [
        { run_id: "done", status: "success" },
        { run_id: "stopped", status: "interrupted" },
      ],
      { suppressRejoin: false },
    ),
  ).toBeUndefined();
});
