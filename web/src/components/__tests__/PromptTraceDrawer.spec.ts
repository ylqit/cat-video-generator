import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import PromptTraceDrawer from "../canvas/PromptTraceDrawer.vue";

describe("PromptTraceDrawer", () => {
  it("separates exact application prompt from unobservable provider rewriting", () => {
    const wrapper = mount(PromptTraceDrawer, {
      props: {
        modelValue: true,
        prompt: {
          id: "prompt-1",
          stepId: "step-1",
          purpose: "story_candidate",
          templateName: "story.relationship.v1",
          templateVersion: "1.0.0",
          systemPrompt: "你是短剧策划。",
          userPrompt: "主题：雨前收画。",
          finalPrompt: "你是短剧策划。\n主题：雨前收画。",
          providerInternalTransform: "not_observable",
          providerRequestSnapshot: { temperature: 0.7 },
          inputSnapshot: { subjectRevisionIds: ["subject-rev-1", "subject-rev-2"] },
          provider: "ark",
          model: "doubao-test",
          parameters: {},
          rawResponse: { id: "provider-response" },
          structuredResponse: { title: "雨前收画" },
          retryChain: [],
          status: "succeeded",
        },
      },
      global: {
        stubs: {
          ElDrawer: { template: "<section><slot /></section>" },
          ElTabs: { template: "<div><slot /></div>" },
          ElTabPane: { template: "<div><slot /></div>" },
          ElAlert: { template: "<div><slot />供应商内部改写不可见</div>" },
          ElDescriptions: { template: "<dl><slot /></dl>" },
          ElDescriptionsItem: { template: "<div><slot /></div>" },
          ElTag: { template: "<span><slot /></span>" },
        },
      },
    });

    expect(wrapper.text()).toContain("应用发送的精确 Prompt");
    expect(wrapper.text()).toContain("主题：雨前收画");
    expect(wrapper.text()).toContain("供应商内部改写不可见");
    expect(wrapper.text()).toContain("story.relationship.v1");
  });
});
