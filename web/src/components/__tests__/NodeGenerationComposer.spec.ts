import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import NodeGenerationComposer from "../canvas/NodeGenerationComposer.vue";

describe("NodeGenerationComposer", () => {
  it("renders provider capabilities and keeps submit disabled without a prompt", () => {
    const wrapper = mount(NodeGenerationComposer, {
      props: {
        nodeRevision: 1,
        capabilities: {
          provider: "ark",
          model: "seedance-2",
          modes: ["text_to_video", "image_to_video"],
          aspectRatios: ["16:9", "9:16"],
          resolutions: ["720p"],
          durations: [5, 10],
          candidateCounts: [1],
          audio: true,
        },
        actualReferences: [],
      },
    });

    expect(wrapper.text()).toContain("720p");
    expect(wrapper.get("button[data-action='generate']").attributes("disabled")).toBeDefined();
  });

  it("shows omitted references and queues only after an explicit click", async () => {
    const wrapper = mount(NodeGenerationComposer, {
      props: {
        nodeRevision: 1,
        capabilities: {
          provider: "ark",
          model: "seedance-2",
          modes: ["image_to_video"],
          aspectRatios: ["9:16"],
          resolutions: ["720p"],
          durations: [5],
          candidateCounts: [1],
          audio: true,
        },
        actualReferences: [
          {
            assetId: "asset-2",
            semanticRole: "co_protagonist",
            providerIncluded: false,
            omissionReason: "供应商本模式最多接受一张主体参考图",
          },
        ],
      },
    });

    expect(wrapper.text()).toContain("未进入供应商请求");
    expect(wrapper.text()).toContain("最多接受一张主体参考图");
    await wrapper.get("textarea").setValue("两位主体在雨前一起收画");
    await wrapper.get("button[data-action='generate']").trigger("click");

    expect(wrapper.emitted("generate")).toHaveLength(1);
  });
});
