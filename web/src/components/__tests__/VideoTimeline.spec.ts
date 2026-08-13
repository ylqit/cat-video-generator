import ElementPlus from "element-plus";
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import VideoTimeline from "../VideoTimeline.vue";

vi.mock("../../api/client", () => ({
  api: {},
}));

describe("VideoTimeline", () => {
  it("maps numbered subshots to AI boundaries and prefills the selected repair", async () => {
    const wrapper = mount(VideoTimeline, {
      attachTo: document.body,
      global: { plugins: [ElementPlus] },
      props: {
        shotId: "shot-1",
        assetId: "asset-1",
        src: "/video.mp4",
        durationMs: 10_000,
        direction: "1. 中景，小孩打开柜门。\n2. 近景，拿出装备。\n3. 中景，猫咪观察并稳定收尾。",
        markersMs: [0, 3_000, 7_000, 10_000],
      },
    });

    expect(wrapper.text()).toContain("子镜头 2 · 3.00–7.00s");
    const buttons = wrapper.findAll(".subshot-list button");
    await buttons[1].trigger("click");

    const textarea = wrapper.find("textarea");
    expect((textarea.element as HTMLTextAreaElement).value).toContain("只重拍子镜头2");
    wrapper.unmount();
  });

  it("labels proportional fallback intervals as estimated", () => {
    const wrapper = mount(VideoTimeline, {
      global: { plugins: [ElementPlus] },
      props: {
        shotId: "shot-1",
        assetId: "asset-1",
        src: "/video.mp4",
        durationMs: 9_000,
        direction: "1. 建立。\n2. 动作。\n3. 收尾。",
        markersMs: [],
      },
    });

    expect(wrapper.text()).toContain("按总时长估算，可手工调整");
  });
});
