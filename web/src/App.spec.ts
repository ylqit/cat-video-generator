import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App.vue";

const calls = vi.hoisted(() => ({ tasks: vi.fn(), push: vi.fn() }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: calls.push }) }));
vi.mock("./api/client", () => ({
  creatorApi: { tasks: calls.tasks, cancellation: vi.fn(), cancelTask: vi.fn() },
}));
vi.mock("element-plus", () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}));

enableAutoUnmount(afterEach);

describe("Creator application shell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    calls.tasks.mockResolvedValue([]);
  });

  afterEach(() => vi.useRealTimers());

  it("keeps one rail for projects, tasks, and settings", async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          RouterView: { template: "<main data-testid='route-view' />" },
          ElDrawer: { template: "<aside><slot /></aside>" },
          ElButton: { template: "<button><slot /></button>" },
          ElAlert: true,
          ElEmpty: true,
          ElTag: { template: "<span><slot /></span>" },
        },
      },
    });
    await flushPromises();
    expect(wrapper.findAll("[aria-label='应用导航']")).toHaveLength(1);
    expect(wrapper.find("[aria-label='项目列表']").exists()).toBe(true);
    expect(wrapper.find("[aria-label^='全局任务']").exists()).toBe(true);
    expect(wrapper.find("[aria-label='设置']").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("旧版");
    expect(wrapper.text()).not.toContain("系统图");
  });
});
