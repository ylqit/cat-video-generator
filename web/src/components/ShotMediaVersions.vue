<script setup lang="ts">
import { computed } from "vue";

import { assetContentUrl } from "../api/client";
import type { AssetDto, AttemptDto, ShotDto, ShotPromptPreview } from "../api/types";

const props = defineProps<{
  shot: ShotDto;
  anchorPreview: ShotPromptPreview | null;
  videoPreview: ShotPromptPreview | null;
}>();
const emit = defineEmits<{
  review: [asset: AssetDto, decision: "approved" | "rejected", stale: boolean];
  select: [asset: AssetDto, stale: boolean];
  resume: [stepId: string];
  reconcile: [stepId: string];
  retry: [kind: "anchor" | "video"];
}>();

interface MediaVersionEntry {
  key: string;
  kind: "anchor" | "video";
  attempt?: AttemptDto;
  asset?: AssetDto;
}

const relevantAssets = computed(() => props.shot.assets.filter(
  (item) => ["shot_anchor", "shot_video", "shot_video_edit"].includes(item.role),
));
const versions = computed<MediaVersionEntry[]>(() => {
  const operations = new Set(["image:anchor", "video:shot", "video:range-edit"]);
  const attemptEntries: MediaVersionEntry[] = props.shot.attempts
    .filter((item) => operations.has(item.operationKey))
    .map((attempt) => ({
      key: `step:${attempt.id}`,
      kind: attempt.operationKey === "image:anchor" ? "anchor" as const : "video" as const,
      attempt,
      asset: relevantAssets.value.find((item) => item.producingStepId === attempt.id),
    }));
  const knownSteps = new Set(attemptEntries.flatMap(
    (item) => item.attempt ? [item.attempt.id] : [],
  ));
  const orphanEntries: MediaVersionEntry[] = relevantAssets.value
    .filter((asset) => !asset.producingStepId || !knownSteps.has(asset.producingStepId))
    .map((asset) => ({
      key: `asset:${asset.id}`,
      kind: asset.mediaType === "image" ? "anchor" as const : "video" as const,
      asset,
    }));
  return [...attemptEntries, ...orphanEntries].sort((left, right) => {
    const leftDate = left.asset?.createdAt ?? left.attempt?.createdAt ?? "";
    const rightDate = right.asset?.createdAt ?? right.attempt?.createdAt ?? "";
    if (leftDate !== rightDate) return rightDate.localeCompare(leftDate);
    return Number(right.attempt?.attempt ?? 0) - Number(left.attempt?.attempt ?? 0);
  });
});

function currentHash(entry: MediaVersionEntry): string | undefined {
  return entry.kind === "anchor"
    ? props.anchorPreview?.sourceRevisionHash
    : props.videoPreview?.sourceRevisionHash;
}

function isStale(entry: MediaVersionEntry): boolean {
  const snapshot = entry.attempt?.inputSnapshot;
  const generatedHash = snapshot?.sourceRevisionHash ?? snapshot?.inputHash;
  const current = currentHash(entry);
  return typeof generatedHash === "string" && Boolean(current) && generatedHash !== current;
}

function isSelected(entry: MediaVersionEntry): boolean {
  if (!entry.asset) return false;
  return entry.kind === "anchor"
    ? props.shot.selectedAnchorAssetId === entry.asset.id
    : props.shot.selectedVideoAssetId === entry.asset.id;
}

function statusText(entry: MediaVersionEntry): string {
  return entry.asset?.status ?? entry.attempt?.status ?? "unknown";
}

function referenceCount(entry: MediaVersionEntry): number {
  const snapshot = entry.attempt?.inputSnapshot;
  const sources = snapshot?.sourceAssets ?? snapshot?.references ?? snapshot?.sourceAssetIds;
  return Array.isArray(sources) ? sources.length : 0;
}

function versionTitle(entry: MediaVersionEntry): string {
  const label = entry.kind === "anchor" ? "锚点" : "视频";
  return `${label} V${entry.attempt?.attempt ?? "历史"}`;
}
</script>

