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
  currentShotSnapshotHash: "empty-shot-snapshot",
  stages: { expansion: [], diagnosis: [], rewrite: [], storyboard: [] },
  reviews: [],
};

const diagnosisV1 = {
  overallAssessment: "第一版 LLM 诊断。",
  issues: [],
  rewriteOptions: [
    { strategy: "balanced" as const, title: "平衡优化", summary: "调整动作关系。", tradeoffs: "少量改写。" },
  ],
};

const diagnosisV2 = {
  overallAssessment: "第二版 LLM 诊断，应在刷新后直接可见。",
  issues: [],
  rewriteOptions: [
    { strategy: "balanced" as const, title: "平衡优化", summary: "进一步调整。", tradeoffs: "保留核心事件。" },
  ],
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
  ElInput: { props: ["modelValue"], template: "<textarea :value='modelValue' />" },
  ElInputNumber: { props: ["modelValue"], template: "<input type='number' :value='modelValue' />" },
  ElFormItem: { template: "<label><slot /></label>" },
  ElDivider: { template: "<div><slot /></div>" },
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

  it("shows every diagnosis version after reload and lets the user inspect an older accepted version", async () => {
    workflow.stages.diagnosis = [
      {
        stepId: "diagnosis-2",
        operationKey: "director:story-diagnosis",
        status: "succeeded",
        attempt: 2,
        sourceHash: "hash-1",
        providerOutput: diagnosisV2,
        acceptedOutput: null,
        acceptedAt: null,
        createdAt: "2026-08-13T02:00:00Z",
      },
      {
        stepId: "diagnosis-1",
        operationKey: "director:story-diagnosis",
        status: "succeeded",
        attempt: 1,
        sourceHash: "hash-1",
        providerOutput: diagnosisV1,
        acceptedOutput: {
          diagnosis: { ...diagnosisV1, overallAssessment: "第一版人工接受评价。" },
          selectedStrategy: "balanced",
          additionalInstructions: "保留核心事件",
          preserveOriginal: false,
        },
        acceptedAt: "2026-08-13T01:00:00Z",
        createdAt: "2026-08-13T00:30:00Z",
      },
    ];

    const wrapper = mount(CreativeWorkflowPanel, {
      props: { scene },
      global: { stubs, directives: { loading: () => undefined } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("诊断 V2");
    expect(wrapper.text()).toContain("待确认");
    expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toBe(
      "第二版 LLM 诊断，应在刷新后直接可见。",
    );

    const firstVersion = wrapper.findAll("button").find((item) => item.text().includes("诊断 V1"));
    expect(firstVersion).toBeDefined();
    await firstVersion!.trigger("click");

    expect(wrapper.text()).toContain("当前采用");
    expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toBe(
      "第一版人工接受评价。",
    );

    workflow.stages.diagnosis = [];
  });

  it("shows storyboard versions and identifies the version synchronized to current clips", async () => {
    const storyboard = {
      sceneTitle: "出门准备",
      lookPlan: {
        personWardrobe: "浅色外套",
        personAccessories: "帆布包",
        catAppearance: "保持灰白猫",
        keyProps: "伸缩鱼竿",
        environmentStyle: "indoor" as const,
        personPose: "自然站立",
        catPose: "脚边坐姿",
        composition: "人猫空间关系清晰",
        additionalInstructions: "",
        imageRecommended: true,
        recommendationReason: "统一造型",
      },
      shots: [
        {
          title: "准备出发",
          direction: "1. 中景建立。\n2. 稳定收尾。",
          suggestedDurationSeconds: 10,
        },
      ],
    };
    workflow.currentShotSnapshotHash = "current-shots";
    workflow.stages.storyboard = [
      {
        stepId: "storyboard-2",
        operationKey: "director:shot-suggestions",
        status: "succeeded",
        attempt: 2,
        sourceHash: "hash-1",
        providerOutput: storyboard,
        acceptedOutput: { ...storyboard, appliedShotSnapshotHash: "current-shots" },
        acceptedAt: "2026-08-13T04:00:00Z",
        createdAt: "2026-08-13T03:30:00Z",
      },
      {
        stepId: "storyboard-1",
        operationKey: "director:shot-suggestions",
        status: "succeeded",
        attempt: 1,
        sourceHash: "hash-1",
        providerOutput: storyboard,
        acceptedOutput: { ...storyboard, appliedShotSnapshotHash: "old-shots" },
        acceptedAt: "2026-08-13T02:00:00Z",
        createdAt: "2026-08-13T01:30:00Z",
      },
    ];

    const wrapper = mount(CreativeWorkflowPanel, {
      props: { scene },
      global: { stubs, directives: { loading: () => undefined } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("分镜 V2");
    expect(wrapper.text()).toContain("分镜 V1");
    expect(wrapper.text()).toContain("当前片段已同步");
    expect(wrapper.text()).toContain("当前采用");
    expect(wrapper.text()).toContain("历史已采用");

    workflow.currentShotSnapshotHash = "empty-shot-snapshot";
    workflow.stages.storyboard = [];
  });
});
