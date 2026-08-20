import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CanvasTemplateLibrary from "../canvas/CanvasTemplateLibrary.vue";

describe("CanvasTemplateLibrary", () => {
  it("starts a universal project from short drama, product ad or blank canvas", async () => {
    const wrapper = mount(CanvasTemplateLibrary, {
      props: {
        templates: [
          {
            key: "short_drama",
            title: "AIGC 短剧",
            description: "先定故事再生成媒体",
            defaultCandidateCount: 3,
            nodeTypes: [],
          },
          {
            key: "product_ad",
            title: "产品广告",
            description: "包装和模特参考进入候选批次",
            defaultCandidateCount: 4,
            nodeTypes: [],
          },
          {
            key: "blank",
            title: "空白画布",
            description: "从任意素材开始",
            defaultCandidateCount: 4,
            nodeTypes: [],
          },
        ],
      },
    });

    expect(wrapper.text()).toContain("AIGC 媒体工作台");
    expect(wrapper.text()).toContain("默认 4 个图片候选");
    await wrapper.get('[data-template="product_ad"]').trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["product_ad"]);
  });
});
