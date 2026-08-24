import { describe, expect, it } from "vitest";

import type { CanvasNodeDto } from "../../api/types";
import type { TaskCenterItem } from "../../tasks/taskCenter";
import { tasksForCanvasNode } from "../canvas/canvasTaskBinding";

function task(overrides: Partial<TaskCenterItem>): TaskCenterItem {
  return {
    key: overrides.key ?? "task",
    kind: "workflow",
    label: "剧情脚本扩写",
    status: "running",
    updatedAt: "2026-08-24T12:00:00Z",
    source: "workflow",
    ...overrides,
  };
}

function scriptNode(): CanvasNodeDto {
  return {
    id: "script-node",
    type: "StoryScriptNode",
    objectType: "story_event",
    objectId: "event-selected",
    position: { x: 0, y: 0 },
    data: {},
    executionScope: {
      kind: "business_object",
      objectType: "story_event",
      recipeInstanceId: "recipe-1",
      businessObjectId: "event-selected",
      operationKeys: ["recipe:story_script"],
      phases: ["story"],
      includeChildTasks: true,
    },
  };
}

describe("canvas task binding", () => {
  it("does not attach an unrelated business object from the same recipe", () => {
    const related = task({
      key: "related",
      businessObjectId: "event-selected",
      recipeInstanceId: "recipe-1",
      operationKey: "recipe:story_script",
      phase: "story",
    });
    const unrelated = task({
      key: "unrelated",
      businessObjectId: "event-other",
      recipeInstanceId: "recipe-1",
      operationKey: "recipe:story_script",
      phase: "story",
    });

    expect(tasksForCanvasNode([related, unrelated], scriptNode())).toEqual([related]);
  });

  it("uses an explicit canvas node binding before every broader scope", () => {
    const explicitMismatch = task({
      key: "explicit-mismatch",
      canvasNodeId: "another-node",
      businessObjectId: "event-selected",
      recipeInstanceId: "recipe-1",
      operationKey: "recipe:story_script",
      phase: "story",
    });

    expect(tasksForCanvasNode([explicitMismatch], scriptNode())).toEqual([]);
  });

  it("includes declared children only when the parent belongs to the node", () => {
    const parent = task({
      key: "parent",
      stepId: "parent-step",
      childStepIds: ["child-step"],
      businessObjectId: "event-selected",
    });
    const child = task({
      key: "child",
      stepId: "child-step",
      parentStepId: "parent-step",
      businessObjectId: "generated-script",
      updatedAt: "2026-08-24T12:01:00Z",
    });

    expect(tasksForCanvasNode([parent, child], scriptNode())).toEqual([child, parent]);
  });
});
