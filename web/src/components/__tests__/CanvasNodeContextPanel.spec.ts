import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { CanvasNodeDto } from "../../api/types";
import CanvasNodeContextPanel from "../canvas/CanvasNodeContextPanel.vue";

describe("CanvasNodeContextPanel", () => {
  it("presents domain content without exposing raw JSON or a fake editor action", () => {
    const node: CanvasNodeDto = {
      id: "story-1",
      type: "StoryCandidateNode" as const,
      objectType: "story_revision",
      objectId: "revision-1",
      position: { x: 0, y: 0 },
      data: {
        title: "关系情感型",
        logline: "孩子和猫在雨前一起收回风筝。",
        scorecard: { average: 8.7 },
        privateDebugPayload: { shouldNotLeak: true },
      },
    };
    const wrapper = mount(CanvasNodeContextPanel, { props: { node } });

    expect(wrapper.text()).toContain("孩子和猫在雨前一起收回风筝");
    expect(wrapper.text()).toContain("综合评分 8.7");
    expect(wrapper.text()).toContain("原创故事候选");
    expect(wrapper.text()).not.toContain("StoryCandidateNode");
    expect(wrapper.text()).not.toContain("story_revision");
    expect(wrapper.text()).not.toContain("privateDebugPayload");
    expect(wrapper.text()).not.toContain("shouldNotLeak");

    expect(wrapper.find('[data-action="activate"]').exists()).toBe(false);
  });

  it("exposes only a real action declared by the backend contract", async () => {
    const node: CanvasNodeDto = {
      id: "brief-1",
      type: "BriefNode" as const,
      objectType: "story_brief",
      objectId: "brief-1",
      position: { x: 0, y: 0 },
      data: { title: "创意简报", theme: "雨前收画", targetDurationSeconds: 60 },
      availableActions: [{ key: "edit_brief", label: "编辑创意简报", enabled: true, execution: "client" as const }],
    };
    const wrapper = mount(CanvasNodeContextPanel, { props: { node } });

    expect(wrapper.get('[data-action="edit_brief"]').text()).toContain("编辑创意简报");
    await wrapper.get('[data-action="edit_brief"]').trigger("click");
    expect(wrapper.emitted("action")?.[0]).toEqual([
      { key: "edit_brief", label: "编辑创意简报", enabled: true, execution: "client" },
    ]);
  });

  it("explains a disabled action instead of silently ignoring the click", async () => {
    const node: CanvasNodeDto = {
      id: "storyboard-1",
      type: "StoryboardDirectorNode",
      objectType: "storyboard_director",
      objectId: "director-1",
      position: { x: 0, y: 0 },
      data: { title: "分镜导演", shotCount: 0 },
      availableActions: [{
        key: "review_storyboard",
        label: "批准当前分镜",
        enabled: false,
        execution: "unavailable",
        disabledReason: "请先生成并保存镜头表",
      }],
    };
    const wrapper = mount(CanvasNodeContextPanel, { props: { node } });

    await wrapper.get('[data-action="review_storyboard"]').trigger("click");
    expect(wrapper.text()).toContain("请先生成并保存镜头表");
    expect(wrapper.emitted("action")).toBeUndefined();
  });
});
