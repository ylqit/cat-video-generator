import { createRouter, createWebHistory } from "vue-router";

import CanonView from "./views/CanonView.vue";
import RunDetailView from "./views/RunDetailView.vue";
import RunListView from "./views/RunListView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/runs" },
    { path: "/runs", component: RunListView },
    { path: "/runs/:id", component: RunDetailView, props: true },
    { path: "/canon", component: CanonView },
  ],
});
