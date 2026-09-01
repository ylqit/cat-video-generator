import { flushPromises, mount } from "@vue/test-utils";
import { reactive } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CreatorReferenceDto, CreatorStateDto } from "../../api/types";
import AssetWorkspace from "../director/AssetWorkspace.vue";

const calls = vi.hoisted(() => ({ state: vi.fn(), updateState: vi.fn(), assets: vi.fn() }));
vi.mock("../../api/client", () => ({
  creatorApi: {
    state: calls.state,
    updateState: calls.updateState,
    assets: calls.assets,
  },
}));
vi.mock("element-plus", () => ({ ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));

const bindings: CreatorReferenceDto[] = [
  { assetId: "child", role: "child_identity", providerEligible: true, title: "固定儿童", instruction: "" },
  { assetId: "cat", role: "cat_identity", providerEligible: true, title: "固定猫咪", instruction: "" },
  { assetId: "style", role: "style_board", providerEligible: true, title: "净化画风板", instruction: "" },
  { assetId: "leaf", role: "style_source", providerEligible: false, title: "叶片材质来源", instruction: "" },
];
const creatorState: CreatorStateDto = {
  projectId: "project-1",
  projectTitle: "纸星星",
  contentDate: "2026-09-01",
  version: 2,
  briefBody: "brief",
  storyCandidates: [],
  currentStory: {},
  targetDurationSeconds: 8,
  aspectRatio: "9:16",
  qualityTier: "quick",
  referenceBindings: bindings,
  updatedAt: "2026-09-01T00:00:00Z",
};
const assetRows = bindings.map((binding) => ({ id: binding.assetId, role: binding.role, semanticKey: binding.role, status: "approved", mediaType: "image", contentUrl: `/api/v2/media-assets/${binding.assetId}/content`, sha256: "a".repeat(64), metadata: { providerEligible: binding.providerEligible } }));

describe("AssetWorkspace creator references", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.state.mockResolvedValue(creatorState);
    calls.assets.mockResolvedValue([...assetRows, { id: "environment", role: "environment", semanticKey: "environment", status: "approved", mediaType: "image", contentUrl: "/api/v2/media-assets/environment/content", sha256: "b".repeat(64), metadata: { providerEligible: true } }]);
    calls.updateState.mockImplementation(async (_id, _version, payload) => ({ ...creatorState, version: 3, referenceBindings: payload.referenceBindings }));
  });

  it("shows fixed authorities and keeps style_source Provider-ineligible", async () => {
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    expect(calls.state).toHaveBeenCalledWith("project-1", expect.any(AbortSignal));
    expect(wrapper.text()).toContain("固定儿童");
    expect(wrapper.text()).toContain("固定猫咪");
    expect(wrapper.text()).toContain("净化画风板");
    expect(wrapper.text()).toContain("叶片材质来源");
    expect(wrapper.text()).toContain("不可提交 Provider");
  });

  it("accepts the parent workspace's reactive initial state", async () => {
    const wrapper = mount(AssetWorkspace, {
      props: { projectId: "project-1", initialState: reactive(creatorState) },
    });
    await flushPromises();
    expect(calls.state).not.toHaveBeenCalled();
    expect(wrapper.findAll(".grid article")).toHaveLength(4);
    expect(wrapper.text()).not.toContain("could not be cloned");
  });

  it("adds and saves an optional environment reference", async () => {
    const wrapper = mount(AssetWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='选择项目素材']").setValue("environment");
    await wrapper.get("[aria-label='参考职责']").setValue("environment");
    const buttons = wrapper.findAll(".board-actions button");
    await buttons[0].trigger("click");
    await buttons[1].trigger("click");
    await flushPromises();
    expect(calls.updateState).toHaveBeenCalledWith("project-1", 2, expect.objectContaining({ referenceBindings: expect.arrayContaining([expect.objectContaining({ assetId: "environment", role: "environment" })]) }));
  });
});
