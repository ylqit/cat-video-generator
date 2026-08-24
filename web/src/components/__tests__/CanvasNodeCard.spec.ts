import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CanvasNodeCard from "../canvas/CanvasNodeCard.vue";

describe("CanvasNodeCard", () => {
  it("renders the collapsed healing recipe with textual progress and one primary action", async () => {
    const node = {
      id: "recipe-node",
      type: "RecipeGroupNode" as const,
      objectType: "production_recipe_instance",
      objectId: "recipe-1",
      position: { x: 0, y: 0 },
      data: {
        title: "一人一猫治愈短片",
        theme: "雨后收集落叶",
        targetDurationSeconds: 31,
        shotDurations: [11, 10, 10],
        qualityTier: "balanced",
        currentBlocker: "故事尚未人工批准",
        primaryAction: "生成故事候选",
        reviewStages: [
          { key: "story", complete: false },
          { key: "anchors", complete: false },
          { key: "video", complete: false },
          { key: "sequence", complete: false },
        ],
      },
    };
    const wrapper = mount(CanvasNodeCard, { props: { node } });

    expect(wrapper.text()).toContain("31 秒");
    expect(wrapper.text()).toContain("3 镜");
    expect(wrapper.text()).toContain("故事：待处理");
    expect(wrapper.text()).toContain("当前阻塞：故事尚未人工批准");
    const action = wrapper.findAll("button").filter((button) => button.isVisible());
    expect(action).toHaveLength(1);
    await action[0].trigger("click");
    expect(wrapper.emitted("open-recipe")?.[0]).toEqual([node]);
  });

  it("selects every node with pointer or keyboard and exposes selection state", async () => {
    const node = {
      id: "reference-1",
      type: "ReferenceAssetNode" as const,
      objectType: "reference_asset",
      objectId: null,
      position: { x: 0, y: 0 },
      data: { title: "产品包装参考" },
    };
    const wrapper = mount(CanvasNodeCard, {
      props: { node, selected: true, selectionState: "compatible" },
    });

    expect(wrapper.get("article").attributes("aria-selected")).toBe("true");
    expect(wrapper.get("article").classes()).toContain("is-selected");
    expect(wrapper.get("article").classes()).toContain("selection-compatible");
    await wrapper.get("article").trigger("click");
    await wrapper.get("article").trigger("keydown", { key: "Enter" });
    await wrapper.get("article").trigger("keydown", { key: " " });
    await wrapper.get("article").trigger("dblclick");

    expect(wrapper.emitted("select-node")).toHaveLength(2);
    expect(wrapper.emitted("select-node")?.[0]).toEqual([node]);
    expect(wrapper.emitted("activate-node")).toHaveLength(1);
    expect(wrapper.emitted("activate-node")?.[0]).toEqual([node]);
  });

  it("shows the selection order while picking references", () => {
    const wrapper = mount(CanvasNodeCard, {
      props: {
        node: {
          id: "reference-2",
          type: "ImageAssetNode",
          objectType: "asset",
          objectId: "asset-2",
          position: { x: 0, y: 0 },
          data: { title: "第二张参考" },
        },
        selectionState: "chosen",
        selectionIndex: 2,
      },
    });

    expect(wrapper.get(".selection-badge").text()).toBe("2");
    expect(wrapper.get(".selection-badge").attributes("aria-label")).toContain("第 2 个");
  });

  it("shows story score, approval state and both auditable prompt calls", async () => {
    const wrapper = mount(CanvasNodeCard, {
      props: {
        node: {
          id: "story-1",
          type: "StoryCandidateNode",
          objectType: "story_revision",
          objectId: "story-1",
          position: { x: 0, y: 0 },
          data: {
            title: "雨前收画",
            strategy: "relationship",
            status: "candidate",
            logline: "小孩和猫必须共同保住晾晒的画。",
            scorecard: { average: 8.4, subjectNecessity: 9, warnings: [] },
            candidatePromptId: "prompt-plan",
            criticPromptId: "prompt-critic",
          },
        },
      },
    });

    expect(wrapper.text()).toContain("雨前收画");
    expect(wrapper.text()).toContain("8.4");
    expect(wrapper.text()).toContain("主体必要性 9");

    const promptButtons = wrapper.findAll("button").filter(
      (button) => button.text().includes("查看 Prompt"),
    );
    expect(promptButtons).toHaveLength(2);
    await promptButtons[0].trigger("click");
    expect(wrapper.emitted("inspect-prompt")?.[0]).toEqual(["prompt-plan"]);

    const approveButton = wrapper.findAll("button").find(
      (button) => button.text().includes("批准定稿"),
    );
    await approveButton!.trigger("click");
    expect(wrapper.emitted("approve-story")?.[0]).toEqual(["story-1"]);
  });

  it("renders a character design slot as a real image node", () => {
    const wrapper = mount(CanvasNodeCard, {
      props: {
        node: {
          id: "character-child",
          type: "CharacterDesignNode",
          objectType: "character_design_slot",
          objectId: null,
          position: { x: 0, y: 0 },
          data: {
            title: "儿童本集造型图",
            slot: "child",
            candidates: [
              { id: "asset-1", assetId: "asset-1", title: "候选 1", thumbnailUrl: "/asset-1.png" },
              { id: "asset-2", assetId: "asset-2", title: "候选 2", thumbnailUrl: "/asset-2.png" },
            ],
          },
        },
      },
    });

    expect(wrapper.text()).toContain("儿童本集造型");
    expect(wrapper.text()).toContain("Canon identity 固定");
    expect(wrapper.findAll(".character-node-candidates img")).toHaveLength(2);
  });

  it("renders an editable beat as an independent duration and prompt unit", async () => {
    const wrapper = mount(CanvasNodeCard, {
      props: {
        node: {
          id: "beat-1",
          type: "ShotBeatNode",
          objectType: "shot_beat",
          objectId: "beat-1",
          position: { x: 0, y: 0 },
          data: {
            title: "猫发现雨滴",
            action: "猫抬头看雨，小孩转向晾衣绳。",
            camera: "中景固定",
            durationSeconds: 8,
            revision: 2,
            status: "ready",
            promptId: "prompt-storyboard",
          },
        },
      },
    });

    expect(wrapper.text()).toContain("8 秒");
    expect(wrapper.text()).toContain("Revision 2");
    expect(wrapper.text()).toContain("中景固定");
    const promptButton = wrapper.findAll("button").find(
      (button) => button.text().includes("查看 Prompt"),
    );
    await promptButton!.trigger("click");
    expect(wrapper.emitted("inspect-prompt")?.[0]).toEqual(["prompt-storyboard"]);
  });

  it("exposes explicit subject assistance and node-local generation without auto calls", async () => {
    const subject = {
      id: "subject-node",
      type: "SubjectNode" as const,
      objectType: "subject",
      objectId: "subject-1",
      position: { x: 0, y: 0 },
      data: {
        title: "包装罐",
        name: "包装罐",
        kind: "product",
        role: "hero_product",
        revision: 1,
        identityAnchors: ["蓝色罐身"],
      },
    };
    const subjectWrapper = mount(CanvasNodeCard, { props: { node: subject } });
    await subjectWrapper.get('[data-action="assist-subject"]').trigger("click");
    expect(subjectWrapper.emitted("assist-subject")?.[0]).toEqual([subject]);

    const generation = {
      ...subject,
      id: "video-generation",
      type: "VideoGenerationNode" as const,
      objectType: "video_generation",
      objectId: null,
      data: { title: "视频生成", status: "draft" },
    };
    const generationWrapper = mount(CanvasNodeCard, { props: { node: generation } });
    expect(generationWrapper.emitted("open-composer")).toBeUndefined();
    await generationWrapper.get('[data-action="open-composer"]').trigger("click");
    expect(generationWrapper.emitted("open-composer")?.[0]).toEqual([generation]);
  });

  it("renders approved Canon evidence instead of a hidden reference count", () => {
    const wrapper = mount(CanvasNodeCard, {
      props: {
        node: {
          id: "child-canon",
          type: "SubjectNode",
          objectType: "subject_revision",
          objectId: "subject-child",
          position: { x: 0, y: 0 },
          data: {
            title: "固定儿童",
            kind: "person",
            role: "protagonist",
            revision: 3,
            identityAnchors: ["固定脸型、年龄感与身体比例"],
            references: [
              {
                assetId: "child-headshot",
                semanticKey: "person:headshot",
                title: "儿童面部",
                contentUrl: "/api/v1/assets/child-headshot/content",
                thumbnailUrl: "/api/v1/assets/child-headshot/content",
                approvalStatus: "approved",
                sha256: "a".repeat(64),
                required: true,
              },
              {
                assetId: "child-fullbody",
                semanticKey: "person:fullbody",
                title: "儿童全身比例",
                contentUrl: "/api/v1/assets/child-fullbody/content",
                thumbnailUrl: "/api/v1/assets/child-fullbody/content",
                approvalStatus: "approved",
                sha256: "b".repeat(64),
                required: true,
              },
            ],
          },
        },
      },
    });

    expect(wrapper.findAll(".evidence-strip img")).toHaveLength(2);
    expect(wrapper.text()).toContain("2 张已批准身份证据");
    expect(wrapper.text()).toContain("表情、多角度或背面证据可补充");
  });

  it("offers actionable binding choices when a reference node is empty", async () => {
    const node = {
      id: "reference-node",
      type: "ReferenceAssetNode" as const,
      objectType: "reference_asset",
      objectId: null,
      position: { x: 0, y: 0 },
      data: { title: "产品包装参考", assets: [] },
    };
    const wrapper = mount(CanvasNodeCard, { props: { node } });

    expect(wrapper.text()).toContain("等待绑定素材");
    await wrapper.get('[data-action="upload-reference"]').trigger("click");
    await wrapper.get('[data-action="select-history"]').trigger("click");
    await wrapper.get('[data-action="create-subject"]').trigger("click");

    expect(wrapper.emitted("upload-reference")?.[0]).toEqual([node]);
    expect(wrapper.emitted("select-history")?.[0]).toEqual([node]);
    expect(wrapper.emitted("create-subject")?.[0]).toEqual([node]);
  });

  it("opens the recoverable removal menu from a node right click", async () => {
    const node = {
      id: "prompt-node",
      type: "PromptArtifactNode" as const,
      objectType: "prompt_run",
      objectId: "prompt-1",
      position: { x: 0, y: 0 },
      data: { title: "镜头提示词" },
    };
    const wrapper = mount(CanvasNodeCard, { props: { node } });

    await wrapper.trigger("contextmenu", { clientX: 240, clientY: 180 });

    const emitted = wrapper.emitted("open-context-menu")?.[0];
    expect(emitted?.[0]).toEqual(node);
    expect(emitted?.[1]).toBeInstanceOf(MouseEvent);
  });
});
