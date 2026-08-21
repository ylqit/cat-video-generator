import { describe, expect, it } from "vitest";

import {
  CANVAS_CONSOLE_PRESETS,
  clampCanvasPanelPosition,
  consolePresetForNode,
  positionCanvasPanel,
  resolveCanvasConsoleSize,
} from "../canvas/canvasPanels";

describe("canvas panel placement", () => {
  it("places the panel below the node without inheriting canvas zoom", () => {
    expect(positionCanvasPanel(
      { left: 100, top: 80, width: 280, height: 180 },
      { width: 1200, height: 900 },
      { width: 720, height: 420 },
    )).toEqual({ left: 100, top: 272, placement: "below" });
  });

  it("flips above and clamps horizontally when the lower viewport is full", () => {
    expect(positionCanvasPanel(
      { left: 900, top: 700, width: 280, height: 160 },
      { width: 1200, height: 900 },
      { width: 720, height: 420 },
    )).toEqual({ left: 464, top: 268, placement: "above" });
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
      .toEqual({ width: 920, height: 560 });
    expect(resolveCanvasConsoleSize(CANVAS_CONSOLE_PRESETS.storyboard, { width: 900, height: 600 }))
      .toEqual({ width: 876, height: 504 });
  });

  it("clamps a fixed screen position on resize without re-anchoring it to the node", () => {
    expect(clampCanvasPanelPosition(
      { left: 900, top: 700, placement: "below" },
      { width: 1200, height: 800 },
      { width: 720, height: 320 },
    )).toEqual({ left: 464, top: 464, placement: "below" });
  });
});
