import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CanvasContextToolbar from "../canvas/CanvasContextToolbar.vue";

describe("CanvasContextToolbar", () => {
  const node = {
    id: "video-1",
    type: "VideoAssetNode" as const,
    objectType: "asset",
    objectId: "asset-1",
    position: { x: 0, y: 0 },
    data: { title: "成品视频" },
  };

  it("shows the complete video action row and explains unavailable actions", async () => {
    const wrapper = mount(CanvasContextToolbar, { props: { node } });

    for (const label of ["编辑", "片段重拍", "裁剪", "高清", "逐帧拉片", "智能续写", "智能去字幕", "音频分离", "画面编辑", "下载", "全屏"]) {
      expect(wrapper.text()).toContain(label);
    }
    const upscale = wrapper.get('[data-action="upscale"]');
    expect(upscale.attributes("aria-disabled")).toBe("true");
    await upscale.trigger("click");
    expect(wrapper.text()).toContain("尚未配置高清执行器");
    expect(wrapper.emitted("action")).toBeUndefined();

    await wrapper.get('[data-action="segment_reshoot"]').trigger("click");
    expect(wrapper.emitted("action")?.[0]?.[0]).toMatchObject({ key: "segment_reshoot" });
  });
});
