import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CanvasNodeLibrary from "../canvas/CanvasNodeLibrary.vue";

describe("CanvasNodeLibrary", () => {
  it("groups production, media and resource nodes and emits the selected type", async () => {
    const wrapper = mount(CanvasNodeLibrary);

    expect(wrapper.text()).toContain("创作流程");
    expect(wrapper.text()).toContain("媒体工具");
    expect(wrapper.text()).toContain("资源");
    const prompt = wrapper.findAll("button").find((item) => item.text().includes("Prompt"));
    await prompt!.trigger("click");

    expect(wrapper.emitted("create-node")?.[0]).toEqual(["PromptArtifactNode"]);
  });

  it("opens asset history as a resource action instead of a fake graph node", async () => {
    const wrapper = mount(CanvasNodeLibrary);
    const history = wrapper.findAll("button").find((item) => item.text().includes("素材历史"));

    await history!.trigger("click");

    expect(wrapper.emitted("open-asset-history")).toHaveLength(1);
  });
});
