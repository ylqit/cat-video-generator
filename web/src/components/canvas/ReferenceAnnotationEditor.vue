<script setup lang="ts">
import { computed, ref } from "vue";

import type { GenerationReferenceAnnotationDto } from "../../api/types";

const props = defineProps<{
  assetId: string;
  imageUrl: string;
}>();
const emit = defineEmits<{
  save: [annotation: GenerationReferenceAnnotationDto];
  close: [];
}>();

const tool = ref<GenerationReferenceAnnotationDto["tool"]>("rectangle");
const points = ref<Array<{ x: number; y: number }>>([]);
const label = ref("");
const drawing = ref(false);

function normalizedPoint(event: PointerEvent, element: HTMLElement) {
  const rect = element.getBoundingClientRect();
  const clamp = (value: number) => Math.max(0, Math.min(1, Number(value.toFixed(4))));
  return {
    x: clamp((event.clientX - rect.left) / rect.width),
    y: clamp((event.clientY - rect.top) / rect.height),
  };
}

function startDrawing(event: PointerEvent) {
  const element = event.currentTarget as HTMLElement;
  points.value = [normalizedPoint(event, element)];
  drawing.value = true;
  element.setPointerCapture?.(event.pointerId);
}

function continueDrawing(event: PointerEvent) {
  if (!drawing.value) return;
  const start = points.value[0];
  if (!start) return;
  points.value = [start, normalizedPoint(event, event.currentTarget as HTMLElement)];
}

function finishDrawing(event: PointerEvent) {
  continueDrawing(event);
  drawing.value = false;
}

const rectangleStyle = computed(() => {
  const [start, end] = points.value;
  if (!start || !end) return null;
  return {
    left: `${Math.min(start.x, end.x) * 100}%`,
    top: `${Math.min(start.y, end.y) * 100}%`,
    width: `${Math.abs(end.x - start.x) * 100}%`,
    height: `${Math.abs(end.y - start.y) * 100}%`,
  };
});

function save() {
  if (!points.value.length) return;
  emit("save", {
    assetId: props.assetId,
    tool: tool.value,
    points: points.value,
    label: label.value.trim(),
  });
}
</script>

<template>
  <section class="annotation-editor" aria-label="参考图标注编辑器">
    <div class="annotation-tools" role="toolbar" aria-label="标注工具">
      <button
        v-for="item in [{ value: 'rectangle', label: '矩形' }, { value: 'arrow', label: '箭头' }, { value: 'text', label: '文字' }, { value: 'marker', label: '时间点/重点' }]"
        :key="item.value"
        type="button"
        :class="{ active: tool === item.value }"
        @click="tool = item.value as GenerationReferenceAnnotationDto['tool']"
      >{{ item.label }}</button>
    </div>
    <div
      data-role="annotation-stage"
      class="annotation-stage"
      @pointerdown.prevent="startDrawing"
      @pointermove.prevent="continueDrawing"
      @pointerup.prevent="finishDrawing"
    >
      <img :src="imageUrl" alt="待标注参考图" draggable="false" />
      <div v-if="rectangleStyle" class="annotation-box" :style="rectangleStyle"><span>{{ label }}</span></div>
    </div>
    <label>标注说明<input v-model="label" aria-label="标注说明" placeholder="例如：保持标签文字与罐身比例" /></label>
    <p>坐标按原图尺寸归一化保存，调用时会与精确 Prompt 和输入快照一起审计。</p>
    <footer>
      <button type="button" @click="emit('close')">取消</button>
      <button data-action="save-annotation" type="button" :disabled="!points.length" @click="save">保存标注</button>
    </footer>
  </section>
</template>

<style scoped>
.annotation-editor { display: grid; gap: 12px; color: #dfe6f0; }.annotation-tools { display: flex; gap: 7px; }.annotation-tools button { padding: 7px 10px; color: #aeb8c8; background: #292e36; border: 1px solid #3c4450; border-radius: 7px; }.annotation-tools button.active { color: #12202e; background: #d7e8f7; }.annotation-stage { position: relative; width: 100%; aspect-ratio: 16 / 9; overflow: hidden; touch-action: none; background: #101217; border: 1px solid #3d4652; border-radius: 10px; cursor: crosshair; }.annotation-stage img { width: 100%; height: 100%; object-fit: contain; pointer-events: none; user-select: none; }.annotation-box { position: absolute; box-sizing: border-box; border: 2px solid #68b4ff; background: rgb(65 155 239 / 15%); pointer-events: none; }.annotation-box span { position: absolute; left: 0; bottom: 100%; max-width: 260px; padding: 3px 5px; color: #e8f4ff; background: #245c8d; font-size: 11px; white-space: nowrap; }.annotation-editor label { display: grid; gap: 5px; }.annotation-editor input { padding: 9px; color: #e8edf4; background: #1b1e23; border: 1px solid #3c4450; border-radius: 7px; }.annotation-editor p { margin: 0; color: #8792a3; font-size: 11px; }.annotation-editor footer { display: flex; justify-content: flex-end; gap: 8px; }.annotation-editor footer button { padding: 8px 11px; }
</style>
