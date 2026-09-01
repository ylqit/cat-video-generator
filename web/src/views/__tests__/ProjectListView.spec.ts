import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProjectListView from "../ProjectListView.vue";

const calls = vi.hoisted(() => ({ projects: vi.fn(), canon: vi.fn(), create: vi.fn(), push: vi.fn() }));
vi.mock("../../api/client", () => ({
  creatorApi: { projects: calls.projects, canon: calls.canon, createProject: calls.create },
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: calls.push }) }));
vi.mock("element-plus", () => ({ ElMessage: { success: vi.fn(), error: vi.fn() } }));

const canon = { ready: true, references: [
  { role: "child_identity", semanticKey: "person:headshot", assetId: "child", title: "固定儿童", instruction: "identity", providerEligible: true },
  { role: "cat_identity", semanticKey: "cat:front", assetId: "cat", title: "固定猫咪", instruction: "identity", providerEligible: true },
  { role: "style_board", semanticKey: "style:healing_line_texture_v4", assetId: "style", title: "净化画风板", instruction: "style", providerEligible: true },
] };

describe("ProjectListView minimal creator project", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    calls.projects.mockResolvedValue([{ id: "existing", title: "已有项目", contentDate: "2026-09-01", status: "active" }]); calls.canon.mockResolvedValue(canon);
    calls.create.mockResolvedValue({ projectId: "new-project", title: "窗边纸星星", version: 1, providerCallCount: 0 });
  });

  it("offers one clear project entry", async () => {
    const wrapper = mount(ProjectListView); await flushPromises();
    await wrapper.get("[data-testid='project-existing'] .open-project").trigger("click");
    expect(calls.push).toHaveBeenCalledWith({ name: "project-production", params: { projectId: "existing" } });
  });

  it("atomically creates project, Brief and fixed Canon without Provider work", async () => {
    const wrapper = mount(ProjectListView, {
      global: {
        stubs: {
          ElDialog: { template: "<div><slot/><slot name='footer'/></div>" },
          ElButton: { template: "<button v-bind='$attrs' @click='$emit(\"click\")'><slot/></button>" },
        },
      },
    });
    await flushPromises();
    await wrapper.get(".new-project-card").trigger("click");
    await wrapper.get("input[placeholder='例如：窗边的纸星星']").setValue("窗边纸星星"); await wrapper.get("textarea").setValue("孩子和猫咪一起把纸星星贴到玻璃上。");
    await flushPromises();
    await wrapper.get(".create-form").trigger("submit");
    await flushPromises();
    expect(calls.create).toHaveBeenCalledWith(expect.objectContaining({ title: "窗边纸星星", references: [
      expect.objectContaining({ assetId: "child", role: "child_identity" }), expect.objectContaining({ assetId: "cat", role: "cat_identity" }), expect.objectContaining({ assetId: "style", role: "style_board" }),
    ] }));
    expect(calls.push).toHaveBeenCalledWith({ name: "project-script", params: { projectId: "new-project" } });
  });
});
