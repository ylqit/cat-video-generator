import { beforeEach, describe, expect, it } from "vitest";

import { canvasSyncQueue } from "../canvas/canvasSync";

describe("canvasSyncQueue", () => {
  beforeEach(() => localStorage.clear());

  it("persists unsent layout operations and clears only confirmed operations", () => {
    canvasSyncQueue.enqueue("project-1", { operationId: "op-1", type: "move_node" });
    canvasSyncQueue.enqueue("project-1", { operationId: "op-2", type: "viewport" });

    expect(canvasSyncQueue.pending("project-1").map((item) => item.operationId)).toEqual([
      "op-1",
      "op-2",
    ]);
    canvasSyncQueue.confirm("project-1", ["op-1"]);

    expect(canvasSyncQueue.pending("project-1")).toEqual([
      { operationId: "op-2", type: "viewport" },
    ]);
  });
});
