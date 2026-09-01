import { mount } from "@vue/test-utils";
import { nextTick, reactive } from "vue";
import { describe, expect, it, vi } from "vitest";

import CreatorShotEditor from "../production/CreatorShotEditor.vue";

vi.mock("../../api/client", () => ({
  creatorApi: { replaceShots: vi.fn() },
}));
vi.mock("element-plus", () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}));

describe("CreatorShotEditor reactive inputs", () => {
  it("creates editable drafts from parent-owned reactive shots", async () => {
    const creatorState = reactive({
      projectId: "project-1",
      projectTitle: "纸星星",
      contentDate: "2026-09-01",
      version: 1,
      briefBody: "brief",
      storyCandidates: [],
      currentStory: { title: "纸星星", body: "正文" },
      targetDurationSeconds: 10,
      aspectRatio: "9:16" as const,
      qualityTier: "quick" as const,
      referenceBindings: [],
      updatedAt: "2026-09-01T00:00:00Z",
    });
    const shots = reactive([{
      id: "shot-1",
      projectId: "project-1",
      sortOrder: 1,
      version: 1,
      title: "纸星星迎光",
      direction: "孩子与猫咪在窗边完成连续动作。",
      durationSeconds: 10,
      sceneLabel: "清晨窗边",
      referenceBindings: [],
    }]);

    const wrapper = mount(CreatorShotEditor, {
      props: { projectId: "project-1", creatorState, shots },
    });
    await nextTick();

    expect(wrapper.get("[aria-label='镜头编辑器']").text()).toContain("纸星星迎光");
    expect(wrapper.get("input").element.value).toBe("纸星星迎光");
    expect(wrapper.text()).not.toContain("could not be cloned");
  });
});
