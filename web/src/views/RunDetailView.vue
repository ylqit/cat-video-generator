<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref } from "vue";

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

/** 运行级诊断只展示确定性的可见世界矛盾。 */
const diagnostics = computed(() => {
  const run = graph.value?.run;
  if (!run) {
    return null;
  }
  const items: string[] = [];
  if (run.contradictions?.length) {
    items.push(`世界一致性矛盾：${run.contradictions.join("；")}`);
  }
  return items.length ? items : null;
});

const trackedJobs = computed(() => Object.values(jobs.byDedupKey));

/** 从被技术审核拒绝的最新导演步骤定位需要人工重规划的时段。 */
const planningReviewSlot = computed(() => {
  if (graph.value?.run.status !== "planning_review") {
    return null;
  }
  const rejectedStepIds = new Set(
    graph.value.reviews
      .filter(
        (review) =>
          review.decision === "rejected" &&
          review.evidence.phase === "episode_contract",
      )
      .map((review) => review.stepId),
  );
  const step = [...graph.value.steps]
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .find(
      (item) =>
        rejectedStepIds.has(item.id) &&
        item.operationKey.startsWith("director:episode:"),
    );
  const slot = step?.operationKey.split(":").at(-1);
  return slot && ["morning", "noon", "evening"].includes(slot) ? slot : null;
});

/** 规划审核必须明确给出人工原因并再次确认付费，不能误走初始规划恢复。 */
async function replanFailedEpisode() {
  if (!planningReviewSlot.value) {
    ElMessage.error("无法定位失败时段，请在工作流步骤中查看审核记录");
    return;
  }
  try {
    const { value: reason } = await ElMessageBox.prompt(
      `重新调用${planningReviewSlot.value}时段导演会产生一次Ark付费请求，请说明修正目标。`,
      "重规划失败时段",
      {
        confirmButtonText: "确认付费并重规划",
        cancelButtonText: "取消",
        inputValue: "保留主事件，减少重复描述，使执行Prompt聚焦且满足长度预算",
        inputValidator: (value) => Boolean(value.trim()) || "必须填写重规划原因",
      },
    );
    const accepted = await api.replanEpisode(
      props.id,
      planningReviewSlot.value,
      reason.trim(),
      true,
    );
    jobs.track(accepted);
    ElMessage.success(`${planningReviewSlot.value}时段重规划任务已提交`);
    await refresh();
  } catch (error) {
    if (error === "cancel" || error === "close") {
      return;
    }
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
        v-if="planningReviewSlot"
        size="small"
        type="warning"
        @click="replanFailedEpisode"
      >
        重规划{{ planningReviewSlot }}时段
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

  </div>
  <div v-else class="page" v-loading="true" style="min-height: 300px" />
</template>
