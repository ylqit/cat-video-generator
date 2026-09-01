import { createRouter, createWebHistory } from "vue-router";

import ProjectListView from "./views/ProjectListView.vue";
import ProjectWorkspaceView from "./views/ProjectWorkspaceView.vue";
import RuntimeSettingsView from "./views/RuntimeSettingsView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/projects" },
    { path: "/projects", component: ProjectListView, name: "projects" },
    {
      path: "/projects/:projectId/script",
      component: ProjectWorkspaceView,
      name: "project-script",
      meta: { workspaceModule: "script" },
    },
    {
      path: "/projects/:projectId/assets",
      component: ProjectWorkspaceView,
      name: "project-assets",
      meta: { workspaceModule: "assets" },
    },
    {
      path: "/projects/:projectId/production",
      component: ProjectWorkspaceView,
      name: "project-production",
      meta: { workspaceModule: "production" },
    },
    { path: "/settings", component: RuntimeSettingsView },
    { path: "/:pathMatch(.*)*", component: () => import("./views/NotFoundView.vue") },
  ],
});
