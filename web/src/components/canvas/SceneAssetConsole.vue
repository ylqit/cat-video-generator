<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { api } from "../../api/client";
import type {
  ProjectGraph,
  SceneDto,
  SceneVisualAssetsDto,
} from "../../api/types";
import SceneLookWorkbench from "../SceneLookWorkbench.vue";
import VisualAssetWorkbench from "../VisualAssetWorkbench.vue";

const props = defineProps<{
  projectId: string;
  sceneId: string;
}>();

const loading = ref(false);
const errorText = ref("");
const graph = ref<ProjectGraph | null>(null);
const visualAssets = ref<SceneVisualAssetsDto | null>(null);
let loadSequence = 0;

const scene = computed<SceneDto | null>(() => (
  graph.value?.scenes.find((item) => item.id === props.sceneId) ?? null
));
const continuity = computed<Record<string, unknown>>(() => {
  const note = scene.value?.contextNote;
  if (!note) return {};
  try {
    const document = JSON.parse(note) as Record<string, unknown>;
    const value = document.continuity;
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
});

function textList(field: string): string {
  const value = continuity.value[field];
  return Array.isArray(value) && value.length ? value.join("、") : "无额外要求";
}

async function load() {
  const sequence = ++loadSequence;
  loading.value = true;
  errorText.value = "";
  try {
    const nextGraph = await api.project(props.projectId);
    if (sequence !== loadSequence) return;
    graph.value = nextGraph;
    const nextAssets = await api.sceneVisualAssets(props.sceneId);
    if (sequence !== loadSequence) return;
    visualAssets.value = nextAssets;
  } catch (error) {
    if (sequence === loadSequence) {
      errorText.value = error instanceof Error ? error.message : String(error);
    }
  } finally {
    if (sequence === loadSequence) loading.value = false;
  }
}

watch(() => props.sceneId, () => void load(), { immediate: true });
</script>

<template>
  <section class="scene-asset-console" :aria-busy="loading">
    <header>
      <div>
        <b>{{ scene?.title ?? "场景资产准备" }}</b>
        <small>
          {{ String(continuity.location ?? "待确认地点") }} ·
          {{ continuity.environment === "outdoor" ? "户外" : continuity.environment === "indoor" ? "室内" : "环境待确认" }} ·
          {{ String(continuity.timeWeather ?? "时间天气待确认") }}
        </small>
      </div>
      <span v-if="loading">同步中</span>
      <span v-else-if="visualAssets?.readiness.canCompileShotPrompt" class="ready">可生成锚点</span>
      <span v-else class="blocked">资产未就绪</span>
    </header>

    <p v-if="errorText" class="error">{{ errorText }}</p>

    <div class="continuity-grid">
      <div><small>关键装饰</small><span>{{ textList("decorations") }}</span></div>
      <div><small>核心道具</small><span>{{ textList("props") }}</span></div>
      <div><small>换场原因</small><span>{{ String(continuity.transitionReason || "首场景，无需换场") }}</span></div>
    </div>

    <div v-if="visualAssets?.readiness" class="readiness-strip">
      <span
        v-for="slot in visualAssets.readiness.requiredSlots"
        :key="slot.key"
        :class="slot.status"
      >
        {{ slot.displayName }} · {{ slot.status === "ready" ? "就绪" : slot.status === "stale" ? "过期" : "缺失" }}
      </span>
      <span :class="visualAssets.readiness.sceneLookStatus === 'approved' ? 'ready' : visualAssets.readiness.sceneLookStatus">
        Scene Look · {{ visualAssets.readiness.sceneLookStatus === "approved" ? "就绪" : visualAssets.readiness.sceneLookStatus === "stale" ? "过期" : "缺失" }}
      </span>
    </div>

    <ul v-if="visualAssets?.readiness.blockers.length" class="blockers">
      <li v-for="blocker in visualAssets.readiness.blockers" :key="blocker">{{ blocker }}</li>
    </ul>

    <div v-if="scene && graph" class="workbench-actions">
      <VisualAssetWorkbench
        :project-id="projectId"
        :scene="scene"
        :assets="graph.assets"
        @refreshed="load"
      />
      <SceneLookWorkbench
        :project-id="projectId"
        :scene="scene"
        :assets="graph.assets"
        required
        @refreshed="load"
      />
    </div>
  </section>
</template>

<style scoped>
.scene-asset-console { display: grid; gap: 12px; color: #edf1f7; }
.scene-asset-console > header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.scene-asset-console header b { display: block; font-size: 18px; }.scene-asset-console header small { display: block; margin-top: 4px; color: #96a2b3; }
.scene-asset-console header > span { padding: 5px 9px; border-radius: 999px; color: #e4b36d; background: #322718; }.scene-asset-console header > span.ready { color: #8ad7b0; background: #173225; }
.error { margin: 0; padding: 9px 11px; border: 1px solid #7d3f49; border-radius: 8px; color: #f2a9b2; background: #2a1519; }
.continuity-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }.continuity-grid div { display: grid; gap: 5px; min-height: 64px; padding: 9px; border: 1px solid #39414d; border-radius: 9px; background: #1b2027; }.continuity-grid small { color: #8e99a8; }.continuity-grid span { font-size: 13px; line-height: 1.45; }
.readiness-strip { display: flex; flex-wrap: wrap; gap: 7px; }.readiness-strip span { padding: 5px 8px; border: 1px solid #66502d; border-radius: 999px; color: #ddb673; }.readiness-strip span.ready { border-color: #336b52; color: #89d8b1; }.readiness-strip span.stale { border-color: #80513a; color: #e09a75; }
.blockers { margin: 0; padding: 10px 12px 10px 30px; border-radius: 9px; color: #efbd72; background: #2b2113; }
.workbench-actions { display: grid; gap: 9px; }.workbench-actions :deep(.visual-asset-card),.workbench-actions :deep(.scene-look-card) { min-height: 94px; }
@media (max-width: 720px) { .continuity-grid { grid-template-columns: 1fr; } }
</style>
