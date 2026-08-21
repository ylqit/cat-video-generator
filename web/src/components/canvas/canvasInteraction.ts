import type { CanvasNodeDto } from "../../api/types";

export type CanvasConsoleKind = "node" | "video" | "video_segment";

export type CanvasInteractionState =
  | { mode: "idle" }
  | { mode: "node_selected"; nodeId: string }
  | { mode: "video_selected"; nodeId: string }
  | { mode: "video_segment_reshoot"; nodeId: string }
  | {
      mode: "reference_picking";
      nodeId: string;
      returnMode: "node_selected" | "video_segment_reshoot" | "storyboard_characters";
      snapshotNodeIds: string[];
    }
  | {
      mode: "fullscreen_editing";
      nodeId: string;
      returnMode: "video_selected" | "video_segment_reshoot";
    };

export interface VideoEditConsoleDraft {
  startMs: number;
  endMs: number;
  instruction: string;
  referenceAssetIds: string[];
  annotations: Array<{
    frameTimestampMs: number;
    coordinateSpace?: "source_normalized";
    tool: "rectangle" | "brush" | "arrow" | "text" | "marker";
    points: Array<{ x: number; y: number }>;
    label: string;
  }>;
}

export interface ReferencePickSession {
  targetNodeId: string;
  returnMode: "node_selected" | "video_segment_reshoot" | "storyboard_characters";
  snapshotNodeIds: string[];
}

export function selectedInteraction(node: CanvasNodeDto): CanvasInteractionState {
  return node.type === "VideoAssetNode"
    ? { mode: "video_selected", nodeId: node.id }
    : { mode: "node_selected", nodeId: node.id };
}

export function selectedNodeId(state: CanvasInteractionState): string | null {
  return state.mode === "idle" ? null : state.nodeId;
}

export function consoleKind(state: CanvasInteractionState): CanvasConsoleKind | null {
  if (state.mode === "idle" || state.mode === "fullscreen_editing") return null;
  if (state.mode === "video_selected") return "video";
  if (state.mode === "video_segment_reshoot") return "video_segment";
  if (state.mode === "reference_picking") {
    return state.returnMode === "video_segment_reshoot" ? "video_segment" : "node";
  }
  return "node";
}

export function escapeInteraction(state: CanvasInteractionState): CanvasInteractionState {
  if (state.mode === "reference_picking") {
    return state.returnMode === "video_segment_reshoot"
      ? { mode: "video_segment_reshoot", nodeId: state.nodeId }
      : { mode: "node_selected", nodeId: state.nodeId };
  }
  if (state.mode === "video_segment_reshoot") {
    return { mode: "video_selected", nodeId: state.nodeId };
  }
  if (state.mode === "fullscreen_editing") {
    return state.returnMode === "video_segment_reshoot"
      ? { mode: "video_segment_reshoot", nodeId: state.nodeId }
      : { mode: "video_selected", nodeId: state.nodeId };
  }
  return { mode: "idle" };
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest("input, textarea, select, button, a, [contenteditable='true']"));
}
