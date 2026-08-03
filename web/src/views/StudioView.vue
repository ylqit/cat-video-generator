<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, ApiError } from "../api/client";
import type {
  EpisodeDto,
  EpisodePromptPreview,
  PipelineSettings,
  PromptDto,
  PromptOverrides,
  RunGraph,
  StageMode,
} from "../api/types";
import AssetReviewPanel from "../components/AssetReviewPanel.vue";
import AssetThumb from "../components/AssetThumb.vue";
import DayBriefPanel from "../components/DayBriefPanel.vue";
import PromptCollapse from "../components/PromptCollapse.vue";
import ScriptEditorPanel from "../components/ScriptEditorPanel.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { usePolling } from "../composables/usePolling";
import { useJobsStore } from "../stores/jobs";
import { useRunsStore } from "../stores/runs";

const route = useRoute();
const router = useRouter();
const jobs = useJobsStore();
const runs = useRunsStore();

const SLOT_LABEL: Record<string, string> = {
  morning: "早间",
  noon: "午间",
  evening: "晚间",
};
const STAGE_LABEL: Record<string, string> = {
  dayBrief: "主题",
  dayBriefReview: "日导演",
  script: "三集剧本",
  keyframes: "首末帧",
  video: "视频成片",
  done: "视频成片",
};
const STAGE_INDEX: Record<string, number> = {
  dayBrief: 0,
  dayBriefReview: 1,
  script: 2,
  keyframes: 3,
  video: 4,
  done: 4,
};
const STAGE_TAB: Record<string, string> = {
  dayBriefReview: "brief",
  script: "scripts",
  keyframes: "frames",
  video: "video",
  done: "video",
};

const form = reactive({
  theme: "",
  targetDate: "",
  allowPaidGeneration: false,
  stages: {
    dayBrief: "auto",
    script: "auto",
    keyframes: "auto",
    video: "auto",
  } as Record<string, StageMode>,
});
const submitting = ref(false);

const runId = ref<string | null>((route.query.run as string) || null);
const graph = ref<RunGraph | null>(null);
const loadingGraph = ref(false);
const activeTab = ref("brief");

const previews = reactive<Record<string, EpisodePromptPreview>>({});
const drafts = reactive<
  Record<string, { firstFrame: string; lastFrame: string; video: string }>
>({});
const saving = reactive<Record<string, boolean>>({});
const generating = reactive<Record<string, boolean>>({});
const continuing = ref(false);
const historyVisible = ref(false);

/** 打开历史Run（含未完成断点），复用当前页面继续推进。 */
function openRun(id: string) {
  runId.value = id;
  historyVisible.value = false;
  router.replace({ query: { run: id } });
  void loadGraph();
}

/** 回到空白主题表单开始新的全天方案。 */
function newTheme() {
  runId.value = null;
  graph.value = null;
  historyVisible.value = false;
  router.replace({ query: {} });
}

const run = computed(() => graph.value?.run ?? null);
const settings = computed<PipelineSettings>(
  () =>
    run.value?.pipelineSettings ?? {
      allowPaidGeneration: false,
      dayBrief: "auto",
      script: "auto",
      keyframes: "auto",
      video: "auto",
    },
);
const currentStage = computed(() => run.value?.currentStage ?? "dayBrief");
const stageIndex = computed(() => STAGE_INDEX[currentStage.value] ?? 0);
const episodes = computed(() =>
  [...(graph.value?.episodes ?? [])].sort((a, b) => a.sortOrder - b.sortOrder),
);

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

function frameAssets(episode: EpisodeDto, role: string) {
  return (graph.value?.assets ?? []).filter(
    (asset) => asset.episodeId === episode.id && asset.role === role,
  );
}

function frameReady(episode: EpisodeDto): boolean {
  const ok = new Set(["approved", "ready"]);
  return ["first_frame", "last_frame"].every((role) =>
    frameAssets(episode, role).some((asset) => ok.has(asset.status)),
  );
}

