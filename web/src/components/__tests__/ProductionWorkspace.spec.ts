import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProductionWorkspace from "../production/ProductionWorkspace.vue";

const calls = vi.hoisted(() => ({ state: vi.fn(), shots: vi.fn(), diagnostics: vi.fn(), assets: vi.fn(), fitView: vi.fn() }));
vi.mock("../../api/client", () => ({ creatorApi: { state: calls.state, shots: calls.shots, diagnostics: calls.diagnostics, assets: calls.assets } }));
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
vi.mock("@vue-flow/background", () => ({ Background: defineComponent({ template: "<div />" }) }));
vi.mock("@vue-flow/core", () => ({
  VueFlow: defineComponent({ name: "VueFlow", props: ["nodes", "edges"], emits: ["init", "nodeClick", "nodeDoubleClick", "nodeDragStop", "paneClick", "update:nodes", "update:edges"], template: "<div data-testid='flow'><div v-for='node in nodes' :key='node.id' class='test-node'>{{ node.data.artifact.title }}</div><slot /></div>" }),
  useVueFlow: () => ({ fitView: calls.fitView, getNodes: { value: [] } }), MarkerType: { ArrowClosed: "arrow" },
}));

const state = { projectId: "project-1", version: 1, briefBody: "brief", storyCandidates: [], currentStory: { title: "纸星星", body: "完整故事" }, targetDurationSeconds: 8, aspectRatio: "9:16", qualityTier: "quick", referenceBindings: [
  { assetId: "child", role: "child_identity", providerEligible: true, title: "儿童", instruction: "" },
  { assetId: "cat", role: "cat_identity", providerEligible: true, title: "猫咪", instruction: "" },
  { assetId: "style", role: "style_board", providerEligible: true, title: "画风", instruction: "" },
] };

describe("ProductionWorkspace derived navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks(); localStorage.clear();
    calls.state.mockResolvedValue(state);
    calls.shots.mockResolvedValue([{ id: "shot-1", projectId: "project-1", sortOrder: 1, version: 1, title: "窗边镜头", direction: "孩子与猫咪完成连续动作", durationSeconds: 8, referenceBindings: [], selectedVideoAssetId: "video-1" }]);
    calls.diagnostics.mockResolvedValue({ projectId: "project-1", items: [], tasks: [] });
    calls.assets.mockResolvedValue([{ id: "video-1", role: "creator_video_candidate", semanticKey: "v1", status: "approved", mediaType: "video", contentUrl: "/video", creatorShotId: "shot-1", metadata: {} }]);
  });

  it("derives current facts without a Canvas graph", async () => {
    const wrapper = mount(ProductionWorkspace, { props: { projectId: "project-1" }, global: { stubs: { CreatorShotEditor: true, VideoWorkbenchOverlay: true } } });
    await flushPromises();
    const text = wrapper.get("[data-testid='flow']").text();
    expect(text).toContain("当前故事"); expect(text).toContain("Canon 与参考"); expect(text).toContain("窗边镜头"); expect(text).toContain("视频版本"); expect(text).toContain("时间线与成片");
    expect(wrapper.findAll("[data-testid='flow'] .test-node")).toHaveLength(5);
  });

  it("reads only Creator facts and media", async () => {
    mount(ProductionWorkspace, { props: { projectId: "project-1" }, global: { stubs: { CreatorShotEditor: true, VideoWorkbenchOverlay: true } } });
    await flushPromises();
    expect(calls.state).toHaveBeenCalledOnce(); expect(calls.shots).toHaveBeenCalledOnce(); expect(calls.diagnostics).toHaveBeenCalledOnce(); expect(calls.assets).toHaveBeenCalledOnce();
  });
});
