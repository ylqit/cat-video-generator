import { createRouter, createWebHistory } from "vue-router";

import CanonView from "./views/CanonView.vue";
import RunListView from "./views/RunListView.vue";
import StudioView from "./views/StudioView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/studio" },
    { path: "/studio", component: StudioView },
    { path: "/runs", component: RunListView },
    {
      path: "/runs/:id",
      redirect: (to) => ({
        path: "/studio",
        query: { ...to.query, run: String(to.params.id) },
      }),
    },
    { path: "/canon", component: CanonView },
  ],
});
