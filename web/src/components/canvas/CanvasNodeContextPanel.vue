<script setup lang="ts">
import { computed } from "vue";

import type { CanvasNodeActionDto, CanvasNodeDto } from "../../api/types";
import type { TaskCenterItem } from "../../tasks/taskCenter";

const props = withDefaults(defineProps<{
  node: CanvasNodeDto;
  embedded?: boolean;
  executions?: TaskCenterItem[];
}>(), { embedded: false, executions: () => [] });
const emit = defineEmits<{
  action: [action: CanvasNodeActionDto];
}>();

const title = computed(() => String(props.node.data.title ?? props.node.objectType ?? props.node.type));
const summary = computed(() => {
  const data = props.node.data;
  if (props.node.type === "BriefNode") {
    return `${String(data.theme ?? "待填写主题")} · ${Number(data.targetDurationSeconds ?? 0)} 秒 · ${String(data.aspectRatio ?? "待定比例")}`;
  }
  if (props.node.type === "SubjectNode") {
    const anchors = Array.isArray(data.identityAnchors) ? data.identityAnchors.join("；") : "待补充身份锚点";
    return `${String(data.kind ?? "主体")} · Revision ${Number(data.revision ?? props.node.revision ?? 1)} · ${anchors}`;
  }
  if (props.node.type === "StoryCandidateNode") return String(data.logline ?? "等待故事候选");
  if (props.node.type === "ShotBeatNode") {
    return `${String(data.action ?? "待填写动作")} · ${Number(data.durationSeconds ?? 0)} 秒 · ${String(data.camera ?? "待设置机位")}`;
  }
  if (props.node.type === "ReferenceAssetNode") {
    const count = Array.isArray(data.assets) ? data.assets.length : Number(Boolean(data.assetId));
    return count ? `已绑定 ${count} 个素材 · ${String(data.semanticRole ?? "通用参考")}` : "尚未绑定素材；可上传、选择历史素材或创建主体。";
  }
  if (props.node.type === "VideoEditNode" || props.node.type === "VideoSegmentNode") {
    return `${String(data.instruction ?? "视频局部重编配方")} · Revision ${Number(data.revision ?? props.node.revision ?? 1)}`;
  }
  return String(data.description ?? data.synopsis ?? data.status ?? props.node.status ?? "打开节点查看可执行操作");
});
const score = computed(() => {
  const scorecard = props.node.data.scorecard;
  if (!scorecard || typeof scorecard !== "object" || Array.isArray(scorecard)) return null;
  const average = (scorecard as Record<string, unknown>).average;
  return typeof average === "number" ? average : null;
});
const detailText = computed(() => {
  if (props.node.type !== "StoryCandidateNode") return "";
  const synopsis = String(props.node.data.synopsis ?? "");
  return synopsis === summary.value ? "" : synopsis;
});
const actions = computed(() => props.node.availableActions ?? []);
</script>

<template>
  <section class="context-panel" :class="{ embedded }" aria-label="节点内容">
    <header>
      <div><small>{{ node.type }}</small><h2>{{ title }}</h2></div>
      <div class="node-meta" aria-label="节点版本与状态">
        <span>{{ node.objectType }}</span>
        <span v-if="node.revision">Revision {{ node.revision }}</span>
        <span>{{ node.status ?? node.data.status ?? "待处理" }}</span>
      </div>
    </header>
    <div class="content-summary">
      <p>{{ summary }}</p>
      <p v-if="detailText" class="detail-text">{{ detailText }}</p>
      <strong v-if="score !== null">综合评分 {{ score }}</strong>
      <small v-if="node.outputs?.length">已产生 {{ node.outputs.length }} 个可追溯产物</small>
    </div>
    <div v-if="executions.length" class="execution-list" aria-label="节点执行过程">
      <h3>执行过程</h3>
      <article v-for="item in executions.slice(0, 5)" :key="item.key">
        <span :class="`status-${item.status}`" /><b>{{ item.label }}</b><small>{{ item.status }}</small>
        <p v-if="item.error">{{ String(item.error.message ?? '任务失败') }}</p>
      </article>
    </div>
    <div v-if="node.blocker" class="blocker" role="status">当前阻塞：{{ node.blocker }}</div>
    <footer>
      <button
        v-for="(action, index) in actions"
        :key="action.key"
        :data-action="action.key"
        :class="{ primary: index === 0 && action.enabled }"
        type="button"
        :aria-disabled="!action.enabled"
        :title="action.enabled ? action.label : action.disabledReason"
        @click="action.enabled && emit('action', action)"
      >{{ action.label }}</button>
    </footer>
  </section>
</template>

<style scoped>
.context-panel { box-sizing: border-box; width: min(520px, calc(100vw - 32px)); padding: 16px; color: #e9eef5; background: #202329; border: 1px solid #3b424d; border-radius: 14px; box-shadow: 0 22px 70px rgb(0 0 0 / 48%); }
.context-panel.embedded { width: 100%; height: 100%; min-height: 0; display: grid; grid-template-rows: auto minmax(0, 1fr) auto auto auto; align-content: start; gap: 12px; padding: 22px; overflow: auto; background: #252525; border: 0; border-radius: 0; box-shadow: none; }
header { display: flex; align-items: start; justify-content: space-between; gap: 16px; padding-right: 2px; }header > div:first-child { min-width: 0; }h2 { margin: 3px 0 0; overflow: hidden; font-size: 18px; text-overflow: ellipsis; white-space: nowrap; }small { color: #7f8b9e; font-size: 10px; letter-spacing: .08em; }.node-meta { display: flex; justify-content: flex-end; gap: 6px; flex-wrap: wrap; }.node-meta span { padding: 5px 8px; color: #aab4c3; background: #30343a; border-radius: 999px; font-size: 9px; }
.content-summary { min-height: 0; padding: 14px; overflow: auto; background: #1d2025; border: 1px solid #343941; border-radius: 11px; }.content-summary > small { display: block; margin-top: 10px; color: #7fa5c9; }p { margin: 0; color: #c5ccd6; line-height: 1.65; }.detail-text { max-width: 920px; margin-top: 10px; color: #949eac; }strong { display: block; margin-top: 12px; color: #f0cb73; }footer { display: flex; justify-content: flex-end; gap: 8px; }footer button { min-height: 42px; padding: 8px 11px; color: #d8e0ea; background: #2c3139; border: 1px solid #414956; border-radius: 8px; cursor: pointer; }.primary { color: #15191f; background: #d8e5f4; border-color: #d8e5f4; font-weight: 700; }
.execution-list { display: grid; gap: 7px; margin-top: 16px; padding-top: 12px; border-top: 1px solid #383d45; }.execution-list h3 { margin: 0 0 3px; font-size: 12px; }.execution-list article { display: grid; grid-template-columns: 9px 1fr auto; align-items: center; gap: 8px; }.execution-list article > span { width: 8px; height: 8px; border-radius: 50%; background: #717b89; }.execution-list article > span.status-running,.execution-list article > span.status-queued { background: #69a5f6; }.execution-list article > span.status-succeeded,.execution-list article > span.status-awaiting_review { background: #60d08d; }.execution-list article > span.status-failed,.execution-list article > span.status-submission_unknown { background: #e97868; }.execution-list article p { grid-column: 2 / 4; color: #e6a08b; font-size: 11px; }.execution-list small { color: #8994a3; }.blocker { margin-top: 14px; padding: 10px; color: #e6bd7c; background: #30291c; border-radius: 8px; }footer button[aria-disabled="true"] { opacity: .45; cursor: not-allowed; }
</style>
