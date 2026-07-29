import { defineStore } from "pinia";

import { api } from "../api/client";
import type { Job, JobAccepted } from "../api/types";

/** 后台任务登记：dedupKey → 任务，用于按钮置灰与状态展示。 */
export const useJobsStore = defineStore("jobs", {
  state: () => ({
    byDedupKey: {} as Record<string, Job>,
  }),
  getters: {
    isActive(): (dedupKey: string) => boolean {
      return (dedupKey: string) => {
        const job = this.byDedupKey[dedupKey];
        return (
          job !== undefined &&
          (job.status === "queued" || job.status === "running")
        );
      };
    },
    jobFor(): (dedupKey: string) => Job | undefined {
      return (dedupKey: string) => this.byDedupKey[dedupKey];
    },
    hasActive(): boolean {
      return Object.values(this.byDedupKey).some(
        (job) => job.status === "queued" || job.status === "running",
      );
    },
  },
  actions: {
    track(accepted: JobAccepted) {
      this.byDedupKey[accepted.dedupKey] = {
        ...accepted,
        createdAt: new Date().toISOString(),
        startedAt: null,
        finishedAt: null,
        result: null,
        error: null,
      };
    },
    /** 刷新全部活跃任务；返回是否有任务刚进入终态。 */
    async refreshActive(): Promise<boolean> {
      const active = Object.values(this.byDedupKey).filter(
        (job) => job.status === "queued" || job.status === "running",
      );
      let settled = false;
      for (const job of active) {
        const fresh = await api.job(job.jobId);
        this.byDedupKey[fresh.dedupKey] = fresh;
        if (fresh.status === "succeeded" || fresh.status === "failed") {
          settled = true;
        }
      }
      return settled;
    },
  },
});
