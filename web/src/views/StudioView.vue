<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, assetContentUrl, ApiError } from "../api/client";
import type {
  EpisodeDto,
  EpisodePromptPreview,
  HealthStatus,
  Job,
  PipelineSettings,
  PromptDto,
  PromptOverrides,
  RunGraph,
  StageMode,
  StepDto,
  WorkflowNodeDto,
} from "../api/types";
import AssetReviewPanel from "../components/AssetReviewPanel.vue";
import AssetThumb from "../components/AssetThumb.vue";
import DayBriefPanel from "../components/DayBriefPanel.vue";
import DeliveryPanel from "../components/DeliveryPanel.vue";
import PromptCollapse from "../components/PromptCollapse.vue";
import ScriptEditorPanel from "../components/ScriptEditorPanel.vue";
import StatusBadge from "../components/StatusBadge.vue";
import WorkflowNodeDrawer from "../components/WorkflowNodeDrawer.vue";
import { usePolling } from "../composables/usePolling";
import { useJobsStore } from "../stores/jobs";

const route = useRoute();
const router = useRouter();
const jobs = useJobsStore();

const SLOT_LABEL: Record<string, string> = {
  morning: "早间",
  noon: "午间",
  evening: "晚间",
};
const STAGE_LABEL: Record<string, string> = {
  dayBrief: "总导演",
  dayBriefReview: "总导演",
  script: "三集导演",
  storyboard: "故事板",
  video: "视频成片",
  done: "审核交付",
};
const STAGE_INDEX: Record<string, number> = {
  dayBrief: 0,
  dayBriefReview: 0,
  script: 1,
  storyboard: 2,
  video: 3,
  done: 4,
};
const STAGE_TAB: Record<string, string> = {
  dayBriefReview: "brief",
  script: "scripts",
  storyboard: "storyboard",
  video: "video",
  done: "review",
};

const form = reactive({
  theme: "",
  targetDate: "",
  personPersonality: "",
  catPersonality: "",
  humorStyle: "",
  storyMode: "auto" as "auto" | "create" | "expand",
  allowPaidGeneration: false,
  stages: {
    dayBrief: "auto",
    script: "auto",
    storyboard: "auto",
    video: "auto",
  } as Record<string, StageMode>,
});
const submitting = ref(false);

const runId = ref<string | null>((route.query.run as string) || null);
const graph = ref<RunGraph | null>(null);
const loadingGraph = ref(false);
const TAB_NAMES = new Set(["brief", "scripts", "storyboard", "video", "review"]);
const initialStage = typeof route.query.stage === "string" ? route.query.stage : "";
const activeTab = ref(TAB_NAMES.has(initialStage) ? initialStage : "brief");
const initializedTabRunId = ref<string | null>(null);
const persistentFailure = ref<Job["error"]>(null);
const health = ref<HealthStatus | null>(null);
const selectedNodeId = ref(
  typeof route.query.node === "string" ? route.query.node : "",
);
const nodeDrawerVisible = ref(Boolean(selectedNodeId.value));

const previews = reactive<Record<string, EpisodePromptPreview>>({});
const drafts = reactive<Record<string, { storyboard: string; video: string }>>({});
const saving = reactive<Record<string, boolean>>({});
const generating = reactive<Record<string, boolean>>({});
const continuing = ref(false);

/** 回到空白主题表单开始新的全天方案。 */
function newTheme() {
  runId.value = null;
  graph.value = null;
  activeTab.value = "brief";
  initializedTabRunId.value = null;
  persistentFailure.value = null;
  router.replace({ query: {} });
}

const run = computed(() => graph.value?.run ?? null);
const settings = computed<PipelineSettings>(
  () =>
    run.value?.pipelineSettings ?? {
      allowPaidGeneration: false,
      dayBrief: "auto",
      script: "auto",
      storyboard: "auto",
      video: "auto",
    },
);
const currentStage = computed(() => run.value?.currentStage ?? "dayBrief");
const stageIndex = computed(() => STAGE_INDEX[currentStage.value] ?? 0);
const episodes = computed(() =>
  [...(graph.value?.episodes ?? [])].sort((a, b) => a.sortOrder - b.sortOrder),
);
const workflowNodes = computed(() => graph.value?.workflowNodes ?? []);
const storyPatterns = computed(
  () => run.value?.planningMetadata?.storyPatterns ?? {},
);
const selectedNode = computed(
  () => workflowNodes.value.find((item) => item.id === selectedNodeId.value) ?? null,
);
const workflowGroups = computed(() => [
  {
    key: "brief",
    label: "总导演",
    nodes: workflowNodes.value.filter((item) => item.id === "director:day"),
  },
  {
    key: "scripts",
    label: "三集导演",
    nodes: workflowNodes.value.filter(
      (item) => item.type === "director" && item.slot !== null,
    ),
  },
  {
    key: "storyboard",
    label: "故事板",
    nodes: workflowNodes.value.filter(
      (item) =>
        item.type === "look" ||
        item.type === "storyboard" ||
        item.type === "storyboard_review",
    ),
  },
  {
    key: "video",
    label: "视频成片",
    nodes: workflowNodes.value.filter(
      (item) => item.type === "video" || item.type === "video_shot",
    ),
  },
  {
    key: "review",
    label: "审核与交付",
    nodes: workflowNodes.value.filter((item) => item.type === "content_review"),
  },
]);

function openNode(node: WorkflowNodeDto) {
  selectedNodeId.value = node.id;
  nodeDrawerVisible.value = true;
  activeTab.value =
    node.type === "director"
      ? node.slot === null
        ? "brief"
        : "scripts"
      : node.type === "look" ||
          node.type === "storyboard" ||
          node.type === "storyboard_review"
        ? "storyboard"
        : node.type === "video"
          ? "video"
          : "review";
  void router.replace({
    query: {
      ...route.query,
      run: runId.value ?? undefined,
      stage: activeTab.value,
      slot: node.slot ?? undefined,
      node: node.id,
    },
  });
}

function openEpisodeNode(prefix: string, episode: EpisodeDto) {
  const node = workflowNodes.value.find(
    (item) => item.id === `${prefix}:${episode.slot}`,
  );
  if (node) {
    openNode(node);
  }
}

function closeNodeDrawer() {
  nodeDrawerVisible.value = false;
  selectedNodeId.value = "";
  const query = { ...route.query };
  delete query.node;
  void router.replace({ query });
}

