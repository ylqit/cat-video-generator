import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { CanvasNodeDto, EpisodeVisualProfileDto } from "../../api/types";
import VisualEvidenceConsole from "../canvas/VisualEvidenceConsole.vue";

const bindingIds = ["child-headshot", "child-fullbody", "cat-front", "cat-side", "style"];

const profile: EpisodeVisualProfileDto = {
  id: "profile-v3",
  projectId: "project-1",
  revision: 3,
  sourceProfileId: "canon-v3-healing-child-cat-line-texture",
  personIdentity: "固定儿童脸型、五官、年龄感与身份特征",
  personHair: "固定儿童短发轮廓、发色与发际线",
  personBody: "固定儿童全身比例与非成人化身体结构",
  catIdentity: "固定猫咪脸部、毛色分区、体型与环纹尾巴",
  stylePositive: ["克制轮廓线", "湿润半透明高光", "柔和漫射光"],
  styleNegative: ["摄影写实", "复制参考物体或构图"],
  referenceBindings: bindingIds.map((assetId, index) => ({
    assetId,
    purpose: (["person_identity", "person_body", "cat_identity", "cat_identity", "style"] as const)[index],
    instruction: "固定视觉职责",
  })),
  references: [],
  lockedSemanticKeys: [
    "person:headshot",
    "person:fullbody",
    "cat:front",
    "cat:side",
    "style:line_texture",
  ],
  createdAt: "2026-08-23T00:00:00Z",
};

const node: CanvasNodeDto = {
  id: "child-canon-node",
  type: "SubjectNode",
  objectType: "subject_revision",
  objectId: "subject-child",
  position: { x: 0, y: 0 },
  revision: 1,
  data: {
    title: "固定儿童",
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
        instruction: "固定儿童面部身份",
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
        instruction: "固定儿童全身比例",
      },
    ],
  },
};

describe("VisualEvidenceConsole", () => {
  it("renders actual Canon thumbnails, semantic keys, version and lock state", () => {
    const wrapper = mount(VisualEvidenceConsole, { props: { node, profile } });

    expect(wrapper.findAll("img").map((item) => item.attributes("src"))).toEqual([
      "/api/v1/assets/child-headshot/content",
      "/api/v1/assets/child-fullbody/content",
    ]);
    expect(wrapper.text()).toContain("person:headshot");
    expect(wrapper.text()).toContain("person:fullbody");
    expect(wrapper.text()).toContain("Revision 3");
    expect(wrapper.text()).toContain("必需并锁定");
  });

  it("edits descriptions while preserving all required Canon bindings", async () => {
    const wrapper = mount(VisualEvidenceConsole, { props: { node, profile } });
    const textareas = wrapper.findAll("textarea");
    await textareas[0].setValue("固定儿童脸型、五官、年龄感、发型轮廓和身份特征");
    await wrapper.get(".profile-editor footer button").trigger("click");

    const draft = wrapper.emitted("save")?.[0]?.[0] as EpisodeVisualProfileDto;
    expect(draft.personIdentity).toContain("发型轮廓");
    expect(draft.referenceBindings).toEqual(profile.referenceBindings);
    expect(draft.stylePositive).toEqual(profile.stylePositive);
    expect(draft.styleNegative).toEqual(profile.styleNegative);
  });
});
