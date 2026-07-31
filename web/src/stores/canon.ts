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
    /** 按语义键（如 person:side）取最新一张Canon图，适配三视图。 */
    bySemanticKey(): (semanticKey: string) => CanonAsset | undefined {
      return (semanticKey: string) =>
        [...this.items]
          .reverse()
          .find((item) => item.semanticKey === semanticKey);
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
    async upload(
      role: string,
      semanticKey: string,
      view: string | null,
      file: File,
    ) {
      await api.uploadCanon(role, semanticKey, view, file);
      await this.fetch(true);
    },
  },
});
