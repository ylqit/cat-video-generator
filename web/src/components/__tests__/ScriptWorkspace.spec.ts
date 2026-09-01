import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ScriptWorkspace from "../director/ScriptWorkspace.vue";

const calls = vi.hoisted(() => ({
  state: vi.fn(), updateState: vi.fn(), saveStory: vi.fn(), snapshot: vi.fn(), submit: vi.fn(), confirm: vi.fn(),
}));
vi.mock("../../api/client", () => ({ creatorApi: {
  state: calls.state, updateState: calls.updateState, saveStory: calls.saveStory,
  storyCandidateSnapshot: calls.snapshot, submitSnapshot: calls.submit,
} }));
vi.mock("element-plus", () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: calls.confirm },
}));

function creatorState(candidateCount = 2) {
  return {
    projectId: "project-1", version: 3, briefBody: "清晨窗边的一人一猫故事",
    storyCandidates: Array.from({ length: candidateCount }, (_, index) => ({ title: `候选 ${index + 1}`, body: `完整正文 ${index + 1}`, summary: `摘要 ${index + 1}` })),
    currentStory: { title: "当前故事", body: "当前完整正文", summary: "当前摘要" },
    targetDurationSeconds: 8, aspectRatio: "9:16", qualityTier: "quick", referenceBindings: [],
  };
}

describe("ScriptWorkspace creator flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.state.mockResolvedValue(creatorState());
    calls.updateState.mockResolvedValue(creatorState());
    calls.saveStory.mockResolvedValue(creatorState());
    calls.confirm.mockResolvedValue("confirm");
    calls.snapshot.mockResolvedValue({ id: "snapshot-1", projectId: "project-1", creatorShotId: null, kind: "story_text", promptText: "prompt", orderedReferences: [], providerConfig: { provider: "ark", model: "director" }, inputHash: "a".repeat(64), estimatedCostMicros: 1200 });
    calls.submit.mockResolvedValue({ taskId: "task-1", status: "local_queued" });
  });

  it.each([1, 2, 4, 5])("accepts %s complete text candidates without Canvas data", async (count) => {
    calls.state.mockResolvedValue(creatorState(count));
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    expect(calls.state).toHaveBeenCalledWith("project-1", expect.any(AbortSignal));
    expect(wrapper.findAll("[aria-label='故事候选'] button")).toHaveLength(count + 1);
    expect(wrapper.get("[aria-label='完整故事正文']").element).toHaveProperty("value", "当前完整正文");
    expect(wrapper.text()).not.toContain("StoryScore");
  });

  it("saves one long current story with optimistic project version", async () => {
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get("[aria-label='完整故事正文']").setValue("用户直接编辑的长正文");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(calls.saveStory).toHaveBeenCalledWith("project-1", 3, { title: "当前故事", body: "用户直接编辑的长正文", summary: "当前摘要" });
  });

  it("creates no task until the immutable snapshot is explicitly confirmed", async () => {
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".brief-actions .primary").trigger("click");
    await flushPromises();
    expect(calls.snapshot).toHaveBeenCalledOnce();
    expect(calls.confirm).toHaveBeenCalledWith(expect.stringContaining("取消不会创建任务"), "确认故事候选生成", expect.any(Object));
    expect(calls.submit).toHaveBeenCalledWith(expect.objectContaining({ id: "snapshot-1" }), 1200, expect.any(String));
  });

  it("does not submit when confirmation is cancelled", async () => {
    calls.confirm.mockRejectedValue("cancel");
    const wrapper = mount(ScriptWorkspace, { props: { projectId: "project-1" } });
    await flushPromises();
    await wrapper.get(".brief-actions .primary").trigger("click");
    await flushPromises();
    expect(calls.snapshot).toHaveBeenCalledOnce();
    expect(calls.submit).not.toHaveBeenCalled();
  });
});
