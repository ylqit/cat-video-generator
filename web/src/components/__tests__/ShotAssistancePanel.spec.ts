import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it } from "vitest";

import type { ShotAssistContext, ShotAssistRecord } from "../../api/types";
import ShotAssistancePanel from "../ShotAssistancePanel.vue";

const context: ShotAssistContext = {
  shotId: "shot-2",
  sourceDraftRevision: 3,
  model: "fake-multimodal",
  localAnalysis: {
    suggestedSubshotMin: 2,
    suggestedSubshotMax: 3,
    detectedSubshotCount: 2,
    actionCount: 3,
    cameraMoveCount: 1,
    hasStableEnding: true,
    hasSound: true,
    qualitativePacing: "简洁建立，主体动作完整展开",
    findings: [],
  },
  previousShot: { id: "shot-1", title: "上一片段" },
  nextShot: { id: "shot-3", title: "下一片段" },
  previousTail: {
    available: true,
    stale: false,
    assetId: "tail-asset",
    previousShotId: "shot-1",
    sourceVideoAssetId: "video-1",
  },
  candidates: [],
  defaultCandidateAssetIds: [],
  warnings: [],
};

const record: ShotAssistRecord = {
  stepId: "step-1",
  status: "succeeded",
  sourceDraftRevision: 3,
  stale: false,
  analysis: {
    actionDensityAssessment: "动作密度适中",
    pacingPlan: {
      recommendedDurationSeconds: 12,
      rationale: "保留互动反馈",
      beats: [
        { ordinal: 1, description: "建立", rhythm: "brief" },
        { ordinal: 2, description: "反馈", rhythm: "expanded" },
      ],
    },
    recommendedSceneLookUsage: "appearance_only",
    recommendedAnchorMode: "text_only",
    referenceDecisions: [],
    continuity: { previousIssues: [], nextIssues: [], recommendation: "保持水桶已放稳" },
    promptRisks: [],
    patch: { durationSeconds: 12, sceneLookUsage: "appearance_only" },
  },
};

const stubs = {
  ElAlert: { props: ["title"], template: "<div>{{ title }}</div>" },
  ElTag: { template: "<span><slot /></span>" },
  ElButton: { template: "<button @click=\"$emit('click')\"><slot /></button>" },
  ElCheckboxGroup: { template: "<div><slot /></div>" },
  ElCheckbox: { template: "<label><slot /></label>" },
  ElRadio: { template: "<label><slot /></label>" },
};

describe("ShotAssistancePanel", () => {
  it("emits the selected field patch and exposes image failure details", async () => {
    const wrapper = mount(ShotAssistancePanel, {
      props: { context, records: [record] },
      global: { stubs },
    });

    await wrapper.get("img").trigger("error");
    await nextTick();
    expect(wrapper.text()).toContain("加载失败 · tail-asset");

    const apply = wrapper.findAll("button").find((item) => item.text().includes("应用勾选字段"));
    await apply?.trigger("click");
    expect(wrapper.emitted("apply")?.[0]?.[1]).toEqual({
      durationSeconds: 12,
      sceneLookUsage: "appearance_only",
    });
  });

  it("renders stale tail and analysis failure without treating them as current", () => {
    const wrapper = mount(ShotAssistancePanel, {
      props: {
        context: { ...context, previousTail: { ...context.previousTail, stale: true } },
        records: [{
          ...record,
          analysis: null,
          stale: true,
          error: { message: "分析超时，已保存内容不受影响" },
        }],
      },
      global: { stubs },
    });

    expect(wrapper.text()).toContain("尾帧已过期");
    expect(wrapper.text()).toContain("该分析对应旧草稿");
    expect(wrapper.text()).toContain("分析超时，已保存内容不受影响");
  });
});
