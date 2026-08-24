import type { CanvasNodeType } from "../../api/types";
import type { CanvasConsoleKind } from "./canvasInteraction";

export interface ScreenRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface PanelSize {
  width: number;
  height: number;
}

export interface PanelPosition {
  left: number;
  top: number;
  placement: "below" | "above";
}

export interface CanvasViewportTransform {
  x: number;
  y: number;
  zoom: number;
}

export interface CanvasNodeGeometry {
  computedPosition: { x: number; y: number };
  dimensions: PanelSize;
}

export interface CanvasOverlayGeometry {
  toolbar: PanelPosition;
  console: PanelPosition;
}

export type CanvasConsolePresetKey = "compact" | "text" | "image" | "video" | "storyboard";

export interface CanvasConsolePreset extends PanelSize {
  key: CanvasConsolePresetKey;
}

export interface CanvasOverlaySession extends PanelSize {
  nodeId: string;
  presetKey: CanvasConsolePresetKey;
  surfaceRect: ScreenRect;
  anchorGap: number;
}

export const CANVAS_CONSOLE_PRESETS: Record<CanvasConsolePresetKey, CanvasConsolePreset> = {
  compact: { key: "compact", width: 660, height: 280 },
  text: { key: "text", width: 660, height: 320 },
  image: { key: "image", width: 760, height: 440 },
  video: { key: "video", width: 860, height: 480 },
  storyboard: { key: "storyboard", width: 760, height: 420 },
};

const IMAGE_CONSOLE_NODES = new Set<CanvasNodeType>([
  "CharacterDesignNode",
  "GenerationBatchNode",
  "ImageAssetNode",
  "ImageGenerationNode",
  "ReferenceAssetNode",
  "SceneNode",
  "StylePresetNode",
]);

const VIDEO_CONSOLE_NODES = new Set<CanvasNodeType>([
  "VideoAssetNode",
  "VideoEditNode",
  "VideoGenerationNode",
  "VideoSegmentNode",
]);

const TEXT_CONSOLE_NODES = new Set<CanvasNodeType>([
  "ApprovalGateNode",
  "AudioGenerationNode",
  "BriefNode",
  "PromptArtifactNode",
  "RecipeGroupNode",
  "ReviewNode",
  "ShotBeatNode",
  "StoryCandidateNode",
  "StoryCriticNode",
  "StoryPlannerNode",
  "SubjectNode",
  "TimelineNode",
]);

export const CANVAS_OVERLAY_ANCHOR_GAP = 12;

export function consolePresetForNode(
  nodeType: CanvasNodeType,
  consoleKind: CanvasConsoleKind,
): CanvasConsolePreset {
  if (consoleKind === "video" || consoleKind === "video_segment" || VIDEO_CONSOLE_NODES.has(nodeType)) {
    return CANVAS_CONSOLE_PRESETS.video;
  }
  if (nodeType === "StoryboardDirectorNode") return CANVAS_CONSOLE_PRESETS.storyboard;
  if (IMAGE_CONSOLE_NODES.has(nodeType)) return CANVAS_CONSOLE_PRESETS.image;
  if (TEXT_CONSOLE_NODES.has(nodeType)) return CANVAS_CONSOLE_PRESETS.text;
  return CANVAS_CONSOLE_PRESETS.compact;
}

export function resolveCanvasConsoleSize(
  preset: CanvasConsolePreset,
  viewport: PanelSize,
): PanelSize {
  const horizontalMargin = viewport.width < 1280 ? 24 : 32;
  return {
    width: Math.min(preset.width, Math.max(0, viewport.width - horizontalMargin)),
    height: Math.min(preset.height, Math.max(0, viewport.height - 96)),
  };
}

export function projectCanvasNodeRect(
  node: CanvasNodeGeometry,
  viewport: CanvasViewportTransform,
  surfaceRect: ScreenRect,
): ScreenRect {
  return {
    left: surfaceRect.left + viewport.x + node.computedPosition.x * viewport.zoom,
    top: surfaceRect.top + viewport.y + node.computedPosition.y * viewport.zoom,
    width: node.dimensions.width * viewport.zoom,
    height: node.dimensions.height * viewport.zoom,
  };
}

export function anchorCanvasOverlay(
  nodeRect: ScreenRect,
  panel: PanelSize,
  toolbar: PanelSize,
  gap = CANVAS_OVERLAY_ANCHOR_GAP,
): CanvasOverlayGeometry {
  const centerX = nodeRect.left + nodeRect.width / 2;
  return {
    toolbar: {
      left: centerX - toolbar.width / 2,
      top: nodeRect.top - toolbar.height - gap,
      placement: "above",
    },
    console: {
      left: centerX - panel.width / 2,
      top: nodeRect.top + nodeRect.height + gap,
      placement: "below",
    },
  };
}
