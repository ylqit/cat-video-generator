<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, nextTick, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type {
  AssetDto,
  EpisodeDto,
  JobAccepted,
  ReviewDto,
  VideoSequenceDto,
  WorkflowNodeDto,
} from "../api/types";

const props = defineProps<{
  episode: EpisodeDto;
  node: WorkflowNodeDto;
  sequences: VideoSequenceDto[];
  assets: AssetDto[];
  reviews: ReviewDto[];
  outcomeConfirmed: boolean;
  sequenceId?: string;
}>();
const emit = defineEmits<{
  job: [job: JobAccepted];
  changed: [];
  sequenceSelected: [sequenceId: string];
}>();

const activeSequenceId = ref("");
const compareSequenceId = ref("");
const startMs = ref(0);
const endMs = ref(0);
const boundaryMode = ref<"snap_to_shot" | "exact">("snap_to_shot");
const loopSelection = ref(false);
const thumbnails = ref<string[]>([]);
const player = ref<HTMLVideoElement>();
const strip = ref<HTMLDivElement>();
const currentMs = ref(0);
const dragOriginMs = ref<number | null>(null);

const sortedSequences = computed(() => [...props.sequences].sort((a, b) => b.revision - a.revision));
const activeSequence = computed(() =>
  sortedSequences.value.find((item) => item.id === activeSequenceId.value) ?? sortedSequences.value[0],
);
const activeAsset = computed(() =>
  props.assets.find((item) => item.id === activeSequence.value?.renderedAssetId),
);
const compareSequence = computed(() =>
  sortedSequences.value.find((item) => item.id === compareSequenceId.value),
);
const compareAsset = computed(() =>
  props.assets.find((item) => item.id === compareSequence.value?.renderedAssetId),
);
const durationMs = computed(() => activeSequence.value?.durationMs ?? 0);
const boundaries = computed(() => {
  const raw = activeAsset.value?.metadata.diagnosticShotBoundariesMs
    ?? activeAsset.value?.metadata.shotBoundariesMs;
  return Array.isArray(raw) ? raw.map(Number).filter(Number.isFinite) : [];
});
const shotCards = computed(() => {
  const cuts = [0, ...boundaries.value.filter((value) => value > 0 && value < durationMs.value), durationMs.value];
  return props.episode.script.shots.map((shot, index) => {
    const start = cuts[index] ?? Math.round(durationMs.value * index / props.episode.script.shots.length);
    const end = cuts[index + 1] ?? Math.round(durationMs.value * (index + 1) / props.episode.script.shots.length);
    return {
      order: shot.order,
      direction: shot.direction,
      startMs: start,
      endMs: end,
      aiFindings: findingsForRange(start, end),
      humanNotes: notesForRange(start, end),
      thumbnail: thumbnails.value[Math.min(thumbnails.value.length - 1, Math.max(0, Math.round(index * thumbnails.value.length / props.episode.script.shots.length)))],
    };
  });
});

function assetReviews() {
  return props.reviews.filter((item) => item.assetId === activeAsset.value?.id);
}

function findingsForRange(start: number, end: number) {
  const findings: string[] = [];
  for (const review of assetReviews().filter((item) => item.source === "ark_visual")) {
    const evidence = review.evidence.evidence;
    if (!Array.isArray(evidence)) continue;
    for (const item of evidence) {
      if (!item || typeof item !== "object") continue;
      const row = item as Record<string, unknown>;
      const timestamp = Number.parseFloat(String(row.timestamp ?? "")) * 1000;
      if (!Number.isFinite(timestamp) || timestamp < start || timestamp > end) continue;
      const message = String(row.relationError ?? row.observation ?? "").trim();
      if (message && !findings.includes(message)) findings.push(message);
    }
  }
  return findings;
}

function notesForRange(start: number, end: number) {
  return assetReviews()
    .filter((item) => item.source === "human_note")
    .filter((item) => {
      const noteStart = Number(item.evidence.startMs ?? -1);
      const noteEnd = Number(item.evidence.endMs ?? -1);
      return noteStart < end && noteEnd > start;
    })
    .map((item) => item.reason ?? "人工备注");
}

