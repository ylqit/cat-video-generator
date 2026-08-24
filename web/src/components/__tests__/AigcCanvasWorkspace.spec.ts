import { flushPromises, shallowMount } from "@vue/test-utils";
import { ElMessageBox } from "element-plus";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";

import AigcCanvasWorkspace from "../canvas/AigcCanvasWorkspace.vue";
import CanvasLocalConsole from "../canvas/CanvasLocalConsole.vue";
import CanvasNodeCard from "../canvas/CanvasNodeCard.vue";
import NodeGenerationComposer from "../canvas/NodeGenerationComposer.vue";
import VideoAssetPanel from "../canvas/VideoAssetPanel.vue";
import VideoEditWorkspace from "../canvas/VideoEditWorkspace.vue";

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
  providerCapabilities: vi.fn(),
  createEdge: vi.fn(),
  deleteEdge: vi.fn(),
  archiveNode: vi.fn(),
  restoreNode: vi.fn(),
  bindNodeAssets: vi.fn(),
  assets: vi.fn(),
  subjects: vi.fn(),
  createGenerationBatch: vi.fn(),
  recipeInstance: vi.fn(),
  compileCanvasGroup: vi.fn(),
  runCanvasGroup: vi.fn(),
  saveCanvasGroupTemplate: vi.fn(),
  convertCanvasGroupToShots: vi.fn(),
  ungroupCanvasGroup: vi.fn(),
  canvasGroupDownloadManifest: vi.fn(),
  canvasGroupDownloadUrl: vi.fn(),
  eventsUrl: vi.fn(() => "/api/v2/projects/project-1/events"),
}));
const legacyCalls = vi.hoisted(() => ({ uploadReference: vi.fn() }));

vi.mock("../../api/client", () => ({
  canvasApi: calls,
  api: legacyCalls,
  assetContentUrl: (assetId: string) => `/api/v1/assets/${assetId}/content`,
}));

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

