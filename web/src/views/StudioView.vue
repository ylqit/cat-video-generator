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
  JobAccepted,
} from "../api/types";
import AssetReviewPanel from "../components/AssetReviewPanel.vue";
import CrossSlotReferencesPanel from "../components/CrossSlotReferencesPanel.vue";
import EpisodeSourcePanel from "../components/EpisodeSourcePanel.vue";
import DeliveryPanel from "../components/DeliveryPanel.vue";
import PlanCreateDialog from "../components/PlanCreateDialog.vue";
import ProjectOutlinePanel from "../components/ProjectOutlinePanel.vue";
import ScriptEditorPanel from "../components/ScriptEditorPanel.vue";
import StoryConnectionPanel from "../components/StoryConnectionPanel.vue";
import StatusBadge from "../components/StatusBadge.vue";
import WorkflowNodeDrawer from "../components/WorkflowNodeDrawer.vue";
import WorkflowCanvas from "../components/WorkflowCanvas.vue";
import VideoTimeline from "../components/VideoTimeline.vue";
import { useJobsStore } from "../stores/jobs";
import { useRunsStore } from "../stores/runs";

type Stage = "projectOutline" | "script" | "visual" | "video" | "review";
const STAGES: Array<{ key: Stage; label: string }> = [
  { key: "projectOutline", label: "项目大纲" },
  { key: "script", label: "当前时段" },
  { key: "visual", label: "视觉锚点" },
  { key: "video", label: "视频成片" },
  { key: "review", label: "审核交付" },
];
const AUTO_STAGE_KEYS: Array<"script" | "visual" | "video"> = [
  "script",
  "visual",
  "video",
];
const SLOT_LABEL: Record<Slot, string> = { morning: "上午", noon: "中午", evening: "傍晚" };
const SLOT_ORDER: Slot[] = ["morning", "noon", "evening"];
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
type OutcomeForm = { summary: string; carryForwardText: string; doNotCarryForwardText: string };
const emptyOutcomeForm = (): OutcomeForm => ({
  summary: "",
  carryForwardText: "",
  doNotCarryForwardText: "",
});
const outcomeForms = reactive<Record<Slot, OutcomeForm>>({
  morning: emptyOutcomeForm(),
  noon: emptyOutcomeForm(),
  evening: emptyOutcomeForm(),
});
const outcomeLoaded = reactive<Record<Slot, boolean>>({
  morning: false,
  noon: false,
  evening: false,
});
let poller: number | undefined;

const runId = computed(() => String(route.query.run ?? ""));
const graph = computed(() => (runId.value ? runs.graphs[runId.value] : undefined));
const episodes = computed(() => graph.value?.episodes ?? []);
const activeStage = computed<Stage>(() => {
  const value = String(route.query.stage ?? "");
  return STAGES.some((item) => item.key === value) ? (value as Stage) : "projectOutline";
});
const selectedSlot = computed<Slot | null>(() => {
  const value = String(route.query.slot ?? "");
  return ["morning", "noon", "evening"].includes(value) ? (value as Slot) : null;
});
const failedNodes = computed(() =>
  (graph.value?.workflowNodes ?? []).filter((item) => item.error || ["failed", "planning_rejected", "rejected"].includes(item.status)),
);
const settings = computed<PipelineSettings | null>(() => graph.value?.run.pipelineSettings ?? null);
const readOnlyProject = computed(
  () => graph.value?.run.compatible === false || Number(graph.value?.run.contractVersion ?? 0) < 3,
);
const canDeliver = computed(() => !readOnlyProject.value && graph.value?.run.status === "ready");
const guided = computed(() => settings.value?.planningMode === "guided_sequential");
const slotCards = computed(() =>
  SLOT_ORDER.map((slot) => ({
    slot,
    episode: episodes.value.find((item) => item.slot === slot),
    state: graph.value?.run.slotPlanning?.find((item) => item.slot === slot),
    directorNode: graph.value?.workflowNodes?.find(
      (item) => item.semanticNodeId === `${slot}:director`,
    ),
  })),
);
const workspaceSlot = computed<Slot>(() =>
  selectedSlot.value ?? graph.value?.run.activeSlot ?? "morning",
);
const activeCard = computed(() =>
  slotCards.value.find((item) => item.slot === workspaceSlot.value),
);
const activeEpisode = computed(() => activeCard.value?.episode);
const connectionSuggestion = computed(() => {
  const step = [...(graph.value?.steps ?? [])]
    .reverse()
    .find(
      (item) =>
        item.operationKey === `director:connection:${workspaceSlot.value}` &&
        item.status === "succeeded",
    );
  if (!step) return null;
  const value = (step.inputSnapshot.normalized_output ?? step.inputSnapshot.provider_output) as
    | { mode?: "independent" | "selected_link" | "direct_continue"; brief?: string }
    | undefined;
  return value?.mode && value.brief ? { mode: value.mode, brief: value.brief } : null;
});

