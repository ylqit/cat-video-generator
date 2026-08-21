<script setup lang="ts">
import { computed, ref } from "vue";

import type { CanvasNodeActionDto, CanvasNodeDto } from "../../api/types";

type ToolbarAction = CanvasNodeActionDto;

const props = defineProps<{ node: CanvasNodeDto }>();
const emit = defineEmits<{ action: [action: ToolbarAction]; close: [] }>();

const disabledReason = ref("");
const videoFallbackActions: ToolbarAction[] = [
  { key: "edit", label: "编辑", enabled: true, execution: "client" },
  { key: "segment_reshoot", label: "片段重拍", enabled: true, execution: "provider" },
  { key: "crop", label: "裁剪", enabled: false, execution: "unavailable", disabledReason: "当前版本尚未配置非破坏裁剪执行器" },
  { key: "upscale", label: "高清", enabled: false, execution: "unavailable", disabledReason: "当前视频 ProviderCapability 尚未配置高清执行器" },
  { key: "frame_interpolation", label: "逐帧拉片", enabled: false, execution: "unavailable", disabledReason: "当前版本尚未配置逐帧插值执行器" },
  { key: "extend", label: "智能续写", enabled: false, execution: "unavailable", disabledReason: "当前视频 ProviderCapability 尚未配置续写执行器" },
  { key: "subtitles", label: "智能去字幕", enabled: false, execution: "unavailable", disabledReason: "当前版本尚未配置字幕检测与画面修复执行器" },
  { key: "audio_separation", label: "音频分离", enabled: false, execution: "unavailable", disabledReason: "当前版本尚未配置音轨分离执行器" },
  { key: "image_edit", label: "画面编辑", enabled: false, execution: "unavailable", disabledReason: "请使用片段重拍完成画面修改；通用画面编辑执行器尚未配置" },
  { key: "download", label: "下载", enabled: true, execution: "client" },
  { key: "fullscreen", label: "全屏", enabled: true, execution: "client" },
];

const actions = computed<ToolbarAction[]>(() => {
  if (Array.isArray(props.node.availableActions) && props.node.availableActions.length) {
    return props.node.availableActions;
  }
  if (props.node.type === "VideoAssetNode") {
    return Array.isArray(props.node.data.availableActions)
      ? props.node.data.availableActions as unknown as ToolbarAction[]
      : videoFallbackActions;
  }
  return [{ key: "unavailable", label: "暂无可执行操作", enabled: false, execution: "unavailable", disabledReason: "该节点未返回动作契约，请刷新画布或检查后端投影" }];
});

function run(action: ToolbarAction) {
  if (!action.enabled) {
    disabledReason.value = action.disabledReason || "当前功能尚未接通执行能力";
    return;
  }
  disabledReason.value = "";
  emit("action", action);
}
</script>

<template>
  <section class="context-toolbar" aria-label="选中节点操作">
    <div class="toolbar-scroll" role="toolbar" :aria-label="`${node.data.title ?? '节点'}操作`">
      <button
        v-for="action in actions"
        :key="action.key"
        type="button"
        :data-action="action.key"
        :aria-disabled="!action.enabled"
        :class="{ unavailable: !action.enabled, primary: ['segment_reshoot', 'recipe_primary', 'storyboard_from_story'].includes(action.key) }"
        :title="action.enabled ? action.label : action.disabledReason"
        @click="run(action)"
        @focus="disabledReason = action.enabled ? '' : action.disabledReason || '当前功能尚未接通执行能力'"
      >{{ action.label }}</button>
    </div>
    <button class="toolbar-close" type="button" aria-label="取消选择" @click="emit('close')">×</button>
    <p v-if="disabledReason" class="disabled-reason" role="status">{{ disabledReason }}</p>
  </section>
</template>

<style scoped>
.context-toolbar { position: fixed; z-index: 1200; max-width: calc(100vw - 32px); display: flex; align-items: center; gap: 5px; padding: 6px; color: #dce6f2; background: rgb(31 35 42 / 97%); border: 1px solid #414955; border-radius: 12px; box-shadow: 0 16px 44px rgb(0 0 0 / 48%); transform-origin: center bottom; animation: toolbar-in 140ms ease-out; }
.toolbar-scroll { min-width: 0; overflow-x: auto; display: flex; gap: 3px; scrollbar-width: thin; }.toolbar-scroll button,.toolbar-close { min-height: 44px; flex: 0 0 auto; padding: 9px 11px; color: #cbd5e2; background: transparent; border: 1px solid transparent; border-radius: 9px; font: inherit; font-size: 11px; cursor: pointer; white-space: nowrap; }.toolbar-scroll button:hover,.toolbar-scroll button:focus-visible,.toolbar-close:hover,.toolbar-close:focus-visible { color: #fff; background: #303741; border-color: #4b5869; outline: none; }.toolbar-scroll button.primary { color: #e8f3ff; background: #244e7c; border-color: #396d9f; }.toolbar-scroll button.unavailable { color: #687486; text-decoration: line-through; text-decoration-thickness: 1px; }.toolbar-scroll button.unavailable:hover,.toolbar-scroll button.unavailable:focus-visible { color: #a7b2c1; background: #292e36; }.toolbar-close { width: 44px; padding: 0; font-size: 19px; }
.disabled-reason { position: absolute; left: 8px; top: calc(100% + 7px); max-width: 420px; margin: 0; padding: 7px 9px; color: #e9bd7a; background: #292419; border: 1px solid #604e30; border-radius: 8px; box-shadow: 0 10px 28px rgb(0 0 0 / 38%); font-size: 10px; line-height: 1.45; }
@keyframes toolbar-in { from { opacity: 0; transform: translateY(4px) scale(.98); } }
@media (prefers-reduced-motion: reduce) { .context-toolbar { animation: none; } }
</style>