const VueFlowStub = defineComponent({
  props: {
    nodes: { type: Array, default: () => [] },
    panOnDrag: { type: Boolean, default: true },
    panActivationKeyCode: { type: String, default: undefined },
  },
  template: '<div><div v-for="item in nodes" :data-canvas-node-id="item.id"><slot :name="`node-${item.type}`" :data="item.data" /></div></div>',
});
const CanvasLocalConsoleStub = defineComponent({
  props: { title: String, preset: String },
  template: '<section data-testid="canvas-local-console"><slot /></section>',
});

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

  it("opens segment reshoot in the node-local console and expands only on request", async () => {
    const video = {
      id: "video-node",
      type: "VideoAssetNode" as const,
      objectType: "asset",
      objectId: "asset-video",
      revision: 1,
      position: { x: 300, y: 200 },
      data: {
        title: "成品视频",
        assetId: "asset-video",
        contentUrl: "/api/v1/assets/asset-video/content",
        durationMs: 5_000,
      },
    };
    calls.canvas.mockResolvedValue({
      projectId: "project-1",
      canvasV2Enabled: true,
      layoutVersion: 0,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      edges: [],
      nodes: [video],
    });
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: {
        stubs: {
          VueFlow: VueFlowStub,
          Background: true,
          Handle: true,
          PromptTraceDrawer: true,
          Teleport: false,
          CanvasLocalConsole: CanvasLocalConsoleStub,
        },
      },
    });
    await flushPromises();

    wrapper.findComponent(CanvasNodeCard).vm.$emit("select-node", video);
    await flushPromises();
    wrapper.findComponent(VideoAssetPanel).vm.$emit("segment-reshoot", video);
    await flushPromises();

    const editor = wrapper.findComponent(VideoEditWorkspace);
    expect(editor.props("embedded")).toBe(true);
    editor.vm.$emit("expand");
    await flushPromises();
    expect(wrapper.findComponent(VideoEditWorkspace).props("embedded")).toBe(false);
  });

  it("requires Space for canvas panning and dismisses selection on an ordinary pane click", async () => {
    const video = {
      id: "video-node",
      type: "VideoAssetNode" as const,
      objectType: "asset",
      objectId: "asset-video",
      revision: 1,
      position: { x: 300, y: 200 },
      data: { title: "成品视频", assetId: "asset-video", durationMs: 5_000 },
    };
    calls.canvas.mockResolvedValue({
      projectId: "project-1",
      canvasV2Enabled: true,
      layoutVersion: 0,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      edges: [],
      nodes: [video],
    });
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: {
        stubs: {
          VueFlow: VueFlowStub,
          Background: true,
          Handle: true,
          PromptTraceDrawer: true,
          Teleport: false,
          CanvasLocalConsole: CanvasLocalConsoleStub,
        },
      },
    });
    await flushPromises();

    const flow = wrapper.findComponent(VueFlowStub);
    expect(flow.props("panOnDrag")).toBe(false);
    expect(flow.props("panActivationKeyCode")).toBeUndefined();

    const surface = wrapper.get(".canvas-surface");
    const surfaceElement = surface.element as HTMLElement;
    const setPointerCapture = vi.fn();
    const releasePointerCapture = vi.fn();
    Object.assign(surfaceElement, {
      setPointerCapture,
      hasPointerCapture: () => true,
      releasePointerCapture,
    });
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " ", code: "Space" }));
    await flushPromises();
    expect(surface.classes()).toContain("space-ready");

    const pointerDown = new MouseEvent("pointerdown", {
      bubbles: true,
      button: 0,
      clientX: 120,
      clientY: 140,
    });
    Object.defineProperty(pointerDown, "pointerId", { value: 7 });
    wrapper.get('[data-canvas-node-id="video-node"]').element.dispatchEvent(pointerDown);
    await flushPromises();
    expect(setPointerCapture).toHaveBeenCalledWith(7);
    expect(surface.classes()).toContain("panning");

    window.dispatchEvent(new KeyboardEvent("keyup", { key: " ", code: "Space" }));
    await flushPromises();
    expect(surface.classes()).not.toContain("panning");
    expect(releasePointerCapture).toHaveBeenCalledWith(7);

    wrapper.findComponent(CanvasNodeCard).vm.$emit("select-node", video);
    await flushPromises();
    expect(wrapper.findComponent(CanvasLocalConsole).exists()).toBe(true);

    flow.vm.$emit("pane-click");
    await flushPromises();
    expect(wrapper.findComponent(CanvasLocalConsole).exists()).toBe(false);

    wrapper.unmount();
  });

  it("keeps the console centered below the node while the canvas moves without remeasuring DOM", async () => {
    const video = {
      id: "video-fixed-console",
      type: "VideoAssetNode" as const,
      objectType: "asset",
      objectId: "asset-video",
      revision: 1,
      position: { x: 300, y: 200 },
      data: { title: "成品视频", assetId: "asset-video", durationMs: 5_000 },
    };
    calls.canvas.mockResolvedValue({
      projectId: "project-1",
      canvasV2Enabled: true,
      layoutVersion: 0,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      edges: [],
      nodes: [video],
    });
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("canvas-surface")
        ? { left: 100, top: 50, right: 1300, bottom: 950, width: 1200, height: 900, x: 100, y: 50, toJSON: () => ({}) } as DOMRect
        : { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: {
        stubs: {
          VueFlow: VueFlowStub,
          Background: true,
          Handle: true,
          PromptTraceDrawer: true,
          Teleport: false,
          CanvasLocalConsole: CanvasLocalConsoleStub,
        },
      },
    });
    await flushPromises();

    wrapper.findComponent(CanvasNodeCard).vm.$emit("select-node", video);
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    await flushPromises();
    expect(wrapper.findComponent(CanvasLocalConsole).attributes("style"))
      .toContain("translate3d(190px, 592px, 0)");
    const measurementsAfterSelection = rectSpy.mock.calls.length;
    const scheduledFrames: FrameRequestCallback[] = [];
    const animationFrameSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      scheduledFrames.push(callback);
      return scheduledFrames.length;
    });

    wrapper.findComponent(VueFlowStub).vm.$emit("move", {
      flowTransform: { x: 120, y: -20, zoom: 1 },
    });
    wrapper.findComponent(VueFlowStub).vm.$emit("move", {
      flowTransform: { x: 140, y: -30, zoom: 1 },
    });
    wrapper.findComponent(VueFlowStub).vm.$emit("move", {
      flowTransform: { x: 160, y: -40, zoom: 1 },
    });
    expect(scheduledFrames).toHaveLength(1);
    scheduledFrames[0]?.(performance.now());
    await flushPromises();

    expect(wrapper.findComponent(CanvasLocalConsole).attributes("style"))
      .toContain("translate3d(350px, 552px, 0)");
    expect(rectSpy.mock.calls.length).toBe(measurementsAfterSelection);
    animationFrameSpy.mockRestore();
    rectSpy.mockRestore();
    wrapper.unmount();
  });

  it("archives a removable selected node and restores it from the undo notice", async () => {
    const removable = {
      id: "prompt-removable",
      type: "PromptArtifactNode" as const,
      objectType: "prompt_run",
      objectId: "prompt-run-1",
      revision: 1,
      position: { x: 300, y: 200 },
      availableActions: [
        { key: "archive_node" as const, label: "从画布移除", enabled: true, execution: "client" as const },
      ],
      data: { title: "镜头提示词" },
    };
    calls.canvas
      .mockResolvedValueOnce({
        projectId: "project-1",
        canvasV2Enabled: true,
        layoutVersion: 3,
        syncStatus: "saved",
        viewport: { x: 0, y: 0, zoom: 1 },
        edges: [],
        nodes: [removable],
      })
      .mockResolvedValueOnce({
        projectId: "project-1",
        canvasV2Enabled: true,
        layoutVersion: 4,
        syncStatus: "saved",
        viewport: { x: 0, y: 0, zoom: 1 },
        edges: [],
        nodes: [],
      })
      .mockResolvedValueOnce({
        projectId: "project-1",
        canvasV2Enabled: true,
        layoutVersion: 5,
        syncStatus: "saved",
        viewport: { x: 0, y: 0, zoom: 1 },
        edges: [],
        nodes: [removable],
      });
    calls.archiveNode.mockResolvedValue({
      projectId: "project-1",
      nodeId: removable.id,
      archived: true,
      layoutVersion: 4,
    });
    calls.restoreNode.mockResolvedValue({
      projectId: "project-1",
      nodeId: removable.id,
      archived: false,
      layoutVersion: 5,
    });
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: {
        stubs: {
          VueFlow: VueFlowStub,
          Background: true,
          Handle: true,
          PromptTraceDrawer: true,
          Teleport: false,
          CanvasLocalConsole: CanvasLocalConsoleStub,
        },
      },
    });
    await flushPromises();

    wrapper.findComponent(CanvasNodeCard).vm.$emit("select-node", removable);
    await flushPromises();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete" }));
    await flushPromises();

    expect(calls.archiveNode).toHaveBeenCalledWith("project-1", removable.id, 3);
    expect(document.body.textContent).toContain("已从画布移除");
    const undoButton = document.body.querySelector<HTMLButtonElement>(".canvas-archive-undo button");
    expect(undoButton).not.toBeNull();
    undoButton!.click();
    await flushPromises();
    expect(calls.restoreNode).toHaveBeenCalledWith("project-1", removable.id, 4);
    expect(wrapper.findAllComponents(CanvasNodeCard).some((item) => item.props("node").id === removable.id)).toBe(true);

    wrapper.unmount();
  });

  it("renders the six-stage group frame and executes only to the next review gate", async () => {
    const group = {
      id: "group-1",
      projectId: "project-1",
      recipeInstanceId: "recipe-1",
      parentGroupId: null,
      type: "recipe",
      title: "一人一猫治愈短片",
      lifecycleStatus: "active",
      color: "#7c9cff",
      revision: 2,
      memberNodeIds: ["brief-1", "character-child"],
      phase: "creative",
      phaseProgress: [
        { key: "creative", label: "补全创意输入", status: "current" },
        { key: "story", label: "AI剧情生成", status: "blocked" },
        { key: "character_design", label: "角色设计", status: "blocked" },
        { key: "storyboard", label: "分镜生成", status: "blocked" },
        { key: "render", label: "视频渲染", status: "blocked" },
        { key: "export", label: "成品导出", status: "blocked" },
      ],
      blocker: "创意简报尚未人工批准",
      availableActions: [
        { key: "run_group", label: "整组执行", enabled: true },
        { key: "save_group_template", label: "添加到工具箱", enabled: true },
        { key: "convert_shot_groups", label: "转分镜组", enabled: false, disabledReason: "请先批准分镜" },
        { key: "ungroup", label: "解组", enabled: true },
        { key: "download_group", label: "批量下载", enabled: false, disabledReason: "暂无成功产物" },
      ],
      data: {},
    };
    calls.canvas.mockResolvedValue({
      projectId: "project-1",
      canvasV2Enabled: true,
      layoutVersion: 0,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      edges: [],
      groups: [group],
      nodes: [
        {
          id: "brief-1",
          type: "BriefNode",
          objectType: "story_brief",
          objectId: "brief-1",
          position: { x: 20, y: 20 },
          data: { title: "补全创意输入", theme: "雨后亮叶" },
        },
        {
          id: "character-child",
          type: "CharacterDesignNode",
          objectType: "character_design_slot",
          objectId: null,
          position: { x: 420, y: 20 },
          data: { title: "儿童本集造型图", slot: "child" },
        },
      ],
    });
    calls.recipeInstance.mockResolvedValue({
      id: "recipe-1",
      projectId: "project-1",
      recipeKey: "healing_child_cat_v1",
      recipeVersion: 1,
      revision: 1,
      theme: "雨后亮叶",
      targetDurationSeconds: 15,
      qualityTier: "balanced",
      canonProfileId: "canon-v2-healing-child-cat",
      stage: "concept",
      phase: "creative",
      shotDurations: [15],
      currentBlocker: "创意简报尚未人工批准",
      primaryAction: "补全创意输入",
      reviewStages: [],
      progress: {
        storyApproved: false,
        episodeRulesLocked: false,
        shotCount: 0,
        approvedAnchorCount: 0,
        approvedVideoCount: 0,
        sequenceReady: false,
        finalApproved: false,
      },
      shots: [],
    });
    calls.compileCanvasGroup.mockResolvedValue({
      groupId: "group-1",
      phase: "creative",
      primaryAction: "补全创意输入",
      estimatedCostMicros: 0,
    });
    calls.runCanvasGroup.mockResolvedValue({
      jobId: "job-group-1",
      kind: "recipe_group",
      status: "queued",
      context: {
        projectId: "project-1",
        canvasGroupId: "group-1",
        recipeInstanceId: "recipe-1",
        phase: "creative",
      },
    });
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue({ action: "confirm" } as never);
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: {
        stubs: {
          VueFlow: VueFlowStub,
          Background: true,
          Handle: true,
          PromptTraceDrawer: true,
          Teleport: false,
          CanvasLocalConsole: CanvasLocalConsoleStub,
        },
      },
    });
    await flushPromises();

    expect(wrapper.findAllComponents(CanvasNodeCard)).toHaveLength(2);
    expect(wrapper.text()).toContain("补全创意输入");
    expect(wrapper.text()).toContain("成品导出");
    await wrapper.get(".canvas-group-frame").trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".canvas-group-toolbar button").map((button) => button.text())).toEqual([
      "整组执行",
      "添加到工具箱",
      "转分镜组",
      "解组",
      "批量下载",
    ]);

    await wrapper.findAll(".canvas-group-toolbar button")[0].trigger("click");
    await flushPromises();
    expect(calls.compileCanvasGroup).toHaveBeenCalledWith("group-1");
    expect(calls.runCanvasGroup).toHaveBeenCalledWith("group-1", 0);
    expect(calls.runStoryStrategies).not.toHaveBeenCalled();
    expect(calls.createStoryboard).not.toHaveBeenCalled();
  });

  it("enables explicit story generation with two subjects and never auto-generates media", async () => {
    calls.canvas.mockResolvedValue(canvasWithSubjects(2));
    calls.runStoryStrategies.mockResolvedValue({
      jobId: "job-story-strategy",
      kind: "story_strategy",
      status: "queued",
      context: { projectId: "project-1", canvasNodeId: "planner-1" },
    });
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

  it("selects compatible references across the canvas and persists only typed domain edges", async () => {
    const reference = {
      id: "reference-1",
      type: "ReferenceAssetNode" as const,
      objectType: "reference_asset",
      objectId: "asset-1",
      position: { x: 20, y: 20 },
      data: { title: "包装正面", assetId: "asset-1" },
    };
    const generation = {
      id: "video-generation",
      type: "VideoGenerationNode" as const,
      objectType: "video_generation",
      objectId: null,
      revision: 1,
      position: { x: 420, y: 20 },
      data: { title: "视频生成" },
    };
    calls.canvas.mockResolvedValue({
      projectId: "project-1",
      canvasV2Enabled: true,
      layoutVersion: 0,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      templateKey: "product_ad",
      edges: [],
      nodes: [reference, generation],
    });
    calls.providerCapabilities.mockResolvedValue([{
      provider: "ark",
      model: "seedance-2",
      capabilities: {
        modes: ["text_to_video", "image_to_video"],
        aspectRatios: ["16:9"],
        resolutions: ["480p"],
        durations: [8],
        candidateCounts: [1],
        maxReferenceImages: 1,
      },
    }]);
    calls.createEdge.mockResolvedValue({
      id: "edge-1",
      sourceNodeId: reference.id,
      sourceNodeType: reference.type,
      sourcePort: "media_reference[]",
      targetNodeId: generation.id,
      targetNodeType: generation.type,
      targetPort: "media_reference[]",
    });
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VueFlow: VueFlowStub, Background: true, Handle: true, PromptTraceDrawer: true, Teleport: false, CanvasLocalConsole: CanvasLocalConsoleStub } },
    });
    await flushPromises();

    const cards = wrapper.findAllComponents(CanvasNodeCard);
    const referenceCard = cards.find((item) => item.props("node").id === reference.id)!;
    const generationCard = cards.find((item) => item.props("node").id === generation.id)!;
    generationCard.vm.$emit("open-composer", generation);
    await flushPromises();
    wrapper.findComponent(NodeGenerationComposer).vm.$emit("select-references");
    await flushPromises();

    expect(wrapper.text()).toContain("从画布选择参考");
    expect(referenceCard.props("selectionState")).toBe("compatible");
    referenceCard.vm.$emit("select-node", reference);
    await flushPromises();
    const finish = wrapper.findAll("button").find((button) => button.text().includes("完成选择"));
    await finish!.trigger("click");
    await flushPromises();

    expect(calls.createEdge).toHaveBeenCalledWith("project-1", expect.objectContaining({
      sourceNodeId: "reference-1",
      sourcePort: "media_reference[]",
      targetNodeId: "video-generation",
      targetPort: "media_reference[]",
    }));
  });

  it("uploads a file from an empty reference node and binds it without a media generation call", async () => {
    const reference = {
      id: "reference-1",
      type: "ReferenceAssetNode" as const,
      objectType: "reference_asset",
      objectId: null,
      revision: 1,
      position: { x: 20, y: 20 },
      data: { title: "包装正面", semanticRole: "packshot_front", assets: [] },
    };
    calls.canvas.mockResolvedValue({
      projectId: "project-1",
      canvasV2Enabled: true,
      layoutVersion: 0,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      templateKey: "product_ad",
      edges: [],
      nodes: [reference],
    });
    legacyCalls.uploadReference.mockResolvedValue({ id: "asset-uploaded" });
    calls.bindNodeAssets.mockResolvedValue({ id: reference.id, revision: 2 });
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VueFlow: VueFlowStub, Background: true, Handle: true, PromptTraceDrawer: true, Teleport: false, CanvasLocalConsole: CanvasLocalConsoleStub } },
    });
    await flushPromises();

    wrapper.findComponent(CanvasNodeCard).vm.$emit("upload-reference", reference);
    await flushPromises();
    const input = wrapper.get('input[data-role="reference-upload"]');
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [new File(["image"], "package.png", { type: "image/png" })],
    });
    await input.trigger("change");
    await flushPromises();

    expect(legacyCalls.uploadReference).toHaveBeenCalledOnce();
    expect(calls.bindNodeAssets).toHaveBeenCalledWith(
      "reference-1",
      1,
      [{ assetId: "asset-uploaded", semanticRole: "packshot_front" }],
      false,
    );
    expect(calls.createGenerationBatch).not.toHaveBeenCalled();
  });

  it("binds a selected historical asset to the requesting reference node", async () => {
    const reference = {
      id: "reference-1",
      type: "ReferenceAssetNode" as const,
      objectType: "reference_asset",
      objectId: null,
      revision: 3,
      position: { x: 20, y: 20 },
      data: { title: "包装正面", semanticRole: "packshot_front", assets: [] },
    };
    calls.canvas.mockResolvedValue({
      projectId: "project-1",
      canvasV2Enabled: true,
      layoutVersion: 0,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      templateKey: "product_ad",
      edges: [],
      nodes: [reference],
    });
    calls.assets.mockResolvedValue([{
      id: "asset-history",
      projectId: "project-1",
      mediaType: "image",
      role: "identity",
      status: "ready",
      semanticKey: "包装图",
      sha256: "abc",
      metadata: {},
      contentUrl: "/content/asset-history",
    }]);
    calls.bindNodeAssets.mockResolvedValue({ id: reference.id, revision: 4 });
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VueFlow: VueFlowStub, Background: true, Handle: true, PromptTraceDrawer: true, Teleport: false, CanvasLocalConsole: CanvasLocalConsoleStub } },
    });
    await flushPromises();

    wrapper.findComponent(CanvasNodeCard).vm.$emit("select-history", reference);
    await flushPromises();
    const bind = wrapper.findAll("button").find((button) => button.text().includes("绑定到此节点"));
    expect(bind).toBeDefined();
    await bind!.trigger("click");
    await flushPromises();

    expect(calls.bindNodeAssets).toHaveBeenCalledWith(
      "reference-1",
      3,
      [{ assetId: "asset-history", semanticRole: "packshot_front" }],
      false,
    );
  });

  it("opens the frozen subject library and selects a compatible subject for the generator", async () => {
    const subjectNode = {
      id: "subject-node",
      type: "SubjectNode" as const,
      objectType: "subject",
      objectId: "subject-1",
      revision: 1,
      position: { x: 20, y: 20 },
      data: { title: "蓝色汽水罐", kind: "product", role: "hero_product", revision: 2 },
    };
    const generation = {
      id: "image-generation",
      type: "ImageGenerationNode" as const,
      objectType: "image_generation",
      objectId: null,
      revision: 1,
      position: { x: 420, y: 20 },
      data: { title: "广告图片生成" },
    };
    calls.canvas.mockResolvedValue({
      projectId: "project-1",
      canvasV2Enabled: true,
      layoutVersion: 0,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      templateKey: "product_ad",
      edges: [],
      nodes: [subjectNode, generation],
    });
    calls.providerCapabilities.mockResolvedValue([{
      provider: "ark",
      model: "image-model",
      capabilities: {
        modes: ["text_to_image"],
        aspectRatios: ["1:1"],
        resolutions: ["1080p"],
        durations: [1],
        candidateCounts: [1, 4],
      },
    }]);
    calls.subjects.mockResolvedValue([{
      id: "subject-1",
      projectId: "project-1",
      revisionId: "subject-revision-2",
      revision: 2,
      status: "frozen",
      name: "蓝色汽水罐",
      kind: "product",
      role: "hero_product",
      identityAnchors: ["蓝色罐身"],
      immutableTraits: ["标签不可变"],
      references: [],
    }]);
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VueFlow: VueFlowStub, Background: true, Handle: true, PromptTraceDrawer: true, Teleport: false, CanvasLocalConsole: CanvasLocalConsoleStub } },
    });
    await flushPromises();

    const generationCard = wrapper.findAllComponents(CanvasNodeCard).find(
      (item) => item.props("node").id === generation.id,
    )!;
    generationCard.vm.$emit("open-composer", generation);
    await flushPromises();
    wrapper.findComponent(NodeGenerationComposer).vm.$emit("open-subject-library");
    await flushPromises();

    expect(calls.subjects).toHaveBeenCalledWith("project-1");
    expect(wrapper.text()).toContain("蓝色汽水罐");
    const choose = wrapper.findAll("button").find((button) => button.text().includes("选择到当前生成节点"));
    expect(choose).toBeDefined();
    await choose!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("完成选择（1）");
  });

  it("leaves task events to the global task center without starting canvas polling", async () => {
    const eventSource = vi.fn();
    const setInterval = vi.spyOn(window, "setInterval");
    vi.stubGlobal("EventSource", eventSource);
    calls.canvas.mockResolvedValue(canvasWithSubjects(2));
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VueFlow: true, Background: true, PromptTraceDrawer: true } },
    });
    await flushPromises();

    expect(eventSource).not.toHaveBeenCalled();
    expect(setInterval).not.toHaveBeenCalled();
    expect(wrapper.find(".el-loading-mask").exists()).toBe(false);
    expect(wrapper.text()).toContain("已保存");
    expect(wrapper.text()).not.toContain("服务异常");
    wrapper.unmount();
    setInterval.mockRestore();
    vi.unstubAllGlobals();
  });

  it("shows a first-load skeleton and removes it without an Element loading mask", async () => {
    let resolveCanvas!: (value: ReturnType<typeof canvasWithSubjects>) => void;
    calls.canvas.mockReturnValue(new Promise((resolve) => { resolveCanvas = resolve; }));
    const wrapper = shallowMount(AigcCanvasWorkspace, {
      props: { projectId: "project-1" },
      global: { stubs: { VueFlow: true, Background: true, PromptTraceDrawer: true } },
    });
    await flushPromises();

    expect(wrapper.find(".canvas-initial-skeleton").exists()).toBe(true);
    expect(wrapper.find(".el-loading-mask").exists()).toBe(false);

    resolveCanvas(canvasWithSubjects(2));
    await flushPromises();
    expect(wrapper.find(".canvas-initial-skeleton").exists()).toBe(false);
    expect(wrapper.find(".el-loading-mask").exists()).toBe(false);
  });
});