const selectedVideoEpisode = computed(() => {
  if (selectedNode.value?.type !== "video" || !selectedNode.value.slot) return undefined;
  return episodes.value.find((item) => item.slot === selectedNode.value?.slot);
});
const selectedVideoSequences = computed(() => {
  const episode = selectedVideoEpisode.value;
  return episode
    ? (graph.value?.videoSequences ?? []).filter((item) => item.episodeId === episode.id)
    : [];
});

function setLocation(
  stage: Stage,
  slot: Slot | null = selectedSlot.value,
  node?: string,
  sequence?: string | null,
) {
  router.replace({
    path: "/studio",
    query: {
      run: runId.value || undefined,
      stage,
      slot: slot ?? undefined,
      node: node ?? undefined,
      sequence: sequence === null ? undefined : sequence ?? route.query.sequence ?? undefined,
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
      setLocation(
        backend && STAGES.some((item) => item.key === backend) ? backend : "projectOutline",
        runs.graphs[runId.value]?.run.activeSlot ?? null,
      );
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
  SLOT_ORDER.forEach((slot) => {
    outcomeForms[slot] = emptyOutcomeForm();
    outcomeLoaded[slot] = false;
  });
  void refresh();
});

watch(
  [graph, () => route.query.node],
  () => {
    const nodeId = String(route.query.node ?? "");
    if (!nodeId || !graph.value) return;
    const node = graph.value.workflowNodes?.find((item) => item.semanticNodeId === nodeId);
    if (node) {
      const changedNode = selectedNode.value?.semanticNodeId !== node.semanticNodeId;
      selectedNode.value = node;
      // 轮询会替换Graph对象，但不应把用户刚关闭的详情抽屉重新打开。
      // 只有URL实际切换到另一个语义节点时才自动展开；重复点击仍由openNode显式打开。
      if (changedNode) drawerOpen.value = true;
    }
  },
  { immediate: true },
);

function nodeFor(id: string) {
  return graph.value?.workflowNodes?.find((item) => item.semanticNodeId === id);
}

function openNode(node: WorkflowNodeDto | undefined) {
  if (!node || !graph.value) return;
  selectedNode.value = node;
  drawerOpen.value = true;
  const stage: Stage = node.type === "director" || node.type === "story_connection" || node.type === "project_input" || node.type === "project_confirmation"
    ? (node.slot ? "script" : "projectOutline")
    : node.type === "look" || node.type === "opening_anchor"
    ? "visual"
    : node.type === "video"
    ? "video"
    : "review";
  setLocation(
    stage,
    node.slot,
    node.semanticNodeId,
    node.type === "video" ? undefined : null,
  );
}

function trackCanvasJob(job: JobAccepted) {
  jobs.track(job);
  ElMessage.info("节点任务已提交，旧版本保持不变");
}

function setDrawerOpen(value: boolean) {
  drawerOpen.value = value;
  // 关闭详情抽屉不等于取消语义节点选择。视频时间轴依赖当前节点与版本，
  // 若在这里清空URL，用户刚关闭抽屉就会同时失去时间轴和未提交的选区。
  // 节点选择只在点击另一个节点、切换Run或显式导航时改变。
}

function assetsFor(episode: EpisodeDto, roles: string[]): AssetDto[] {
  return (graph.value?.assets ?? []).filter(
    (asset) => asset.episodeId === episode.id && roles.includes(asset.role),
  );
}

function reviewsFor(asset: AssetDto) {
  return (graph.value?.reviews ?? []).filter((review) => review.assetId === asset.id);
}

async function uploadEpisodeReference(episode: EpisodeDto, role: "element" | "scene") {
  try {
    const prefix = `${role}:`;
    const { value } = await ElMessageBox.prompt(
      `填写${role === "element" ? "关键道具" : "场景"}语义键，例如 ${prefix}red_kite。`,
      `添加${SLOT_LABEL[episode.slot]}参考素材`,
      {
        inputValue: prefix,
        inputValidator: (text) =>
          new RegExp(`^${role}:[a-z0-9][a-z0-9_-]{1,80}$`).test(text.trim())
          || `必须使用 ${prefix}英文标识`,
      },
    );
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".png,.jpg,.jpeg,.webp";
    const file = await new Promise<File | null>((resolve) => {
      input.onchange = () => resolve(input.files?.[0] ?? null);
      input.oncancel = () => resolve(null);
      input.click();
    });
    if (!file) return;
    await api.uploadReference(episode.id, role, value.trim(), file);
    ElMessage.success("参考素材已导入；后续视觉attempt会按语义键重新选择素材");
    await refresh();
  } catch (error) {
    if (!isDialogCancellation(error)) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error));
    }
  }
}

