import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { CanvasNodeDto } from "../../api/types";
import BriefNodeConsole from "../canvas/BriefNodeConsole.vue";

const node: CanvasNodeDto = {
  id: "brief-1",
  type: "BriefNode",
  objectType: "story_brief",
  objectId: "brief-1",
  revision: 3,
  status: "draft",
  position: { x: 0, y: 0 },
  data: {
    title: "创意简报",
    theme: "孩子和猫咪在雨后发现一片发亮的叶子",
    targetDurationSeconds: 15,
    aspectRatio: "9:16",
    audience: "亲子与泛生活观众",
    tone: "温暖、安静",
  },
  blocker: "提交前确认",
  availableActions: [
    { key: "complete_creative", label: "AI 补全创意", enabled: true, execution: "provider" },
  ],
};

describe("BriefNodeConsole", () => {
  it("shows an editable compact brief instead of raw node type fallback content", () => {
    const wrapper = mount(BriefNodeConsole, { props: { node } });

    expect((wrapper.get('textarea[aria-label="一句话创意"]').element as HTMLTextAreaElement).value).toContain("雨后发现");
    expect((wrapper.get('input[aria-label="总时长"]').element as HTMLInputElement).value).toBe("15");
    expect(wrapper.text()).toContain("9:16");
    expect(wrapper.text()).toContain("提交前确认");
    expect(wrapper.text()).not.toContain("BriefNode / story_brief");
  });

  it("saves a changed brief and dispatches only a declared action", async () => {
    const wrapper = mount(BriefNodeConsole, { props: { node } });
    await wrapper.get('textarea[aria-label="一句话创意"]').setValue("孩子和猫咪一起收好雨伞");
    await wrapper.get('input[aria-label="总时长"]').setValue(30);
    await wrapper.get("button.primary").trigger("click");

    expect(wrapper.emitted("save")?.[0]).toEqual([{
      theme: "孩子和猫咪一起收好雨伞",
      targetDurationSeconds: 30,
      aspectRatio: "9:16",
    }]);

    await wrapper.get('button[title="AI 补全创意"]').trigger("click");
    expect(wrapper.emitted("action")?.[0]?.[0]).toMatchObject({ key: "complete_creative" });
  });

  it("shows persistent execution progress inside the node console", () => {
    const wrapper = mount(BriefNodeConsole, {
      props: {
        node,
        executions: [{
          key: "step-1",
          kind: "director",
          label: "补全创意输入",
          status: "running",
          progress: { percent: 45, message: "正在整理结构化简报" },
          updatedAt: "2026-08-21T00:00:00Z",
          source: "workflow",
        }],
      },
    });

    expect(wrapper.text()).toContain("正在整理结构化简报");
    expect(wrapper.text()).toContain("45%");
  });
});
