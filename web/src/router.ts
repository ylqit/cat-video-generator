import { createRouter, createWebHistory } from "vue-router";

import CanonView from "./views/CanonView.vue";
import RunDetailView from "./views/RunDetailView.vue";
import RunListView from "./views/RunListView.vue";
import StudioView from "./views/StudioView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/studio" },
    { path: "/studio", component: StudioView },
    { path: "/runs", component: RunListView },
    { path: "/runs/:id", component: RunDetailView, props: true },
    { path: "/canon", component: CanonView },
  ],
});