function setNodeDrawerVisible(value: boolean) {
  if (value) {
    nodeDrawerVisible.value = true;
  } else {
    closeNodeDrawer();
  }
}

function focusReview(step: StepDto) {
  const episode = episodes.value.find((item) => item.id === step.episodeId);
  activeTab.value = "review";
  void router.replace({
    query: {
      ...route.query,
      stage: "review",
      slot: episode?.slot,
      node: episode ? `content-review:${episode.slot}` : undefined,
    },
  });
}

const ACTIVE_EPISODE = new Set([
  "preparing_visuals",
  "video_pending",
  "video_generating",
  "media_qc",
]);
const isActive = computed(
  () =>
    jobs.hasActive ||
    episodes.value.some((episode) => ACTIVE_EPISODE.has(episode.status)) ||
    run.value?.status === "generating",
);

function storyboardAssets(episode: EpisodeDto) {
  const latestStep = latestStoryboardStep(episode);
  return (graph.value?.assets ?? []).filter(
    (asset) =>
      asset.episodeId === episode.id &&
      asset.role === "storyboard_panel" &&
      asset.stepId === latestStep?.id,
  ).sort(
    (left, right) =>
      Number(left.metadata.panelOrdinal ?? 0) -
      Number(right.metadata.panelOrdinal ?? 0),
  );
}

function storyboardReady(episode: EpisodeDto): boolean {
  const ok = new Set(["approved", "ready"]);
  const assets = storyboardAssets(episode);
  return (
    [3, 4].includes(assets.length) && assets.every((asset) => ok.has(asset.status))
  );
}

function lookAssets(episode: EpisodeDto) {
  const referenced = new Set(referenceAssetIds(episode));
  return (graph.value?.assets ?? []).filter(
    (asset) =>
      asset.role === "look_reference" &&
      (asset.episodeId === episode.id || referenced.has(asset.id)),
  );
}

function latestStoryboardStep(episode: EpisodeDto): StepDto | null {
  return [...(graph.value?.steps ?? [])]
    .filter(
      (step) =>
        step.episodeId === episode.id &&
        step.operationKey === "image:storyboard",
    )
    .sort(
      (left, right) =>
        right.createdAt.localeCompare(left.createdAt),
    )[0] ?? null;
}

function latestLookStep(episode: EpisodeDto): StepDto | null {
  return [...(graph.value?.steps ?? [])]
    .filter(
      (step) =>
        step.episodeId === episode.id && step.operationKey === "image:look",
    )
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0] ?? null;
}

function lookRetryRequired(episode: EpisodeDto): boolean {
  return ["failed", "expired", "cancelled", "submission_unknown"].includes(
    latestLookStep(episode)?.status ?? "",
  );
}

function storyboardRetryRequired(episode: EpisodeDto): boolean {
  return ["failed", "expired", "cancelled", "submission_unknown"].includes(
    latestStoryboardStep(episode)?.status ?? "",
  );
}

function videoAssets(episode: EpisodeDto) {
  return (graph.value?.assets ?? []).filter(
    (asset) => asset.episodeId === episode.id && asset.role === "video",
  );
}

/** 逐镜头生成的镜头片段，按镜头序号排序。 */
function shotClips(episode: EpisodeDto) {
  return (graph.value?.assets ?? [])
    .filter(
      (asset) => asset.episodeId === episode.id && asset.role === "video_shot",
    )
    .sort((a, b) => (a.semanticKey ?? "").localeCompare(b.semanticKey ?? ""));
}

function canSubmitVideo(episode: EpisodeDto): boolean {
  return (
    storyboardReady(episode) &&
    ["preparing_visuals", "video_pending", "failed"].includes(episode.status)
  );
}

/** 按步骤定位某节点实际发送的Prompt记录（审计留档版，区别于可编辑预览）。 */
function promptsFor(match: {
  operationKey?: string;
  kind?: string;
  episodeId?: string;
  purpose: string;
}): PromptDto[] {
  const stepIds = new Set(
    (graph.value?.steps ?? [])
      .filter(
        (step) =>
          (match.operationKey === undefined ||
            step.operationKey === match.operationKey) &&
          (match.kind === undefined || step.kind === match.kind) &&
          (match.episodeId === undefined ||
            step.episodeId === match.episodeId),
      )
      .map((step) => step.id),
  );
  return (graph.value?.prompts ?? []).filter(
    (prompt) => stepIds.has(prompt.stepId) && prompt.purpose === match.purpose,
  );
}

/** 该集最新故事板步骤实际使用的参考图资产ID。 */
function referenceAssetIds(episode: EpisodeDto): string[] {
  const step = [...(graph.value?.steps ?? [])]
    .filter(
      (item) =>
        item.operationKey === "image:storyboard" &&
        item.episodeId === episode.id,
    )
    .pop();
  const ids = step?.inputSnapshot?.reference_asset_ids;
  return Array.isArray(ids) ? ids.map(String) : [];
}

const allStoryboardsReady = computed(
  () => episodes.value.length > 0 && episodes.value.every(storyboardReady),
);

const graphFailure = computed<Job["error"]>(() => {
  const latestByOperation = new Map<string, StepDto>();
  for (const step of graph.value?.steps ?? []) {
    const key = `${step.episodeId ?? "run"}:${step.operationKey}`;
    const current = latestByOperation.get(key);
    if (
      !current ||
      step.attempt > current.attempt ||
      (step.attempt === current.attempt && step.createdAt > current.createdAt)
    ) {
      latestByOperation.set(key, step);
    }
  }
  const failed = [...latestByOperation.values()]
    .filter((step) => step.error)
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))[0];
  if (!failed?.error) {
    return null;
  }
  const episode = episodes.value.find((item) => item.id === failed.episodeId);
  return {
    code: failed.error.code ?? "workflow_step_failed",
    message: failed.error.message ?? "工作流步骤失败",
    runId: failed.runId,
    episodeId: failed.episodeId ?? undefined,
    slot: episode?.slot,
    operationKey: failed.operationKey,
  };
});
const backgroundFailure = computed<Job["error"]>(() => {
  const failed = Object.values(jobs.byDedupKey)
    .filter(
      (job) =>
        job.status === "failed" &&
        job.error &&
        (!runId.value || !job.error.runId || job.error.runId === runId.value),
    )
    .sort((left, right) =>
      (right.finishedAt ?? right.createdAt).localeCompare(
        left.finishedAt ?? left.createdAt,
      ),
    )[0];
  return failed?.error ?? null;
});
const visibleFailure = computed(
  // PostgreSQL工作流节点包含真实Episode与operationKey，应优先于最初提交
  // 全天任务时记录的粗粒度后台上下文，避免把故事板失败显示成总导演失败。
  () => graphFailure.value ?? persistentFailure.value ?? backgroundFailure.value,
);

