import { createRouter, createWebHistory } from "vue-router";

import CanonView from "./views/CanonView.vue";
import ProjectListView from "./views/ProjectListView.vue";
import StudioView from "./views/StudioView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/studio" },
    { path: "/studio", component: StudioView },
    { path: "/projects", component: ProjectListView },
    { path: "/canon", component: CanonView },
  ],
});
