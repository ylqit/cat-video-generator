<script setup lang="ts">
import { computed } from "vue";

import type {
  AssetDto,
  EpisodeDto,
  PromptDto,
  ReviewDto,
  StepDto,
} from "../api/types";
import { useCanonStore } from "../stores/canon";
import AssetThumb from "./AssetThumb.vue";
import GenerateButton from "./GenerateButton.vue";
import PromptCollapse from "./PromptCollapse.vue";
import StatusBadge from "./StatusBadge.vue";
import VideoReviewPanel from "./VideoReviewPanel.vue";

const props = defineProps<{
  runId: string;
  episode: EpisodeDto;
  assets: AssetDto[];
  steps: StepDto[];
  prompts: PromptDto[];
  reviews: ReviewDto[];
}>();
const emit = defineEmits<{ changed: [] }>();

const canon = useCanonStore();

const SLOT_LABEL: Record<string, string> = {
  morning: "早晨",
  noon: "中午",
  evening: "傍晚",
};

/** Episode状态机顺序，用于进度条定位。 */
const STATUS_FLOW = [
  "planned",
  "preparing_visuals",
  "video_pending",
  "video_generating",
  "media_qc",
  "content_review",
  "ready",
];

const episodeAssets = computed(() =>
  props.assets.filter((asset) => asset.episodeId === props.episode.id),
);
const frameAssets = computed(() =>
  episodeAssets.value.filter((asset) =>
    ["first_frame", "last_frame"].includes(asset.role),
  ),
);
const videoAsset = computed(() =>
  [...episodeAssets.value].reverse().find((asset) => asset.role === "video"),
);
const episodeSteps = computed(() =>
  props.steps.filter((step) => step.episodeId === props.episode.id),
);
const stepIds = computed(() => new Set(episodeSteps.value.map((s) => s.id)));
const videoPrompts = computed(() =>
  props.prompts.filter(
    (prompt) => prompt.purpose === "video" && stepIds.value.has(prompt.stepId),
  ),
);
const imagePrompts = computed(() =>
  props.prompts.filter(
    (prompt) => prompt.purpose === "image" && stepIds.value.has(prompt.stepId),
  ),
);
const frozenStep = computed(() =>
  episodeSteps.value.find((step) => step.status === "submission_unknown"),
);
const videoStep = computed(() =>
  [...episodeSteps.value].reverse().find((step) => step.kind === "video"),
);
const activeIndex = computed(() => {
  const index = STATUS_FLOW.indexOf(props.episode.status);
  return index === -1 ? 0 : index;
});
const script = computed(() => props.episode.script);
const referenceRoles = computed(
  () => script.value.required_reference_roles ?? ["person", "cat", "style"],
);
</script>

<template>
  <el-card shadow="never" style="background: #16181d; border-color: #26282e">
    <template #header>
      <div style="display: flex; align-items: center; gap: 10px">
        <el-tag effect="dark" size="small">#{{ episode.sortOrder }}</el-tag>
        <el-tag type="info" size="small">
          {{ SLOT_LABEL[episode.slot] ?? episode.slot }}
        </el-tag>
        <strong>{{ episode.title }}</strong>
        <span class="muted">{{ script.duration_seconds }}s</span>
        <StatusBadge :status="episode.status" />
        <div style="flex: 1" />
        <GenerateButton
          :run-id="runId"
          :slot="episode.slot as 'morning' | 'noon' | 'evening'"
          label="生成视频"
          @submitted="emit('changed')"
        />
      </div>
    </template>

    <el-steps
      :active="activeIndex"
      align-center
      :process-status="episode.status === 'failed' ? 'error' : 'process'"
      finish-status="success"
      style="margin-bottom: 14px"
    >
      <el-step title="规划" />
      <el-step title="画面" />
      <el-step title="视频" />
      <el-step title="QC" />
      <el-step title="审核" />
      <el-step title="就绪" />
    </el-steps>

    <el-alert
      v-if="frozenStep"
      type="error"
      :closable="false"
      title="提交状态未知，已冻结"
      description="该步骤提交时断线，不确定供应商是否已受理。请先在 Ark 控制台人工对账，不要重复提交。"
      style="margin-bottom: 12px"
    />

    <div class="section">
      <div class="section-title">视频提示词（剧本）</div>
      <div><strong>主事件：</strong>{{ script.main_event }}</div>
      <div><strong>场景：</strong>{{ script.scene }}</div>
      <div>
        <strong>服饰：</strong>{{ script.appearance.description }}
        <span
          v-if="script.appearance.changes_from_previous.length"
          class="muted"
        >
          （变化：{{ script.appearance.changes_from_previous.join("、") }}；
          {{ script.appearance.change_reason }}）
        </span>
      </div>
      <ol style="margin: 6px 0; padding-left: 20px">
        <li v-for="action in [...script.actions].sort((a, b) => a.order - b.order)" :key="action.order">
          {{ action.action }}
          <span class="muted">→ {{ action.visible_result }}</span>
        </li>
      </ol>
      <div><strong>结尾：</strong>{{ script.ending }}</div>
      <PromptCollapse :prompts="videoPrompts" title="完整视频Prompt" />
      <PromptCollapse :prompts="imagePrompts" title="完整图片Prompt" />
    </div>

    <div class="section">
      <div class="section-title">出场资产</div>
      <template v-for="role in referenceRoles" :key="role">
        <AssetThumb
          v-if="canon.latestByRole(role)"
          :asset-id="canon.latestByRole(role)!.id"
          :label="role"
        />
        <el-tag v-else type="danger" size="small" style="margin-right: 6px">
          缺少{{ role }}
        </el-tag>
      </template>
    </div>

    <div v-if="frameAssets.length" class="section">
      <div class="section-title">分镜图</div>
      <AssetThumb
        v-for="asset in frameAssets"
        :key="asset.id"
        :asset-id="asset.id"
        :label="asset.role === 'first_frame' ? '首帧' : '尾帧'"
      />
    </div>

    <div class="section">
      <div class="section-title">视频</div>
      <div v-if="videoStep?.providerTaskId" class="muted">
        Ark任务：{{ videoStep.providerTaskId }}
        <StatusBadge :status="videoStep.status" style="margin-left: 6px" />
      </div>
      <VideoReviewPanel
        v-if="videoAsset"
        :asset="videoAsset"
        :reviews="reviews"
        @reviewed="emit('changed')"
      />
      <span v-else class="muted">尚未生成视频</span>
    </div>
  </el-card>
</template>

<style scoped>
.section {
  margin-bottom: 14px;
  font-size: 13px;
  line-height: 1.7;
}
.section-title {
  color: #9ca3af;
  font-size: 12px;
  margin-bottom: 6px;
  border-left: 3px solid #409eff;
  padding-left: 8px;
}
</style>
