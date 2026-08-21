import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import VideoAssetPanel from "../canvas/VideoAssetPanel.vue";

describe("VideoAssetPanel", () => {
  const node = {
    id: "video-asset-node",
    type: "VideoAssetNode" as const,
    objectType: "asset",
    objectId: "asset-1",
    revision: 2,
    position: { x: 0, y: 0 },
    data: {
      title: "成品视频",
      contentUrl: "/api/v1/assets/asset-1/content",
      durationMs: 5_000,
      availableActions: [
        { key: "edit", label: "编辑", enabled: true, execution: "client" },
        { key: "segment_reshoot", label: "片段重拍", enabled: true, execution: "provider" },
        { key: "download", label: "下载", enabled: true, execution: "client" },
        { key: "fullscreen", label: "全屏", enabled: true, execution: "client" },
        { key: "upscale", label: "高清", enabled: false, execution: "unavailable", disabledReason: "Ark 尚未配置视频高清执行器" },
      ],
    },
  };

  it("offers real asset actions and explains unavailable tools", async () => {
    const wrapper = mount(VideoAssetPanel, { props: { node } });

    expect(wrapper.get("video").attributes("controls")).toBeDefined();
    expect(wrapper.get('[data-action="upscale"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-action="upscale"]').attributes("title")).toContain("高清执行器");

    await wrapper.get('[data-action="segment_reshoot"]').trigger("click");
    await wrapper.get('[data-action="edit"]').trigger("click");
    expect(wrapper.emitted("segment-reshoot")?.[0]).toEqual([node]);
    expect(wrapper.emitted("edit")?.[0]).toEqual([node]);
  });

  it("falls back to an honest core toolbar when the projection predates action capabilities", () => {
    const wrapper = mount(VideoAssetPanel, {
      props: { node: { ...node, data: { ...node.data, availableActions: undefined } } },
    });

    expect(wrapper.get('[data-action="segment_reshoot"]').attributes("disabled")).toBeUndefined();
    expect(wrapper.get('[data-action="crop"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-action="crop"]').attributes("title")).toContain("执行器");
    expect(wrapper.get('[data-action="subtitles"]').attributes("disabled")).toBeDefined();
  });
});
