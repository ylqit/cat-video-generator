import { createRouter, createWebHistory } from "vue-router";

import CanonView from "./views/CanonView.vue";
import ProjectListView from "./views/ProjectListView.vue";
import RuntimeSettingsView from "./views/RuntimeSettingsView.vue";
import StudioView from "./views/StudioView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/studio" },
    { path: "/studio", component: StudioView },
    {
      path: "/studio/projects/:projectId/shots/:shotId",
      component: StudioView,
      name: "shot-generation-workspace",
    },
    { path: "/projects", component: ProjectListView },
    { path: "/canon", component: CanonView },
    { path: "/settings", component: RuntimeSettingsView },
  ],
});
