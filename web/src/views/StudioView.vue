<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, ApiError } from "../api/client";
import type {
  AssetDto,
  EpisodeDto,
  EpisodePromptPreview,
  PipelineSettings,
  PromptOverrides,
  Slot,
  WorkflowNodeDto,
} from "../api/types";
import AssetReviewPanel from "../components/AssetReviewPanel.vue";
import DayBriefPanel from "../components/DayBriefPanel.vue";
import DeliveryPanel from "../components/DeliveryPanel.vue";
import PlanCreateDialog from "../components/PlanCreateDialog.vue";
import ScriptEditorPanel from "../components/ScriptEditorPanel.vue";
import StatusBadge from "../components/StatusBadge.vue";
import WorkflowNodeDrawer from "../components/WorkflowNodeDrawer.vue";
import { useJobsStore } from "../stores/jobs";
import { useRunsStore } from "../stores/runs";

type Stage = "dayBrief" | "script" | "visual" | "video" | "review";
const STAGES: Array<{ key: Stage; label: string }> = [
  { key: "dayBrief", label: "总导演" },
  { key: "script", label: "三集导演" },
  { key: "visual", label: "视觉锚点" },
  { key: "video", label: "视频成片" },
  { key: "review", label: "审核交付" },
];
const AUTO_STAGE_KEYS: Array<"dayBrief" | "script" | "visual" | "video"> = [
  "dayBrief",
  "script",
  "visual",
  "video",
];
const SLOT_LABEL: Record<Slot, string> = { morning: "上午", noon: "中午", evening: "傍晚" };
const FOCUS_LABEL = { cat_lead: "猫咪主活动", person_lead: "人物主活动", balanced: "人猫平衡" };

const route = useRoute();
const router = useRouter();
const runs = useRunsStore();
const jobs = useJobsStore();
const createDialog = ref<InstanceType<typeof PlanCreateDialog>>();
const loading = ref(false);
const drawerOpen = ref(false);
const selectedNode = ref<WorkflowNodeDto | null>(null);
const previews = reactive<Record<string, EpisodePromptPreview>>({});
const drafts = reactive<Record<string, PromptOverrides>>({});
const overrideEnabled = reactive<Record<string, boolean>>({});
let poller: number | undefined;

const runId = computed(() => String(route.query.run ?? ""));
const graph = computed(() => (runId.value ? runs.graphs[runId.value] : undefined));
const episodes = computed(() => graph.value?.episodes ?? []);
const activeStage = computed<Stage>(() => {
  const value = String(route.query.stage ?? "");
  return STAGES.some((item) => item.key === value) ? (value as Stage) : "dayBrief";
});
const selectedSlot = computed<Slot | null>(() => {
  const value = String(route.query.slot ?? "");
  return ["morning", "noon", "evening"].includes(value) ? (value as Slot) : null;
});
const failedNodes = computed(() =>
  (graph.value?.workflowNodes ?? []).filter((item) => item.error || ["failed", "planning_rejected", "rejected"].includes(item.status)),
);
const settings = computed<PipelineSettings | null>(() => graph.value?.run.pipelineSettings ?? null);
const canDeliver = computed(() => graph.value?.run.status === "ready");

function setLocation(stage: Stage, slot: Slot | null = selectedSlot.value, node?: string) {
  router.replace({
    path: "/studio",
    query: {
      run: runId.value || undefined,
      stage,
      slot: slot ?? undefined,
      node: node ?? undefined,
    },
  });
}

