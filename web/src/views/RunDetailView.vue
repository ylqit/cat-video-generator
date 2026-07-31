<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";

import { api, ApiError } from "../api/client";
import DeliveryPanel from "../components/DeliveryPanel.vue";
import EpisodeCard from "../components/EpisodeCard.vue";
import GenerateButton from "../components/GenerateButton.vue";
import StatusBadge from "../components/StatusBadge.vue";
import StepList from "../components/StepList.vue";
import { usePolling } from "../composables/usePolling";
import { useCanonStore } from "../stores/canon";
import { useJobsStore } from "../stores/jobs";
import { useRunsStore } from "../stores/runs";

const props = defineProps<{ id: string }>();

const runs = useRunsStore();
const canon = useCanonStore();
const jobs = useJobsStore();
const deliveryPanel = ref<InstanceType<typeof DeliveryPanel>>();

const graph = computed(() => runs.graphs[props.id]);
const episodes = computed(() =>
  [...(graph.value?.episodes ?? [])].sort(
    (a, b) => a.sortOrder - b.sortOrder,
  ),
);

/** 分镜或步骤仍处于推进态时加快轮询。 */
const ACTIVE_EPISODE = new Set([
  "preparing_visuals",
  "video_pending",
  "video_generating",
  "media_qc",
]);
const isActive = computed(() => {
  if (jobs.hasActive) {
    return true;
  }
  if (episodes.value.some((episode) => ACTIVE_EPISODE.has(episode.status))) {
    return true;
  }
  return (graph.value?.steps ?? []).some((step) =>
    ["queued", "running", "submitting"].includes(step.status),
  );
});

/** 服务重启后数据库里仍有在途步骤，但内存任务已丢失时提示恢复。 */
const needsResume = computed(() => {
  const inFlight = (graph.value?.steps ?? []).some((step) =>
    ["queued", "running"].includes(step.status),
  );
  return inFlight && !jobs.hasActive;
});

async function refresh() {
  await jobs.refreshActive();
  await runs.fetchGraph(props.id);
}

const polling = usePolling(refresh, () => (isActive.value ? 5000 : 30000));

/** 运行级诊断：世界一致性矛盾与渲染风险摘要。 */
const diagnostics = computed(() => {
  const run = graph.value?.run;
  if (!run) {
    return null;
  }
  const items: string[] = [];
  if (run.contradictions?.length) {
    items.push(`世界一致性矛盾：${run.contradictions.join("；")}`);
  }
  if (run.renderRiskLevel && run.renderRiskLevel !== "low") {
    items.push(
      `渲染风险·${run.renderRiskLevel}` +
        (run.renderRiskReasons?.length
          ? `：${run.renderRiskReasons.join("；")}`
          : ""),
    );
  }
  if (run.multiClipRecommended) {
    items.push("导演建议改用 multi_clip 分段生成");
  }
  return items.length ? items : null;
});

const trackedJobs = computed(() => Object.values(jobs.byDedupKey));

/** 规划审核/失败时可恢复规划（付费闸）。 */
async function resumePlanning() {
  try {
    const accepted = await api.resumePlanning(props.id, true);
    jobs.track(accepted);
    ElMessage.success("恢复规划任务已提交");
    await refresh();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

const compareVisible = ref(false);
const compareForm = reactive({
  resolution: "480p" as "480p" | "720p",
  allowPaidGeneration: false,
});

async function submitCompare() {
  try {
    const accepted = await api.compareResolution(
      props.id,
      compareForm.resolution,
      true,
    );
    jobs.track(accepted);
    compareVisible.value = false;
    ElMessage.success("分辨率对比任务已提交");
    await refresh();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

async function resume() {
  try {
    const accepted = await api.resume(props.id);
    jobs.track(accepted);
    ElMessage.success("恢复任务已提交");
    await refresh();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

async function onChanged() {
  await refresh();
  await deliveryPanel.value?.refresh();
}

onMounted(() => {
  void canon.fetch();
  polling.start();
});
</script>

<template>
  <div class="page" v-if="graph">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 14px">
      <router-link to="/runs" style="font-size: 13px">← 返回列表</router-link>
      <h2 style="margin: 0">{{ graph.run.contentDate }}</h2>
      <StatusBadge :status="graph.run.status" />
      <span class="muted">{{ graph.run.theme }}</span>
      <div style="flex: 1" />
      <el-button
        v-if="graph.run.status === 'planning_review'"
        size="small"
        type="warning"
        @click="resumePlanning"
      >
        恢复规划
      </el-button>
      <el-button size="small" @click="compareVisible = true">
        分辨率对比
      </el-button>
      <el-button size="small" @click="resume">恢复在途任务</el-button>
      <GenerateButton
        :run-id="id"
        :slot="null"
        label="生成全天"
        @submitted="onChanged"
      />
    </div>

    <div
      v-if="trackedJobs.length"
      style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px"
    >
      <el-tag
        v-for="job in trackedJobs"
        :key="job.jobId"
        :type="
          job.status === 'failed'
            ? 'danger'
            : job.status === 'succeeded'
              ? 'success'
              : 'warning'
        "
        size="small"
      >
        {{ job.kind }} · {{ job.status }}
      </el-tag>
    </div>

    <el-alert
      v-if="diagnostics"
      type="warning"
      :closable="false"
      style="margin-bottom: 14px"
      title="运行诊断"
      :description="diagnostics.join('；')"
    />

    <el-alert
      v-if="needsResume"
      type="warning"
      :closable="false"
      style="margin-bottom: 14px"
      title="检测到在途的供应商任务，但本服务没有对应的后台任务"
      description="服务可能已重启。可点击右上角「恢复在途任务」继续轮询，不会重复扣费。"
    />

    <div style="display: flex; flex-direction: column; gap: 16px">
      <EpisodeCard
        v-for="episode in episodes"
        :key="episode.id"
        :run-id="id"
        :episode="episode"
        :assets="graph.assets"
        :steps="graph.steps"
        :prompts="graph.prompts"
        :reviews="graph.reviews"
        @changed="onChanged"
      />
    </div>

    <el-collapse style="margin-top: 16px">
      <el-collapse-item title="工作流步骤（失败可重试）" name="steps">
        <StepList :steps="graph.steps" @changed="onChanged" />
      </el-collapse-item>
    </el-collapse>

    <DeliveryPanel
      ref="deliveryPanel"
      :run-id="id"
      :can-deliver="graph.run.status === 'ready'"
    />

    <el-dialog v-model="compareVisible" title="分辨率对比" width="440px">
      <el-form label-width="110px">
        <el-form-item label="对比分辨率">
          <el-radio-group v-model="compareForm.resolution">
            <el-radio value="480p">480p</el-radio>
            <el-radio value="720p">720p</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="compareForm.allowPaidGeneration">
            <span style="color: #f56c6c">
              我已知晓本次对比将产生 Ark 付费模型调用
            </span>
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="compareVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!compareForm.allowPaidGeneration"
          @click="submitCompare"
        >
          提交对比
        </el-button>
      </template>
    </el-dialog>
  </div>
  <div v-else class="page" v-loading="true" style="min-height: 300px" />
</template>
