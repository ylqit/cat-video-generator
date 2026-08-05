<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, ref } from "vue";

import { api, assetContentUrl, ApiError } from "../api/client";
import type { AssetDto, ReviewDto } from "../api/types";
import StatusBadge from "./StatusBadge.vue";

const props = defineProps<{
  asset: AssetDto;
  reviews: ReviewDto[];
  /** 预览最大宽度，默认适配视频 */
  maxWidth?: number;
}>();
const emit = defineEmits<{ reviewed: [] }>();

const reason = ref("");
const submitting = ref(false);

const url = computed(() => assetContentUrl(props.asset.id));
// 后端用业务媒体类型（video/image），部分历史数据则保存具体 MIME。
// 两种形式都应走浏览器原生视频播放器，避免把 MP4 误交给图片组件。
const isVideo = computed(
  () =>
    props.asset.mediaType === "video" ||
    props.asset.mediaType.startsWith("video/"),
);

/** 该资产最近一条人工审核决定。 */
const humanDecision = computed(() =>
  [...props.reviews]
    .filter(
      (review) => review.assetId === props.asset.id && review.source === "human",
    )
    .pop(),
);

/** 技术QC证据（ffprobe结果）。 */
const technical = computed(() =>
  [...props.reviews]
    .filter(
      (review) =>
        review.assetId === props.asset.id && review.source === "technical",
    )
    .pop(),
);

const technicalPassed = computed(() => {
  const evidence = technical.value?.evidence;
  return Boolean(
    evidence && typeof evidence === "object" && evidence.passed === true,
  );
});

/** Ark 视觉语义审核证据（身份/画风/世界连续性/叙事）。 */
const visualReview = computed(() =>
  [...props.reviews]
    .filter(
      (review) =>
        review.assetId === props.asset.id && review.source === "ark_visual",
    )
    .pop(),
);

const visualEvidence = computed(() => {
  const evidence = visualReview.value?.evidence;
  if (!evidence || typeof evidence !== "object") {
    return null;
  }
  return evidence as Record<string, unknown>;
});

const VISUAL_FLAG_LABEL: Record<string, string> = {
  identityOk: "身份一致",
  styleOk: "画风一致",
  worldContinuityOk: "世界连续",
  narrativeOrderOk: "叙事顺序",
};

const visualFlags = computed(() => {
  const evidence = visualEvidence.value;
  if (!evidence) {
    return [];
  }
  return Object.entries(VISUAL_FLAG_LABEL)
    .filter(([key]) => typeof evidence[key] === "boolean")
    .map(([key, label]) => ({ label, ok: evidence[key] as boolean }));
});

const visualViolations = computed(() => {
  const value = visualEvidence.value?.violations;
  return Array.isArray(value) ? (value as string[]) : [];
});

const visualObservations = computed(() => {
  const value = visualEvidence.value?.observations;
  return Array.isArray(value) ? (value as string[]) : [];
});

const visualConfidence = computed(() => {
  const value = visualEvidence.value?.confidence;
  return typeof value === "number" ? value : null;
});

const visualDecisionLabel = computed(() => {
  if (visualReview.value?.decision === "approved") {
    return "通过";
  }
  if (visualReview.value?.decision === "rejected") {
    return "打回";
  }
  if (typeof visualEvidence.value?.diagnosticError === "string") {
    return "诊断未完成";
  }
  return "待处理";
});

const visualDiagnosticError = computed(() => {
  const value = visualEvidence.value?.diagnosticError;
  return typeof value === "string" ? value : null;
});

async function decide(approve: boolean) {
  if (!reason.value.trim()) {
    ElMessage.warning("请填写审核理由");
    return;
  }
  submitting.value = true;
  try {
    await api.review(props.asset.id, approve, reason.value.trim());
    ElMessage.success(approve ? "已通过" : "已打回");
    reason.value = "";
    emit("reviewed");
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div style="margin-top: 10px">
    <video
      v-if="isVideo"
      :src="url"
      controls
      preload="metadata"
      :style="`width: 100%; max-width: ${maxWidth ?? 320}px; border-radius: 8px; background: #000`"
    />
    <el-image
      v-else
      :src="url"
      fit="contain"
      :preview-src-list="[url]"
      preview-teleported
      :style="`width: 100%; max-width: ${maxWidth ?? 240}px; border-radius: 8px; background: #0c0d10`"
    />
    <div style="margin-top: 6px">
      <StatusBadge :status="asset.status" />
      <a
        :href="url"
        :download="`${asset.sha256.slice(0, 8)}${isVideo ? '.mp4' : '.png'}`"
        style="margin-left: 10px; font-size: 12px"
      >
        下载
      </a>
    </div>
    <div v-if="technical" class="muted" style="margin-top: 6px">
      技术QC：{{ technicalPassed ? "通过" : "未通过" }}
      <span v-if="technical.warnings?.length">
        · {{ technical.warnings.length }} 条警告
      </span>
    </div>
    <div v-if="visualReview" style="margin-top: 6px">
      <div class="muted">
        语义审核：{{ visualDecisionLabel }}
        <span v-if="visualConfidence !== null">
          · 置信度 {{ (visualConfidence * 100).toFixed(0) }}%
        </span>
      </div>
      <div v-if="visualDiagnosticError" class="muted" style="margin-top: 4px">
        诊断错误：{{ visualDiagnosticError }}；最终结论仍由人工审核决定
      </div>
      <el-tag
        v-for="flag in visualFlags"
        :key="flag.label"
        :type="flag.ok ? 'success' : 'danger'"
        size="small"
        style="margin: 4px 6px 0 0"
      >
        {{ flag.label }}{{ flag.ok ? "✓" : "✗" }}
      </el-tag>
      <div v-if="visualViolations.length" class="muted" style="margin-top: 4px">
        违规：{{ visualViolations.join("；") }}
      </div>
      <div
        v-if="visualObservations.length"
        class="muted"
        style="margin-top: 4px"
      >
        观察：{{ visualObservations.join("；") }}
      </div>
    </div>
    <div v-if="humanDecision" class="muted" style="margin-top: 4px">
      人工审核：{{ humanDecision.decision === "approved" ? "通过" : "打回" }}
      · {{ humanDecision.reason }}
    </div>
    <template v-if="asset.status === 'candidate'">
      <el-input
        v-model="reason"
        size="small"
        placeholder="审核理由（必填）"
        style="margin-top: 8px"
      />
      <div style="margin-top: 8px">
        <el-button
          type="success"
          size="small"
          :loading="submitting"
          @click="decide(true)"
        >
          通过
        </el-button>
        <el-button
          type="danger"
          size="small"
          :loading="submitting"
          @click="decide(false)"
        >
          打回
        </el-button>
      </div>
    </template>
  </div>
</template>
