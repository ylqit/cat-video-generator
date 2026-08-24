import { describe, expect, it } from "vitest";

import type { CanvasEdgeDto, CanvasNodeDto } from "../../api/types";
import { planReferenceEdgeChanges, referenceConnection } from "../canvas/canvasSelection";

const node = (id: string, type: CanvasNodeDto["type"]): CanvasNodeDto => ({
  id,
  type,
  objectType: type,
  objectId: null,
  position: { x: 0, y: 0 },
  data: {},
});

describe("canvas reference selection", () => {
  it("maps subject and media references to valid target-specific ports", () => {
    expect(referenceConnection(node("subject", "SubjectNode"), node("batch", "GenerationBatchNode"))).toMatchObject({
      sourcePort: "product_subject",
      targetPort: "product_subject",
    });
    expect(referenceConnection(node("reference", "ReferenceAssetNode"), node("video", "VideoGenerationNode"))).toMatchObject({
      sourcePort: "media_reference[]",
      targetPort: "media_reference[]",
    });
    expect(referenceConnection(node("image", "ImageAssetNode"), node("video", "VideoGenerationNode"))).toMatchObject({
      sourcePort: "image_asset",
      targetPort: "image_reference[]",
    });
    expect(
      referenceConnection(node("character", "CharacterDesignNode"), node("storyboard", "StoryboardDirectorNode")),
    ).toMatchObject({
      sourcePort: "character_design",
      targetPort: "character_design",
    });
    expect(referenceConnection(node("timeline", "TimelineNode"), node("video", "VideoGenerationNode"))).toBeNull();
  });

  it("diffs desired references without touching unrelated domain edges", () => {
    const edges: CanvasEdgeDto[] = [
      {
        id: "edge-remove",
        sourceNodeId: "reference-old",
        sourceNodeType: "ReferenceAssetNode",
        sourcePort: "media_reference[]",
        targetNodeId: "video",
        targetNodeType: "VideoGenerationNode",
        targetPort: "media_reference[]",
      },
      {
        id: "edge-unrelated",
        sourceNodeId: "brief",
        sourceNodeType: "BriefNode",
        sourcePort: "brief",
        targetNodeId: "planner",
        targetNodeType: "StoryPlannerNode",
        targetPort: "brief",
      },
    ];
    const target = node("video", "VideoGenerationNode");
    const selected = [node("reference-new", "ReferenceAssetNode")];

    const changes = planReferenceEdgeChanges(edges, target, selected);

    expect(changes.deleteEdgeIds).toEqual(["edge-remove"]);
    expect(changes.createEdges).toHaveLength(1);
    expect(changes.createEdges[0]).toMatchObject({
      sourceNodeId: "reference-new",
      targetNodeId: "video",
    });
  });
});
