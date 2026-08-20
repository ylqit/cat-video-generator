import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CanvasNodeCard from "../canvas/CanvasNodeCard.vue";

describe("CanvasNodeCard", () => {
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
});
