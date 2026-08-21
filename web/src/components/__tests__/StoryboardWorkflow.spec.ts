import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { defineComponent } from "vue";

import StoryboardWorkflow, { type StoryboardShotDraft } from "../canvas/StoryboardWorkflow.vue";

const TeleportStub = defineComponent({ template: "<div><slot /></div>" });

function shot(order: number, title: string): StoryboardShotDraft {
  return {
    order,
    durationSeconds: 15,
    title,
    action: `${title}的画面动作`,
    shotSize: "中景",
    lighting: "柔和自然光",
    dialogue: "",
    soundEffect: "雨后环境声",
    camera: "固定机位",
    prompt: "",
  };
}

describe("StoryboardWorkflow", () => {
  it("reorders, compiles and saves a new manually reviewed storyboard revision", async () => {
    const wrapper = mount(StoryboardWorkflow, {
      props: {
        modelValue: true,
        shots: [shot(1, "窗台"), shot(2, "亮叶")],
        healingRecipe: true,
        targetDurationSeconds: 30,
      },
      global: { stubs: { Teleport: TeleportStub } },
    });

    await wrapper.findAll('button[aria-label="下移镜头"]')[0].trigger("click");
    await wrapper.findAll("button").find((button) => button.text().includes("下一步：准备资产"))!.trigger("click");
    expect(wrapper.text()).toContain("儿童身份 · Canon");
    await wrapper.findAll("button").find((button) => button.text().includes("下一步：合成提示词"))!.trigger("click");
    expect(wrapper.text()).toContain("可生成");
    await wrapper.findAll("button").find((button) => button.text().includes("保存为新分镜版本"))!.trigger("click");

    const saved = wrapper.emitted("save")?.[0]?.[0] as StoryboardShotDraft[];
    expect(saved.map((row) => row.title)).toEqual(["亮叶", "窗台"]);
    expect(saved.every((row) => row.prompt.includes("固定机位"))).toBe(true);
  });

  it("blocks healing shots with invalid duration or dialogue", () => {
    const invalid = shot(1, "不合规镜头");
    invalid.durationSeconds = 7;
    invalid.dialogue = "一句对白";
    const wrapper = mount(StoryboardWorkflow, {
      props: {
        modelValue: true,
        shots: [invalid],
        healingRecipe: true,
        targetDurationSeconds: 7,
      },
      global: { stubs: { Teleport: TeleportStub } },
    });

    expect(wrapper.text()).toContain("治愈组合包每镜必须为 8–15 秒");
    expect(wrapper.text()).toContain("治愈组合包禁止对白");
    const next = wrapper.findAll("button").find((button) => button.text().includes("下一步：准备资产"))!;
    expect(next.attributes("disabled")).toBeDefined();
  });
});
