<script setup lang="ts">
import { ElMessageBox } from "element-plus";
import { computed, ref, watch } from "vue";

import { api } from "../api/client";

const props = defineProps<{
  shotId: string;
  assetId: string;
  src: string;
  durationMs: number;
  direction: string;
  frames?: Array<{ src: string; label: string; timestampMs: number }>;
  markersMs?: number[];
}>();
const emit = defineEmits<{ completed: [] }>();

const startMs = ref(0);
const endMs = ref(Math.min(props.durationMs, 4000));
const instruction = ref("");
const busy = ref(false);
const message = ref("");
const loopSelection = ref(false);
const player = ref<HTMLVideoElement | null>(null);
const durationSeconds = computed(() => Math.max(0.1, props.durationMs / 1000));
const subshots = computed(() => {
  const matches = [...props.direction.matchAll(
    /(?:^|\n)\s*(\d+)\s*[.、．]\s*([\s\S]*?)(?=(?:\n\s*\d+\s*[.、．])|$)/g,
  )];
  if (!matches.length) return [];
  const markers = [
    0,
    ...(props.markersMs ?? []).filter((item) => item > 0 && item < props.durationMs),
    props.durationMs,
  ].sort((left, right) => left - right);
  const uniqueMarkers = [...new Set(markers)];
  return matches.map((match, index) => {
    const proportionalStart = Math.round((props.durationMs * index) / matches.length);
    const proportionalEnd = Math.round((props.durationMs * (index + 1)) / matches.length);
    return {
      ordinal: Number(match[1]),
      description: match[2].trim(),
      startMs: uniqueMarkers[index] ?? proportionalStart,
      endMs: uniqueMarkers[index + 1] ?? proportionalEnd,
      estimated: uniqueMarkers.length < matches.length + 1,
    };
  });
});

watch(
  () => [props.assetId, props.durationMs] as const,
  () => {
    startMs.value = 0;
    endMs.value = Math.min(props.durationMs, 4000);
    instruction.value = "";
    message.value = "";
  },
);

function handleTimeUpdate() {
  if (!loopSelection.value || !player.value) return;
  if (player.value.currentTime * 1000 >= endMs.value) {
    player.value.currentTime = startMs.value / 1000;
    void player.value.play();
  }
}

function seek(timestampMs: number) {
  if (!player.value) return;
  player.value.currentTime = timestampMs / 1000;
}

function selectSubshot(item: (typeof subshots.value)[number]) {
  startMs.value = Math.max(0, item.startMs);
  endMs.value = Math.min(props.durationMs, Math.max(item.startMs + 500, item.endMs));
  instruction.value = `只重拍子镜头${item.ordinal}：${item.description}`;
  seek(startMs.value);
}