function planningStateFor(slot: Slot) {
  return graph.value?.run.slotPlanning?.find((item) => item.slot === slot);
}

async function planSlot(slot: Slot, generateFromTheme = false) {
  try {
    const usesConnection = Boolean(graph.value?.run.storyConnections?.[slot]?.useForDirector);
    await confirmPaid(
      `调用${SLOT_LABEL[slot]}时段导演？${usesConnection ? "本次只会加载已启用的关联卡正文。" : "本次不会加载前序结果。"}`,
    );
    const accepted = await api.planSlot(runId.value, slot, true, generateFromTheme);
    jobs.track(accepted);
    ElMessage.info(`${SLOT_LABEL[slot]}导演任务已提交`);
  } catch (error) {
    if (!isDialogCancellation(error)) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error));
    }
  }
}

async function replanSlot(slot: Slot) {
  try {
    const { value } = await ElMessageBox.prompt(
      "请说明上一候选的具体问题；新导演会读取该原因并重新输出完整时段脚本。",
      `重规划${SLOT_LABEL[slot]}`,
      {
        inputPlaceholder: "例如：关系汇合不清楚，或镜头没有稳定切点",
        inputValidator: (text) => text.trim().length >= 4 || "请至少填写4个字符",
        confirmButtonText: "继续",
        cancelButtonText: "取消",
      },
    );
    await confirmPaid(`重新调用${SLOT_LABEL[slot]}时段导演？原失败attempt会永久保留。`);
    const accepted = await api.replanEpisode(
      runId.value,
      slot,
      value.trim(),
      true,
      true,
    );
    jobs.track(accepted);
    ElMessage.info(`${SLOT_LABEL[slot]}重规划任务已提交`);
  } catch (error) {
    if (!isDialogCancellation(error)) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error));
    }
  }
}

async function loadOutcome(slot: Slot) {
  const result = await api.outcome(runId.value, slot);
  outcomeForms[slot] = {
    summary: result.summary,
    carryForwardText: result.carryForward.join("\n"),
    doNotCarryForwardText: result.doNotCarryForward.join("\n"),
  };
  outcomeLoaded[slot] = true;
}

function outcomeLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function confirmOutcome(slot: Slot) {
  const form = outcomeForms[slot];
  try {
    await ElMessageBox.confirm(
      `确认${SLOT_LABEL[slot]}实际结果？它用于审计、解锁和可选关联建议，不会自动写入后续导演Prompt。`,
      "锁定结果卡",
      { confirmButtonText: "确认并解锁下一时段", cancelButtonText: "继续编辑", type: "warning" },
    );
    await api.confirmOutcome(runId.value, slot, {
      summary: form.summary.trim(),
      carryForward: outcomeLines(form.carryForwardText),
      doNotCarryForward: outcomeLines(form.doNotCarryForwardText),
    });
    ElMessage.success(`${SLOT_LABEL[slot]}结果卡已确认`);
    await refresh();
    if (slot !== "evening") {
      const nextSlot = SLOT_ORDER[SLOT_ORDER.indexOf(slot) + 1];
      setLocation("script", nextSlot);
    }
  } catch (error) {
    if (!isDialogCancellation(error)) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error));
    }
  }
}

