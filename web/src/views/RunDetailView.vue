<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onMounted, ref } from "vue";

import { api, ApiError } from "../api/client";
import DeliveryPanel from "../components/DeliveryPanel.vue";
import EpisodeCard from "../components/EpisodeCard.vue";
import GenerateButton from "../components/GenerateButton.vue";
import StatusBadge from "../components/StatusBadge.vue";
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
      <el-button size="small" @click="resume">恢复在途任务</el-button>
      <GenerateButton
        :run-id="id"
        :slot="null"
        label="生成全天"
        @submitted="onChanged"
      />
    </div>

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

    <DeliveryPanel
      ref="deliveryPanel"
      :run-id="id"
      :can-deliver="graph.run.status === 'ready'"
    />
  </div>
  <div v-else class="page" v-loading="true" style="min-height: 300px" />
</template>