function videoAssets(episode: EpisodeDto) {
  return (graph.value?.assets ?? []).filter(
    (asset) => asset.episodeId === episode.id && asset.role === "video",
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

/** 该集最新图片步骤实际使用的参考图资产ID。 */
function referenceAssetIds(episode: EpisodeDto): string[] {
  const step = [...(graph.value?.steps ?? [])]
    .filter((item) => item.kind === "image" && item.episodeId === episode.id)
    .pop();
  const ids = step?.inputSnapshot?.reference_asset_ids;
  return Array.isArray(ids) ? ids.map(String) : [];
}

const allFramesReady = computed(
  () => episodes.value.length > 0 && episodes.value.every(frameReady),
);

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
        rejectedStepIds.has(item.id) &&
        item.operationKey.startsWith("director:episode:"),
    );
  const slot = step?.operationKey.split(":").at(-1) ?? null;
  const reasons = g.reviews
    .filter((review) => review.stepId === step?.id && review.reason)
    .map((review) => String(review.reason));
  return { slot, reasons };
});

const replanVisible = ref(false);
const replanReason = ref("");
const replanPaid = ref(false);
const replanning = ref(false);

/** 人工给出修正理由重规划失败时段；成功后后端按开关自动链式推进。 */
async function submitReplan() {
  if (!runId.value || !planningFailure.value?.slot) {
    return;
  }
  replanning.value = true;
  try {
    const accepted = await api.replanEpisode(
      runId.value,
      planningFailure.value.slot,
      replanReason.value.trim(),
      true,
    );
    jobs.track(accepted);
    replanVisible.value = false;
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded") {
      ElMessage.success("重规划完成，流水线已按开关自动推进");
      replanReason.value = "";
      replanPaid.value = false;
    } else {
      ElMessage.error(final.error?.message ?? "重规划失败");
    }
    await loadGraph();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    replanning.value = false;
  }
}

function buildOverrides(episodeId: string): PromptOverrides {
  const preview = previews[episodeId];
  const draft = drafts[episodeId];
  if (!preview || !draft) {
    return {};
  }
  const overrides: PromptOverrides = {};
  if (draft.firstFrame.trim() && draft.firstFrame !== preview.firstFrame) {
    overrides.first_frame = draft.firstFrame.trim();
  }
  if (draft.lastFrame.trim() && draft.lastFrame !== preview.lastFrame) {
    overrides.last_frame = draft.lastFrame.trim();
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
    const tab = STAGE_TAB[currentStage.value];
    if (tab) {
      activeTab.value = tab;
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    loadingGraph.value = false;
  }
}

/** 拉取实时编译的三段Prompt；有未保存编辑时轮询不覆盖草稿。 */
async function loadPreview(episode: EpisodeDto) {
  const preview = await api.getPromptPreview(episode.id);
  previews[episode.id] = preview;
  if (drafts[episode.id] && hasEdits(episode.id)) {
    return;
  }
  const stored = episode.promptOverrides ?? {};
  drafts[episode.id] = {
    firstFrame: stored.first_frame ?? preview.firstFrame,
    lastFrame: stored.last_frame ?? preview.lastFrame,
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
      allowPaidGeneration: true,
      pipelineSettings: {
        allowPaidGeneration: true,
        ...form.stages,
      },
    });
    jobs.track(accepted);
    ElMessage.info("规划任务已提交，按流水线开关自动推进…");
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded" && final.result?.runId) {
      ElMessage.success("规划完成");
      runId.value = String(final.result.runId);
      router.replace({ query: { run: runId.value } });
      await loadGraph();
    } else {
      ElMessage.error(final.error?.message ?? "规划任务失败");
      if (final.error?.runId) {
        runId.value = final.error.runId;
        router.replace({ query: { run: runId.value } });
        await loadGraph();
      }
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
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
      ElMessage.success("已推进到下一阶段");
    } else {
      ElMessage.error(final.error?.message ?? "续跑失败");
    }
    await loadGraph();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
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

