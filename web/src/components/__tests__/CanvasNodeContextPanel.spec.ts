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

  it("keeps long planner details in one scroll region while actions remain in the fixed footer", () => {
    const node: CanvasNodeDto = {
      id: "planner-1",
      type: "StoryPlannerNode",
      objectType: "story_planner",
      objectId: "planner-1",
      position: { x: 0, y: 0 },
      data: {
        title: "三案故事策划",
        briefSummary: "孩子和猫咪在雨后发现一片发亮的叶子",
        canonDependencies: ["固定儿童", "固定猫咪", "线条材质画风"],
        candidateCount: 3,
        candidateRules: ["儿童主动行动", "猫咪自然参与", "以温暖变化收尾"],
      },
      availableActions: [{
        key: "generate_stories",
        label: "生成三个剧情候选",
        enabled: true,
        execution: "local_worker",
      }],
    };
    const wrapper = mount(CanvasNodeContextPanel, {
      props: {
        node,
        embedded: true,
        executions: Array.from({ length: 5 }, (_, index) => ({
          key: `task-${index}`,
          jobId: `task-${index}`,
          kind: "story_strategy",
          label: `剧情任务 ${index + 1}`,
          status: "running" as const,
          projectId: "project-1",
          updatedAt: "2026-08-24T00:00:00Z",
          source: "runtime" as const,
        })),
      },
    });

    expect(wrapper.findAll(".content-scroll")).toHaveLength(1);
    expect(wrapper.get(".content-scroll").find("footer").exists()).toBe(false);
    expect(wrapper.get("footer").element.parentElement).toBe(wrapper.get(".context-panel").element);
    expect(wrapper.text()).toContain("固定儿童、固定猫咪、线条材质画风");
    expect(wrapper.text()).toContain("儿童主动行动");
    expect(wrapper.get('[data-action="generate_stories"]').text()).toBe("生成三个剧情候选");
  });
});
