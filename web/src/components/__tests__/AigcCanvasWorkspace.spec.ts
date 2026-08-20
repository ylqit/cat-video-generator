import { flushPromises, shallowMount } from "@vue/test-utils";
import { ElMessageBox } from "element-plus";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AigcCanvasWorkspace from "../canvas/AigcCanvasWorkspace.vue";

const calls = vi.hoisted(() => ({
  canvas: vi.fn(),
  promptRun: vi.fn(),
  approveStory: vi.fn(),
  runStoryStrategies: vi.fn(),
  createStoryboard: vi.fn(),
  saveLayout: vi.fn(),
  saveBrief: vi.fn(),
  createSubject: vi.fn(),
  updateBeat: vi.fn(),
}));

vi.mock("../../api/client", () => ({ canvasApi: calls }));

function canvasWithSubjects(count: number) {
  return {
    projectId: "project-1",
    canvasV2Enabled: true,
    layoutVersion: 0,
    syncStatus: "saved",
    viewport: { x: 0, y: 0, zoom: 1 },
    edges: [],
    nodes: [
      {
        id: "brief-1",
        type: "BriefNode",
        objectType: "story_brief",
        objectId: "brief-1",
        position: { x: 20, y: 20 },
        data: { title: "创意简报", theme: "雨前收画", targetDurationSeconds: 60 },
      },
      ...Array.from({ length: count }, (_, index) => ({
        id: `subject-${index}`,
        type: "SubjectNode",
        objectType: "subject",
        objectId: `subject-${index}`,
        position: { x: 20, y: 220 + index * 140 },
        data: {
          title: `主体${index + 1}`,
          role: index === 0 ? "protagonist" : "co_protagonist",
        },
      })),
    ],
  };
}

describe("AigcCanvasWorkspace", () => {
  beforeEach(() => vi.clearAllMocks());

  it("blocks three-story generation until two narrative subjects exist", async () => {
    calls.canvas.mockResolvedValue(canvasWithSubjects(1));
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VueFlow: true, Background: true, PromptTraceDrawer: true } },
    });
    await flushPromises();

    const generate = wrapper.findAll("button").find(
      (button) => button.text().includes("生成三案"),
    );
    expect(generate?.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("还需 1 个叙事主体");
  });

  it("enables explicit story generation with two subjects and never auto-generates media", async () => {
    calls.canvas.mockResolvedValue(canvasWithSubjects(2));
    calls.runStoryStrategies.mockResolvedValue({ status: "succeeded", candidates: [] });
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({ action: "confirm" } as never);
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VueFlow: true, Background: true, PromptTraceDrawer: true } },
    });
    await flushPromises();

    const generate = wrapper.findAll("button").find(
      (button) => button.text().includes("生成三案"),
    );
    expect(generate?.attributes("disabled")).toBeUndefined();
    await generate!.trigger("click");
    await flushPromises();

    expect(calls.runStoryStrategies).toHaveBeenCalledOnce();
    expect(calls.createStoryboard).not.toHaveBeenCalled();
  });
});