function rememberFailure(error: Job["error"], fallback: string) {
  persistentFailure.value = error ?? { code: "internal", message: fallback };
  ElMessage.error(persistentFailure.value.message);
}

/** 把同步HTTP失败也提升为可持续查看的节点错误，而不是只显示瞬时Toast。 */
function rememberRequestFailure(
  error: unknown,
  fallback: string,
  context: Partial<NonNullable<Job["error"]>> = {},
) {
  const detail =
    error instanceof ApiError && typeof error.detail === "object" && error.detail
      ? (error.detail as Record<string, unknown>)
      : {};
  rememberFailure(
    {
      code: String(detail.code ?? (error instanceof ApiError ? `http_${error.status}` : "internal")),
      message:
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : fallback,
      runId: String(detail.runId ?? context.runId ?? runId.value ?? "") || undefined,
      episodeId: String(detail.episodeId ?? context.episodeId ?? "") || undefined,
      slot: String(detail.slot ?? context.slot ?? "") || undefined,
      operationKey:
        String(detail.operationKey ?? context.operationKey ?? "") || undefined,
    },
    fallback,
  );
}

function focusFailure() {
  const operationKey = visibleFailure.value?.operationKey ?? "";
  const slot = visibleFailure.value?.slot;
  const nodeId = operationKey === "image:look"
    ? slot
      ? `look:${slot}`
      : ""
    : operationKey.startsWith("image:")
    ? slot
      ? `storyboard:${slot}`
      : ""
    : operationKey.startsWith("video:")
      ? slot
        ? `video:${slot}`
        : ""
      : operationKey === "director:day"
        ? "director:day"
        : slot
          ? `director:${slot}`
          : "";
  const node = workflowNodes.value.find((item) => item.id === nodeId);
  if (node) {
    openNode(node);
    return;
  }
  if (operationKey.startsWith("image:")) {
    activeTab.value = "storyboard";
  } else if (operationKey.startsWith("video:")) {
    activeTab.value = "video";
  } else if (operationKey === "director:day") {
    activeTab.value = "brief";
  } else {
    activeTab.value = "scripts";
  }
}

watch(
  () => route.query.run,
  (value) => {
    const nextRunId = typeof value === "string" && value ? value : null;
    if (nextRunId === runId.value) {
      return;
    }
    runId.value = nextRunId;
    graph.value = null;
    initializedTabRunId.value = null;
    persistentFailure.value = null;
    if (nextRunId) {
      void loadGraph();
    }
  },
);

watch(
  () => route.query.stage,
  (stage) => {
    if (typeof stage === "string" && TAB_NAMES.has(stage) && stage !== activeTab.value) {
      activeTab.value = stage;
    }
  },
);

watch(
  () => route.query.node,
  (node) => {
    selectedNodeId.value = typeof node === "string" ? node : "";
    nodeDrawerVisible.value = Boolean(selectedNodeId.value);
  },
);

watch(activeTab, (stage) => {
  if (!runId.value || route.query.stage === stage) {
    return;
  }
  void router.replace({
    query: { ...route.query, run: runId.value, stage },
  });
});

/** 规划审核/失败定位：最新一条契约校验rejected review对应的时段与原因。 */
const planningFailure = computed(() => {
  const g = graph.value;
  if (!g || !["planning_review", "failed"].includes(g.run.status)) {
    return null;
  }
  const rejectedStepIds = new Set(
    g.reviews
      .filter(
        (review) =>
          review.decision === "rejected" &&
          review.evidence.phase === "episode_contract",
      )
      .map((review) => review.stepId),
  );
  const step = [...g.steps]
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .find(
      (item) =>
        item.operationKey.startsWith("director:episode:") &&
        (rejectedStepIds.has(item.id) || item.error !== null),
    );
  if (!step) {
    return null;
  }
  const slot = step?.operationKey.split(":").at(-1) ?? null;
  const reasons = g.reviews
    .filter((review) => review.stepId === step?.id && review.reason)
    .map((review) => String(review.reason));
  if (!reasons.length && step.error?.message) {
    reasons.push(step.error.message);
  }
  return { slot, reasons };
});

const replanVisible = ref(false);
const replanSlot = ref<string | null>(null);
const replanReason = ref("");
const replanPaid = ref(false);
const replanning = ref(false);

/** 人工给出修正理由重规划失败时段；成功后后端按开关自动链式推进。 */
async function submitReplan() {
  const slot = replanSlot.value ?? planningFailure.value?.slot ?? null;
  if (!runId.value || !slot) {
    return;
  }
  replanning.value = true;
  try {
    const accepted = await api.replanEpisode(
      runId.value,
      slot,
      replanReason.value.trim(),
      true,
    );
    jobs.track(accepted);
    replanVisible.value = false;
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded") {
      persistentFailure.value = null;
      ElMessage.success("重规划完成，流水线已按开关自动推进");
      replanReason.value = "";
      replanPaid.value = false;
    } else {
      rememberFailure(final.error, "重规划失败");
    }
    await loadGraph();
  } catch (error) {
    rememberRequestFailure(error, "重规划失败", {
      slot,
      operationKey: `director:episode:${slot}`,
    });
  } finally {
    replanning.value = false;
  }
}

function openReplan(slot: string) {
  replanSlot.value = slot;
  replanReason.value = "";
  replanPaid.value = false;
  replanVisible.value = true;
}

function buildOverrides(episodeId: string): PromptOverrides {
  const preview = previews[episodeId];
  const draft = drafts[episodeId];
  if (!preview || !draft) {
    return {};
  }
  const overrides: PromptOverrides = {};
  if (draft.storyboard.trim() && draft.storyboard !== preview.storyboard) {
    overrides.storyboard = draft.storyboard.trim();
  }
  if (draft.video.trim() && draft.video !== preview.video) {
    overrides.video = draft.video.trim();
  }
  return overrides;
}

function hasEdits(episodeId: string): boolean {
  return Object.keys(buildOverrides(episodeId)).length > 0;
}