watch(
  [() => props.sequences, () => props.sequenceId] as const,
  () => {
    if (props.sequenceId && props.sequences.some((item) => item.id === props.sequenceId)) {
      activeSequenceId.value = props.sequenceId;
      return;
    }
    const selected = props.sequences.find(
      (item) => item.renderedAssetId === props.episode.selectedVideoAssetId,
    );
    if (!activeSequenceId.value || !props.sequences.some((item) => item.id === activeSequenceId.value)) {
      activeSequenceId.value = (
        props.sequences.find((item) => item.id === props.sequenceId)?.id
        ?? selected?.id
        ?? sortedSequences.value[0]?.id
        ?? ""
      );
    }
  },
  { immediate: true, deep: true },
);
watch(
  [activeSequenceId, () => activeSequence.value?.renderedAssetId],
  async () => {
    const value = activeSequence.value;
    startMs.value = 0;
    endMs.value = Math.min(value?.durationMs ?? 0, 4000);
    thumbnails.value = [];
    if (value) emit("sequenceSelected", value.id);
    await nextTick();
    if (activeAsset.value) {
      void captureThumbnails(`/api/v1/assets/${activeAsset.value.id}/content`);
    }
  },
  { immediate: true },
);

function formatTime(value: number) {
  return `${(value / 1000).toFixed(2)}s`;
}

function onTimeUpdate() {
  const video = player.value;
  if (!video) return;
  currentMs.value = Math.round(video.currentTime * 1000);
  if (loopSelection.value && currentMs.value >= endMs.value) {
    video.currentTime = startMs.value / 1000;
  }
}

function timelineMs(event: PointerEvent) {
  const element = strip.value;
  if (!element || durationMs.value <= 0) return 0;
  const bounds = element.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
  return Math.round(ratio * durationMs.value / 40) * 40;
}

function beginRangeDrag(event: PointerEvent) {
  const value = timelineMs(event);
  dragOriginMs.value = value;
  startMs.value = Math.max(0, Math.min(value, durationMs.value - 500));
  endMs.value = Math.min(durationMs.value, startMs.value + 500);
  strip.value?.setPointerCapture(event.pointerId);
}

function moveRangeDrag(event: PointerEvent) {
  if (dragOriginMs.value === null) return;
  const value = timelineMs(event);
  const left = Math.min(dragOriginMs.value, value);
  const right = Math.max(dragOriginMs.value, value);
  startMs.value = Math.max(0, Math.min(left, durationMs.value - 500));
  endMs.value = Math.min(durationMs.value, Math.max(startMs.value + 500, right));
}

function finishRangeDrag(event: PointerEvent) {
  if (dragOriginMs.value === null) return;
  moveRangeDrag(event);
  dragOriginMs.value = null;
  if (strip.value?.hasPointerCapture(event.pointerId)) {
    strip.value.releasePointerCapture(event.pointerId);
  }
  if (player.value) player.value.currentTime = startMs.value / 1000;
}

async function captureThumbnails(url: string) {
  try {
    const source = document.createElement("video");
    source.muted = true;
    source.preload = "auto";
    source.src = url;
    await new Promise<void>((resolve, reject) => {
      source.onloadedmetadata = () => resolve();
      source.onerror = () => reject(new Error("无法读取视频缩略图"));
    });
    const count = 8;
    const result: string[] = [];
    const canvas = document.createElement("canvas");
    canvas.width = 100;
    canvas.height = 178;
    const context = canvas.getContext("2d");
    if (!context) return;
    for (let index = 0; index < count; index += 1) {
      source.currentTime = Math.max(0, Math.min(source.duration - 0.05, source.duration * index / count));
      await new Promise<void>((resolve) => { source.onseeked = () => resolve(); });
      context.drawImage(source, 0, 0, canvas.width, canvas.height);
      result.push(canvas.toDataURL("image/jpeg", 0.7));
    }
    thumbnails.value = result;
  } catch {
    // 缩略图只是本地浏览辅助；读取失败不能影响视频播放、版本选择或收费操作。
    thumbnails.value = [];
  }
}

async function regenerateWhole() {
  if (!props.node.stepId) return;
  try {
    const { value } = await ElMessageBox.prompt(
      "说明整条重生成要修复的问题；旧版本和媒体会永久保留。",
      "整条重新生成",
      { inputValidator: (text) => text.trim().length >= 4 || "至少填写4个字符" },
    );
    await ElMessageBox.confirm("该操作会创建新的Seedance收费attempt，确认继续？", "付费确认", { type: "warning" });
    const job = await api.regenerateStep(props.node.stepId, {
      reason: value.trim(),
      allowPaidGeneration: true,
      acknowledgeDownstreamReplacement: true,
    });
    emit("job", job);
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof ApiError ? error.message : String(error));
    }
  }
}

