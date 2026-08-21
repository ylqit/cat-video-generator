import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { ProductionRecipeInstanceDto } from "../../api/types";
import RecipeInspectorPanel from "../canvas/RecipeInspectorPanel.vue";

const recipe: ProductionRecipeInstanceDto = {
  id: "recipe-1",
  projectId: "project-1",
  recipeKey: "healing_child_cat_v1",
  recipeVersion: 1,
  revision: 2,
  theme: "雨后收集落叶",
  targetDurationSeconds: 15,
  qualityTier: "balanced",
  canonProfileId: "canon-v2-healing-child-cat",
  stage: "anchors",
  shotDurations: [15],
  currentBlocker: "有视觉锚点待生成或审核",
  primaryAction: "生成下一镜视觉锚点",
  reviewStages: [
    { key: "story", complete: true },
    { key: "anchors", complete: false },
    { key: "video", complete: false },
    { key: "sequence", complete: false },
  ],
  progress: {
    storyApproved: true,
    episodeRulesLocked: true,
    shotCount: 1,
    approvedAnchorCount: 0,
    approvedVideoCount: 0,
    sequenceReady: false,
    finalApproved: false,
  },
  episodeRules: {
    personWardrobe: "米白上衣与棕色背带裤",
    timeWeather: "秋日雨后",
    mainScene: "林间小路",
    environment: "outdoor",
    coreProps: ["布篮"],
    catBehaviorMode: "natural",
    soundPlan: { ambient: ["树叶声"], foley: ["脚步"], musicMood: "轻柔", dialoguePolicy: "none" },
    stylePositive: ["原创水彩", "纸张纹理", "低对比"],
    styleExcluded: ["准写实", "3D"],
    canonProfileId: "canon-v2-healing-child-cat",
  },
  shots: [{
    beatId: "beat-1",
    shotId: "shot-1",
    title: "发现叶子",
    durationSeconds: 15,
    status: "ready",
    temporalBeats: [
      { phase: "beginning", startSecond: 0, endSecond: 5, childAction: "孩子蹲下" },
      { phase: "change", startSecond: 5, endSecond: 10, childAction: "叶子翻面" },
      { phase: "warm_ending", startSecond: 10, endSecond: 15, childAction: "孩子与猫靠近" },
    ],
    selectedAnchorAssetId: null,
    selectedVideoAssetId: null,
    anchorCandidates: [],
    videoCandidates: [],
  }],
};

describe("RecipeInspectorPanel", () => {
  it("shows four textual review stages, locked rules and per-shot temporal beats", async () => {
    const wrapper = mount(RecipeInspectorPanel, { props: { recipe } });

    expect(wrapper.text()).toContain("创意与故事");
    expect(wrapper.text()).toContain("已完成");
    expect(wrapper.text()).toContain("自然四足猫");
    expect(wrapper.text()).toContain("0–5秒 · 孩子蹲下");
    expect(wrapper.text()).toContain("有视觉锚点待生成或审核");
    const anchor = wrapper.findAll("button").find((button) => button.text() === "生成视觉锚点");
    await anchor!.trigger("click");
    expect(wrapper.emitted("run-anchor")?.[0]).toEqual([recipe.shots[0]]);
  });

  it("plays the rendered sequence and requires an explicit final review", async () => {
    const finalRecipe: ProductionRecipeInstanceDto = {
      ...recipe,
      stage: "sequence",
      currentBlocker: "最终音画尚未人工批准",
      primaryAction: "审核最终成片",
      progress: { ...recipe.progress, sequenceReady: true },
      sequenceCandidate: {
        id: "sequence-1",
        revision: 3,
        status: "content_review",
        durationMs: 15_000,
        audioPolicy: "native_fades",
        renderedAssetId: "asset-final",
        contentUrl: "/api/v1/assets/asset-final/content",
        sha256: "a".repeat(64),
        qc: { passed: true },
      },
    };
    const wrapper = mount(RecipeInspectorPanel, { props: { recipe: finalRecipe } });

    expect(wrapper.find("video.final-preview").attributes("src")).toBe(
      "/api/v1/assets/asset-final/content",
    );
    await wrapper.get("button.final-review").trigger("click");
    expect(wrapper.emitted("review-sequence")?.[0]).toEqual([finalRecipe.sequenceCandidate]);
  });

  it("lets the user choose a 300-1000ms transition before final composition", async () => {
    const secondShot = {
      ...recipe.shots[0],
      beatId: "beat-2",
      shotId: "shot-2",
      title: "温暖收尾",
      selectedAnchorAssetId: "anchor-2",
      selectedVideoAssetId: "video-2",
    };
    const sequenceRecipe: ProductionRecipeInstanceDto = {
      ...recipe,
      stage: "sequence",
      primaryAction: "合成最终成片",
      shots: [recipe.shots[0], secondShot],
      progress: {
        ...recipe.progress,
        shotCount: 2,
        approvedAnchorCount: 2,
        approvedVideoCount: 2,
      },
    };
    const wrapper = mount(RecipeInspectorPanel, { props: { recipe: sequenceRecipe } });

    await wrapper.get("select.transition-type").setValue("cross_dissolve");
    await wrapper.get("input.transition-duration").setValue(600);
    await wrapper.get("button.primary").trigger("click");

    expect(wrapper.emitted("primary")?.[0]).toEqual([[
      {
        afterShotId: "shot-2",
        transition: { type: "cross_dissolve", durationMs: 600 },
      },
    ]]);
  });

  it("keeps story choice and EpisodeRules review inside the collapsed recipe", async () => {
    const candidate = {
      id: "story-1",
      revision: 1,
      strategy: "relationship",
      status: "candidate" as const,
      title: "叶子上的雨滴",
      logline: "孩子和猫咪发现一片发亮的叶子。",
      synopsis: "一个低压力的小发现以相互依偎收尾。",
      episodeRules: recipe.episodeRules!,
      scoreAverage: 8.6,
      scoreRationale: "固定主体参与清晰。",
    };
    const wrapper = mount(RecipeInspectorPanel, {
      props: { recipe: { ...recipe, storyCandidates: [candidate] }, expanded: false },
    });

    expect(wrapper.text()).toContain("叶子上的雨滴");
    await wrapper.get("section.story-candidates button").trigger("click");
    expect(wrapper.emitted("review-story")?.[0]).toEqual([candidate]);
    await wrapper.findAll("header button")[0].trigger("click");
    expect(wrapper.emitted("toggle-expanded")).toHaveLength(1);
  });
});
