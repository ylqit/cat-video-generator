import { describe, expect, it } from "vitest";

import {
  escapeInteraction,
  selectedInteraction,
  selectedNodeId,
  consoleKind,
} from "../canvas/canvasInteraction";

describe("canvasInteraction", () => {
  it("selects video and normal nodes into mutually exclusive local consoles", () => {
    const video = selectedInteraction({
      id: "video-1",
      type: "VideoAssetNode",
      objectType: "asset",
      objectId: "asset-1",
      position: { x: 0, y: 0 },
      data: {},
    });
    const reference = selectedInteraction({
      id: "reference-1",
      type: "ReferenceAssetNode",
      objectType: "asset",
      objectId: "asset-2",
      position: { x: 0, y: 0 },
      data: {},
    });

    expect(video).toEqual({ mode: "video_selected", nodeId: "video-1" });
    expect(consoleKind(video)).toBe("video");
    expect(reference).toEqual({ mode: "node_selected", nodeId: "reference-1" });
    expect(consoleKind(reference)).toBe("node");
  });

  it("escapes reference picking, segment reshoot and selection in semantic order", () => {
    const picking = {
      mode: "reference_picking" as const,
      nodeId: "video-1",
      returnMode: "video_segment_reshoot" as const,
      snapshotNodeIds: ["reference-1"],
    };
    const segment = escapeInteraction(picking);
    const selected = escapeInteraction(segment);
    const idle = escapeInteraction(selected);

    expect(segment).toEqual({ mode: "video_segment_reshoot", nodeId: "video-1" });
    expect(selected).toEqual({ mode: "video_selected", nodeId: "video-1" });
    expect(idle).toEqual({ mode: "idle" });
    expect(selectedNodeId(idle)).toBeNull();
  });
});
