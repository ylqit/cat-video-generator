import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { VisualPresetProfileDto } from "../../api/types";
import VisualPresetLibrary from "../canvas/VisualPresetLibrary.vue";

const preset: VisualPresetProfileDto = {
  key: "healing_child_cat_line_texture_v3",
  canonProfileId: "canon-v3-healing-child-cat-line-texture",
  title: "一人一猫 · 线条材质",
  description: "固定儿童、固定猫咪与统一线条材质",
  version: 3,
  ready: true,
  slots: [
    {
      assetId: "child-headshot",
      semanticKey: "person:headshot",
      title: "儿童面部",
      contentUrl: "/api/v1/assets/child-headshot/content",
      thumbnailUrl: "/api/v1/assets/child-headshot/content",
      approvalStatus: "approved",
      sha256: "a".repeat(64),
      required: true,
      role: "person",
      purpose: "identity",
      instruction: "固定儿童面部身份",
    },
    {
      assetId: "cat-front",
      semanticKey: "cat:front",
      title: "猫咪正面",
      contentUrl: "/api/v1/assets/cat-front/content",
      thumbnailUrl: "/api/v1/assets/cat-front/content",
      approvalStatus: "approved",
      sha256: "b".repeat(64),
      required: true,
      role: "cat",
      purpose: "identity",
      instruction: "固定猫咪正面身份",
    },
    {
      assetId: "line-style",
      semanticKey: "style:line_texture",
      title: "线条材质",
      contentUrl: "/api/v1/assets/line-style/content",
      thumbnailUrl: "/api/v1/assets/line-style/content",
      approvalStatus: "approved",
      sha256: "c".repeat(64),
      required: true,
      role: "style",
      purpose: "style",
      instruction: "只提取线条、材质与光线",
    },
  ],
};

const dialogStub = {
  props: ["modelValue"],
  template: "<section><slot name='header' /><slot /></section>",
};

describe("VisualPresetLibrary", () => {
  it("shows real role evidence and separates the style library", async () => {
    const wrapper = mount(VisualPresetLibrary, {
      props: { modelValue: true, presets: [preset] },
      global: { stubs: { ElDialog: dialogStub } },
    });

    expect(wrapper.text()).toContain("儿童面部");
    expect(wrapper.text()).toContain("猫咪正面");
    expect(wrapper.text()).not.toContain("线条材质style:line_texture");
    expect(wrapper.findAll("img").map((item) => item.attributes("src"))).toEqual([
      "/api/v1/assets/child-headshot/content",
      "/api/v1/assets/cat-front/content",
    ]);

    await wrapper.get(".library-tabs button:nth-child(2)").trigger("click");

    expect(wrapper.text()).toContain("线条材质");
    expect(wrapper.findAll("img")).toHaveLength(1);
    expect(wrapper.get("img").attributes("src")).toBe(
      "/api/v1/assets/line-style/content",
    );
  });

  it("applies the preset without exposing a binary-copy action", async () => {
    const wrapper = mount(VisualPresetLibrary, {
      props: { modelValue: true, presets: [preset] },
      global: { stubs: { ElDialog: dialogStub } },
    });

    await wrapper.get(".preset-card footer button").trigger("click");

    expect(wrapper.emitted("apply")?.[0]).toEqual([
      "healing_child_cat_line_texture_v3",
    ]);
    expect(wrapper.text()).toContain("不复制二进制资产");
  });
});
