import { describe, expect, it } from "vitest";

import {
  CANVAS_CONSOLE_PRESETS,
  anchorCanvasOverlay,
  consolePresetForNode,
  projectCanvasNodeRect,
  resolveCanvasConsoleSize,
} from "../canvas/canvasPanels";

describe("canvas panel placement", () => {
  it("projects graph coordinates through the current viewport and cached surface rect", () => {
    expect(projectCanvasNodeRect(
      {
        computedPosition: { x: 100, y: 80 },
        dimensions: { width: 280, height: 180 },
      },
      { x: 30, y: -10, zoom: 0.5 },
      { left: 20, top: 70, width: 1200, height: 900 },
    )).toEqual({ left: 100, top: 100, width: 140, height: 90 });
  });

  it("centers the toolbar above and the console strictly below the node", () => {
    expect(anchorCanvasOverlay(
      { left: 100, top: 80, width: 280, height: 180 },
      { width: 720, height: 420 },
      { width: 720, height: 56 },
    )).toEqual({
      toolbar: { left: -120, top: 12, placement: "above" },
      console: { left: -120, top: 272, placement: "below" },
    });
  });

  it("does not flip or clamp the console when its anchored position leaves the viewport", () => {
    expect(anchorCanvasOverlay(
      { left: 900, top: 700, width: 280, height: 160 },
      { width: 720, height: 420 },
      { width: 720, height: 56 },
    )).toEqual({
      toolbar: { left: 680, top: 632, placement: "above" },
      console: { left: 680, top: 872, placement: "below" },
    });
  });

  it("maps domain node types to fixed LibTV-style console presets", () => {
    expect(consolePresetForNode("BriefNode", "node")).toEqual(CANVAS_CONSOLE_PRESETS.text);
    expect(consolePresetForNode("StoryCandidateNode", "node")).toEqual(CANVAS_CONSOLE_PRESETS.text);
    expect(consolePresetForNode("ImageGenerationNode", "node")).toEqual(CANVAS_CONSOLE_PRESETS.image);
    expect(consolePresetForNode("CharacterDesignNode", "node")).toEqual(CANVAS_CONSOLE_PRESETS.image);
    expect(consolePresetForNode("VideoAssetNode", "video")).toEqual(CANVAS_CONSOLE_PRESETS.video);
    expect(consolePresetForNode("StoryboardDirectorNode", "node")).toEqual(CANVAS_CONSOLE_PRESETS.storyboard);
  });

  it("keeps desktop presets fixed and safely shrinks them inside a small viewport", () => {
    expect(resolveCanvasConsoleSize(CANVAS_CONSOLE_PRESETS.image, { width: 1440, height: 1024 }))
      .toEqual({ width: 760, height: 440 });
    expect(resolveCanvasConsoleSize(CANVAS_CONSOLE_PRESETS.storyboard, { width: 900, height: 600 }))
      .toEqual({ width: 760, height: 420 });
  });

});
