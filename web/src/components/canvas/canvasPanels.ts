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
  placement: "below" | "above" | "right";
}

export type CanvasConsolePresetKey = "compact" | "text" | "image" | "video" | "storyboard";

export interface CanvasConsolePreset extends PanelSize {
  key: CanvasConsolePresetKey;
}

export interface CanvasOverlaySession extends PanelPosition, PanelSize {
  nodeId: string;
  presetKey: CanvasConsolePresetKey;
}

export const CANVAS_CONSOLE_PRESETS: Record<CanvasConsolePresetKey, CanvasConsolePreset> = {
  compact: { key: "compact", width: 720, height: 320 },
  text: { key: "text", width: 760, height: 360 },
  image: { key: "image", width: 920, height: 560 },
  video: { key: "video", width: 1040, height: 560 },
  storyboard: { key: "storyboard", width: 1120, height: 620 },
};

const IMAGE_CONSOLE_NODES = new Set<CanvasNodeType>([
  "CharacterDesignNode",
  "GenerationBatchNode",
  "ImageAssetNode",
  "ImageGenerationNode",
  "ReferenceAssetNode",
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
  "SceneNode",
  "ShotBeatNode",
  "StoryCandidateNode",
  "StoryCriticNode",
  "StoryPlannerNode",
  "SubjectNode",
  "TimelineNode",
]);

const PANEL_GAP = 12;
const VIEWPORT_MARGIN = 16;

const clamp = (value: number, minimum: number, maximum: number) => (
  Math.min(Math.max(value, minimum), Math.max(minimum, maximum))
);

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

export function clampCanvasPanelPosition(
  position: PanelPosition,
  viewport: PanelSize,
  panel: PanelSize,
): PanelPosition {
  return {
    ...position,
    left: clamp(position.left, VIEWPORT_MARGIN, viewport.width - panel.width - VIEWPORT_MARGIN),
    top: clamp(position.top, VIEWPORT_MARGIN, viewport.height - panel.height - VIEWPORT_MARGIN),
  };
}

export function positionCanvasPanel(
  anchor: ScreenRect,
  viewport: PanelSize,
  panel: PanelSize,
): PanelPosition {
  const maxLeft = viewport.width - panel.width - VIEWPORT_MARGIN;
  const maxTop = viewport.height - panel.height - VIEWPORT_MARGIN;
  const left = clamp(anchor.left, VIEWPORT_MARGIN, maxLeft);
  const belowTop = anchor.top + anchor.height + PANEL_GAP;

  if (belowTop <= maxTop) {
    return { left, top: belowTop, placement: "below" };
  }

  const aboveTop = anchor.top - panel.height - PANEL_GAP;
  if (aboveTop >= VIEWPORT_MARGIN) {
    return { left, top: aboveTop, placement: "above" };
  }

  return {
    left: clamp(anchor.left + anchor.width + PANEL_GAP, VIEWPORT_MARGIN, maxLeft),
    top: clamp(anchor.top, VIEWPORT_MARGIN, maxTop),
    placement: "right",
  };
}
