import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import type { CreativeWorkflowDto, SceneDto } from "../../api/types";
import CreativeWorkflowPanel from "../CreativeWorkflowPanel.vue";

const workflow: CreativeWorkflowDto = {
  sceneId: "scene-1",
  originalStory: "人物和猫咪准备出门。",
  currentStory: "人物和猫咪准备出门。",
  currentStoryHash: "hash-1",
  currentStorySource: "scene_draft",
  currentStorySourceStepId: null,
  stages: { diagnosis: [], rewrite: [], storyboard: [] },
  reviews: [],
};

vi.mock("../../api/client", () => ({
  api: {
    creativeWorkflow: vi.fn(async () => workflow),
    health: vi.fn(async () => ({ arkPlanningModel: "fake-planner" })),
  },
}));

const scene: SceneDto = {
  id: "scene-1",
  order: 1,
  title: "出门准备",
  sourceText: "人物和猫咪准备出门。",
  storyMode: "multi",
  targetShotCount: 4,
  lookDraftRevision: 0,
  status: "draft",
  attempts: [],
  shots: [],
};

const stubs = {
  ElAlert: { props: ["title"], template: "<div>{{ title }}</div>" },
  ElTag: { template: "<span><slot /></span>" },
  ElButton: { props: ["disabled"], template: "<button :disabled='disabled' @click=\"$emit('click')\"><slot /></button>" },
  ElInput: { template: "<textarea />" },
  ElRadioGroup: { template: "<div><slot /></div>" },
  ElRadioButton: { template: "<label><slot /></label>" },
  ElCheckbox: { template: "<label><slot /></label>" },
  ElCollapse: { template: "<div><slot /></div>" },
  ElCollapseItem: { template: "<div><slot /></div>" },
};

describe("CreativeWorkflowPanel", () => {
  it("shows the serial paid stages and blocks storyboard before story approval", async () => {
    const wrapper = mount(CreativeWorkflowPanel, {
      props: { scene },
      global: { stubs, directives: { loading: () => undefined } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("1. 原始剧情");
    expect(wrapper.text()).toContain("2. 剧情诊断");
    expect(wrapper.text()).toContain("3. 剧情重写");
    expect(wrapper.text()).toContain("4. 分镜导演");
    expect(wrapper.text()).toContain("5. 片段视觉与 Prompt 审稿");
    expect(wrapper.text()).toContain("fake-planner");
    const storyboard = wrapper.findAll("button").find((item) => item.text().includes("运行分镜导演"));
    expect(storyboard?.attributes("disabled")).toBeDefined();
  });
});
