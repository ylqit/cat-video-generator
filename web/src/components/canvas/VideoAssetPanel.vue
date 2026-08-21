<script setup lang="ts">
import { computed, ref } from "vue";

import type { CanvasNodeDto, CanvasNodeActionDto } from "../../api/types";

const props = withDefaults(defineProps<{ node: CanvasNodeDto; embedded?: boolean }>(), { embedded: false });
const emit = defineEmits<{
  edit: [node: CanvasNodeDto];
  "segment-reshoot": [node: CanvasNodeDto];
  close: [];
}>();

const videoElement = ref<HTMLVideoElement | null>(null);
const fallbackActions: CanvasNodeActionDto[] = [
  { key: "edit", label: "编辑", enabled: true, execution: "client" },
  { key: "segment_reshoot", label: "片段重拍", enabled: true, execution: "provider" },
  { key: "crop", label: "裁剪", enabled: false, execution: "unavailable", disabledReason: "当前版本尚未配置非破坏裁剪执行器" },
  { key: "upscale", label: "高清", enabled: false, execution: "unavailable", disabledReason: "当前 Ark ProviderCapability 尚未配置视频高清执行器" },
  { key: "frame_interpolation", label: "插帧", enabled: false, execution: "unavailable", disabledReason: "当前版本尚未配置逐帧插值执行器" },
  { key: "extend", label: "智能续写", enabled: false, execution: "unavailable", disabledReason: "当前 Ark ProviderCapability 尚未配置视频续写执行器" },
  { key: "subtitles", label: "智能字幕", enabled: false, execution: "unavailable", disabledReason: "当前版本尚未配置字幕识别与回填执行器" },
  { key: "audio_separation", label: "音频分离", enabled: false, execution: "unavailable", disabledReason: "当前版本尚未配置音轨分离执行器" },
  { key: "image_edit", label: "画面编辑", enabled: false, execution: "unavailable", disabledReason: "请使用片段重拍完成画面修改；通用画面编辑执行器尚未配置" },
  { key: "download", label: "下载", enabled: true, execution: "client" },
  { key: "fullscreen", label: "全屏", enabled: true, execution: "client" },
];
const actions = computed(() => (
  Array.isArray(props.node.data.availableActions)
    ? props.node.data.availableActions as unknown as CanvasNodeActionDto[]
    : fallbackActions
));
const videoUrl = computed(() => String(props.node.data.contentUrl ?? ""));

function run(action: CanvasNodeActionDto) {
  if (!action.enabled) return;
  if (action.key === "edit") emit("edit", props.node);
  else if (action.key === "segment_reshoot") emit("segment-reshoot", props.node);
  else if (action.key === "download") {
    const link = document.createElement("a");
    link.href = videoUrl.value;
    link.download = String(props.node.data.title ?? "video.mp4");
    link.click();
  } else if (action.key === "fullscreen") {
    void videoElement.value?.requestFullscreen?.();
  }
}
</script>

<template>
  <section class="video-asset-panel" :class="{ embedded }" aria-label="视频资产操作">
    <header>
      <div><small>VIDEO ASSET · REVISION {{ node.revision ?? 1 }}</small><h2>{{ node.data.title ?? '视频资产' }}</h2></div>
      <button v-if="!embedded" type="button" aria-label="关闭视频资产面板" @click="emit('close')">×</button>
    </header>
    <div class="asset-layout">
      <video ref="videoElement" :src="videoUrl" :poster="String(node.data.posterUrl ?? '')" controls playsinline />
      <section class="asset-summary">
        <span>当前资产</span>
        <b>{{ Number(node.data.durationMs ?? 0) ? `${(Number(node.data.durationMs) / 1000).toFixed(1)} 秒` : '时长待读取' }}</b>
        <p>片段重拍会创建新的配方 Revision 与新视频资产，原始视频不会被覆盖。</p>
        <div class="quick-actions">
          <button type="button" @click="emit('segment-reshoot', node)">片段重拍</button>
          <button type="button" @click="emit('edit', node)">展开高级编辑</button>
        </div>
      </section>
    </div>
    <nav aria-label="视频资产工具">
      <button
        v-for="action in actions"
        :key="action.key"
        type="button"
        :data-action="action.key"
        :disabled="!action.enabled"
        :title="action.enabled ? action.label : action.disabledReason"
        :aria-describedby="!action.enabled ? `reason-${action.key}` : undefined"
        @click="run(action)"
      >{{ action.label }}</button>
    </nav>
    <p v-for="action in actions.filter((item) => !item.enabled)" :id="`reason-${action.key}`" :key="`reason-${action.key}`" class="sr-only">
      {{ action.disabledReason }}
    </p>
  </section>
</template>

<style scoped>
.video-asset-panel { width: min(760px, calc(100vw - 32px)); padding: 14px; color: #edf1f6; background: #202329; border: 1px solid #3b424d; border-radius: 16px; box-shadow: 0 22px 70px rgb(0 0 0 / 48%); }
.video-asset-panel.embedded { box-sizing: border-box; width: 100%; min-height: 100%; padding: 14px 22px 18px; background: #171a20; border: 0; border-radius: 0; box-shadow: none; }
header { display: flex; align-items: center; justify-content: space-between; }h2 { margin: 3px 0 10px; font-size: 16px; }small { color: #7f8b9e; font-size: 10px; }header button { border: 0; color: #aab4c3; background: transparent; font-size: 22px; cursor: pointer; }
video { width: 100%; max-height: 400px; display: block; object-fit: contain; background: #0b0d10; border-radius: 11px; }.asset-layout { display: grid; grid-template-columns: minmax(260px, 420px) minmax(260px, 1fr); gap: 16px; }.embedded video { height: 190px; }.asset-summary { display: grid; align-content: start; gap: 8px; padding: 14px; background: #1d2128; border: 1px solid #303742; border-radius: 12px; }.asset-summary span { color: #738198; font-size: 10px; font-weight: 800; letter-spacing: .12em; }.asset-summary b { font-size: 18px; }.asset-summary p { margin: 0; color: #9ca8b8; line-height: 1.55; }.quick-actions { display: flex; gap: 8px; margin-top: auto; }.quick-actions button { min-height: 40px; padding: 8px 12px; color: #e8f3ff; background: #244e7c; border: 1px solid #3d6f9f; border-radius: 9px; cursor: pointer; }.quick-actions button + button { color: #cbd5e2; background: #292f38; border-color: #414b59; }
nav { margin-top: 12px; display: flex; gap: 7px; flex-wrap: wrap; }nav button { min-height: 34px; padding: 6px 11px; color: #dce3ec; background: #2c3139; border: 1px solid #3b424d; border-radius: 8px; cursor: pointer; }nav button:disabled { color: #727c8b; cursor: not-allowed; opacity: .6; }
.embedded nav { display: none; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 900px) { .asset-layout { grid-template-columns: 1fr; }.embedded video { height: 150px; } }
</style>
