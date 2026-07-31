<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, ApiError } from "../api/client";
import type {
  EpisodeDto,
  EpisodePromptPreview,
  PromptOverrides,
  RunGraph,
} from "../api/types";
import AssetReviewPanel from "../components/AssetReviewPanel.vue";
import AssetThumb from "../components/AssetThumb.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { useJobsStore } from "../stores/jobs";

const route = useRoute();
const router = useRouter();
const jobs = useJobsStore();

const SLOT_LABEL: Record<string, string> = {
  morning: "早间",
  noon: "午间",
  evening: "晚间",
};

const form = reactive({
  theme: "",
  targetDate: "",
  autoGenerateKeyframes: true,
  allowPaidGeneration: false,
});
const submitting = ref(false);

const runId = ref<string | null>((route.query.run as string) || null);
const graph = ref<RunGraph | null>(null);
const loadingGraph = ref(false);

/** 每集的Prompt预览（编译原文）与可编辑草稿。 */
const previews = reactive<Record<string, EpisodePromptPreview>>({});
const drafts = reactive<
  Record<string, { firstFrame: string; lastFrame: string; video: string }>
>({});
const saving = reactive<Record<string, boolean>>({});
const generating = reactive<Record<string, boolean>>({});

const episodes = computed(() =>
  [...(graph.value?.episodes ?? [])].sort((a, b) => a.sortOrder - b.sortOrder),
);

/** 首末帧资产：candidate 走人工审核面板，其余缩略图展示。 */
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

const allFramesReady = computed(
  () => episodes.value.length > 0 && episodes.value.every(frameReady),
);

/** 草稿与编译原文的差异即需要保存/生成时携带的覆盖。 */
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
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    loadingGraph.value = false;
  }
}

/** 拉取实时编译的三段Prompt；已有编辑覆盖优先回填草稿。 */
async function loadPreview(episode: EpisodeDto) {
  const preview = await api.getPromptPreview(episode.id);
  previews[episode.id] = preview;
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

/** 主题 → 全天三集规划（开关开时同一任务链式生成首末帧）。 */
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
      autoGenerateKeyframes: form.autoGenerateKeyframes,
    });
    jobs.track(accepted);
    ElMessage.info(
      form.autoGenerateKeyframes
        ? "规划任务已提交，完成后将链式生成三集首末帧…"
        : "规划任务已提交，正在等待导演输出…",
    );
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded" && final.result?.runId) {
      ElMessage.success("全天方案已生成");
      runId.value = String(final.result.runId);
      router.replace({ query: { run: runId.value } });
      await loadGraph();
    } else {
      ElMessage.error(final.error?.message ?? "规划任务失败");
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    submitting.value = false;
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

function goRun() {
  if (runId.value) {
    router.push(`/runs/${runId.value}`);
  }
}

onMounted(loadGraph);
</script>

<template>
  <div style="padding: 20px 24px">
    <h2 style="margin-top: 0">主题创作台</h2>

    <el-card shadow="never" style="margin-bottom: 16px">
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
        <el-form-item label="自动生成首末图">
          <el-switch v-model="form.autoGenerateKeyframes" />
          <span class="muted" style="margin-left: 10px">
            开启：规划完成后链式调用 Seedream 真实生成三集首末帧；关闭：先只展示
            Prompt，编辑后再手动生成
          </span>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.allowPaidGeneration">
            <span style="color: #f56c6c">
              我已知晓本次规划/生图将产生 Ark 付费模型调用
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
          <el-button v-if="runId" style="margin-left: 12px" @click="goRun">
            前往 Run 详情
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <template v-if="graph">
      <el-alert
        v-if="allFramesReady"
        type="success"
        :closable="false"
        style="margin-bottom: 16px"
      >
        三集首末帧均已审核就绪，可前往
        <el-link type="primary" @click="goRun">Run 详情页</el-link>
        续跑视频生成（将复用本页生成/编辑后的帧）。
      </el-alert>

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

        <el-row :gutter="16">
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
            {{ frameAssets(episode, "first_frame").length ? "重新生成首末帧" : "生成首末帧" }}
          </el-button>
          <span v-if="hasEdits(episode.id)" class="muted" style="margin-left: 10px">
            有未保存编辑
          </span>
        </div>

        <div v-for="role in ['first_frame', 'last_frame']" :key="role">
          <template v-for="asset in frameAssets(episode, role)" :key="asset.id">
            <AssetReviewPanel
              v-if="asset.status === 'candidate'"
              :asset="asset"
              :reviews="graph.reviews"
              :max-width="220"
              @reviewed="loadGraph"
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
    </template>

    <el-empty
      v-else-if="!loadingGraph"
      description="输入主题并提交后，这里将展示三集的 Prompt 与首末帧工作台"
    />
  </div>
</template>

<style scoped>
.muted {
  color: #8a8f99;
  font-size: 12px;
}
</style>
