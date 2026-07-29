import { defineStore } from "pinia";

import { api } from "../api/client";
import type { CanonAsset } from "../api/types";

/** Canon资产缓存：人物、猫咪、画风三类参考图。 */
export const useCanonStore = defineStore("canon", {
  state: () => ({
    items: [] as CanonAsset[],
    loaded: false,
  }),
  getters: {
    /** 按role取最新一张已批准Canon图。 */
    latestByRole(): (role: string) => CanonAsset | undefined {
      return (role: string) =>
        [...this.items].reverse().find((item) => item.role === role);
    },
  },
  actions: {
    async fetch(force = false) {
      if (this.loaded && !force) {
        return;
      }
      this.items = await api.listCanon();
      this.loaded = true;
    },
    async upload(role: string, file: File) {
      await api.uploadCanon(role, file);
      await this.fetch(true);
    },
  },
});
