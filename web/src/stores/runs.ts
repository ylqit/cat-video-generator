import { defineStore } from "pinia";

import { api } from "../api/client";
import type { RunGraph, RunSummary } from "../api/types";

/** Run列表与详情图缓存；详情页轮询直接写入缓存。 */
export const useRunsStore = defineStore("runs", {
  state: () => ({
    runs: [] as RunSummary[],
    graphs: {} as Record<string, RunGraph>,
    loading: false,
  }),
  actions: {
    async fetchRuns() {
      this.loading = true;
      try {
        this.runs = await api.listRuns();
      } finally {
        this.loading = false;
      }
    },
    async fetchGraph(runId: string) {
      this.graphs[runId] = await api.runGraph(runId);
    },
  },
});
