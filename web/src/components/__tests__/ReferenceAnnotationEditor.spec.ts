import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import ReferenceAnnotationEditor from "../canvas/ReferenceAnnotationEditor.vue";

describe("ReferenceAnnotationEditor", () => {
  it("stores drag coordinates normalized to the original reference image", async () => {
    const wrapper = mount(ReferenceAnnotationEditor, {
      props: { assetId: "asset-1", imageUrl: "/assets/asset-1" },
    });
    const stage = wrapper.get('[data-role="annotation-stage"]');
    vi.spyOn(stage.element, "getBoundingClientRect").mockReturnValue({
      x: 10,
      y: 20,
      left: 10,
      top: 20,
      right: 210,
      bottom: 120,
      width: 200,
      height: 100,
      toJSON: () => ({}),
    });

    await stage.trigger("pointerdown", { clientX: 30, clientY: 30 });
    await stage.trigger("pointermove", { clientX: 110, clientY: 70 });
    await stage.trigger("pointerup", { clientX: 110, clientY: 70 });
    await wrapper.get('input[aria-label="标注说明"]').setValue("保持标签文字");
    await wrapper.get('button[data-action="save-annotation"]').trigger("click");

    expect(wrapper.emitted("save")?.[0]?.[0]).toEqual({
      assetId: "asset-1",
      tool: "rectangle",
      points: [{ x: 0.1, y: 0.1 }, { x: 0.5, y: 0.5 }],
      label: "保持标签文字",
    });
  });
});