async function refresh() {
  if (!runId.value) {
    await runs.fetchRuns();
    return;
  }
  loading.value = true;
  try {
    await runs.fetchGraph(runId.value);
    if (!route.query.stage) {
      const backend = runs.graphs[runId.value]?.run.currentStage as Stage | undefined;
      setLocation(backend && STAGES.some((item) => item.key === backend) ? backend : "dayBrief", null);
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    loading.value = false;
  }
}

async function refreshAll() {
  const settled = await jobs.refreshActive();
  if (runId.value) await runs.fetchGraph(runId.value);
  if (settled) await runs.fetchRuns();
}

watch(runId, () => {
  Object.keys(previews).forEach((key) => delete previews[key]);
  Object.keys(drafts).forEach((key) => delete drafts[key]);
  Object.keys(overrideEnabled).forEach((key) => delete overrideEnabled[key]);
  void refresh();
});

watch(
  [graph, () => route.query.node],
  () => {
    const nodeId = String(route.query.node ?? "");
    if (!nodeId || !graph.value) return;
    const node = graph.value.workflowNodes?.find((item) => item.id === nodeId);
    if (node) {
      selectedNode.value = node;
      drawerOpen.value = true;
    }
  },
  { immediate: true },
);

function nodeFor(id: string) {
  return graph.value?.workflowNodes?.find((item) => item.id === id);
}

function openNode(node: WorkflowNodeDto | undefined) {
  if (!node || !graph.value) return;
  selectedNode.value = node;
  drawerOpen.value = true;
  setLocation(activeStage.value, node.slot, node.id);
}

function setDrawerOpen(value: boolean) {
  drawerOpen.value = value;
  if (!value && route.query.node) {
    selectedNode.value = null;
    setLocation(activeStage.value, selectedSlot.value);
  }
}

function assetsFor(episode: EpisodeDto, roles: string[]): AssetDto[] {
  return (graph.value?.assets ?? []).filter(
    (asset) => asset.episodeId === episode.id && roles.includes(asset.role),
  );
}

function reviewsFor(asset: AssetDto) {
  return (graph.value?.reviews ?? []).filter((review) => review.assetId === asset.id);
}

async function loadPreview(episode: EpisodeDto) {
  if (previews[episode.id]) return;
  const preview = await api.getPromptPreview(episode.id);
  previews[episode.id] = preview;
  const saved = preview.overrideState.values;
  overrideEnabled[episode.id] = preview.overrideState.enabled;
  drafts[episode.id] = {
    look: saved.look ?? preview.look,
    opening_anchor: saved.opening_anchor ?? preview.openingAnchor,
    video: saved.video ?? preview.videoSections[0]?.prompt ?? "",
  };
}

async function saveOverrides(episode: EpisodeDto) {
  const preview = previews[episode.id];
  const draft = drafts[episode.id];
  if (!preview || !draft) return;
  const overrides: PromptOverrides = {};
  if (draft.look?.trim() && draft.look !== preview.look) overrides.look = draft.look.trim();
  if (draft.opening_anchor?.trim() && draft.opening_anchor !== preview.openingAnchor) {
    overrides.opening_anchor = draft.opening_anchor.trim();
  }
  if (draft.video?.trim() && draft.video !== preview.videoSections[0]?.prompt) overrides.video = draft.video.trim();
  await api.savePromptOverrides(episode.id, overrides, overrideEnabled[episode.id] ?? false);
  ElMessage.success(
    overrideEnabled[episode.id]
      ? "高级Prompt覆盖已重新确认；只有尚未提交的节点会使用它"
      : "已关闭高级Prompt覆盖，后续节点将使用结构化脚本编译结果",
  );
  delete previews[episode.id];
  delete drafts[episode.id];
  delete overrideEnabled[episode.id];
  await refresh();
  await loadPreview(episode);
}

async function onScriptSaved(episodeId: string) {
  delete previews[episodeId];
  delete drafts[episodeId];
  delete overrideEnabled[episodeId];
  await refresh();
}

async function confirmPaid(message: string) {
  await ElMessageBox.confirm(message, "确认Ark付费调用", {
    confirmButtonText: "确认生成",
    cancelButtonText: "取消",
    type: "warning",
  });
}

function isDialogCancellation(error: unknown) {
  return error === "cancel" || error === "close";
}

async function generateVisuals(episode: EpisodeDto) {
  try {
    await confirmPaid(`生成${SLOT_LABEL[episode.slot]}定妆图和开场锚点？`);
    const accepted = await api.generateVisuals(episode.id, true);
    jobs.track(accepted);
    ElMessage.info("视觉任务已提交");
  } catch (error) {
    if (!isDialogCancellation(error)) ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

async function generateVideo(episode: EpisodeDto) {
  try {
    const count = episode.renderPlan.sections.length;
    await confirmPaid(
      `生成${SLOT_LABEL[episode.slot]}${episode.script.duration_seconds}秒视频？预计${count}次Seedance任务（含官方延展）。`,
    );
    const accepted = await api.generate(episode.runId, { slot: episode.slot, allowPaidGeneration: true });
    jobs.track(accepted);
    ElMessage.info("视频任务已提交");
  } catch (error) {
    if (!isDialogCancellation(error)) ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

async function continuePipeline() {
  if (!runId.value) return;
  try {
    await confirmPaid("继续当前Run会按自动设置调用尚未完成的Ark节点，是否继续？");
    const accepted = await api.continueRun(runId.value);
    jobs.track(accepted);
  } catch (error) {
    if (!isDialogCancellation(error)) ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

async function saveSettings(next: PipelineSettings) {
  if (!runId.value) return;
  await api.savePipelineSettings(runId.value, next);
  await refresh();
}

function updateStageMode(
  key: "dayBrief" | "script" | "visual" | "video",
  value: string | number | boolean,
) {
  if (!settings.value) return;
  void saveSettings({
    ...settings.value,
    [key]: value ? "auto" : "manual",
  });
}

onMounted(() => {
  void refresh();
  poller = window.setInterval(() => void refreshAll(), 5000);
});
onBeforeUnmount(() => window.clearInterval(poller));
</script>

<template>
  <div class="page studio-page" v-loading="loading">
    <template v-if="!runId">
      <div class="page-header">
        <div><h2>统一生产工作台</h2><p class="muted">总导演 → 三集导演 → 视觉锚点 → 视频成片 → 审核交付</p></div>
        <el-button type="primary" @click="createDialog?.open()">新建全天计划</el-button>
      </div>
      <el-empty v-if="!runs.runs.length" description="还没有生产Run" />
      <el-card v-for="item in runs.runs" :key="item.id" class="run-card" shadow="hover" @click="router.push(`/studio?run=${item.id}`)">
        <div class="row"><strong>{{ item.contentDate }} · {{ item.theme ?? "未完成总导演" }}</strong><StatusBadge :status="item.status" /></div>
        <div class="muted">{{ item.nextAction }}</div>
      </el-card>
    </template>

    <template v-else-if="graph">
      <div class="page-header">
        <div>
          <div class="row"><h2>{{ graph.run.contentDate }} · {{ graph.run.theme ?? "全天计划" }}</h2><StatusBadge :status="graph.run.status" /></div>
          <p class="muted">{{ graph.run.nextAction }}</p>
        </div>
        <div class="row"><el-button @click="router.push('/studio')">切换Run</el-button><el-button type="primary" @click="continuePipeline">继续流程</el-button></div>
      </div>

      <el-alert
        v-for="node in failedNodes"
        :key="node.id"
        type="error"
        :closable="false"
        show-icon
        class="persistent-error"
        @click="openNode(node)"
      >
        <template #title>{{ node.label }}：{{ node.error?.message ?? node.nextAction ?? "节点未通过" }}</template>
      </el-alert>

      <el-steps :active="STAGES.findIndex((item) => item.key === graph.run.currentStage)" finish-status="success" align-center class="workflow-steps">
        <el-step v-for="stage in STAGES" :key="stage.key" :title="stage.label" />
      </el-steps>
      <el-tabs :model-value="activeStage" @tab-change="(name) => setLocation(name as Stage, null)">
        <el-tab-pane v-for="stage in STAGES" :key="stage.key" :label="stage.label" :name="stage.key" />
      </el-tabs>

      <section v-if="activeStage === 'dayBrief'" class="stage-panel">
        <div class="section-title"><h3>全天总导演</h3><el-button @click="openNode(nodeFor('director:day'))">查看实际Prompt与任务</el-button></div>
        <DayBriefPanel
          v-if="graph.run.dayBrief"
          :run-id="runId"
          :day-brief="graph.run.dayBrief"
          :editable="graph.run.status === 'draft' || graph.run.status === 'planning_review'"
          @saved="refresh"
        />
        <el-empty v-else description="总导演尚未生成DayBrief" />
        <el-card v-if="settings" shadow="never" class="settings-card">
          <template #header><strong>自动推进设置</strong></template>
          <div class="row wrap">
            <span v-for="key in AUTO_STAGE_KEYS" :key="key">
              {{ STAGES.find((item) => item.key === key)?.label }}
              <el-switch
                :model-value="settings[key] === 'auto'"
                inline-prompt active-text="自动" inactive-text="人工"
                @change="(value) => updateStageMode(key, value)"
              />
            </span>
          </div>
        </el-card>
      </section>

      <section v-else-if="activeStage === 'script'" class="stage-panel">
        <div class="section-title"><h3>早中晚时段导演</h3><span class="muted">猫咪默认推动主要信息；每集精确时长决定是否使用官方延展。</span></div>
        <el-card v-for="episode in episodes" :key="episode.id" class="episode-card" shadow="never">
          <template #header>
            <div class="row"><strong>{{ SLOT_LABEL[episode.slot] }} · {{ episode.title }}</strong><el-tag>{{ FOCUS_LABEL[episode.activityFocus] }}</el-tag><el-tag type="info">{{ episode.script.duration_seconds }}秒 · {{ episode.renderPlan.sections.length }}个任务</el-tag><el-button text type="primary" @click="openNode(nodeFor(`director:${episode.slot}`))">导演节点</el-button></div>
          </template>
          <el-descriptions :column="1" border size="small" style="margin-bottom: 12px">
            <el-descriptions-item label="关系弧">{{ episode.relationshipArc }}</el-descriptions-item>
          </el-descriptions>
          <ScriptEditorPanel :episode="episode" :editable="['planned', 'video_pending', 'failed'].includes(episode.status)" @saved="onScriptSaved(episode.id)" />
        </el-card>
      </section>

      <section v-else-if="activeStage === 'visual'" class="stage-panel">
        <div class="section-title"><h3>定妆图与开场锚点</h3><span class="muted">每集仅一张开场锚点，不再生成多图故事板。</span></div>
        <el-card v-for="episode in episodes" :key="episode.id" class="episode-card" shadow="never">
          <template #header><div class="row"><strong>{{ SLOT_LABEL[episode.slot] }} · {{ episode.title }}</strong><el-button @click="loadPreview(episode)">查看/编辑编译Prompt</el-button><el-button type="primary" @click="generateVisuals(episode)">生成视觉锚点</el-button></div></template>
          <el-collapse v-if="previews[episode.id]">
            <el-collapse-item title="编译Prompt与高级覆盖" name="prompt">
              <el-alert
                v-if="previews[episode.id].overrideState.stale"
                type="warning"
                :closable="false"
                title="上游结构化脚本已经变化，旧Prompt覆盖已过期"
                description="只有重新检查并保存后，覆盖才允许用于下一次收费调用。"
                style="margin-bottom: 10px"
              />
              <div class="advanced-toggle">
                <span>高级Prompt覆盖</span>
                <el-switch v-model="overrideEnabled[episode.id]" active-text="启用" inactive-text="关闭" />
              </div>
              <div class="prompt-label">定妆图Prompt</div><el-input v-model="drafts[episode.id].look" type="textarea" :rows="7" :readonly="!overrideEnabled[episode.id]" />
              <div class="prompt-label">开场锚点Prompt</div><el-input v-model="drafts[episode.id].opening_anchor" type="textarea" :rows="7" :readonly="!overrideEnabled[episode.id]" />
              <el-button style="margin-top: 8px" @click="saveOverrides(episode)">{{ overrideEnabled[episode.id] ? '确认并启用覆盖' : '确认使用编译Prompt' }}</el-button>
            </el-collapse-item>
          </el-collapse>
          <div class="media-grid">
            <div v-for="asset in assetsFor(episode, ['look_reference', 'opening_anchor'])" :key="asset.id">
              <AssetReviewPanel :asset="asset" :reviews="reviewsFor(asset)" @reviewed="refresh" />
            </div>
          </div>
          <div class="node-links"><el-button size="small" @click="openNode(nodeFor(`look:${episode.slot}`))">定妆节点</el-button><el-button size="small" @click="openNode(nodeFor(`opening-anchor:${episode.slot}`))">开场锚点节点</el-button></div>
        </el-card>
      </section>

      <section v-else-if="activeStage === 'video'" class="stage-panel">
        <div class="section-title"><h3>Seedance视频成片</h3><span class="muted">中长视频从上一版末尾续写；新增尾段QC通过后以stream copy无重编码封装。</span></div>
        <el-card v-for="episode in episodes" :key="episode.id" class="episode-card" shadow="never">
          <template #header><div class="row"><strong>{{ SLOT_LABEL[episode.slot] }} · {{ episode.script.duration_seconds }}秒</strong><el-tag>{{ episode.renderPlan.mode }}</el-tag><el-button @click="loadPreview(episode)">查看Prompt与RenderPlan</el-button><el-button type="primary" @click="generateVideo(episode)">生成视频</el-button></div></template>
          <el-collapse v-if="previews[episode.id]">
            <el-collapse-item title="RenderPlan与视频Prompt" name="video-prompt">
              <pre class="json-view">{{ JSON.stringify(previews[episode.id].renderPlan, null, 2) }}</pre>
              <el-alert
                v-if="previews[episode.id].overrideState.stale"
                type="warning"
                :closable="false"
                title="上游结构化脚本已经变化，旧Prompt覆盖已过期"
                description="请重新检查后再显式确认；未经确认的覆盖不会进入收费请求。"
                style="margin-bottom: 10px"
              />
              <div class="advanced-toggle">
                <span>高级Prompt覆盖</span>
                <el-switch v-model="overrideEnabled[episode.id]" active-text="启用" inactive-text="关闭" />
              </div>
              <el-input v-model="drafts[episode.id].video" type="textarea" :rows="12" :readonly="!overrideEnabled[episode.id]" />
              <div class="muted">覆盖只作用于初始区段；延展区段根据对应镜头重新编译。</div>
              <el-button style="margin-top: 8px" @click="saveOverrides(episode)">{{ overrideEnabled[episode.id] ? '确认并启用覆盖' : '确认使用编译Prompt' }}</el-button>
              <div v-for="section in previews[episode.id].videoSections.slice(1)" :key="section.order" class="extension-prompt">
                <strong>延展{{ section.order }} · {{ section.durationSeconds }}秒</strong><pre>{{ section.prompt }}</pre>
              </div>
            </el-collapse-item>
          </el-collapse>
          <div class="media-grid">
            <AssetReviewPanel v-for="asset in assetsFor(episode, ['video_intermediate', 'video'])" :key="asset.id" :asset="asset" :reviews="reviewsFor(asset)" @reviewed="refresh" />
          </div>
          <div class="node-links"><el-button v-for="section in episode.renderPlan.sections" :key="section.order" size="small" @click="openNode(nodeFor(`video:${episode.slot}:${section.order}`))">{{ section.order === 1 ? '初始视频' : `延展${section.order}` }}</el-button></div>
        </el-card>
      </section>

      <section v-else class="stage-panel">
        <div class="section-title"><h3>人工审核与1/2/3交付</h3><span class="muted">最终视频必须逐条观看后批准。</span></div>
        <el-card v-for="episode in episodes" :key="episode.id" class="episode-card" shadow="never">
          <template #header><div class="row"><strong>{{ SLOT_LABEL[episode.slot] }} · {{ episode.title }}</strong><StatusBadge :status="episode.status" /><el-button text @click="openNode(nodeFor(`content-review:${episode.slot}`))">审核节点</el-button></div></template>
          <AssetReviewPanel v-for="asset in assetsFor(episode, ['video'])" :key="asset.id" :asset="asset" :reviews="reviewsFor(asset)" :max-width="360" @reviewed="refresh" />
        </el-card>
        <DeliveryPanel :run-id="runId" :can-deliver="canDeliver" />
      </section>

      <WorkflowNodeDrawer
        :model-value="drawerOpen"
        :node="selectedNode"
        :graph="graph"
        @update:model-value="setDrawerOpen"
        @changed="refresh"
      />
    </template>
    <PlanCreateDialog ref="createDialog" @created="runs.fetchRuns()" />
  </div>
</template>

<style scoped>
.studio-page { max-width: 1500px; margin: 0 auto; }
.page-header, .row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.page-header h2 { margin: 0; }
.wrap { flex-wrap: wrap; }
.run-card { margin-bottom: 10px; cursor: pointer; background: #16181d; border-color: #2b2d33; }
.workflow-steps { margin: 24px 0 14px; }
.stage-panel { padding: 4px 0 28px; }
.section-title { display: flex; align-items: baseline; gap: 14px; margin-bottom: 12px; }
.section-title h3 { margin: 0; }
.episode-card, .settings-card { margin-bottom: 14px; background: #16181d; border-color: #2b2d33; }
.persistent-error { margin-bottom: 8px; cursor: pointer; }
.media-grid { display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-start; }
.node-links { margin-top: 12px; }
.prompt-label { margin: 8px 0 4px; color: #9ca3af; font-size: 12px; }
.advanced-toggle { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.json-view, .extension-prompt pre { white-space: pre-wrap; word-break: break-word; background: #111318; padding: 10px; border-radius: 6px; max-height: 320px; overflow: auto; font-size: 12px; }
.extension-prompt { margin-top: 12px; }
</style>