async function editRange() {
  const sequence = activeSequence.value;
  if (!sequence) return;
  try {
    const { value } = await ElMessageBox.prompt(
      `选区 ${formatTime(startMs.value)}–${formatTime(endMs.value)}，只描述需要修改的一项关系或动作。`,
      "AI区间重生成",
      { inputValidator: (text) => text.trim().length >= 4 || "至少填写4个字符" },
    );
    await ElMessageBox.confirm(
      "区间编辑会创建新的Seedance收费任务与非破坏性版本，原视频不会被覆盖。",
      "付费确认",
      { type: "warning" },
    );
    const job = await api.rangeEdit(props.episode.id, sequence.id, {
      startMs: startMs.value,
      endMs: endMs.value,
      boundaryMode: boundaryMode.value,
      instruction: value.trim(),
      allowPaidGeneration: true,
    });
    emit("job", job);
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof ApiError ? error.message : String(error));
    }
  }
}

async function saveShotNote(start: number, end: number) {
  if (!activeAsset.value) return;
  try {
    const { value } = await ElMessageBox.prompt(
      `记录 ${formatTime(start)}–${formatTime(end)} 的人工观察；本操作不调用Ark。`,
      "添加镜头备注",
      { inputValidator: (text) => text.trim().length >= 2 || "至少填写2个字符" },
    );
    await api.saveShotNote(props.episode.id, {
      assetId: activeAsset.value.id,
      startMs: start,
      endMs: end,
      note: value.trim(),
    });
    ElMessage.success("镜头备注已保存");
    emit("changed");
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof ApiError ? error.message : String(error));
    }
  }
}

async function selectVersion() {
  const sequence = activeSequence.value;
  if (!sequence || sequence.status !== "approved") return;
  let keep = false;
  let revoke = false;
  if (props.outcomeConfirmed) {
    const action = await ElMessageBox.confirm(
      "本时段结果卡已经确认。选择新版本时可以保留既有事实；如新视频改变事实，请撤销结果卡后重新确认。已保存的剧情关联卡不会被系统自动改写。",
      "选择正式版本",
      { confirmButtonText: "保留结果卡", cancelButtonText: "撤销结果卡", distinguishCancelAndClose: true },
    ).then(() => "keep").catch((reason) => reason === "cancel" ? "revoke" : Promise.reject(reason));
    keep = action === "keep";
    revoke = action === "revoke";
  }
  await api.selectVideoSequence(sequence.id, {
    keepConfirmedOutcome: keep,
    revokeConfirmedOutcome: revoke,
  });
  ElMessage.success(`已选择版本 ${sequence.revision}`);
  emit("changed");
}
</script>