async function onProjectConfirmed() {
  await refresh();
  setLocation("script", "morning");
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
  if (readOnlyProject.value) {
    throw new Error("旧生产契约仅供查看，请新建V3生活故事项目");
  }
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
  key: "script" | "visual" | "video",
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
        <div><h2>生活故事生产工作台</h2><p class="muted">项目方向 → 逐集导演 → 视觉锚点 → 视频成片 → 审核交付</p></div>
        <el-button type="primary" @click="createDialog?.open()">新建生活故事项目</el-button>
      </div>
      <el-empty v-if="!runs.runs.length" description="还没有生活故事项目" />
      <el-card v-for="item in runs.runs" :key="item.id" class="run-card" shadow="hover" @click="router.push(`/studio?run=${item.id}`)">
        <div class="row"><strong>{{ item.contentDate }} · {{ item.theme ?? "未命名生活故事" }}</strong><StatusBadge :status="item.status" /></div>
        <div class="muted">{{ item.nextAction }}</div>
      </el-card>
    </template>

    <template v-else-if="graph">
      <el-alert
        v-if="graph.run.compatible === false || graph.run.contractVersion < 3"
        type="warning"
        :closable="false"
        title="旧生产契约，仅供只读查看"
        :description="graph.run.incompatibilityReason ?? '该项目不能继续生成，请新建V3生活故事项目。'"
        style="margin-bottom: 12px"
      />
      <div class="page-header">
        <div>
          <div class="row"><h2>{{ graph.run.contentDate }} · {{ graph.run.theme ?? "生活故事项目" }}</h2><StatusBadge :status="graph.run.status" /></div>
          <p class="muted">{{ graph.run.nextAction }}</p>
        </div>
        <div class="row">
          <el-tag v-if="guided" type="success">顺序人工确认</el-tag>
          <el-button @click="router.push('/studio')">切换Run</el-button>
          <el-button v-if="!guided && !readOnlyProject" type="primary" @click="continuePipeline">继续流程</el-button>
        </div>
      </div>

      <el-alert
        v-for="node in failedNodes"
        :key="node.semanticNodeId"
        type="error"
        :closable="false"
        show-icon
        class="persistent-error"
        @click="openNode(node)"
      >
        <template #title>{{ node.label }}：{{ node.error?.message ?? node.nextAction ?? "节点未通过" }}</template>
      </el-alert>

      <div class="canvas-workspace">
        <aside class="canvas-sidebar">
          <strong>当前生产</strong>
          <span class="mono">{{ runId.slice(0, 8) }}</span>
          <el-tag :type="guided ? 'success' : 'info'">
            {{ guided ? "顺序生产" : "自动全天" }}
          </el-tag>
          <el-divider />
          <strong>素材入口</strong>
          <el-button text @click="router.push('/canon')">Canon资产</el-button>
          <div v-for="episode in episodes" :key="episode.id" class="sidebar-slot">
            <div>{{ SLOT_LABEL[episode.slot] }} · {{ assetsFor(episode, ['element', 'scene']).length }} 项参考</div>
            <div class="sidebar-reference-actions">
              <el-button text size="small" @click="uploadEpisodeReference(episode, 'element')">+ 道具</el-button>
              <el-button text size="small" @click="uploadEpisodeReference(episode, 'scene')">+ 场景</el-button>
            </div>
          </div>
          <el-divider />
          <strong>画布说明</strong>
          <span class="muted">锁定节点仅为路线投影，不会提前创建收费Step。</span>
        </aside>
        <main class="canvas-main">
          <WorkflowCanvas
            :nodes="graph.workflowNodes ?? []"
            :selected-id="selectedNode?.semanticNodeId"
            :planning-mode="graph.run.planningMode ?? settings?.planningMode"
            @select="openNode"
          />
        </main>
      </div>

      <div class="slot-navigator">
        <el-button
          v-for="slot in SLOT_ORDER"
          :key="slot"
          :type="workspaceSlot === slot ? 'primary' : 'default'"
          :disabled="graph.run.slotAvailability?.[slot] === 'locked'"
          @click="setLocation(activeStage, slot)"
        >
          {{ SLOT_LABEL[slot] }}
          · {{ graph.run.slotAvailability?.[slot] ?? 'ready' }}
        </el-button>
      </div>

      <el-steps :active="STAGES.findIndex((item) => item.key === graph.run.currentStage)" finish-status="success" align-center class="workflow-steps">
        <el-step v-for="stage in STAGES" :key="stage.key" :title="stage.label" />
      </el-steps>
      <el-tabs :model-value="activeStage" @tab-change="(name) => setLocation(name as Stage, null)">
        <el-tab-pane v-for="stage in STAGES" :key="stage.key" :label="stage.label" :name="stage.key" />
      </el-tabs>

      <section v-if="activeStage === 'projectOutline'" class="stage-panel">
        <div class="section-title">
          <h3>生活故事项目大纲</h3>
          <el-button
            v-if="graph.run.projectOutline && nodeFor('run:day-director')"
            @click="openNode(nodeFor('run:day-director'))"
          >查看总导演实际Prompt与任务</el-button>
        </div>
        <ProjectOutlinePanel
          v-if="graph.run.projectInput"
          :run-id="runId"
          :project-input="graph.run.projectInput"
          :project-outline="graph.run.projectOutline ?? null"
          :editable="!readOnlyProject && Boolean(graph.run.projectOutline) && !graph.run.projectOutlineConfirmed"
          @saved="onProjectConfirmed"
        />
        <el-empty v-else description="项目输入尚未保存" />
        <el-card v-if="settings && !readOnlyProject" shadow="never" class="settings-card">
          <template #header><strong>自动推进设置</strong></template>
          <el-alert
            v-if="guided"
            type="info"
            :closable="false"
            title="顺序逐集确认模式"
            description="已有剧本项目无需总导演；确认项目输入后只规划上午，结果卡确认后再解锁中午。"
          />
          <div v-else class="row wrap">
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
        <div class="section-title"><h3>{{ SLOT_LABEL[workspaceSlot] }}完整生产 · 原始剧本与导演镜头卡</h3><span class="muted">一次只专注当前时段；后续时段保持锁定且不会创建收费Step。</span></div>
        <StoryConnectionPanel
          v-if="workspaceSlot !== 'morning' && graph.run.slotAvailability?.[workspaceSlot] !== 'locked'"
          :run-id="runId"
          :slot="workspaceSlot"
          :value="graph.run.storyConnections?.[workspaceSlot]"
          :suggestion="connectionSuggestion"
          :disabled="Boolean(activeEpisode)"
          @job="trackCanvasJob"
          @saved="refresh"
        />
        <div
          v-if="workspaceSlot !== 'morning' && nodeFor(`${workspaceSlot}:connection`)"
          class="node-links"
        >
          <el-button
            size="small"
            @click="openNode(nodeFor(`${workspaceSlot}:connection`))"
          >查看关联建议Prompt与尝试</el-button>
        </div>
        <el-card v-if="activeCard" class="episode-card" shadow="never">
          <template #header>
            <div class="row">
              <strong>{{ SLOT_LABEL[activeCard.slot] }} · {{ activeCard.episode?.title ?? "尚未规划" }}</strong>
              <template v-if="activeCard.episode">
                <el-tag>{{ FOCUS_LABEL[activeCard.episode.activityFocus] }}</el-tag>
                <el-tag type="info">{{ activeCard.episode.script.duration_seconds }}秒 · {{ activeCard.episode.renderPlan.sections.length }}个任务</el-tag>
              </template>
              <el-tag v-else :type="activeCard.state?.unlocked ? 'warning' : 'info'">
                {{ activeCard.state?.unlocked ? "已解锁" : "锁定" }}
              </el-tag>
              <el-button text type="primary" @click="openNode(nodeFor(`${activeCard.slot}:director`))">导演节点</el-button>
            </div>
          </template>
          <template v-if="activeCard.episode">
            <EpisodeSourcePanel
              v-if="graph.run.projectInput"
              :run-id="runId"
              :slot="activeCard.slot"
              :source-text="graph.run.projectInput.episode_sources[activeCard.slot]"
              :planned="true"
              :unlocked="Boolean(activeCard.state?.unlocked)"
              :read-only="readOnlyProject"
            />
            <el-descriptions :column="1" border size="small" style="margin-bottom: 12px">
              <el-descriptions-item label="原始场景">{{ graph.run.projectOutline?.episodes[activeCard.slot]?.scene ?? activeCard.episode.script.visual_context }}</el-descriptions-item>
              <el-descriptions-item label="关系弧">{{ activeCard.episode.relationshipArc }}</el-descriptions-item>
              <el-descriptions-item v-if="activeCard.slot !== 'morning'" label="已启用关联卡">
                {{ graph.run.storyConnections?.[activeCard.slot]?.useForDirector ? graph.run.storyConnections?.[activeCard.slot]?.brief : '未加载，当前时段独立镜头化' }}
              </el-descriptions-item>
            </el-descriptions>
            <ScriptEditorPanel :episode="activeCard.episode" :editable="!readOnlyProject && ['planned', 'video_pending', 'failed'].includes(activeCard.episode.status)" @saved="onScriptSaved(activeCard.episode.id)" />
          </template>
          <template v-else>
            <el-alert
              v-if="!activeCard.state?.unlocked"
              type="info"
              :closable="false"
              :title="`${SLOT_LABEL[activeCard.slot]}导演尚未解锁`"
              :description="activeCard.state?.blockReason ?? '先完成前一时段视频审核和结果卡确认'"
              style="margin-bottom: 12px"
            />
            <EpisodeSourcePanel
              v-if="graph.run.projectInput"
              :run-id="runId"
              :slot="activeCard.slot"
              :source-text="graph.run.projectInput.episode_sources[activeCard.slot]"
              :planned="false"
              :unlocked="Boolean(activeCard.state?.unlocked)"
              :read-only="readOnlyProject"
              @saved="refresh"
              @plan="(generateFromTheme) => planSlot(activeCard.slot, generateFromTheme)"
            />
            <div v-if="activeCard.state?.unlocked && activeCard.directorNode?.status === 'planning_rejected'">
              <el-button type="warning" @click="replanSlot(activeCard.slot)">按原因重规划{{ SLOT_LABEL[activeCard.slot] }}</el-button>
            </div>
            <el-button
              v-else-if="activeCard.state?.unlocked && ['failed', 'submission_unknown'].includes(activeCard.directorNode?.status ?? '')"
              type="danger"
              @click="openNode(activeCard.directorNode)"
            >查看失败与恢复操作</el-button>
          </template>
        </el-card>
      </section>

      <section v-else-if="activeStage === 'visual'" class="stage-panel">
        <div class="section-title"><h3>{{ SLOT_LABEL[workspaceSlot] }} · 定妆图与开场锚点</h3><span class="muted">每次只准备当前时段；AI检查只提供建议，批准权属于人工。</span></div>
        <CrossSlotReferencesPanel
          v-if="workspaceSlot !== 'morning' && activeEpisode"
          :run-id="runId"
          :slot="workspaceSlot"
          :disabled="assetsFor(activeEpisode, ['video']).length > 0"
          @saved="refresh"
        />
        <el-card v-if="activeEpisode" :key="activeEpisode.id" class="episode-card" shadow="never">
          <template #header><div class="row"><strong>{{ SLOT_LABEL[activeEpisode.slot] }} · {{ activeEpisode.title }}</strong><el-button @click="loadPreview(activeEpisode)">查看编译Prompt</el-button><el-button v-if="!readOnlyProject" type="primary" @click="generateVisuals(activeEpisode)">生成视觉锚点</el-button></div></template>
          <el-collapse v-if="previews[activeEpisode.id]">
            <el-collapse-item title="编译Prompt与高级覆盖" name="prompt">
              <el-alert
                v-if="previews[activeEpisode.id].overrideState.stale"
                type="warning"
                :closable="false"
                title="上游结构化脚本已经变化，旧Prompt覆盖已过期"
                description="只有重新检查并保存后，覆盖才允许用于下一次收费调用。"
                style="margin-bottom: 10px"
              />
              <div class="advanced-toggle">
                <span>高级Prompt覆盖</span>
                <el-switch v-model="overrideEnabled[activeEpisode.id]" active-text="启用" inactive-text="关闭" />
              </div>
              <div class="prompt-label">定妆图Prompt</div><el-input v-model="drafts[activeEpisode.id].look" type="textarea" :rows="7" :readonly="!overrideEnabled[activeEpisode.id]" />
              <div class="prompt-label">开场锚点Prompt</div><el-input v-model="drafts[activeEpisode.id].opening_anchor" type="textarea" :rows="7" :readonly="!overrideEnabled[activeEpisode.id]" />
              <el-button style="margin-top: 8px" @click="saveOverrides(activeEpisode)">{{ overrideEnabled[activeEpisode.id] ? '确认并启用覆盖' : '确认使用编译Prompt' }}</el-button>
            </el-collapse-item>
          </el-collapse>
          <div class="media-grid">
            <div v-for="asset in assetsFor(activeEpisode, ['look_reference', 'opening_anchor'])" :key="asset.id">
              <AssetReviewPanel :asset="asset" :reviews="reviewsFor(asset)" :read-only="readOnlyProject" @reviewed="refresh" />
            </div>
          </div>
          <div class="node-links"><el-button size="small" @click="openNode(nodeFor(`${activeEpisode.slot}:look`))">定妆节点</el-button><el-button size="small" @click="openNode(nodeFor(`${activeEpisode.slot}:opening-anchor`))">开场锚点节点</el-button></div>
        </el-card>
        <el-empty v-else description="请先完成当前时段导演镜头卡" />
      </section>

      <section v-else-if="activeStage === 'video'" class="stage-panel">
        <div class="section-title"><h3>{{ SLOT_LABEL[workspaceSlot] }} · Seedance视频与版本</h3><span class="muted">生成前可修改镜头卡和素材引用；生成后可整条重做或在时间轴选择区间重拍。</span></div>
        <el-card v-if="activeEpisode" :key="activeEpisode.id" class="episode-card" shadow="never">
          <template #header><div class="row"><strong>{{ SLOT_LABEL[activeEpisode.slot] }} · {{ activeEpisode.script.duration_seconds }}秒</strong><el-tag>{{ activeEpisode.renderPlan.mode }}</el-tag><el-button @click="loadPreview(activeEpisode)">查看Prompt与RenderPlan</el-button><el-button v-if="!readOnlyProject" type="primary" @click="generateVideo(activeEpisode)">生成视频</el-button></div></template>
          <el-collapse v-if="previews[activeEpisode.id]">
            <el-collapse-item title="RenderPlan与视频Prompt" name="video-prompt">
              <pre class="json-view">{{ JSON.stringify(previews[activeEpisode.id].renderPlan, null, 2) }}</pre>
              <el-alert
                v-if="previews[activeEpisode.id].overrideState.stale"
                type="warning"
                :closable="false"
                title="上游结构化脚本已经变化，旧Prompt覆盖已过期"
                description="请重新检查后再显式确认；未经确认的覆盖不会进入收费请求。"
                style="margin-bottom: 10px"
              />
              <div class="advanced-toggle">
                <span>高级Prompt覆盖</span>
                <el-switch v-model="overrideEnabled[activeEpisode.id]" active-text="启用" inactive-text="关闭" />
              </div>
              <el-input v-model="drafts[activeEpisode.id].video" type="textarea" :rows="12" :readonly="!overrideEnabled[activeEpisode.id]" />
              <div class="muted">覆盖只作用于初始区段；延展区段根据对应镜头重新编译。</div>
              <el-button style="margin-top: 8px" @click="saveOverrides(activeEpisode)">{{ overrideEnabled[activeEpisode.id] ? '确认并启用覆盖' : '确认使用编译Prompt' }}</el-button>
              <div v-for="section in previews[activeEpisode.id].videoSections.slice(1)" :key="section.order" class="extension-prompt">
                <strong>延展{{ section.order }} · {{ section.durationSeconds }}秒</strong><pre>{{ section.prompt }}</pre>
              </div>
            </el-collapse-item>
          </el-collapse>
          <div class="media-grid">
            <AssetReviewPanel v-for="asset in assetsFor(activeEpisode, ['video_intermediate', 'video'])" :key="asset.id" :asset="asset" :reviews="reviewsFor(asset)" :read-only="readOnlyProject" @reviewed="refresh" />
          </div>
          <div class="node-links"><el-button size="small" @click="openNode(nodeFor(`${activeEpisode.slot}:video`))">视频节点与全部版本</el-button></div>
        </el-card>
        <el-empty v-else description="请先完成当前时段导演与视觉锚点" />
      </section>

      <section v-else class="stage-panel">
        <div class="section-title"><h3>{{ SLOT_LABEL[workspaceSlot] }} · AI建议、人工审核与结果卡</h3><span class="muted">AI发现只作为提示；最终是否接受画面、剧情和轻微接缝差异由你决定。</span></div>
        <el-card v-if="activeEpisode" :key="activeEpisode.id" class="episode-card" shadow="never">
          <template #header><div class="row"><strong>{{ SLOT_LABEL[activeEpisode.slot] }} · {{ activeEpisode.title }}</strong><StatusBadge :status="activeEpisode.status" /><el-button text @click="openNode(nodeFor(`${activeEpisode.slot}:review`))">审核节点</el-button></div></template>
          <AssetReviewPanel v-for="asset in assetsFor(activeEpisode, ['video'])" :key="asset.id" :asset="asset" :reviews="reviewsFor(asset)" :max-width="360" :read-only="readOnlyProject" @reviewed="refresh" />
          <el-divider />
          <div class="section-title">
            <h4>实际结果卡</h4>
            <el-tag v-if="planningStateFor(activeEpisode.slot)?.outcomeConfirmed" type="success">已确认</el-tag>
          </div>
          <template v-if="planningStateFor(activeEpisode.slot)?.outcomeConfirmed">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="实际结果">{{ graph.run.acceptedOutcomes?.[activeEpisode.slot]?.summary }}</el-descriptions-item>
              <el-descriptions-item label="可供关联建议参考">{{ graph.run.acceptedOutcomes?.[activeEpisode.slot]?.carryForward.join('；') || '无' }}</el-descriptions-item>
              <el-descriptions-item label="关联建议应排除">{{ graph.run.acceptedOutcomes?.[activeEpisode.slot]?.doNotCarryForward.join('；') || '无' }}</el-descriptions-item>
            </el-descriptions>
          </template>
          <template v-else-if="outcomeLoaded[activeEpisode.slot]">
            <el-form label-position="top">
              <el-form-item label="实际成片结果"><el-input v-model="outcomeForms[activeEpisode.slot].summary" type="textarea" :rows="3" /></el-form-item>
              <el-form-item label="可供关联建议参考（每行一项）"><el-input v-model="outcomeForms[activeEpisode.slot].carryForwardText" type="textarea" :rows="3" /></el-form-item>
              <el-form-item label="关联建议应排除的偶发错误（每行一项）"><el-input v-model="outcomeForms[activeEpisode.slot].doNotCarryForwardText" type="textarea" :rows="3" /></el-form-item>
              <el-button v-if="!readOnlyProject" type="primary" @click="confirmOutcome(activeEpisode.slot)">确认结果卡</el-button>
            </el-form>
          </template>
          <el-button
            v-else
            :disabled="activeEpisode.status !== 'ready'"
            @click="loadOutcome(activeEpisode.slot)"
          >{{ activeEpisode.status === 'ready' ? '读取诊断并编辑结果卡' : '批准视频后才能确认结果卡' }}</el-button>
        </el-card>
        <el-empty v-else description="当前时段尚无可审核视频" />
        <DeliveryPanel v-if="canDeliver" :run-id="runId" :can-deliver="canDeliver" />
      </section>

      <VideoTimeline
        v-if="selectedVideoEpisode && selectedNode"
        :episode="selectedVideoEpisode"
        :node="selectedNode"
        :sequences="selectedVideoSequences"
        :assets="graph.assets"
        :reviews="graph.reviews"
        :outcome-confirmed="Boolean(graph.run.acceptedOutcomes?.[selectedVideoEpisode.slot])"
        :sequence-id="String(route.query.sequence ?? '') || undefined"
        @job="trackCanvasJob"
        @changed="refresh"
        @sequence-selected="(id) => setLocation('video', selectedVideoEpisode?.slot ?? null, selectedNode?.semanticNodeId, id)"
      />

      <WorkflowNodeDrawer
        :model-value="drawerOpen"
        :node="selectedNode"
        :graph="graph"
        @update:model-value="setDrawerOpen"
        @job="trackCanvasJob"
        @changed="refresh"
        @replan="replanSlot"
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
.slot-navigator { display: flex; justify-content: center; gap: 10px; margin: 16px 0; }
.run-card { margin-bottom: 10px; cursor: pointer; background: #16181d; border-color: #2b2d33; }
.workflow-steps { margin: 24px 0 14px; }
.canvas-workspace { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 12px; margin-top: 18px; }
.canvas-sidebar { display: flex; flex-direction: column; align-items: flex-start; gap: 9px; padding: 14px; border: 1px solid #2b2d33; border-radius: 12px; background: #16181d; }
.canvas-sidebar .el-divider { margin: 4px 0; }
.sidebar-slot { color: #9ca3af; font-size: 12px; }
.sidebar-reference-actions { display: flex; gap: 2px; }
.sidebar-reference-actions .el-button { margin: 0; padding: 2px 3px; }
.canvas-main { min-width: 0; }
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
