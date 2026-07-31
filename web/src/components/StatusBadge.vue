<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ status: string }>();

/** 状态 → 标签类型与中文文案的集中映射。 */
const META: Record<string, { type: "" | "success" | "warning" | "danger" | "info" | "primary"; label: string }> = {
  draft: { type: "info", label: "草稿" },
  planned: { type: "info", label: "已规划" },
  planning_review: { type: "warning", label: "规划审核" },
  awaiting_review: { type: "warning", label: "等待审核" },
  generating: { type: "primary", label: "生成中" },
  reviewing: { type: "warning", label: "审核中" },
  ready: { type: "success", label: "就绪" },
  delivered: { type: "success", label: "已交付" },
  failed: { type: "danger", label: "失败" },
  expired: { type: "info", label: "已过期" },
  cancelled: { type: "info", label: "已取消" },
  archived: { type: "info", label: "已归档" },
  preparing_visuals: { type: "primary", label: "准备画面" },
  video_pending: { type: "primary", label: "等待视频" },
  video_generating: { type: "primary", label: "视频生成中" },
  media_qc: { type: "warning", label: "技术QC" },
  content_review: { type: "warning", label: "内容审核" },
  pending: { type: "info", label: "待处理" },
  submitting: { type: "primary", label: "提交中" },
  submission_unknown: { type: "danger", label: "提交未知·冻结" },
  queued: { type: "primary", label: "排队中" },
  running: { type: "primary", label: "运行中" },
  succeeded: { type: "success", label: "成功" },
  candidate: { type: "warning", label: "候选" },
  approved: { type: "success", label: "已通过" },
  rejected: { type: "danger", label: "已拒绝" },
  building: { type: "primary", label: "构建中" },
};

const meta = computed(
  () => META[props.status] ?? { type: "info" as const, label: props.status },
);
</script>

<template>
  <el-tag :type="meta.type" size="small" disable-transitions>
    {{ meta.label }}
  </el-tag>
</template>
