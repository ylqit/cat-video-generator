import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import StoryboardNodeConsole from "../canvas/StoryboardNodeConsole.vue";

describe("StoryboardNodeConsole", () => {
  it("keeps the three script entries independent", async () => {
    const wrapper = mount(StoryboardNodeConsole, {
      props: {
        approvedStoryAvailable: true,
        selectedReferenceCount: 0,
        healingRecipe: true,
      },
    });

    const buttons = wrapper.findAll("button");
    await buttons.find((button) => button.text().includes("剧本生成分镜脚本"))!.trigger("click");
    await wrapper.get("textarea").setValue("固定机位，收尾更安静");
    await wrapper.get("button.primary").trigger("click");
    expect(wrapper.emitted("run")?.[0]).toEqual([{
      mode: "from_story",
      instruction: "固定机位，收尾更安静",
    }]);

    await wrapper.get("button.back").trigger("click");
    await wrapper.findAll("button").find((button) => button.text().includes("自己编写分镜脚本"))!.trigger("click");
    expect(wrapper.emitted("manual")).toHaveLength(1);
  });

  it("requires both healing Canon subjects before character-driven generation", async () => {
    const wrapper = mount(StoryboardNodeConsole, {
      props: {
        approvedStoryAvailable: true,
        selectedReferenceCount: 1,
        healingRecipe: true,
      },
    });

    await wrapper.findAll("button").find((button) => button.text().includes("基于固定角色补充分镜"))!.trigger("click");
    expect(wrapper.text()).toContain("必须同时选择固定儿童与固定猫咪素材");
    expect(wrapper.get("button.primary").attributes("disabled")).toBeDefined();
    await wrapper.findAll("button").find((button) => button.text().includes("从画布选择角色素材"))!.trigger("click");
    expect(wrapper.emitted("select-references")).toHaveLength(1);

    await wrapper.setProps({ selectedReferenceCount: 2 });
    expect(wrapper.text()).toContain("请填写本集要发生的低压力事件");
    expect(wrapper.get("button.primary").attributes("disabled")).toBeDefined();
    await wrapper.get("textarea").setValue("孩子和猫咪一起整理窗台");
    await wrapper.get("button.primary").trigger("click");
    expect(wrapper.emitted("run")?.[0]).toEqual([{
      mode: "from_characters",
      instruction: "孩子和猫咪一起整理窗台",
    }]);
  });

  it("exposes the pinned storyboard approval without silently approving", async () => {
    const wrapper = mount(StoryboardNodeConsole, {
      props: {
        approvedStoryAvailable: true,
        selectedReferenceCount: 2,
        storyboardReady: true,
        storyboardApproved: false,
      },
    });

    expect(wrapper.text()).toContain("等待人工批准");
    const approve = wrapper.findAll("button").find((button) => button.text().includes("批准当前分镜"));
    await approve!.trigger("click");
    expect(wrapper.emitted("approve")).toHaveLength(1);

    await wrapper.setProps({ storyboardApproved: true });
    expect(wrapper.text()).toContain("当前分镜已人工批准");
    expect(wrapper.findAll("button").find((button) => button.text() === "已批准")!.attributes("disabled")).toBeDefined();
  });
});