<template>
  <section class="timeline-panel">
    <div class="timeline-header">
      <div>
        <strong>{{ episode.title }} · 非破坏性时间轴</strong>
        <span class="muted">原音轨完整保留，候选版本批准前不替换正式视频</span>
      </div>
      <div class="timeline-actions">
        <el-button @click="regenerateWhole">整条重新生成</el-button>
        <el-button type="primary" :disabled="!activeSequence" @click="editRange">选区重新生成</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      title="只向AI重新生成选中区间；区间外沿用原视频素材。精确区间合成可能产生轻微编码差异。"
      style="margin: 12px 0"
    />

    <div class="version-controls">
      <el-select v-model="activeSequenceId" style="width: 220px">
        <el-option v-for="item in sortedSequences" :key="item.id" :value="item.id" :label="`版本 ${item.revision} · ${item.status}`" />
      </el-select>
      <el-button :disabled="activeSequence?.status !== 'approved' || activeSequence?.renderedAssetId === episode.selectedVideoAssetId" @click="selectVersion">设为正式版本</el-button>
      <el-select v-model="compareSequenceId" clearable placeholder="选择对比版本" style="width: 220px">
        <el-option v-for="item in sortedSequences.filter((item) => item.id !== activeSequenceId)" :key="item.id" :value="item.id" :label="`版本 ${item.revision} · ${item.status}`" />
      </el-select>
    </div>

    <div class="player-grid" :class="{ comparing: compareAsset }">
      <video v-if="activeAsset" ref="player" controls :src="`/api/v1/assets/${activeAsset.id}/content`" @timeupdate="onTimeUpdate" />
      <video v-if="compareAsset" controls :src="`/api/v1/assets/${compareAsset.id}/content`" />
    </div>

    <div
      ref="strip"
      class="thumb-strip"
      @pointerdown="beginRangeDrag"
      @pointermove="moveRangeDrag"
      @pointerup="finishRangeDrag"
      @pointercancel="finishRangeDrag"
    >
      <img v-for="(image, index) in thumbnails" :key="index" :src="image" alt="时间轴缩略图" />
      <div v-for="boundary in boundaries" :key="boundary" class="shot-marker" :style="{ left: `${boundary / durationMs * 100}%` }" :title="`镜头边界 ${formatTime(boundary)}`" />
      <div
        v-if="durationMs"
        class="range-selection"
        :style="{
          left: `${startMs / durationMs * 100}%`,
          width: `${(endMs - startMs) / durationMs * 100}%`,
        }"
      />
      <div
        v-if="durationMs"
        class="playhead"
        :style="{ left: `${Math.min(currentMs, durationMs) / durationMs * 100}%` }"
      />
    </div>
    <div class="range-controls">
      <label>起点 {{ formatTime(startMs) }}<input v-model.number="startMs" type="range" min="0" :max="Math.max(0, endMs - 500)" step="40" /></label>
      <label>终点 {{ formatTime(endMs) }}<input v-model.number="endMs" type="range" :min="startMs + 500" :max="durationMs" step="40" /></label>
      <el-input-number v-model="startMs" :min="0" :max="Math.max(0, endMs - 500)" :step="40" controls-position="right" />
      <el-input-number v-model="endMs" :min="startMs + 500" :max="durationMs" :step="40" controls-position="right" />
      <el-radio-group v-model="boundaryMode" size="small">
        <el-radio-button value="snap_to_shot">吸附镜头</el-radio-button>
        <el-radio-button value="exact">精确区间</el-radio-button>
      </el-radio-group>
      <el-checkbox v-model="loopSelection">循环选区</el-checkbox>
    </div>
    <div class="post-shot-cards">
      <div v-for="shot in shotCards" :key="shot.order" class="post-shot-card">
        <img v-if="shot.thumbnail" :src="shot.thumbnail" alt="镜头代表帧" />
        <div>
          <strong>镜头{{ shot.order }} · {{ formatTime(shot.startMs) }}–{{ formatTime(shot.endMs) }}</strong>
          <p>{{ shot.direction }}</p>
          <div v-if="shot.aiFindings.length" class="shot-findings">
            AI建议：{{ shot.aiFindings.join("；") }}
          </div>
          <div v-if="shot.humanNotes.length" class="shot-notes">
            人工备注：{{ shot.humanNotes.join("；") }}
          </div>
          <el-button size="small" @click="startMs = shot.startMs; endMs = shot.endMs">选择此镜头区间</el-button>
          <el-button size="small" @click="saveShotNote(shot.startMs, shot.endMs)">添加备注</el-button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.timeline-panel { margin-top: 14px; padding: 14px; border: 1px solid var(--el-border-color); border-radius: 12px; background: #12151a; }
.timeline-header, .version-controls, .timeline-actions, .range-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.timeline-header { justify-content: space-between; }
.timeline-header .muted { margin-left: 12px; }
.post-shot-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; margin-top: 14px; }
.post-shot-card { display: grid; grid-template-columns: 72px 1fr; gap: 10px; padding: 10px; border: 1px solid var(--el-border-color); border-radius: 8px; }
.post-shot-card img { width: 72px; height: 120px; object-fit: cover; border-radius: 6px; }
.post-shot-card p { margin: 6px 0; color: #aab2bf; font-size: 12px; }
.shot-findings { margin: 5px 0; color: #d29922; font-size: 12px; }
.shot-notes { margin: 5px 0; color: #58a6ff; font-size: 12px; }
.version-controls { margin: 12px 0; }
.player-grid { display: grid; grid-template-columns: minmax(260px, 440px); gap: 12px; }
.player-grid.comparing { grid-template-columns: repeat(2, minmax(240px, 1fr)); }
video { width: 100%; max-height: 420px; background: #000; }
.thumb-strip { position: relative; display: grid; grid-template-columns: repeat(8, 1fr); height: 96px; margin-top: 12px; overflow: hidden; border-radius: 8px; background: #080a0d; cursor: crosshair; touch-action: none; user-select: none; }
.thumb-strip img { width: 100%; height: 96px; object-fit: cover; pointer-events: none; }
.shot-marker { position: absolute; top: 0; bottom: 0; width: 2px; background: #f0b429; }
.range-selection { position: absolute; top: 0; bottom: 0; border: 2px solid #58a6ff; background: #58a6ff2e; pointer-events: none; box-sizing: border-box; }
.playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: #ff7b72; pointer-events: none; }
.range-controls { margin-top: 12px; }
.range-controls label { display: grid; min-width: 240px; gap: 3px; color: #a8b3c2; font-size: 12px; }
.range-controls input { width: 100%; }
</style>