async function submit() {
  if (endMs.value <= startMs.value || !instruction.value.trim()) {
    message.value = "请选择有效区间并填写唯一修改目标。";
    return;
  }
  const wholeShot = startMs.value <= 100 && endMs.value >= props.durationMs - 100;
  await ElMessageBox.confirm(
    wholeShot
      ? "当前选区覆盖完整镜头，将创建整镜头新版本并产生一次 Seedance 费用，是否继续？"
      : "区间重拍会产生一次 Seedance 费用，是否继续？",
    "付费确认",
  );
  busy.value = true;
  message.value = "";
  try {
    const accepted = wholeShot
      ? await api.generateVideo(props.shotId, true, `完整镜头重做：${instruction.value}`)
      : await api.rangeEdit(props.shotId, {
          sourceAssetId: props.assetId,
          startMs: startMs.value,
          endMs: endMs.value,
          instruction: instruction.value,
          allowPaidGeneration: true,
        });
    for (;;) {
      const job = await api.job(accepted.jobId);
      if (job.status === "failed") throw new Error(String(job.error?.message ?? "区间重拍失败"));
      if (job.status === "succeeded") break;
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    }
    message.value = wholeShot
      ? "完整镜头新版本已生成并等待人工审核，原版本未被覆盖。"
      : "区间新版本已生成并等待人工审核，原版本未被覆盖。";
    emit("completed");
  } catch (error) {
    message.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="timeline" v-loading="busy">
    <div class="timeline-head">
      <div>
        <h3>视频片段时间轴与区间重拍</h3>
        <p>只重新生成选中区间；区间外沿用原视频素材。精确区间合成可能产生轻微编码差异。</p>
      </div>
      <span>{{ durationSeconds.toFixed(2) }}s</span>
    </div>
    <div class="timeline-body">
      <video ref="player" controls :src="src" @timeupdate="handleTimeUpdate" />
      <div class="range-editor">
        <div v-if="frames?.length" class="filmstrip">
          <figure v-for="frame in frames" :key="frame.src" @click="seek(frame.timestampMs)">
            <img :src="frame.src" />
            <figcaption>{{ frame.label }}</figcaption>
          </figure>
        </div>
        <div v-if="markersMs?.length" class="boundary-markers">
          <span>AI建议切点</span>
          <el-button
            v-for="marker in markersMs"
            :key="marker"
            size="small"
            text
            @click="seek(marker)"
          >{{ (marker / 1000).toFixed(2) }}s</el-button>
        </div>
        <div v-if="subshots.length" class="subshot-list">
          <b>按分镜选择重拍区间</b>
          <button v-for="item in subshots" :key="item.ordinal" type="button" @click="selectSubshot(item)">
            <span>子镜头 {{ item.ordinal }} · {{ (item.startMs / 1000).toFixed(2) }}–{{ (item.endMs / 1000).toFixed(2) }}s</span>
            <small>{{ item.description }}{{ item.estimated ? '（按总时长估算，可手工调整）' : '' }}</small>
          </button>
        </div>
        <div class="range-values">
          <el-input-number v-model="startMs" :min="0" :max="Math.max(0, durationMs - 500)" :step="100" />
          <span>ms 至</span>
          <el-input-number v-model="endMs" :min="500" :max="durationMs" :step="100" />
        </div>
        <el-slider v-model="startMs" :min="0" :max="Math.max(0, durationMs - 500)" :step="100" />
        <el-slider v-model="endMs" :min="500" :max="durationMs" :step="100" />
        <el-switch v-model="loopSelection" active-text="循环播放选区" />
        <el-input v-model="instruction" type="textarea" :rows="3" placeholder="只描述需要修复的一项问题，例如：让钓线始终连接人物手中的线轴，不经过猫咪身体。" />
        <el-button type="primary" :disabled="!instruction.trim()" @click="submit">
          {{ startMs <= 100 && endMs >= durationMs - 100 ? "重做完整镜头" : "生成区间新版本" }}
        </el-button>
        <el-alert v-if="message" :title="message" type="info" :closable="false" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.timeline { margin: 16px 0 0; padding: 16px; border: 1px solid #2b313d; border-radius: 12px; background: #151922; color: #e7e9ef; }
.timeline-head { display: flex; justify-content: space-between; align-items: start; gap: 20px; }.timeline-head h3 { margin: 0; }.timeline-head p { color: #9aa2b1; margin: 6px 0 14px; }.timeline-body { display: grid; grid-template-columns: minmax(360px, 45%) 1fr; gap: 18px; }.timeline video { width: 100%; max-height: 360px; background: #080a0e; }.range-editor { display: grid; gap: 10px; align-content: start; }.range-values { display: flex; align-items: center; gap: 10px; }
.filmstrip { display: flex; gap: 5px; overflow-x: auto; padding-bottom: 4px; }.filmstrip figure { margin: 0; min-width: 72px; cursor: pointer; }.filmstrip img { display: block; width: 72px; height: 112px; object-fit: cover; border-radius: 5px; }.filmstrip figcaption { color: #7f899a; font-size: 10px; text-align: center; margin-top: 3px; }
.boundary-markers { display: flex; align-items: center; flex-wrap: wrap; gap: 3px; color: #8b95a5; font-size: 12px; }
.subshot-list { display: grid; gap: 6px; }.subshot-list button { display: grid; gap: 3px; text-align: left; padding: 8px; color: #cad4e2; background: #101722; border: 1px solid #2f3a4a; border-radius: 7px; cursor: pointer; }.subshot-list button:hover { border-color: #409eff; }.subshot-list small { color: #8791a2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 900px) { .timeline-body { grid-template-columns: 1fr; } }
</style>