async function refresh() {
  await jobs.refreshActive();
  await loadGraph();
}

const polling = usePolling(refresh, () => (isActive.value ? 5000 : 30000));

async function loadGraph() {
  if (!runId.value) {
    return;
  }
  loadingGraph.value = true;
  try {
    graph.value = await api.runGraph(runId.value);
    for (const episode of graph.value.episodes) {
      await loadPreview(episode);
    }
    if (initializedTabRunId.value !== runId.value) {
      const routeStage =
        typeof route.query.stage === "string" ? route.query.stage : "";
      activeTab.value = TAB_NAMES.has(routeStage)
        ? routeStage
        : (STAGE_TAB[currentStage.value] ?? "brief");
      initializedTabRunId.value = runId.value;
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    loadingGraph.value = false;
  }
}

/** 拉取实时编译的故事板与视频 Prompt；未保存编辑不被轮询覆盖。 */
async function loadPreview(episode: EpisodeDto) {
  const preview = await api.getPromptPreview(episode.id);
  previews[episode.id] = preview;
  if (drafts[episode.id] && hasEdits(episode.id)) {
    return;
  }
  const stored = episode.promptOverrides ?? {};
  drafts[episode.id] = {
    storyboard: stored.storyboard ?? preview.storyboard,
    video: stored.video ?? preview.video,
  };
}

async function waitJob(jobId: string) {
  for (;;) {
    const job = await api.job(jobId);
    jobs.byDedupKey[job.dedupKey] = job;
    if (job.status === "succeeded" || job.status === "failed") {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

/** 主题 → 全天三集规划，随后按流水线开关自动推进。 */
async function submitPlan() {
  if (!form.targetDate || !form.allowPaidGeneration) {
    return;
  }
  submitting.value = true;
  try {
    const accepted = await api.createPlan({
      targetDate: form.targetDate,
      planningContext: form.theme || undefined,
      creativeProfile: {
        personPersonality: form.personPersonality || undefined,
        catPersonality: form.catPersonality || undefined,
        humorStyle: form.humorStyle || undefined,
      },
      allowPaidGeneration: true,
      pipelineSettings: {
        allowPaidGeneration: true,
        ...form.stages,
      },
      storyMode: form.storyMode,
    });
    jobs.track(accepted);
    ElMessage.info("规划任务已提交，按流水线开关自动推进…");
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded" && final.result?.runId) {
      persistentFailure.value = null;
      ElMessage.success("规划完成");
      runId.value = String(final.result.runId);
      router.replace({ query: { run: runId.value, stage: activeTab.value } });
      await loadGraph();
    } else {
      rememberFailure(final.error, "规划任务失败");
      if (final.error?.runId) {
        runId.value = final.error.runId;
        router.replace({ query: { run: runId.value, stage: activeTab.value } });
        await loadGraph();
      }
    }
  } catch (error) {
    rememberRequestFailure(error, "规划任务失败", { operationKey: "director:day" });
  } finally {
    submitting.value = false;
  }
}

/** 阶段开关切换即时持久化；本质是"下一次推进决策"的输入。 */
async function toggleStage(name: string, value: StageMode) {
  if (!runId.value) {
    return;
  }
  try {
    await api.savePipelineSettings(runId.value, {
      ...settings.value,
      [name]: value,
    });
    await loadGraph();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

/** 手动阶段的唯一"继续"入口：后端按当前状态分发续跑。 */
async function continuePipeline() {
  if (!runId.value) {
    return;
  }
  continuing.value = true;
  try {
    const accepted = await api.continueRun(runId.value);
    jobs.track(accepted);
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded") {
      persistentFailure.value = null;
      ElMessage.success("已推进到下一阶段");
    } else {
      rememberFailure(final.error, "续跑失败");
    }
    await loadGraph();
  } catch (error) {
    rememberRequestFailure(error, "续跑失败");
  } finally {
    continuing.value = false;
  }
}

async function saveDraft(episode: EpisodeDto) {
  saving[episode.id] = true;
  try {
    await api.savePromptOverrides(episode.id, buildOverrides(episode.id));
    ElMessage.success("编辑已保存，续跑与重新生成将使用该文本");
    await loadGraph();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    saving[episode.id] = false;
  }
}

/** 用当前草稿触发一次 Seedream 故事板组图生成。 */
async function generate(episode: EpisodeDto) {
  try {
    await ElMessageBox.confirm(
      "将调用 Seedream 一次生成3～4张独立故事板图（付费）。未保存的 Prompt 编辑会随本次生成一并生效。",
      "确认生成故事板",
      { confirmButtonText: "生成", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  generating[episode.id] = true;
  try {
    const accepted = await api.generateStoryboard(
      episode.id,
      true,
      buildOverrides(episode.id),
    );
    jobs.track(accepted);
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded") {
      persistentFailure.value = null;
      ElMessage.success("故事板组图生成完成");
    } else {
      rememberFailure(final.error, "故事板组图生成失败");
    }
    await loadGraph();
  } catch (error) {
    rememberRequestFailure(error, "故事板组图生成失败", {
      episodeId: episode.id,
      slot: episode.slot,
      operationKey: "image:storyboard",
    });
  } finally {
    generating[episode.id] = false;
  }
}

/** video阶段为manual时，帧就绪后人工确认提交该集视频。 */
async function confirmVideo(episode: EpisodeDto) {
  if (!runId.value) {
    return;
  }
  try {
    await ElMessageBox.confirm(
      "将提交 Seedance 视频任务（按时长计费）。",
      "确认生成视频",
      { confirmButtonText: "生成", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  try {
    const accepted = await api.generate(runId.value, {
      slot: episode.slot as "morning" | "noon" | "evening",
      allowPaidGeneration: true,
    });
    jobs.track(accepted);
    ElMessage.info("视频任务已提交");
    await refresh();
  } catch (error) {
    rememberRequestFailure(error, "视频任务提交失败", {
      episodeId: episode.id,
      slot: episode.slot,
      operationKey: "video:single_pass",
    });
  }
}

onMounted(() => {
  void loadGraph();
  void api.health().then((value) => {
    health.value = value;
  }).catch(() => undefined);
  polling.start();
});
</script>

<template>
  <div style="padding: 20px 24px">
    <div style="display: flex; align-items: center; margin-bottom: 12px">
      <h2 style="margin: 0">三时段视频生产工作台</h2>
      <div style="flex: 1" />
      <el-button v-if="runId" size="small" @click="newTheme">新建主题</el-button>
    </div>

    <el-card v-if="!runId" shadow="never" style="margin-bottom: 16px">
      <el-form label-width="130px">
        <el-form-item label="剧情模式">
          <el-radio-group v-model="form.storyMode">
            <el-radio value="auto">自动识别</el-radio>
            <el-radio value="create">导演创作</el-radio>
            <el-radio value="expand">我的剧情扩写</el-radio>
          </el-radio-group>
          <span class="muted" style="margin-left: 10px">
            自动识别：粘贴「主题一：… 剧本1：… 剧本2：… 剧本3：…」完整剧情即按原文改编，不重新创作
          </span>
        </el-form-item>
        <el-form-item label="主题" required>
          <el-input
            v-model="form.theme"
            type="textarea"
            :rows="form.storyMode === 'create' ? 3 : 8"
            :placeholder="
              form.storyMode === 'create'
                ? '例：雨天窝在家里的一天 / 第一次出门野餐。导演将围绕主题规划早中晚三集。'
                : '可只写主题；或粘贴完整剧情：主题一：出去钓鱼 ⏎ 剧本1：… ⏎ 剧本2：… ⏎ 剧本3：…，导演将按你的原文逐拍改编。'
            "
          />
        </el-form-item>
        <el-form-item label="内容日期" required>
          <el-date-picker
            v-model="form.targetDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
          />
        </el-form-item>
        <el-form-item label="阶段自动推进">
          <span
            v-for="name in ['dayBrief', 'script', 'storyboard', 'video']"
            :key="name"
            style="margin-right: 16px"
          >
            {{ STAGE_LABEL[name] }}
            <el-switch
              v-model="form.stages[name]"
              active-value="auto"
              inactive-value="manual"
              inline-prompt
              active-text="自动"
              inactive-text="手动"
            />
          </span>
        </el-form-item>
        <el-form-item label="性格与幽默">
          <el-collapse style="width: 100%">
            <el-collapse-item title="可选；留空使用系列默认" name="creative-profile">
              <el-input
                v-model="form.personPersonality"
                placeholder="人物性格，例如：好奇认真，容易被小意外逗笑"
              />
              <el-input
                v-model="form.catPersonality"
                placeholder="猫咪性格，例如：表面高冷，其实贪玩"
                style="margin-top: 8px"
              />
              <el-input
                v-model="form.humorStyle"
                placeholder="幽默方式，例如：可见反差和动作表情，不靠对白"
                style="margin-top: 8px"
              />
            </el-collapse-item>
          </el-collapse>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.allowPaidGeneration">
            <span style="color: #f56c6c">
              我已知晓本次规划及自动推进将产生 Ark 付费模型调用
            </span>
          </el-checkbox>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :disabled="
              !form.theme || !form.targetDate || !form.allowPaidGeneration
            "
            :loading="submitting"
            @click="submitPlan"
          >
            生成全天方案
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <template v-if="graph && run">
      <el-card shadow="never" style="margin-bottom: 16px">
        <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
          <strong>{{ run.contentDate }}</strong>
          <span class="muted">{{ run.theme }}</span>
          <StatusBadge :status="run.status" />
          <el-tag
            v-if="settings.allowPaidGeneration"
            type="danger"
            size="small"
          >
            已授权自动付费续跑
          </el-tag>
          <div style="flex: 1" />
          <span
            v-for="name in ['dayBrief', 'script', 'storyboard', 'video']"
            :key="name"
            class="muted"
          >
            {{ STAGE_LABEL[name] }}
            <el-switch
              :model-value="settings[name as keyof PipelineSettings] as StageMode"
              active-value="auto"
              inactive-value="manual"
              inline-prompt
              active-text="自动"
              inactive-text="手动"
              style="margin: 0 10px 0 4px"
              @change="(value: string | number | boolean) => toggleStage(name, value as StageMode)"
            />
          </span>
          <el-popover placement="bottom-end" :width="360" trigger="click">
            <template #reference>
              <el-button size="small">运行配置</el-button>
            </template>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="导演请求">
                {{ health?.arkDirectorRequestTimeoutSeconds ?? "—" }} 秒
              </el-descriptions-item>
              <el-descriptions-item label="故事板请求">
                {{ health?.arkImageRequestTimeoutSeconds ?? "—" }} 秒
              </el-descriptions-item>
              <el-descriptions-item label="图片超时自动重试">
                {{ health?.arkImageTimeoutAutoRetries ?? "—" }} 次，延迟
                {{ health?.arkImageRetryDelaySeconds ?? "—" }} 秒
              </el-descriptions-item>
              <el-descriptions-item label="审核请求">
                {{ health?.arkReviewRequestTimeoutSeconds ?? "—" }} 秒
              </el-descriptions-item>
              <el-descriptions-item label="视频API请求">
                {{ health?.arkVideoApiTimeoutSeconds ?? "—" }} 秒
              </el-descriptions-item>
              <el-descriptions-item label="视频监看窗口">
                {{ health?.arkTaskTimeoutSeconds ?? "—" }} 秒，间隔
                {{ health?.arkPollIntervalSeconds ?? "—" }} 秒
              </el-descriptions-item>
            </el-descriptions>
            <div class="muted" style="margin-top: 8px">
              只读配置，修改服务器.env并重启服务后生效。
            </div>
          </el-popover>
        </div>
      </el-card>

      <el-alert
        v-if="visibleFailure"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      >
        <template #title>
          {{ visibleFailure.code }}：{{ visibleFailure.message }}
        </template>
        <div style="margin-top: 8px">
          <span v-if="visibleFailure.slot">时段：{{ SLOT_LABEL[visibleFailure.slot] ?? visibleFailure.slot }}；</span>
          <span v-if="visibleFailure.operationKey">节点：{{ visibleFailure.operationKey }}</span>
          <el-button link type="primary" style="margin-left: 10px" @click="focusFailure">
            查看失败节点
          </el-button>
        </div>
      </el-alert>

      <el-steps
        :active="stageIndex"
        align-center
        finish-status="success"
        style="margin-bottom: 16px"
      >
        <el-step title="总导演" />
        <el-step title="三集导演" />
        <el-step title="故事板" />
        <el-step title="视频成片" />
        <el-step title="审核交付" />
      </el-steps>

      <el-card shadow="never" class="workflow-map">
        <template #header>
          <div style="display: flex; align-items: center; gap: 10px">
            <strong>全天生产链</strong>
            <span class="muted">点击任一节点查看Prompt、素材、Provider和attempt历史</span>
          </div>
        </template>
        <div class="workflow-groups">
          <template v-for="(group, index) in workflowGroups" :key="group.key">
            <section class="workflow-group">
              <button
                type="button"
                class="group-title"
                @click="activeTab = group.key"
              >
                {{ group.label }}
              </button>
              <button
                v-for="node in group.nodes"
                :key="node.id"
                type="button"
                class="workflow-node"
                :class="{ selected: selectedNodeId === node.id, failed: !!node.error }"
                @click="openNode(node)"
              >
                <span>{{ node.label }}</span>
                <StatusBadge :status="node.status" />
              </button>
            </section>
            <span v-if="index < workflowGroups.length - 1" class="flow-arrow">→</span>
          </template>
        </div>
      </el-card>

      <el-tabs v-model="activeTab">
        <el-tab-pane label="日导演" name="brief">
          <template v-if="run.dayBrief">
            <el-alert
              v-if="currentStage === 'dayBriefReview'"
              type="warning"
              :closable="false"
              style="margin-bottom: 12px"
              title="日导演阶段为手动：检查或编辑后点击继续"
            />
            <PromptCollapse
              :prompts="promptsFor({ operationKey: 'director:day', purpose: 'director' })"
              title="总导演 Prompt"
            />
            <DayBriefPanel
              :run-id="runId!"
              :day-brief="run.dayBrief"
              :editable="run.status === 'draft'"
              @saved="loadGraph"
            />
            <el-button
              v-if="run.status === 'draft'"
              type="primary"
              :loading="continuing"
              @click="continuePipeline"
            >
              继续：生成三集剧本
            </el-button>
          </template>
          <el-empty v-else description="日导演输出尚未生成" />
        </el-tab-pane>

        <el-tab-pane label="三集剧本" name="scripts">
          <el-alert
            v-if="planningFailure"
            type="error"
            :closable="false"
            style="margin-bottom: 12px"
          >
            <template #title>
              规划审核：{{
                planningFailure.slot
                  ? `${SLOT_LABEL[planningFailure.slot] ?? planningFailure.slot}时段`
                  : "部分时段"
              }}导演输出未通过契约校验，流水线已停在此处
            </template>
            <div
              v-for="(reason, index) in planningFailure.reasons"
              :key="index"
              style="font-size: 12px"
            >
              {{ reason }}
            </div>
            <el-button
              v-if="planningFailure.slot"
              size="small"
              type="danger"
              style="margin-top: 8px"
              @click="openReplan(planningFailure.slot)"
            >
              重规划{{ SLOT_LABEL[planningFailure.slot] ?? planningFailure.slot }}时段
            </el-button>
          </el-alert>
          <template v-if="episodes.length">
            <el-alert
              v-if="currentStage === 'script'"
              type="warning"
              :closable="false"
              style="margin-bottom: 12px"
              title="剧本阶段为手动：检查或编辑后点击继续"
            />
            <el-card
              v-for="episode in episodes"
              :key="episode.id"
              shadow="never"
              style="margin-bottom: 12px"
            >
              <template #header>
                <div style="display: flex; align-items: center; gap: 10px">
                   <strong>{{ SLOT_LABEL[episode.slot] ?? episode.slot }}</strong>
                  <el-tag
                    v-if="storyPatterns[episode.slot]?.name"
                    type="warning"
                    size="small"
                  >
                    {{ storyPatterns[episode.slot]?.name }}
                    <template v-if="storyPatterns[episode.slot]?.mood">
                      · {{ storyPatterns[episode.slot]?.mood }}
                    </template>
                  </el-tag>
                  <StatusBadge :status="episode.status" />
                  <el-button
                    v-if="['planned', 'video_pending', 'failed'].includes(episode.status)"
                    size="small"
                    style="margin-left: auto"
                    @click="openReplan(episode.slot)"
                  >
                    重规划该时段
                  </el-button>
                </div>
              </template>
              <PromptCollapse
                :prompts="
                  promptsFor({
                    operationKey: `director:episode:${episode.slot}`,
                    purpose: 'director',
                  })
                "
                title="时段导演 Prompt"
              />
              <ScriptEditorPanel
                :episode="episode"
                :editable="['planned', 'video_pending', 'failed'].includes(episode.status)"
                @saved="loadGraph"
              />
            </el-card>
            <el-button
              v-if="currentStage === 'script'"
              type="primary"
              :loading="continuing"
              @click="continuePipeline"
            >
              继续：生成故事板
            </el-button>
          </template>
          <template v-else-if="Object.keys(graph.episodeDrafts ?? {}).length">
            <el-card
              v-for="slot in ['morning', 'noon', 'evening']"
              :key="slot"
              shadow="never"
              style="margin-bottom: 12px"
            >
              <template #header>
                <div style="display: flex; align-items: center; gap: 10px">
                  <strong>{{ SLOT_LABEL[slot] }}</strong>
                  <el-tag
                    v-if="planningFailure?.slot === slot"
                    type="danger"
                    size="small"
                  >
                    校验未通过
                  </el-tag>
                  <el-tag
                    v-else-if="graph.episodeDrafts?.[slot]"
                    type="success"
                    size="small"
                  >
                    草稿已生成
                  </el-tag>
                  <el-tag v-else type="info" size="small">未生成</el-tag>
                </div>
              </template>
              <template v-if="graph.episodeDrafts?.[slot]">
                <div><strong>{{ graph.episodeDrafts[slot].title }}</strong></div>
                <div class="muted">
                  主事件：{{ graph.episodeDrafts[slot].main_event }}
                </div>
                <div class="muted">
                  场景：{{ graph.episodeDrafts[slot].scene }}
                </div>
              </template>
              <span v-else class="muted">该时段剧本尚未成功生成</span>
            </el-card>
          </template>
          <el-empty v-else description="剧本尚未生成" />
        </el-tab-pane>

        <el-tab-pane label="故事板" name="storyboard">
          <template v-if="episodes.length">
            <el-card
              v-for="episode in episodes"
              :key="episode.id"
              shadow="never"
              style="margin-bottom: 16px"
            >
              <template #header>
                <div style="display: flex; align-items: center; gap: 10px">
                  <strong>{{ SLOT_LABEL[episode.slot] ?? episode.slot }}</strong>
                  <span>{{ episode.title }}</span>
                  <StatusBadge :status="episode.status" />
                  <el-tag v-if="storyboardReady(episode)" type="success" size="small">
                    故事板就绪
                  </el-tag>
                </div>
              </template>

              <el-row v-if="drafts[episode.id]" :gutter="16">
                <el-col :span="12">
                  <div class="muted" style="margin-bottom: 4px">故事板组图 Prompt</div>
                  <el-input
                    v-model="drafts[episode.id].storyboard"
                    type="textarea"
                    :rows="8"
                  />
                </el-col>
                <el-col :span="12">
                  <div class="muted" style="margin-bottom: 4px">视频 Prompt</div>
                  <el-input
                    v-model="drafts[episode.id].video"
                    type="textarea"
                    :rows="8"
                  />
                </el-col>
              </el-row>

              <div
                style="display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start"
              >
                <PromptCollapse
                  :prompts="
                    promptsFor({
                      kind: 'image',
                      episodeId: episode.id,
                      operationKey: 'image:look',
                      purpose: 'look',
                    })
                  "
                  title="日内定妆实际 Prompt"
                />
                <PromptCollapse
                  :prompts="
                    promptsFor({
                      kind: 'image',
                      episodeId: episode.id,
                      operationKey: 'image:storyboard',
                      purpose: 'storyboard',
                    })
                "
                  title="故事板实际 Prompt"
                />
              </div>
              <el-alert
                v-if="lookRetryRequired(episode)"
                type="error"
                :closable="false"
                show-icon
                style="margin-top: 10px"
                title="日内定妆步骤失败，需在定妆节点显式重试"
              >
                <el-button
                  link
                  type="primary"
                  @click="openEpisodeNode('look', episode)"
                >
                  打开定妆节点并处理
                </el-button>
              </el-alert>
              <el-alert
                v-if="storyboardRetryRequired(episode)"
                type="error"
                :closable="false"
                show-icon
                style="margin-top: 10px"
              >
                <template #title>
                  故事板步骤失败，普通“生成”不会隐式创建第二次付费任务
                </template>
                <div>
                  {{ latestStoryboardStep(episode)?.error?.message }}
                  <el-button
                    link
                    type="primary"
                    @click="openEpisodeNode('storyboard', episode)"
                  >
                    打开失败节点并处理
                  </el-button>
                  <el-button
                    link
                    type="warning"
                    @click="openReplan(episode.slot)"
                  >
                    按审核证据重规划该时段
                  </el-button>
                </div>
              </el-alert>
              <div v-if="referenceAssetIds(episode).length" style="margin-top: 6px">
                <span class="muted" style="margin-right: 6px">参考图</span>
                <AssetThumb
                  v-for="assetId in referenceAssetIds(episode)"
                  :key="assetId"
                  :asset-id="assetId"
                  :size="72"
                />
              </div>
              <div v-if="lookAssets(episode).length" style="margin-top: 10px">
                <span class="muted" style="margin-right: 6px">日内定妆图</span>
                <template v-for="asset in lookAssets(episode)" :key="asset.id">
                  <AssetReviewPanel
                    v-if="asset.status === 'candidate'"
                    :asset="asset"
                    :reviews="graph.reviews"
                    :max-width="180"
                    @reviewed="refresh"
                  />
                  <AssetThumb
                    v-else
                    :asset-id="asset.id"
                    :label="`定妆图 · ${asset.status}`"
                    :size="110"
                  />
                </template>
              </div>

              <div style="margin: 10px 0">
                <el-button
                  size="small"
                  :disabled="!hasEdits(episode.id)"
                  :loading="saving[episode.id]"
                  @click="saveDraft(episode)"
                >
                  保存编辑
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  :loading="generating[episode.id]"
                  :disabled="
                    jobs.isActive(`storyboard:${episode.id}`) ||
                    lookRetryRequired(episode) ||
                    storyboardRetryRequired(episode)
                  "
                  @click="generate(episode)"
                >
                  {{
                    lookRetryRequired(episode) || storyboardRetryRequired(episode)
                      ? "需要显式重试"
                      : storyboardAssets(episode).length
                      ? "重新生成故事板"
                      : "生成故事板"
                  }}
                </el-button>
                <span
                  v-if="hasEdits(episode.id)"
                  class="muted"
                  style="margin-left: 10px"
                >
                  有未保存编辑
                </span>
              </div>

              <div class="storyboard-grid">
                <template v-for="asset in storyboardAssets(episode)" :key="asset.id">
                  <AssetReviewPanel
                    v-if="asset.status === 'candidate'"
                    :asset="asset"
                    :reviews="graph.reviews"
                    :max-width="220"
                    @reviewed="refresh"
                  />
                  <AssetThumb
                    v-else
                    :asset-id="asset.id"
                    :label="`面板 ${asset.metadata.panelOrdinal} · ${asset.status}`"
                    :size="140"
                  />
                </template>
              </div>
            </el-card>
            <el-button
              v-if="currentStage === 'storyboard' && settings.storyboard === 'manual'"
              type="primary"
              :loading="continuing"
              @click="continuePipeline"
            >
              继续：按当前状态推进
            </el-button>
          </template>
          <el-empty v-else description="剧本尚未生成" />
        </el-tab-pane>

        <el-tab-pane label="视频成片" name="video">
          <template v-if="episodes.length">
            <el-alert
              v-if="allStoryboardsReady && settings.video === 'auto'"
              type="success"
              :closable="false"
              style="margin-bottom: 12px"
              title="故事板已就绪，视频阶段将按设置自动推进"
            />
            <el-card
              v-for="episode in episodes"
              :key="episode.id"
              shadow="never"
              style="margin-bottom: 16px"
            >
              <template #header>
                <div style="display: flex; align-items: center; gap: 10px">
                  <strong>{{ SLOT_LABEL[episode.slot] ?? episode.slot }}</strong>
                  <span>{{ episode.title }}</span>
                  <StatusBadge :status="episode.status" />
                  <div style="flex: 1" />
                  <el-tooltip
                    v-if="
                      settings.video === 'manual' &&
                      !videoAssets(episode).length
                    "
                    :content="
                      storyboardReady(episode)
                        ? '提交Seedance视频任务'
                        : '必须先完成并批准3～4张故事板'
                    "
                    placement="top"
                  >
                    <span>
                      <el-button
                        size="small"
                        type="primary"
                        :disabled="!canSubmitVideo(episode)"
                        @click="confirmVideo(episode)"
                      >
                        确认生成视频
                      </el-button>
                    </span>
                  </el-tooltip>
                </div>
              </template>
              <PromptCollapse
                :prompts="
                  promptsFor({
                    kind: 'video',
                    episodeId: episode.id,
                    purpose: 'video',
                  })
                "
                title="视频实际 Prompt"
              />
              <template v-if="shotClips(episode).length">
                <div class="muted" style="margin: 10px 0 6px">
                  镜头片段（逐镜头生成：上一镜真实尾帧锚定下一镜首帧）
                </div>
                <div style="display: flex; gap: 14px; flex-wrap: wrap">
                  <div
                    v-for="asset in shotClips(episode)"
                    :key="asset.id"
                    style="width: 220px"
                  >
                    <AssetReviewPanel
                      v-if="asset.status === 'candidate'"
                      :asset="asset"
                      :reviews="graph.reviews"
                      :max-width="220"
                      @reviewed="loadGraph"
                    />
                    <div v-else class="video-preview">
                      <video
                        :src="assetContentUrl(asset.id)"
                        controls
                        preload="metadata"
                      />
                      <div class="muted">
                        {{ asset.semanticKey }} · {{ asset.status }}
                      </div>
                    </div>
                  </div>
                </div>
              </template>
              <template v-if="videoAssets(episode).length">
                <div
                  v-for="asset in videoAssets(episode)"
                  :key="asset.id"
                  class="video-preview"
                >
                  <video
                    :src="assetContentUrl(asset.id)"
                    controls
                    preload="metadata"
                  />
                  <div class="muted">
                    {{ asset.status }} · SHA-256 {{ asset.sha256.slice(0, 16) }}…
                  </div>
                </div>
              </template>
              <span v-else class="muted">尚未生成视频</span>
              <div style="margin-top: 10px">
                <el-button size="small" @click="openEpisodeNode('video', episode)">
                  查看视频节点详情
                </el-button>
              </div>
            </el-card>
            <el-button type="primary" @click="activeTab = 'review'">
              进入审核与交付
            </el-button>
          </template>
          <el-empty v-else description="剧本尚未生成" />
        </el-tab-pane>

        <el-tab-pane label="审核与交付" name="review">
          <template v-if="episodes.length">
            <el-alert
              type="info"
              :closable="false"
              style="margin-bottom: 12px"
              title="最终视频必须由人工观看后决定；三条全部通过才可构建1/2/3交付包"
            />
            <el-card
              v-for="episode in episodes"
              :key="episode.id"
              shadow="never"
              style="margin-bottom: 16px"
            >
              <template #header>
                <div style="display: flex; align-items: center; gap: 10px">
                  <strong>{{ SLOT_LABEL[episode.slot] ?? episode.slot }}</strong>
                  <span>{{ episode.title }}</span>
                  <StatusBadge :status="episode.status" />
                  <div style="flex: 1" />
                  <el-button
                    size="small"
                    @click="openEpisodeNode('content-review', episode)"
                  >
                    查看审核节点
                  </el-button>
                </div>
              </template>
              <AssetReviewPanel
                v-for="asset in videoAssets(episode)"
                :key="asset.id"
                :asset="asset"
                :reviews="graph.reviews"
                @reviewed="refresh"
              />
              <el-empty
                v-if="!videoAssets(episode).length"
                description="该时段尚未产生可审核视频"
              />
            </el-card>
            <DeliveryPanel
              :run-id="runId!"
              :can-deliver="run.availableActions.some((action) => action.type === 'deliver')"
            />
          </template>
          <el-empty v-else description="剧本尚未生成" />
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-empty
      v-else-if="!loadingGraph"
      description="输入主题并提交后，这里将按阶段展示日导演、三时段剧本、故事板与成片"
    />

    <WorkflowNodeDrawer
      v-if="graph"
      :model-value="nodeDrawerVisible"
      :node="selectedNode"
      :graph="graph"
      @update:model-value="setNodeDrawerVisible"
      @changed="refresh"
      @review="focusReview"
    />

    <el-dialog
      v-model="replanVisible"
      :title="`重规划${SLOT_LABEL[replanSlot ?? planningFailure?.slot ?? ''] ?? ''}时段`"
      width="480px"
    >
      <el-alert
        type="warning"
        :closable="false"
        style="margin-bottom: 12px"
        title="重规划只重调该时段导演；成功后流水线按阶段开关自动推进后续节点"
      />
      <el-form label-width="90px">
        <el-form-item label="修正理由" required>
          <el-input
            v-model="replanReason"
            type="textarea"
            :rows="3"
            placeholder="至少4个字；会注入导演修复Prompt，例如：保留主事件，共享元素声明与其他时段保持一致"
          />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="replanPaid">
            <span style="color: #f56c6c">
              我已知晓重规划将产生 Ark 付费模型调用
            </span>
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="replanVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="replanning"
          :disabled="replanReason.trim().length < 4 || !replanPaid"
          @click="submitReplan"
        >
          确认付费并重规划
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<style scoped>
.muted {
  color: #8a8f99;
  font-size: 12px;
}
:deep(.current-run) {
  background: #1d2733;
}
.workflow-map {
  margin-bottom: 16px;
  background: #14161b;
}
.workflow-groups {
  display: flex;
  align-items: stretch;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.workflow-group {
  min-width: 170px;
  flex: 1 0 170px;
  border: 1px solid #2b2d33;
  border-radius: 8px;
  padding: 10px;
}
.group-title,
.workflow-node {
  width: 100%;
  border: 0;
  color: inherit;
  cursor: pointer;
  text-align: left;
}
.group-title {
  background: transparent;
  font-weight: 700;
  margin-bottom: 8px;
}
.workflow-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: #1b1d23;
  border: 1px solid #2b2d33;
  border-radius: 6px;
  padding: 7px 8px;
  margin-top: 6px;
}
.workflow-node:hover,
.workflow-node.selected {
  border-color: #409eff;
  background: #182536;
}
.workflow-node.failed {
  border-color: #f56c6c;
}
.flow-arrow {
  align-self: center;
  color: #60656f;
  font-size: 20px;
}
.video-preview video {
  width: min(100%, 360px);
  border-radius: 8px;
  background: #000;
}
</style>
