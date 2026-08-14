import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { AssetDto, ShotDto, ShotPromptPreview } from "../../api/types";
import ShotMediaVersions from "../ShotMediaVersions.vue";

const videoAsset: AssetDto = {
  id: "video-1",
  role: "shot_video",
  mediaType: "video",
  scope: "shot",
  status: "approved",
  projectId: "project-1",
  sceneId: "scene-1",
  shotId: "shot-1",
  producingStepId: "step-old",
  sha256: "a".repeat(64),
  semanticKey: "shot:video:1",
  metadata: {},
  contentReady: true,
  displayName: "视频候选",
};

const shot: ShotDto = {
  id: "shot-1",
  sceneId: "scene-1",
  order: 1,
  title: "开柜取装备",
  direction: "1. 中景建立。\n2. 稳定收尾。",
  durationSeconds: 10,
  draftRevision: 2,
  anchorMode: "generate",
  referenceBindings: [],
  inheritProjectReferences: true,
  sceneLookUsage: "derive_anchor",
  useSceneLook: true,
  status: "ready",
  selectedAnchorAssetId: null,
  selectedVideoAssetId: null,
  assets: [videoAsset],
  attempts: [
    {
      id: "step-running",
      kind: "video",
      status: "running",
      attempt: 2,
      operationKey: "video:shot",
      providerTaskId: "provider-2",
      model: "fake-seedance",
      inputSnapshot: { sourceRevisionHash: "current-hash", sourceAssets: [] },
      reviews: [],
      createdAt: "2026-08-14T02:00:00Z",
    },
    {
      id: "step-old",
      kind: "video",
      status: "awaiting_review",
      attempt: 1,
      operationKey: "video:shot",
      model: "fake-seedance",
      inputSnapshot: { sourceRevisionHash: "old-hash", sourceAssets: [{ assetId: "ref-1" }] },
      reviews: [],
      createdAt: "2026-08-14T01:00:00Z",
    },
  ],
};

const preview = {
  sourceRevisionHash: "current-hash",
} as ShotPromptPreview;

const stubs = {
  ElTag: { template: "<span><slot /></span>" },
  ElEmpty: { props: ["description"], template: "<div>{{ description }}</div>" },
  ElAlert: { props: ["title"], template: "<div>{{ title }}</div>" },
  ElButton: { template: "<button @click=\"$emit('click')\"><slot /></button>" },
};

describe("ShotMediaVersions", () => {
  it("shows a running attempt before an asset exists and marks old media stale", async () => {
    const wrapper = mount(ShotMediaVersions, {
      props: { shot, anchorPreview: null, videoPreview: preview },
      global: { stubs },
    });

    expect(wrapper.text()).toContain("视频 V2");
    expect(wrapper.text()).toContain("任务尚未生成媒体文件");
    expect(wrapper.text()).toContain("视频 V1");
    expect(wrapper.text()).toContain("基于旧输入");
    expect(wrapper.find("video").attributes("src")).toContain(videoAsset.id);

    const choose = wrapper.findAll("button").find(
      (button) => button.text().includes("选择此历史版本"),
    );
    expect(choose).toBeDefined();
    await choose!.trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual([videoAsset, true]);
  });
});
