import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VideoWorkbenchOverlay from "../production/VideoWorkbenchOverlay.vue";

const calls = vi.hoisted(() => ({ state: vi.fn(), shots: vi.fn(), diagnostics: vi.fn(), assets: vi.fn(), timeline: vi.fn(), saveTimeline: vi.fn(), snapshot: vi.fn(), submit: vi.fn(), decision: vi.fn(), selectVideo: vi.fn(), confirm: vi.fn() }));
vi.mock("../../api/client", () => ({
  creatorApi: {
    state: calls.state,
    shots: calls.shots,
    diagnostics: calls.diagnostics,
    assets: calls.assets,
    timeline: calls.timeline,
    saveTimeline: calls.saveTimeline,
    createSnapshot: calls.snapshot,
    submitSnapshot: calls.submit,
    decideAsset: calls.decision,
    selectVideo: calls.selectVideo,
    updateShot: vi.fn(),
  },
}));
vi.mock("element-plus", () => ({ ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() }, ElMessageBox: { confirm: calls.confirm } }));

const references = [
  { assetId: "child", role: "child_identity", providerEligible: true, title: "固定儿童", instruction: "" },
  { assetId: "cat", role: "cat_identity", providerEligible: true, title: "固定猫咪", instruction: "" },
  { assetId: "style", role: "style_board", providerEligible: true, title: "净化画风板", instruction: "" },
  { assetId: "leaf", role: "style_source", providerEligible: false, title: "叶片来源", instruction: "" },
];
const creatorState = { projectId: "project-1", version: 1, briefBody: "brief", storyCandidates: [], currentStory: { title: "故事", body: "正文" }, targetDurationSeconds: 8, aspectRatio: "9:16", qualityTier: "quick", referenceBindings: references };
const shot = { id: "shot-1", projectId: "project-1", sortOrder: 1, version: 1, title: "窗边镜头", direction: "儿童发现纸星星，猫咪推回，最后一起贴到玻璃上。", durationSeconds: 8, referenceBindings: [], selectedVideoAssetId: null };

describe("VideoWorkbenchOverlay snapshot boundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.state.mockResolvedValue(creatorState); calls.shots.mockResolvedValue([shot]); calls.diagnostics.mockResolvedValue({ projectId: "project-1", items: [], tasks: [] });
    calls.assets.mockResolvedValue(references.map((item) => ({ id: item.assetId, role: item.role, semanticKey: item.role, status: "approved", mediaType: "image", contentUrl: `/asset/${item.assetId}`, metadata: { providerEligible: item.providerEligible } })));
    calls.timeline.mockResolvedValue({ projectId: "project-1", version: 0, clips: [], status: "draft" });
    calls.snapshot.mockResolvedValue({ id: "snapshot-1", projectId: "project-1", creatorShotId: "shot-1", kind: "video", promptText: "prompt", orderedReferences: references.slice(0, 3), providerConfig: {}, inputHash: "a".repeat(64), estimatedCostMicros: 5000 });
    calls.confirm.mockResolvedValue("confirm"); calls.submit.mockResolvedValue({ taskId: "task-1", status: "local_queued" });
  });

  it("shows exact Provider references and excludes style_source", async () => {
    const wrapper = mount(VideoWorkbenchOverlay, { props: { projectId: "project-1", tab: "generate" } });
    await flushPromises();
    expect(wrapper.text()).toContain("固定儿童"); expect(wrapper.text()).toContain("固定猫咪"); expect(wrapper.text()).toContain("净化画风板"); expect(wrapper.text()).not.toContain("叶片来源");
    expect(wrapper.get("[aria-label='视频生成 Prompt']").element).toHaveProperty("value", expect.stringContaining("身份连续性"));
  });

  it("previews a snapshot before task creation and respects cancellation", async () => {
    calls.confirm.mockRejectedValue("cancel");
    const wrapper = mount(VideoWorkbenchOverlay, { props: { projectId: "project-1", tab: "generate" } });
    await flushPromises();
    await wrapper.get(".result-panel>.primary").trigger("click"); await flushPromises();
    expect(calls.snapshot).toHaveBeenCalledOnce(); expect(calls.submit).not.toHaveBeenCalled();
    await wrapper.get(".snapshot button").trigger("click"); await flushPromises();
    expect(calls.confirm).toHaveBeenCalled(); expect(calls.submit).not.toHaveBeenCalled();
  });
});
