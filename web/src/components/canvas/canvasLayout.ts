import type { Node } from "@vue-flow/core";

import type { CanvasNodeDto } from "../../api/types";

export interface CanvasNodeFootprint {
  width: number;
  height: number;
}

export interface ArrangeCanvasNodesOptions {
  preservePositioned?: boolean;
}

const FALLBACK_LANE_ORDER = 99;
const COLUMN_GAP = 112;
const ITEM_GAP = 48;
const ORIGIN_X = 90;
const ORIGIN_Y = 100;

export function canvasNodeFootprint(node: CanvasNodeDto): CanvasNodeFootprint {
  // CanvasNodeCard uses content-box sizing. These fallbacks include its padding and
  // border so the first semantic layout is close to the dimensions Vue Flow will
  // measure after mount; subsequent layouts always prefer the measured footprint.
  if (node.type === "ImageGenerationNode") return { width: 820, height: 460 };
  if (["ImageAssetNode", "ReferenceAssetNode"].includes(node.type)) {
    return { width: 530, height: 360 };
  }
  if (node.type === "VideoAssetNode") return { width: 440, height: 330 };
  if (node.type === "StoryboardDirectorNode") return { width: 490, height: 460 };
  if (node.type === "StoryCandidateNode") return { width: 342, height: 250 };
  if (node.type === "CharacterDesignNode") return { width: 342, height: 300 };
  if (["SubjectNode", "StylePresetNode"].includes(node.type)) {
    return { width: 362, height: 276 };
  }
  if (node.type === "GenerationBatchNode") return { width: 362, height: 210 };
  return { width: 298, height: 180 };
}

export function renderedCanvasNodeFootprint(flowNode: Node): CanvasNodeFootprint {
  const source = flowNode.data.node as CanvasNodeDto;
  const fallback = canvasNodeFootprint(source);
  const measured = flowNode as Node & { dimensions?: { width?: number; height?: number } };
  const measuredWidth = Number(measured.dimensions?.width ?? 0);
  const measuredHeight = Number(measured.dimensions?.height ?? 0);
  return {
    width: measuredWidth > 0 ? measuredWidth : fallback.width,
    height: measuredHeight > 0 ? measuredHeight : fallback.height,
  };
}

function semanticOrder(flowNode: Node) {
  const source = flowNode.data.node as CanvasNodeDto;
  return {
    laneOrder: source.layoutHint?.laneOrder ?? FALLBACK_LANE_ORDER,
    itemOrder: source.layoutHint?.itemOrder ?? Number.MAX_SAFE_INTEGER,
    stackKey: source.layoutHint?.stackKey ?? "",
  };
}

interface LayoutRect extends CanvasNodeFootprint {
  x: number;
  y: number;
}

function overlaps(left: LayoutRect, right: LayoutRect): boolean {
  return left.x < right.x + right.width + ITEM_GAP
    && left.x + left.width + ITEM_GAP > right.x
    && left.y < right.y + right.height + ITEM_GAP
    && left.y + left.height + ITEM_GAP > right.y;
}

export function arrangeCanvasNodes(
  flowNodes: Node[],
  options: ArrangeCanvasNodesOptions = {},
): Node[] {
  const lanes = new Map<number, Node[]>();
  for (const flowNode of flowNodes) {
    const laneOrder = semanticOrder(flowNode).laneOrder;
    const lane = lanes.get(laneOrder) ?? [];
    lane.push(flowNode);
    lanes.set(laneOrder, lane);
  }

  const occupied: LayoutRect[] = options.preservePositioned
    ? flowNodes.flatMap((node) => {
        const source = node.data.node as CanvasNodeDto;
        if (source.layoutHint?.positioned !== true) return [];
        return [{ ...node.position, ...renderedCanvasNodeFootprint(node) }];
      })
    : [];
  let x = ORIGIN_X;
  const arranged: Node[] = [];
  for (const laneOrder of [...lanes.keys()].sort((left, right) => left - right)) {
    const lane = lanes.get(laneOrder) ?? [];
    lane.sort((left, right) => {
      const leftOrder = semanticOrder(left);
      const rightOrder = semanticOrder(right);
      return leftOrder.itemOrder - rightOrder.itemOrder
        || leftOrder.stackKey.localeCompare(rightOrder.stackKey)
        || left.id.localeCompare(right.id);
    });
    const laneWidth = Math.max(...lane.map((node) => renderedCanvasNodeFootprint(node).width));
    let y = ORIGIN_Y;
    for (const node of lane) {
      const footprint = renderedCanvasNodeFootprint(node);
      const source = node.data.node as CanvasNodeDto;
      if (options.preservePositioned && source.layoutHint?.positioned === true) {
        arranged.push(node);
        y = Math.max(y, node.position.y + footprint.height + ITEM_GAP);
        continue;
      }
      let candidate: LayoutRect = { x, y, ...footprint };
      let collision = occupied.find((rect) => overlaps(candidate, rect));
      while (collision) {
        candidate = { ...candidate, y: collision.y + collision.height + ITEM_GAP };
        collision = occupied.find((rect) => overlaps(candidate, rect));
      }
      occupied.push(candidate);
      arranged.push({ ...node, position: { x: candidate.x, y: candidate.y } });
      y = candidate.y + footprint.height + ITEM_GAP;
    }
    x += laneWidth + COLUMN_GAP;
  }
  return arranged;
}