/** 用当前草稿（含未保存编辑）触发首末帧真实生成。 */
async function generate(episode: EpisodeDto) {
  try {
    await ElMessageBox.confirm(
      "将调用 Seedream 真实生成首末帧（付费）。草稿中未保存的编辑会随本次生成一并生效。",
      "确认生成首末帧",
      { confirmButtonText: "生成", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  generating[episode.id] = true;
  try {
    const accepted = await api.generateKeyframes(
      episode.id,
      true,
      buildOverrides(episode.id),
    );
    jobs.track(accepted);
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded") {
      ElMessage.success("首末帧生成完成");
    } else {
      ElMessage.error(final.error?.message ?? "首末帧生成失败");
    }
    await loadGraph();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
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
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

onMounted(() => {
  void loadGraph();
  void runs.fetchRuns();
  polling.start();
});
</script>

<template>
  <div style="padding: 20px 24px">
    <div style="display: flex; align-items: center; margin-bottom: 12px">
      <h2 style="margin: 0">主题创作台</h2>
      <div style="flex: 1" />
      <el-button v-if="runId" size="small" @click="newTheme">新建主题</el-button>
      <el-button size="small" @click="historyVisible = true">历史运行</el-button>
    </div>

    <el-card v-if="!runId" shadow="never" style="margin-bottom: 16px">
      <el-form label-width="130px">
        <el-form-item label="主题" required>
          <el-input
            v-model="form.theme"
            type="textarea"
            :rows="3"
            placeholder="例：雨天窝在家里的一天 / 第一次出门野餐。导演将围绕主题规划早中晚三集。"
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
            v-for="name in ['dayBrief', 'script', 'keyframes', 'video']"
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

    <el-card v-if="!runId" shadow="never">
      <template #header><strong>历史运行（点击继续未完成断点）</strong></template>
      <el-table
        v-loading="runs.loading"
        :data="runs.runs"
        @row-click="(row: { id: string }) => openRun(row.id)"
      >
        <el-table-column prop="contentDate" label="内容日期" width="110" />
        <el-table-column
          prop="theme"
          label="主题"
          min-width="220"
          show-overflow-tooltip
        />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <StatusBadge :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column label="下一步" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="muted">{{ row.nextAction ?? "—" }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">暂无历史运行</span>
        </template>
      </el-table>
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
            v-for="name in ['dayBrief', 'script', 'keyframes', 'video']"
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
          <el-button
            size="small"
            @click="router.push(`/runs/${runId}`)"
          >
            Run 详情
          </el-button>
        </div>
      </el-card>

      <el-steps
        :active="stageIndex"
        align-center
        finish-status="success"
        style="margin-bottom: 16px"
      >
        <el-step title="主题" />
        <el-step title="日导演" />
        <el-step title="三集剧本" />
        <el-step title="首末帧" />
        <el-step title="视频成片" />
      </el-steps>

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
              @click="replanVisible = true"
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
                  <StatusBadge :status="episode.status" />
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
                :editable="['planned', 'failed'].includes(episode.status)"
                @saved="loadGraph"
              />
            </el-card>
            <el-button
              v-if="currentStage === 'script'"
              type="primary"
              :loading="continuing"
              @click="continuePipeline"
            >
              继续：生成首末帧
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

        <el-tab-pane label="首末帧" name="frames">
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
                  <el-tag v-if="frameReady(episode)" type="success" size="small">
                    首末帧就绪
                  </el-tag>
                </div>
              </template>

              <el-row v-if="drafts[episode.id]" :gutter="16">
                <el-col :span="8">
                  <div class="muted" style="margin-bottom: 4px">首帧 Prompt</div>
                  <el-input
                    v-model="drafts[episode.id].firstFrame"
                    type="textarea"
                    :rows="6"
                  />
                </el-col>
                <el-col :span="8">
                  <div class="muted" style="margin-bottom: 4px">尾帧 Prompt</div>
                  <el-input
                    v-model="drafts[episode.id].lastFrame"
                    type="textarea"
                    :rows="6"
                  />
                </el-col>
                <el-col :span="8">
                  <div class="muted" style="margin-bottom: 4px">视频 Prompt</div>
                  <el-input
                    v-model="drafts[episode.id].video"
                    type="textarea"
                    :rows="6"
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
                      operationKey: 'image:first_frame',
                      purpose: 'image',
                    })
                  "
                  title="首帧实际 Prompt"
                />
                <PromptCollapse
                  :prompts="
                    promptsFor({
                      kind: 'image',
                      episodeId: episode.id,
                      operationKey: 'image:last_frame',
                      purpose: 'image',
                    })
                  "
                  title="尾帧实际 Prompt"
                />
              </div>
              <div v-if="referenceAssetIds(episode).length" style="margin-top: 6px">
                <span class="muted" style="margin-right: 6px">参考图</span>
                <AssetThumb
                  v-for="assetId in referenceAssetIds(episode)"
                  :key="assetId"
                  :asset-id="assetId"
                  :size="72"
                />
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
                  :disabled="jobs.isActive(`keyframes:${episode.id}`)"
                  @click="generate(episode)"
                >
                  {{
                    frameAssets(episode, "first_frame").length
                      ? "重新生成首末帧"
                      : "生成首末帧"
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

              <div v-for="role in ['first_frame', 'last_frame']" :key="role">
                <template
                  v-for="asset in frameAssets(episode, role)"
                  :key="asset.id"
                >
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
                    :label="`${role === 'first_frame' ? '首帧' : '尾帧'} · ${asset.status}`"
                    :size="140"
                  />
                </template>
              </div>
            </el-card>
            <el-button
              v-if="currentStage === 'keyframes' && settings.keyframes === 'manual'"
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
              v-if="allFramesReady && settings.video === 'auto'"
              type="success"
              :closable="false"
              style="margin-bottom: 12px"
              title="首末帧已就绪，视频阶段自动推进（含人工批准帧后的自动续跑）"
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
                    v-if="
                      settings.video === 'manual' &&
                      frameReady(episode) &&
                      !videoAssets(episode).length &&
                      ['planned', 'preparing_visuals', 'failed'].includes(
                        episode.status,
                      )
                    "
                    size="small"
                    type="primary"
                    @click="confirmVideo(episode)"
                  >
                    确认生成视频
                  </el-button>
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
              <template v-if="videoAssets(episode).length">
                <AssetReviewPanel
                  v-for="asset in videoAssets(episode)"
                  :key="asset.id"
                  :asset="asset"
                  :reviews="graph.reviews"
                  @reviewed="refresh"
                />
              </template>
              <span v-else class="muted">尚未生成视频</span>
            </el-card>
            <el-button
              v-if="currentStage === 'video' || currentStage === 'done'"
              @click="router.push(`/runs/${runId}`)"
            >
              前往 Run 详情页处理审核与交付
            </el-button>
          </template>
          <el-empty v-else description="剧本尚未生成" />
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-empty
      v-else-if="!loadingGraph"
      description="输入主题并提交后，这里将按阶段展示日导演、剧本、首末帧与成片"
    />

    <el-dialog
      v-model="replanVisible"
      :title="`重规划${SLOT_LABEL[planningFailure?.slot ?? ''] ?? ''}时段`"
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

    <el-drawer v-model="historyVisible" title="历史运行" size="640px">
      <el-table
        v-loading="runs.loading"
        :data="runs.runs"
        :row-class-name="
          ({ row }: { row: { id: string } }) =>
            row.id === runId ? 'current-run' : ''
        "
        @row-click="(row: { id: string }) => openRun(row.id)"
      >
        <el-table-column prop="contentDate" label="内容日期" width="100" />
        <el-table-column
          prop="theme"
          label="主题"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <StatusBadge :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column label="下一步" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="muted">{{ row.nextAction ?? "—" }}</span>
          </template>
        </el-table-column>
        <template #empty>
          <span class="muted">暂无历史运行</span>
        </template>
      </el-table>
    </el-drawer>
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
</style>
