import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CanvasNodeLibrary from "../canvas/CanvasNodeLibrary.vue";

describe("CanvasNodeLibrary", () => {
  it("opens a compact LibTV-style category before creating a concrete node", async () => {
    const wrapper = mount(CanvasNodeLibrary);

    expect(wrapper.text()).toContain("文本");
    expect(wrapper.text()).toContain("图片");
    expect(wrapper.text()).toContain("视频");
    expect(wrapper.text()).toContain("智能编辑");
    await wrapper.get('[data-category="text"]').trigger("click");
    expect(wrapper.emitted("create-node")).toBeUndefined();

    const prompt = wrapper.findAll("button").find((item) => item.text().includes("Prompt"));
    await prompt!.trigger("click");

    expect(wrapper.emitted("create-node")?.[0]).toEqual(["PromptArtifactNode"]);
  });

  it("opens asset history as a resource action instead of a fake graph node", async () => {
    const wrapper = mount(CanvasNodeLibrary);
    await wrapper.get('[data-category="assets"]').trigger("click");
    const history = wrapper.findAll("button").find((item) => item.text().includes("素材历史"));

    await history!.trigger("click");

    expect(wrapper.emitted("open-asset-history")).toHaveLength(1);
  });
});
