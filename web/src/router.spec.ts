import { afterEach, describe, expect, it } from "vitest";

import { router } from "./router";
import ProjectListView from "./views/ProjectListView.vue";
import ProjectWorkspaceView from "./views/ProjectWorkspaceView.vue";

describe("Creator-only routes", () => {
  afterEach(async () => { await router.replace("/projects"); });

  it("sends the application entry to the project list", async () => {
    await router.push("/");
    expect(router.currentRoute.value).toMatchObject({ fullPath: "/projects", name: "projects" });
    expect(router.currentRoute.value.matched.at(-1)?.components?.default).toBe(ProjectListView);
  });

  it.each([
    ["project-script", "/projects/project-42/script", "script"],
    ["project-assets", "/projects/project-42/assets", "assets"],
    ["project-production", "/projects/project-42/production", "production"],
  ])("registers the canonical %s workspace", (name, path, workspaceModule) => {
    const route = router.resolve({ name, params: { projectId: "project-42" } });
    expect(route.path).toBe(path);
    expect(route.meta.workspaceModule).toBe(workspaceModule);
    expect(route.matched.at(-1)?.components?.default).toBe(ProjectWorkspaceView);
  });

  it.each(["/canvas", "/canvas/project-42", "/studio?project=project-42"])(
    "does not retain the removed route %s",
    async (path) => {
      await router.push(path);
      expect(router.currentRoute.value.name).not.toBe("project-production");
      expect(router.currentRoute.value.matched.at(-1)?.path).toBe("/:pathMatch(.*)*");
    },
  );
});
