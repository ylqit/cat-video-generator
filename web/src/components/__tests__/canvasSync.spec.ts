import { beforeEach, describe, expect, it } from "vitest";

import { ApiError } from "../../api/client";
import { canvasSyncQueue, classifyCanvasSaveError } from "../canvas/canvasSync";

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

  it("classifies layout save failures without labelling every online error as a conflict", () => {
    expect(classifyCanvasSaveError(new ApiError(409, "version conflict"), true)).toBe("conflict");
    expect(classifyCanvasSaveError(new ApiError(412, "version conflict"), true)).toBe("conflict");
    expect(classifyCanvasSaveError(new ApiError(422, "invalid operation"), true)).toBe("service_error");
    expect(classifyCanvasSaveError(new ApiError(500, "server failure"), true)).toBe("service_error");
    expect(classifyCanvasSaveError(new TypeError("Failed to fetch"), false)).toBe("offline");
  });

  it("quarantines a non-replayable 422 operation instead of retrying it forever", () => {
    canvasSyncQueue.enqueue("project-1", { operationId: "op-invalid", type: "move_node" });

    canvasSyncQueue.quarantine("project-1", ["op-invalid"], [{ loc: ["nodes", 0], msg: "invalid" }]);

    expect(canvasSyncQueue.pending("project-1")).toEqual([]);
    expect(canvasSyncQueue.quarantined("project-1")).toEqual([expect.objectContaining({
      operationId: "op-invalid",
      reason: [{ loc: ["nodes", 0], msg: "invalid" }],
    })]);
  });
});
