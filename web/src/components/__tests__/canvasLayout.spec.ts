import type { Node } from "@vue-flow/core";
import { describe, expect, it } from "vitest";

import type { CanvasNodeDto } from "../../api/types";
import {
  arrangeCanvasNodes,
  canvasNodeFootprint,
  renderedCanvasNodeFootprint,
} from "../canvas/canvasLayout";

function flowNode(
  id: string,
  node: Partial<CanvasNodeDto> & Pick<CanvasNodeDto, "type" | "layoutHint">,
  dimensions?: { width: number; height: number },
): Node {
  return {
    id,
    type: "canvas",
    position: { x: 0, y: 0 },
    data: {
      node: {
        id,
        objectType: "test",
        objectId: id,
        position: { x: 0, y: 0 },
        data: {},
        ...node,
      },
    },
    ...(dimensions ? { dimensions } : {}),
  } as Node;
}

describe("semantic canvas layout", () => {
  it("keeps child, cat and style in the canonical lane using backend order", () => {
    const style = flowNode("style", {
      type: "StylePresetNode",
      layoutHint: { lane: "canon", laneOrder: 0, itemOrder: 2 },
    });
    const cat = flowNode("cat", {
      type: "SubjectNode",
      layoutHint: { lane: "canon", laneOrder: 0, itemOrder: 1 },
    });
    const child = flowNode("child", {
      type: "SubjectNode",
      layoutHint: { lane: "canon", laneOrder: 0, itemOrder: 0 },
    });

    const arranged = arrangeCanvasNodes([style, cat, child]);

    expect(arranged.map((node) => node.id)).toEqual(["child", "cat", "style"]);
    expect(new Set(arranged.map((node) => node.position.x))).toEqual(new Set([90]));
    expect(arranged.map((node) => node.position.y)).toEqual([100, 424, 748]);
  });

  it("uses measured footprints for row spacing and the next lane offset", () => {
    const tallCanon = flowNode("canon", {
      type: "SubjectNode",
      layoutHint: { lane: "canon", laneOrder: 0, itemOrder: 0 },
    }, { width: 512, height: 620 });
    const secondCanon = flowNode("style", {
      type: "StylePresetNode",
      layoutHint: { lane: "canon", laneOrder: 0, itemOrder: 1 },
    }, { width: 320, height: 180 });
    const brief = flowNode("brief", {
      type: "BriefNode",
      layoutHint: { lane: "creative", laneOrder: 1, itemOrder: 0 },
    });

    const arranged = arrangeCanvasNodes([brief, secondCanon, tallCanon]);
    const byId = new Map(arranged.map((node) => [node.id, node]));

    expect(renderedCanvasNodeFootprint(tallCanon)).toEqual({ width: 512, height: 620 });
    expect(byId.get("style")?.position.y).toBe(768);
    expect(byId.get("brief")?.position.x).toBe(714);
  });

  it("places only new projected nodes without moving saved user positions", () => {
    const savedChild = flowNode("child", {
      type: "SubjectNode",
      layoutHint: { lane: "canon", laneOrder: 0, itemOrder: 0, positioned: true },
    }, { width: 360, height: 300 });
    savedChild.position = { x: 90, y: 100 };
    const newCat = flowNode("cat", {
      type: "SubjectNode",
      layoutHint: { lane: "canon", laneOrder: 0, itemOrder: 1, positioned: false },
    }, { width: 360, height: 300 });

    const arranged = arrangeCanvasNodes([newCat, savedChild], { preservePositioned: true });
    const byId = new Map(arranged.map((node) => [node.id, node]));

    expect(byId.get("child")?.position).toEqual({ x: 90, y: 100 });
    expect(byId.get("cat")?.position).toEqual({ x: 90, y: 448 });
  });

  it("uses card-aligned media footprints before Vue Flow has measured nodes", () => {
    expect(canvasNodeFootprint({ type: "ImageAssetNode" } as CanvasNodeDto)).toEqual({
      width: 530,
      height: 360,
    });
    expect(canvasNodeFootprint({ type: "VideoAssetNode" } as CanvasNodeDto)).toEqual({
      width: 440,
      height: 330,
    });
  });
});
