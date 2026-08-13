import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import SceneLookWorkbench from "../SceneLookWorkbench.vue";
import type {
  AssetDto,
  SceneDto,
  SceneLookDraftEnvelope,
  SceneLookPromptPreview,
  VisualProfileRevisionDto,
} from "../../api/types";

const calls = vi.hoisted(() => ({
  visualProfile: vi.fn(),
  sceneLookDraft: vi.fn(),
  sceneLookVersions: vi.fn(),
  health: vi.fn(),
  saveSceneLookDraft: vi.fn(),
  previewSceneLookPrompt: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  assetContentUrl: (id: string) => `/api/v1/assets/${id}/content`,
  api: {
    ...calls,
    updateVisualProfile: vi.fn(),
    generateSceneLook: vi.fn(),
    job: vi.fn(),
    reviewAsset: vi.fn(),
    selectSceneLook: vi.fn(),
  },
}));

const asset: AssetDto = {
  id: "10000000-0000-0000-0000-000000000001",
  role: "canon_reference",
  mediaType: "image",
  scope: "canon",
  status: "approved",
  sha256: "1".repeat(64),
  semanticKey: "person:headshot",
  metadata: {},
  contentReady: true,
  displayName: "人物大头照",
  referencePurpose: "person_identity",
};

const lookPlan = {
  personWardrobe: "浅色采茶服",
  personAccessories: "",
  catAppearance: "保持灰白纹路",
  keyProps: "竹篮",
  environmentStyle: "outdoor" as const,
  personPose: "站立",
  catPose: "四足站立",
  composition: "人猫并排",
  additionalInstructions: "",
  imageRecommended: true,
  recommendationReason: "换装需要确认",
};

const profile: VisualProfileRevisionDto = {
  id: "20000000-0000-0000-0000-000000000001",
  projectId: "30000000-0000-0000-0000-000000000001",
  revision: 1,
  profileHash: "2".repeat(64),
  sourceProfileId: "canon-v1",
  personIdentity: "同一个五至七岁东亚儿童，保持脸型和五官",
  personHair: "保持短波波头和轻薄刘海",
  personBody: "保持儿童年龄感和头身比例",
  catIdentity: "同一只灰白虎斑猫，保持眼睛、尾巴和体型",
  stylePositive: ["二维", "水彩", "柔光"],
  styleNegative: ["真人", "三维"],
  referenceBindings: [{
    assetId: asset.id,
    purpose: "person_identity",
    instruction: "锁定人物脸部",
  }],
  referenceSnapshot: [],
};

const envelope: SceneLookDraftEnvelope = {
  sceneId: "40000000-0000-0000-0000-000000000001",
  revision: 1,
  draft: {
    visualProfileRevisionId: profile.id,
    lookPlan,
    referenceBindings: profile.referenceBindings,
  },
};

const scene: SceneDto = {
  id: envelope.sceneId,
  order: 1,
  title: "采茶",
  sourceText: "孩子和猫咪准备采茶。",
  storyMode: "single",
  targetShotCount: 1,
  lookPlan,
  lookDraftRevision: 1,
  status: "ready",
  attempts: [],
  shots: [],
};

const promptPreview: SceneLookPromptPreview = {
  prompt: "【人物身份锁定】保持同一个人。",
  charCount: 16,
  utf8Bytes: 48,
  referenceCount: 1,
  references: [{
    index: 1,
    assetId: asset.id,
    sha256: asset.sha256,
    semanticKey: asset.semanticKey,
    purpose: "person_identity",
    instruction: "锁定人物脸部",
    contentReady: true,
  }],
  warnings: ["至少选择一张猫咪身份参考"],
  visualProfileRevisionId: profile.id,
  visualProfileRevision: 1,
  draftRevision: 1,
};

describe("SceneLookWorkbench", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.clearAllMocks();
  });

  it("loads the editable draft and compiles a no-Ark prompt preview", async () => {
    calls.visualProfile.mockResolvedValue(profile);
    calls.sceneLookDraft.mockResolvedValue(envelope);
    calls.sceneLookVersions.mockResolvedValue([]);
    calls.health.mockResolvedValue({ arkImageModel: "fake-seedream" });
    calls.saveSceneLookDraft.mockResolvedValue(envelope);
    calls.previewSceneLookPrompt.mockResolvedValue(promptPreview);

    const wrapper = mount(SceneLookWorkbench, {
      attachTo: document.body,
      props: { projectId: profile.projectId, scene, assets: [asset] },
      global: { plugins: [ElementPlus] },
    });
    await wrapper.get("button").trigger("click");
    await flushPromises();

    expect(document.body.textContent).toContain("场景视觉基准工作台");
    const fieldValues = [...document.body.querySelectorAll("input, textarea")].map(
      (field) => (field as HTMLInputElement).value,
    );
    expect(fieldValues).toContain("浅色采茶服");

    const previewButton = [...document.body.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("编译预览"),
    );
    expect(previewButton).toBeDefined();
    previewButton!.click();
    await flushPromises();

    expect(calls.saveSceneLookDraft).toHaveBeenCalledWith(
      scene.id,
      1,
      expect.objectContaining({ visualProfileRevisionId: profile.id }),
    );
    expect(calls.previewSceneLookPrompt).toHaveBeenCalledWith(scene.id);
    expect(document.body.textContent).toContain("至少选择一张猫咪身份参考");
    expect(document.body.textContent).toContain("@图片1");
  });
});
