import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";

import StoryboardWorkflow, { type StoryboardShotDraft } from "../canvas/StoryboardWorkflow.vue";
import type { ProjectGraph, SceneAssetReadinessDto, SceneDto } from "../../api/types";

const calls = vi.hoisted(() => ({
  project: vi.fn(),
  sceneVisualAssets: vi.fn(),
  compileStoryboardPrompts: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: calls, canvasApi: calls }));

const TeleportStub = defineComponent({ template: "<div><slot /></div>" });
const projectId = "10000000-0000-0000-0000-000000000001";
const sceneId = "20000000-0000-0000-0000-000000000001";
const storyRevisionId = "30000000-0000-0000-0000-000000000001";
const visualProfileRevisionId = "40000000-0000-0000-0000-000000000001";

const scene: SceneDto = {
  id: sceneId,
  order: 1,
  title: "雨后小院",
  sourceText: "孩子和猫咪在雨后小院发现亮叶。",
  storyMode: "single",
  targetShotCount: 2,
  lookDraftRevision: 1,
  status: "ready",
  attempts: [],
  shots: [],
};

const graph: ProjectGraph = {
  project: {
    id: projectId,
    title: "雨后亮叶",
    contentDate: "2026-08-23",
    status: "active",
    contractVersion: 2,
    defaultReferenceBindings: [],
  },
  assets: [],
  scenes: [scene],
  sequences: [],
};

const readyScene: SceneAssetReadinessDto = {
  requiredSlots: [
    { key: "wardrobe", displayName: "本集服饰与配件", purpose: "wardrobe", required: true, assetIds: ["wardrobe-1"], status: "ready" },
    { key: "environment", displayName: "当前场景环境", purpose: "environment", required: true, assetIds: ["environment-1"], status: "ready" },
  ],
  boundAssetIds: ["wardrobe-1", "environment-1", "look-1"],
  missingAssetKeys: [],
  staleAssetKeys: [],
  sceneLookStatus: "approved",
  canCompileShotPrompt: true,
  blockers: [],
};

function shot(order: number, title: string): StoryboardShotDraft {
  return {
    order,
    durationSeconds: 15,
    title,
    action: `${title}的画面动作`,
    shotSize: "中景",
    lighting: "柔和自然光",
    dialogue: "",
    soundEffect: "雨后环境声",
    camera: "固定机位",
    prompt: "",
    sceneId,
    sceneTitle: scene.title,
  };
}

describe("StoryboardWorkflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.project.mockResolvedValue(graph);
    calls.sceneVisualAssets.mockResolvedValue({ readiness: readyScene });
    calls.compileStoryboardPrompts.mockImplementation((_projectId: string, payload: { shots: Array<Record<string, unknown>> }) => Promise.resolve({
      projectId,
      storyRevisionId,
      visualProfileRevisionId,
      status: "compiled",
      shots: payload.shots.map((row, index) => ({
        beatId: row.beatId,
        order: index + 1,
        finalPrompt: `服务端分层提示词：${row.action}；${row.camera}`,
        promptId: `50000000-0000-0000-0000-00000000000${index + 1}`,
        referenceBindings: [
          { assetId: "child", role: "identity", purpose: "person", source: "canon", sha256: "child-sha" },
          { assetId: "scene", role: "environment", purpose: "scene_look", source: "scene", sha256: "scene-sha" },
        ],
        warnings: [],
        blockers: [],
        estimatedCost: { currency: "CNY", amountMicros: 0 },
        inputHash: "a".repeat(64),
      })),
    }));
  });

  it("reorders, compiles and saves a new manually reviewed storyboard revision", async () => {
    const wrapper = mount(StoryboardWorkflow, {
      props: {
        modelValue: true,
        shots: [shot(1, "窗台"), shot(2, "亮叶")],
        healingRecipe: true,
        targetDurationSeconds: 30,
        projectId,
        storyRevisionId,
        visualProfileRevisionId,
      },
      global: { stubs: { Teleport: TeleportStub } },
    });

    await wrapper.findAll('button[aria-label="下移镜头"]')[0].trigger("click");
    await wrapper.findAll("button").find((button) => button.text().includes("下一步：准备资产"))!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("儿童身份 · Canon");
    await wrapper.findAll("button").find((button) => button.text().includes("下一步：合成提示词"))!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("可进入分镜审核");
    expect(wrapper.text()).toContain("identity · person");
    await wrapper.findAll("button").find((button) => button.text().includes("保存为新分镜版本"))!.trigger("click");

    const saved = wrapper.emitted("save")?.[0]?.[0] as StoryboardShotDraft[];
    expect(saved.map((row) => row.title)).toEqual(["亮叶", "窗台"]);
    expect(saved.every((row) => row.prompt.includes("服务端分层提示词"))).toBe(true);
    expect(saved.every((row) => Boolean(row.promptId && row.promptInputHash))).toBe(true);
    expect(calls.compileStoryboardPrompts).toHaveBeenCalledOnce();
  });

  it("blocks prompt compilation until every scene asset and Scene Look is ready", async () => {
    calls.sceneVisualAssets.mockResolvedValue({
      readiness: {
        ...readyScene,
        requiredSlots: readyScene.requiredSlots.map((slot) => (
          slot.key === "environment" ? { ...slot, assetIds: [], status: "missing" as const } : slot
        )),
        missingAssetKeys: ["environment"],
        sceneLookStatus: "missing",
        canCompileShotPrompt: false,
        blockers: ["缺少已批准并绑定的场景资产：当前场景环境", "尚未选择已批准的场景视觉基准（Scene Look）"],
      },
    });
    const wrapper = mount(StoryboardWorkflow, {
      props: {
        modelValue: true,
        shots: [shot(1, "窗台")],
        healingRecipe: true,
        targetDurationSeconds: 15,
        projectId,
        storyRevisionId,
        visualProfileRevisionId,
      },
      global: { stubs: { Teleport: TeleportStub } },
    });

    await wrapper.findAll("button").find((button) => button.text().includes("下一步：准备资产"))!.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("缺少已批准并绑定的场景资产：当前场景环境");
    const compile = wrapper.findAll("button").find((button) => button.text().includes("下一步：合成提示词"))!;
    expect(compile.attributes("disabled")).toBeDefined();
    await wrapper.findAll("button").find((button) => button.text().includes("准备该场景资产"))!.trigger("click");
    expect(wrapper.emitted("openScene")?.[0]).toEqual([sceneId]);
  });

  it("blocks healing shots with invalid duration or dialogue", () => {
    const invalid = shot(1, "不合规镜头");
    invalid.durationSeconds = 7;
    invalid.dialogue = "一句对白";
    const wrapper = mount(StoryboardWorkflow, {
      props: {
        modelValue: true,
        shots: [invalid],
        healingRecipe: true,
        targetDurationSeconds: 7,
      },
      global: { stubs: { Teleport: TeleportStub } },
    });

    expect(wrapper.text()).toContain("治愈组合包每镜必须为 8–15 秒");
    expect(wrapper.text()).toContain("治愈组合包禁止对白");
    const next = wrapper.findAll("button").find((button) => button.text().includes("下一步：准备资产"))!;
    expect(next.attributes("disabled")).toBeDefined();
  });
});