<template>
  <section class="media-versions">
    <div class="version-heading">
      <div><b>锚点与视频版本</b><small>运行中任务、历史媒体和审核状态统一展示</small></div>
      <el-tag>{{ versions.length }} 个版本/任务</el-tag>
    </div>
    <el-empty v-if="!versions.length" description="还没有锚点或视频生成记录" />
    <article
      v-for="entry in versions"
      :key="entry.key"
      class="media-version-card"
      :class="{ selected: isSelected(entry), stale: isStale(entry) }"
    >
      <div class="version-heading">
        <b>{{ versionTitle(entry) }}</b>
        <div>
          <el-tag v-if="isSelected(entry)" type="success" size="small">当前选中</el-tag>
          <el-tag v-if="isStale(entry)" type="warning" size="small">基于旧输入</el-tag>
          <el-tag size="small">{{ statusText(entry) }}</el-tag>
        </div>
      </div>
      <small>{{ entry.attempt?.model || '历史模型未记录' }} · {{ entry.asset?.createdAt || entry.attempt?.createdAt || '时间未记录' }} · {{ referenceCount(entry) }} 张输入图</small>
      <template v-if="entry.asset?.contentReady">
        <img v-if="entry.asset.mediaType === 'image'" :src="assetContentUrl(entry.asset.id)" />
        <video v-else controls preload="metadata" :src="assetContentUrl(entry.asset.id)" />
      </template>
      <el-alert
        v-else-if="entry.asset"
        type="error"
        :title="`媒体内容不可读取 · 资产 ${entry.asset.id}`"
        :closable="false"
      />
      <el-alert
        v-else-if="entry.attempt?.status === 'failed'"
        type="error"
        :title="String(entry.attempt.error?.message ?? entry.attempt.error?.code ?? '生成失败')"
        :closable="false"
      />
      <p v-else class="running-copy">任务尚未生成媒体文件；可离开页面，状态由全局任务中心继续跟踪。</p>
      <el-alert
        v-if="isStale(entry)"
        type="warning"
        title="片段文字、时长、锚点或引用已变化。该版本仍可查看；重新选择前需要确认风险。"
        :closable="false"
      />
      <div class="version-actions">
        <template v-if="entry.asset?.status === 'candidate'">
          <el-button size="small" type="success" @click="emit('review', entry.asset, 'approved', isStale(entry))">批准并选择</el-button>
          <el-button size="small" type="danger" @click="emit('review', entry.asset, 'rejected', false)">拒绝</el-button>
        </template>
        <el-button
          v-else-if="entry.asset?.status === 'approved' && !isSelected(entry)"
          size="small"
          @click="emit('select', entry.asset, isStale(entry))"
        >选择此历史版本</el-button>
        <el-button
          v-if="entry.attempt?.providerTaskId && ['queued', 'running'].includes(entry.attempt.status)"
          size="small"
          @click="emit('resume', entry.attempt.id)"
        >恢复查询</el-button>
        <el-button
          v-if="entry.attempt?.status === 'submission_unknown' && entry.attempt.kind === 'video'"
          size="small"
          type="warning"
          @click="emit('reconcile', entry.attempt.id)"
        >对账 Provider 任务</el-button>
        <el-button
          v-if="entry.attempt?.status === 'failed'"
          size="small"
          @click="emit('retry', entry.kind)"
        >按当前输入重试</el-button>
      </div>
      <details v-if="entry.attempt?.prompt || entry.attempt?.inputSnapshot">
        <summary>查看本版本 Prompt 与实际输入快照</summary>
        <pre v-if="entry.attempt.prompt">{{ entry.attempt.prompt.text }}</pre>
        <pre>{{ JSON.stringify(entry.attempt.inputSnapshot, null, 2) }}</pre>
      </details>
    </article>
  </section>
</template>

<style scoped>
.media-versions { display: grid; gap: 10px; }.version-heading, .version-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }.version-heading > div { display: grid; gap: 3px; }.version-heading small, .media-version-card > small { color: #8290a4; }.media-version-card { display: grid; gap: 8px; padding: 10px; border: 1px solid #303b4c; border-radius: 8px; background: #101721; }.media-version-card.selected { border-color: #67c23a; }.media-version-card.stale { box-shadow: inset 3px 0 #e6a23c; }.media-version-card img, .media-version-card video { width: 100%; max-height: 360px; object-fit: contain; background: #080b10; border-radius: 6px; }.running-copy { margin: 0; color: #91a0b4; }.media-version-card pre { max-height: 260px; overflow: auto; white-space: pre-wrap; }
</style>
