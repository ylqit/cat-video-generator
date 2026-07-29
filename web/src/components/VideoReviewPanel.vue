<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, ref } from "vue";

import { api, assetContentUrl, ApiError } from "../api/client";
import type { AssetDto, ReviewDto } from "../api/types";
import StatusBadge from "./StatusBadge.vue";

const props = defineProps<{
  asset: AssetDto;
  reviews: ReviewDto[];
}>();
const emit = defineEmits<{ reviewed: [] }>();

const reason = ref("");
const submitting = ref(false);

const url = computed(() => assetContentUrl(props.asset.id));

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
      :src="url"
      controls
      preload="metadata"
      style="width: 100%; max-width: 320px; border-radius: 8px; background: #000"
    />
    <div style="margin-top: 6px">
      <StatusBadge :status="asset.status" />
      <a
        :href="url"
        :download="`${asset.sha256.slice(0, 8)}.mp4`"
        style="margin-left: 10px; font-size: 12px"
      >
        下载 MP4
      </a>
    </div>
    <div v-if="technical" class="muted" style="margin-top: 6px">
      技术QC：{{ technical.decision === "approved" ? "通过" : "未通过" }}
      <span v-if="technical.warnings?.length">
        · {{ technical.warnings.length }} 条警告
      </span>
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
