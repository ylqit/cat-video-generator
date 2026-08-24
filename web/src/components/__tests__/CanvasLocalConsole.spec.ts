import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CanvasLocalConsole from "../canvas/CanvasLocalConsole.vue";

describe("CanvasLocalConsole", () => {
  it("uses a fixed semantic preset without rendering the old selected-node title bar", () => {
    const wrapper = mount(CanvasLocalConsole, {
      props: { title: "创意简报", preset: "text" },
      slots: { default: "简报内容" },
    });

    expect(wrapper.classes()).toContain("preset-text");
    expect(wrapper.text()).toBe("简报内容");
    expect(wrapper.find("header").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("SELECTED NODE");
  });

  it("keeps fullscreen as a real optional shell action", async () => {
    const wrapper = mount(CanvasLocalConsole, {
      props: { title: "分镜导演", preset: "storyboard", fullscreenAvailable: true },
    });

    await wrapper.get('button[aria-label="全屏打开"]').trigger("click");
    expect(wrapper.emitted("fullscreen")).toHaveLength(1);
  });

  it("offers a dedicated close action", async () => {
    const wrapper = mount(CanvasLocalConsole, {
      props: { title: "故事候选", preset: "text" },
    });

    await wrapper.get('button[aria-label="关闭局部控制台"]').trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });
});
