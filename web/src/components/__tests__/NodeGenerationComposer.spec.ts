import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import NodeGenerationComposer from "../canvas/NodeGenerationComposer.vue";

const cameraMotions = [
  { value: "static", label: "固定镜头", enabled: true },
  { value: "follow", label: "跟随拍摄", enabled: true },
  { value: "push_in", label: "缓慢推进", enabled: true },
  { value: "pan_right", label: "镜头右摇", enabled: true },
  { value: "tilt_up", label: "镜头上摇", enabled: true },
  { value: "drone", label: "航拍运镜", enabled: true },
];

describe("NodeGenerationComposer", () => {
  afterEach(() => vi.useRealTimers());
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
          cameraMotions,
        },
        actualReferences: [],
      },
    });

    expect(wrapper.text()).toContain("720p");
    expect(wrapper.get("button[data-action='generate']").attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("请填写生成 Prompt");
    expect(wrapper.get("button[data-action='annotate']").attributes("disabled")).toBeDefined();
    expect(wrapper.get("button[data-action='annotate']").attributes("title")).toContain("参考图");
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
          cameraMotions,
        },
        actualReferences: [
          {
            assetId: "asset-1",
            semanticRole: "protagonist",
            providerIncluded: true,
          },
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

  it("turns reference, subject, annotation and camera controls into explicit actions", async () => {
    const wrapper = mount(NodeGenerationComposer, {
      props: {
        nodeRevision: 2,
        capabilities: {
          provider: "ark",
          model: "seedance-2",
          modes: ["image_to_video"],
          aspectRatios: ["9:16"],
          resolutions: ["480p"],
          durations: [8],
          candidateCounts: [1],
          audio: true,
          cameraMotions,
        },
        actualReferences: [{
          assetId: "asset-1",
          semanticRole: "packshot_front",
          providerIncluded: true,
        }],
        referenceAnnotations: [{
          assetId: "asset-1",
          tool: "rectangle",
          points: [{ x: 0.1, y: 0.1 }, { x: 0.5, y: 0.5 }],
          label: "保持包装标签",
        }],
      },
    });

    await wrapper.get('button[data-action="select-references"]').trigger("click");
    await wrapper.get('button[data-action="open-subject-library"]').trigger("click");
    await wrapper.get('button[data-action="annotate"]').trigger("click");
    await wrapper.get('button[data-action="camera-motion"]').trigger("click");
    await wrapper.get('button[data-camera-motion="push_in"]').trigger("click");
    await wrapper.get("textarea").setValue("保持包装文字，缓慢推近产品");
    await wrapper.get("button[data-action='generate']").trigger("click");

    expect(wrapper.emitted("select-references")).toHaveLength(1);
    expect(wrapper.emitted("open-subject-library")).toHaveLength(1);
    expect(wrapper.emitted("open-annotation")?.[0]).toEqual([["asset-1"]]);
    expect(wrapper.emitted("generate")?.[0]?.[0]).toMatchObject({
      config: {
        cameraMotion: "push_in",
        referenceAnnotations: [expect.objectContaining({ assetId: "asset-1" })],
      },
    });
  });

  it("starts product batches from the node's persisted four-candidate configuration", async () => {
    const wrapper = mount(NodeGenerationComposer, {
      props: {
        nodeRevision: 1,
        capabilities: {
          provider: "ark",
          model: "image-model",
          modes: ["text_to_image"],
          aspectRatios: ["1:1"],
          resolutions: ["1080p"],
          durations: [1],
          candidateCounts: [1, 2, 4],
          audio: false,
        },
        actualReferences: [],
        initialConfig: { candidateCount: 4 },
      },
    });

    await wrapper.get("textarea").setValue("四种产品广告构图");
    await wrapper.get('button[data-action="generate"]').trigger("click");

    expect(wrapper.emitted("generate")?.[0]?.[0]).toMatchObject({
      config: { candidateCount: 4 },
    });
  });

  it("debounces a node-local prompt draft and exposes the complete camera library", async () => {
    vi.useFakeTimers();
    const wrapper = mount(NodeGenerationComposer, {
      props: {
        nodeRevision: 4,
        capabilities: {
          provider: "ark",
          model: "seedance-2",
          modes: ["text_to_video"],
          aspectRatios: ["16:9"],
          resolutions: ["720p"],
          durations: [5],
          candidateCounts: [1],
          audio: false,
          cameraMotions,
        },
        actualReferences: [],
        initialPrompt: "节点 A 的旧草稿",
      },
    });

    await wrapper.get('button[data-action="camera-motion"]').trigger("click");
    expect(wrapper.find('[data-camera-motion="follow"]').exists()).toBe(true);
    expect(wrapper.find('[data-camera-motion="tilt_up"]').exists()).toBe(true);
    expect(wrapper.find('[data-camera-motion="pan_right"]').exists()).toBe(true);
    expect(wrapper.find('[data-camera-motion="drone"]').exists()).toBe(true);
    expect(wrapper.get('[data-action="effect"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-action="effect"]').attributes("title")).toContain("Ark");

    await wrapper.get("textarea").setValue("节点 A 的新草稿");
    await vi.advanceTimersByTimeAsync(499);
    expect(wrapper.emitted("save-config")).toBeUndefined();
    await vi.advanceTimersByTimeAsync(1);
    expect(wrapper.emitted("save-config")?.at(-1)?.[0]).toMatchObject({
      draftPrompt: "节点 A 的新草稿",
      cameraMotion: "static",
    });
  });
});
