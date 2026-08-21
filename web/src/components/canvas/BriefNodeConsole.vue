<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { CanvasNodeActionDto, CanvasNodeDto } from "../../api/types";
import type { TaskCenterItem } from "../../tasks/taskCenter";

const props = withDefaults(defineProps<{
  node: CanvasNodeDto;
  executions?: TaskCenterItem[];
  busy?: boolean;
}>(), { executions: () => [], busy: false });

const emit = defineEmits<{
  save: [payload: { theme: string; targetDurationSeconds: number; aspectRatio: string }];
  action: [action: CanvasNodeActionDto];
}>();

const theme = ref("");
const targetDurationSeconds = ref(15);
const aspectRatio = ref("9:16");

function resetDraft() {
  theme.value = String(props.node.data.theme ?? "");
  targetDurationSeconds.value = Number(props.node.data.targetDurationSeconds ?? 15);
  aspectRatio.value = String(props.node.data.aspectRatio ?? "9:16");
}

watch(
  () => [props.node.id, props.node.revision, props.node.data.theme],
  resetDraft,
  { immediate: true },
);

const actions = computed(() => props.node.availableActions ?? []);
const dirty = computed(() => (
  theme.value !== String(props.node.data.theme ?? "")
  || targetDurationSeconds.value !== Number(props.node.data.targetDurationSeconds ?? 15)
  || aspectRatio.value !== String(props.node.data.aspectRatio ?? "9:16")
));
const latestExecutions = computed(() => props.executions.slice(0, 3));

function save() {
  const normalizedTheme = theme.value.trim();
  if (!normalizedTheme) return;
  emit("save", {
    theme: normalizedTheme,
    targetDurationSeconds: targetDurationSeconds.value,
    aspectRatio: aspectRatio.value,
  });
}
</script>

<template>
  <section class="brief-console" aria-label="创意简报编辑器">
    <header>
      <div><small>BRIEF</small><h2>{{ node.data.title ?? "创意简报" }}</h2></div>
      <span :class="`status-${node.status ?? node.data.status ?? 'draft'}`">{{ node.status ?? node.data.status ?? "草稿" }}</span>
    </header>

    <div class="brief-layout">
      <label class="theme-field">
        <span>一句话创意</span>
        <textarea v-model="theme" aria-label="一句话创意" placeholder="描述这一集发生的温暖小事件" />
      </label>

      <aside>
        <div class="metadata-grid">
          <label><span>总时长</span><input v-model.number="targetDurationSeconds" type="number" min="8" max="180" aria-label="总时长" /><em>秒</em></label>
          <label><span>画幅</span><select v-model="aspectRatio" aria-label="画幅"><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
        </div>
        <p v-if="node.data.audience"><b>受众</b>{{ node.data.audience }}</p>
        <p v-if="node.data.tone"><b>情绪</b>{{ node.data.tone }}</p>
        <p v-if="node.blocker" class="blocker" role="status"><b>当前阻塞</b>{{ node.blocker }}</p>
      </aside>
    </div>

    <div v-if="latestExecutions.length" class="execution-strip" aria-label="简报执行过程">
      <article v-for="item in latestExecutions" :key="item.key">
        <i :class="`status-${item.status}`" /><span><b>{{ item.label }}</b><small>{{ item.progress?.message ?? item.status }}</small></span>
        <em v-if="item.progress?.percent !== undefined">{{ item.progress.percent }}%</em>
      </article>
    </div>

    <footer>
      <button
        v-for="action in actions"
        :key="action.key"
        type="button"
        :aria-disabled="!action.enabled"
        :title="action.enabled ? action.label : action.disabledReason"
        @click="action.enabled && emit('action', action)"
      >{{ action.label }}</button>
      <button class="primary" type="button" :disabled="busy || !dirty || !theme.trim()" @click="save">保存简报</button>
    </footer>
  </section>
</template>

<style scoped>
.brief-console { box-sizing: border-box; height: 100%; display: grid; grid-template-rows: auto minmax(0, 1fr) auto auto; gap: 14px; padding: 20px 22px 18px; color: #efefef; background: #252525; }
header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-right: 4px; }header div { min-width: 0; display: flex; align-items: baseline; gap: 12px; }header small { color: #858585; font-size: 10px; font-weight: 800; letter-spacing: .14em; }h2 { margin: 0; overflow: hidden; font-size: 18px; text-overflow: ellipsis; white-space: nowrap; }header > span { flex: 0 0 auto; padding: 5px 9px; color: #aaa; background: #333; border-radius: 999px; font-size: 10px; }
.brief-layout { min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 220px; gap: 14px; }.theme-field { min-height: 0; display: grid; grid-template-rows: auto minmax(0, 1fr); gap: 8px; }.theme-field > span,.metadata-grid span { color: #999; font-size: 11px; }.theme-field textarea { box-sizing: border-box; width: 100%; min-height: 118px; padding: 14px; resize: none; color: #f3f3f3; background: #1b1b1b; border: 1px solid #464646; border-radius: 12px; font: inherit; line-height: 1.55; }.theme-field textarea:focus,.metadata-grid input:focus,.metadata-grid select:focus { border-color: #7eaef3; outline: 2px solid rgb(78 139 218 / 32%); }
aside { min-width: 0; display: grid; align-content: start; gap: 9px; padding: 12px; background: #202020; border: 1px solid #383838; border-radius: 12px; }.metadata-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }.metadata-grid label { min-width: 0; display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 5px; }.metadata-grid label > span { grid-column: 1 / -1; }.metadata-grid input,.metadata-grid select { box-sizing: border-box; width: 100%; min-width: 0; height: 38px; padding: 7px 8px; color: #eee; background: #151515; border: 1px solid #444; border-radius: 8px; }.metadata-grid em { color: #888; font-size: 10px; font-style: normal; }aside p { margin: 0; display: grid; gap: 3px; color: #aaa; font-size: 10px; line-height: 1.4; }aside p b { color: #777; }.blocker { padding: 8px; color: #e5ba73 !important; background: #30291e; border-radius: 8px; }
.execution-strip { display: flex; gap: 8px; overflow-x: auto; }.execution-strip article { min-width: 170px; flex: 1; display: grid; grid-template-columns: 8px 1fr auto; align-items: center; gap: 8px; padding: 8px 10px; background: #202020; border-radius: 9px; }.execution-strip i { width: 8px; height: 8px; background: #777; border-radius: 50%; }.execution-strip i.status-running,.execution-strip i.status-queued,.execution-strip i.status-pending { background: #70aaff; }.execution-strip i.status-succeeded,.execution-strip i.status-awaiting_review { background: #62d18f; }.execution-strip i.status-failed,.execution-strip i.status-submission_unknown { background: #e77969; }.execution-strip span { min-width: 0; display: grid; }.execution-strip b { overflow: hidden; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.execution-strip small,.execution-strip em { color: #888; font-size: 9px; font-style: normal; }
footer { display: flex; justify-content: flex-end; gap: 8px; }footer button { min-height: 42px; padding: 9px 13px; color: #ddd; background: #333; border: 1px solid #4a4a4a; border-radius: 9px; cursor: pointer; }footer button[aria-disabled="true"],footer button:disabled { opacity: .4; cursor: not-allowed; }.primary { color: #111; background: #f2f2f2; border-color: #f2f2f2; font-weight: 800; }
@media (max-width: 760px) { .brief-console { overflow: auto; }.brief-layout { grid-template-columns: 1fr; }aside { display: block; }.metadata-grid { margin-bottom: 8px; } }
</style>
