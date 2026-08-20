import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SubjectAssistantPanel from "../canvas/SubjectAssistantPanel.vue";

const subject = {
  name: "灰灰",
  kind: "animal" as const,
  role: "co_protagonist" as const,
  identityAnchors: ["灰白虎斑猫"],
  immutableTraits: [],
  relationshipNotes: "",
  dramaticFunction: "",
  visualRisks: [],
  references: [],
};

describe("SubjectAssistantPanel", () => {
  it("only runs analysis after an explicit click", async () => {
    const wrapper = mount(SubjectAssistantPanel, { props: { subject, run: null } });

    expect(wrapper.text()).toContain("缺少 4 项");
    expect(wrapper.emitted("run")).toBeUndefined();
    await wrapper.get("button[data-action='run']").trigger("click");

    expect(wrapper.emitted("run")).toHaveLength(1);
    expect(wrapper.emitted("apply")).toBeUndefined();
  });

  it("shows a field diff and applies only checked fields", async () => {
    const wrapper = mount(SubjectAssistantPanel, {
      props: {
        subject,
        run: {
          id: "run-1",
          status: "awaiting_review",
          missingFields: ["immutableTraits", "dramaticFunction"],
          proposal: {
            identityAnchors: ["灰白虎斑猫"],
            immutableTraits: ["额头 M 纹不变"],
            relationshipNotes: "",
            dramaticFunction: "帮助小满收画",
            visualRisks: [],
            rationale: {},
            warnings: [],
          },
        },
      },
    });

    await wrapper.get("input[value='immutableTraits']").setValue(true);
    await wrapper.get("button[data-action='apply']").trigger("click");

    const payload = wrapper.emitted("apply")?.[0]?.[0] as Record<string, unknown>;
    expect(payload.acceptedFields).toEqual(["immutableTraits"]);
    expect((payload.finalDraft as typeof subject).immutableTraits).toEqual(["额头 M 纹不变"]);
  });
});
