import { shallowMount } from "@vue/test-utils";
import { defineComponent } from "vue";
import { describe, expect, it, vi } from "vitest";

import type { CanvasEdgeDto } from "../../api/types";
import CanvasInteractiveEdge from "../canvas/CanvasInteractiveEdge.vue";

const EdgeLabelRendererStub = defineComponent({
  template: "<div><slot /></div>",
});

function edge(enabled: boolean): CanvasEdgeDto {
  return {
    id: "edge-1",
    sourceNodeId: "source",
    sourceNodeType: "ReferenceAssetNode",
    sourcePort: "media_reference[]",
    targetNodeId: "target",
    targetNodeType: "VideoGenerationNode",
    targetPort: "media_reference[]",
    systemManaged: !enabled,
    availableActions: [{
      key: "disconnect_edge",
      label: "剪断连接",
      enabled,
      disabledReason: enabled ? null : "该连线由 Canon 身份规则派生，不能直接剪断",
    }],
  };
}

function mountEdge(enabled: boolean) {
  const onDisconnect = vi.fn();
  const onUnavailable = vi.fn();
  const wrapper = shallowMount(CanvasInteractiveEdge, {
    props: {
      id: "edge-1",
      source: "source",
      target: "target",
      sourceX: 0,
      sourceY: 0,
      targetX: 200,
      targetY: 120,
      sourcePosition: "right",
      targetPosition: "left",
      selected: false,
      sourceNode: {},
      targetNode: {},
      type: "interactive",
      markerEnd: "",
      markerStart: "",
      events: {},
      data: { edge: edge(enabled), incident: false, onDisconnect, onUnavailable },
    } as never,
    global: { stubs: { EdgeLabelRenderer: EdgeLabelRendererStub } },
  });
  return { wrapper, onDisconnect, onUnavailable };
}

describe("CanvasInteractiveEdge", () => {
  it("renders a wide hit path and disconnects an editable reference", async () => {
    const { wrapper, onDisconnect } = mountEdge(true);

    await wrapper.get(".interactive-edge").trigger("pointerenter");
    expect(wrapper.find(".edge-flow").exists()).toBe(true);
    expect(wrapper.get(".edge-scissors").attributes("aria-disabled")).toBe("false");

    await wrapper.get(".edge-scissors").trigger("click");
    expect(onDisconnect).toHaveBeenCalledTimes(1);
  });

  it("keeps system-managed connections focusable and explains why they are locked", async () => {
    const { wrapper, onDisconnect, onUnavailable } = mountEdge(false);

    await wrapper.get(".edge-hit").trigger("focus");
    const scissors = wrapper.get(".edge-scissors");
    expect(scissors.text()).toContain("已锁定");
    expect(scissors.attributes("title")).toContain("Canon 身份规则");

    await scissors.trigger("click");
    expect(onDisconnect).not.toHaveBeenCalled();
    expect(onUnavailable).toHaveBeenCalledWith(expect.stringContaining("Canon 身份规则"));
  });
});
