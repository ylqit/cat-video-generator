<script setup lang="ts">
import {
  Brush,
  Close,
  Crop,
  Delete,
  EditPen,
  Location,
  Position,
  RefreshLeft,
  RefreshRight,
  VideoPlay,
} from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, nextTick, onMounted, ref, watch } from "vue";

import { canvasApi } from "../../api/client";
import type {
  CapabilityCompilationPlan,
  VideoEditAnnotationInput,
  VideoEditRecipeDto,
  VideoEditTool,
} from "../../api/types";

interface EditorReference {
  id: string;
  title: string;
  thumbnailUrl: string;
  semanticRole?: string;
}

const props = defineProps<{
  projectId: string;
  sourceAssetId: string;
  videoUrl: string;
  posterUrl?: string;
  durationMs: number;
  initialStartMs?: number;
  initialEndMs?: number;
  references?: EditorReference[];
}>();
const emit = defineEmits<{ close: []; submitted: [recipeId: string] }>();

const startMs = ref(props.initialStartMs ?? 0);
const endMs = ref(props.initialEndMs ?? Math.min(props.durationMs, 8_000));
const instruction = ref("");
const activeTool = ref<VideoEditTool>("rectangle");
const selectedReferenceIds = ref<string[]>(props.references?.map((item) => item.id) ?? []);
const annotations = ref<VideoEditAnnotationInput[]>([]);
const redoStack = ref<VideoEditAnnotationInput[]>([]);
const recipe = ref<VideoEditRecipeDto | null>(null);
const plan = ref<CapabilityCompilationPlan | null>(null);
const compiling = ref(false);
const submitting = ref(false);
const canvasElement = ref<HTMLCanvasElement | null>(null);
const videoElement = ref<HTMLVideoElement | null>(null);
const drawingPoints = ref<Array<{ x: number; y: number }>>([]);

const duration = computed(() => endMs.value - startMs.value);
const intervalValid = computed(() => duration.value >= 500 && duration.value <= 13_000);
const selectedReferences = computed(() => (props.references ?? []).filter(
  (item) => selectedReferenceIds.value.includes(item.id),
));
const toolItems: Array<{ key: VideoEditTool | "eraser"; label: string; icon: unknown }> = [
  { key: "rectangle", label: "矩形", icon: Crop },
  { key: "brush", label: "画笔", icon: Brush },
  { key: "arrow", label: "箭头", icon: Position },
  { key: "text", label: "文字", icon: EditPen },
  { key: "marker", label: "时间点", icon: Location },
  { key: "eraser", label: "橡皮擦", icon: Delete },
];

watch([startMs, endMs], () => {
  if (endMs.value <= startMs.value) endMs.value = Math.min(props.durationMs, startMs.value + 500);
  if (duration.value > 13_000) endMs.value = startMs.value + 13_000;
  plan.value = null;
});
watch([instruction, selectedReferenceIds, annotations], () => { plan.value = null; }, { deep: true });

function selectTool(tool: VideoEditTool | "eraser") {
  if (tool === "eraser") {
    const removed = annotations.value.pop();
    if (removed) redoStack.value.push(removed);
    drawAnnotations();
    return;
  }
  activeTool.value = tool;
}

function normalizedPoint(event: PointerEvent) {
  const canvas = canvasElement.value;
  if (!canvas) return { x: 0, y: 0 };
  const bounds = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / Math.max(bounds.width, 1))),
    y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / Math.max(bounds.height, 1))),
  };
}

function beginAnnotation(event: PointerEvent) {
  drawingPoints.value = [normalizedPoint(event)];
  canvasElement.value?.setPointerCapture?.(event.pointerId);
}

function extendAnnotation(event: PointerEvent) {
  if (!drawingPoints.value.length || activeTool.value !== "brush") return;
  drawingPoints.value.push(normalizedPoint(event));
}

function finishAnnotation(event: PointerEvent) {
  if (!drawingPoints.value.length) return;
  const endPoint = normalizedPoint(event);
  const points = activeTool.value === "brush"
    ? [...drawingPoints.value, endPoint]
    : activeTool.value === "marker" || activeTool.value === "text"
      ? [drawingPoints.value[0]]
      : [drawingPoints.value[0], endPoint];
  annotations.value.push({
    frameTimestampMs: Math.round((startMs.value + endMs.value) / 2),
    tool: activeTool.value,
    points,
    label: activeTool.value === "text" ? "重点修改区域" : "",
  });
  drawingPoints.value = [];
  redoStack.value = [];
  drawAnnotations();
}

function undo() {
  const item = annotations.value.pop();
  if (item) redoStack.value.push(item);
  drawAnnotations();
}

function redo() {
  const item = redoStack.value.pop();
  if (item) annotations.value.push(item);
  drawAnnotations();
}

function drawAnnotations() {
  const canvas = canvasElement.value;
  if (!canvas) return;
  if (typeof CanvasRenderingContext2D === "undefined") return;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "#ff5f57";
  context.fillStyle = "#ff5f57";
  context.lineWidth = 5;
  context.font = "20px sans-serif";
  for (const annotation of annotations.value) {
    const points = annotation.points.map((point) => ({
      x: point.x * canvas.width,
      y: point.y * canvas.height,
    }));
    if (annotation.tool === "rectangle" && points[1]) {
      context.strokeRect(points[0].x, points[0].y, points[1].x - points[0].x, points[1].y - points[0].y);
    } else if (annotation.tool === "brush" || annotation.tool === "arrow") {
      context.beginPath();
      context.moveTo(points[0].x, points[0].y);
      points.slice(1).forEach((point) => context.lineTo(point.x, point.y));
      context.stroke();
    } else if (annotation.tool === "text") {
      context.fillText(annotation.label, points[0].x, points[0].y);
    } else {
      context.beginPath();
      context.arc(points[0].x, points[0].y, 8, 0, Math.PI * 2);
      context.fill();
    }
  }
}

async function compileRecipe() {
  if (!intervalValid.value) {
    ElMessage.error("单次重编区间必须在 0.5–13 秒之间");
    return;
  }
  if (!instruction.value.trim()) {
    ElMessage.error("请描述需要修改的动作或画面");
    return;
  }
  compiling.value = true;
  try {
    if (!recipe.value) {
      recipe.value = await canvasApi.createVideoEditRecipe({
        projectId: props.projectId,
        sourceAssetId: props.sourceAssetId,
        startMs: startMs.value,
        endMs: endMs.value,
        instruction: instruction.value.trim(),
        referenceAssetIds: selectedReferenceIds.value,
        annotations: annotations.value,
      });
    } else {
      recipe.value = await canvasApi.updateVideoEditRecipe(
        recipe.value.id,
        recipe.value.revision,
        {
          startMs: startMs.value,
          endMs: endMs.value,
          instruction: instruction.value.trim(),
          referenceAssetIds: selectedReferenceIds.value,
        },
      );
      recipe.value = await canvasApi.replaceVideoEditAnnotations(
        recipe.value.id,
        recipe.value.revision,
        annotations.value,
      );
    }
    plan.value = await canvasApi.compileVideoEditRecipe(recipe.value.id);
  } finally {
    compiling.value = false;
  }
}

async function submitRecipe() {
  if (!recipe.value || !plan.value) return;
  submitting.value = true;
  try {
    await canvasApi.submitVideoEditRecipe(
      recipe.value.id,
      crypto.randomUUID(),
      plan.value.estimatedCostMicros,
    );
    ElMessage.success("视频局部重编已进入持久任务队列，原资产不会被覆盖");
    emit("submitted", recipe.value.id);
  } finally {
    submitting.value = false;
  }
}

function seekTo(value: number) {
  if (videoElement.value) videoElement.value.currentTime = value / 1_000;
}

onMounted(async () => {
  await nextTick();
  drawAnnotations();
});
</script>

<template>
  <section class="video-editor" role="dialog" aria-modal="true" aria-label="视频局部重编器">
    <header class="editor-header">
      <div><span>VIDEO EDIT RECIPE · REVISION {{ recipe?.revision ?? 1 }}</span><h1>视频局部重编</h1></div>
      <button class="icon-button" type="button" aria-label="关闭视频编辑器" @click="emit('close')"><Close /></button>
    </header>

    <main class="editor-layout">
      <section class="preview-column">
        <div class="video-stage">
          <video ref="videoElement" :src="videoUrl" :poster="posterUrl" controls loop playsinline />
          <canvas
            ref="canvasElement"
            width="1280"
            height="720"
            aria-label="视频标注层"
            @pointerdown="beginAnnotation"
            @pointermove="extendAnnotation"
            @pointerup="finishAnnotation"
          />
        </div>

        <nav class="annotation-tools" aria-label="视频标注工具">
          <button
            v-for="tool in toolItems"
            :key="tool.key"
            type="button"
            :data-tool="tool.key"
            :class="{ active: activeTool === tool.key }"
            @click="selectTool(tool.key)"
          ><component :is="tool.icon" /><span>{{ tool.label }}</span></button>
          <i />
          <button type="button" aria-label="撤销标注" :disabled="!annotations.length" @click="undo"><RefreshLeft /></button>
          <button type="button" aria-label="重做标注" :disabled="!redoStack.length" @click="redo"><RefreshRight /></button>
        </nav>

        <section class="range-panel">
          <div class="filmstrip" aria-label="视频帧带">
            <video v-for="index in 8" :key="index" :src="videoUrl" muted preload="metadata" />
            <span
              class="range-selection"
              :style="{ left: `${startMs / durationMs * 100}%`, width: `${duration / durationMs * 100}%` }"
            ><b>{{ (duration / 1000).toFixed(1) }}s</b></span>
          </div>
          <div class="range-inputs">
            <label>起点
              <input v-model.number="startMs" aria-label="重编起点毫秒" type="number" min="0" :max="durationMs - 500" step="100" @change="seekTo(startMs)" />
            </label>
            <span>—</span>
            <label>终点
              <input v-model.number="endMs" aria-label="重编终点毫秒" type="number" min="500" :max="durationMs" step="100" @change="seekTo(endMs)" />
            </label>
            <small :class="{ error: !intervalValid }">单区间 0.5–13 秒 · 区间外画面与原音轨保持不变</small>
          </div>
        </section>
      </section>

      <aside class="recipe-panel">
        <section>
          <span class="section-label">编辑目标</span>
          <textarea v-model="instruction" rows="5" placeholder="描述需要调整的视频动作、人物、产品或镜头，例如：女主转身面向发簪，同时轻触发簪并微笑。" />
        </section>

        <section>
          <div class="section-title"><span class="section-label">主体 / 产品 / 风格参考</span><b>{{ selectedReferences.length }} / {{ references?.length ?? 0 }}</b></div>
          <label v-for="reference in references" :key="reference.id" class="reference-row">
            <input v-model="selectedReferenceIds" type="checkbox" :value="reference.id" />
            <img :src="reference.thumbnailUrl" :alt="reference.title" />
            <span><strong>{{ reference.title }}</strong><small>{{ reference.semanticRole ?? '语义参考' }}</small></span>
            <em>会进入供应商请求</em>
          </label>
          <p v-if="!references?.length" class="empty-copy">未添加额外参考；仍会使用源视频与区间边界帧。</p>
        </section>

        <section v-if="plan" class="compile-plan">
          <div class="section-title"><span class="section-label">能力编译计划</span><b>{{ plan.mode === 'two_stage' ? '两阶段' : '直接提交' }}</b></div>
          <div class="cost-grid">
            <span><strong>{{ plan.imageCallCount }}</strong> 次图片调用</span>
            <span><strong>{{ plan.videoCallCount }}</strong> 次视频调用</span>
            <span><strong>{{ plan.estimatedCostMicros ? `¥${(plan.estimatedCostMicros / 1_000_000).toFixed(3)}` : '待配置' }}</strong> 预计费用</span>
          </div>
          <p v-for="warning in plan.warnings" :key="warning">{{ warning }}</p>
        </section>

        <footer>
          <button type="button" data-action="compile" :disabled="compiling" @click="compileRecipe">
            <VideoPlay />{{ compiling ? '编译中…' : '生成能力计划' }}
          </button>
          <button type="button" class="primary" data-action="submit" :disabled="!plan || submitting" @click="submitRecipe">
            {{ submitting ? '提交中…' : '确认费用并生成新版本' }}
          </button>
          <small>每次提交创建新 Recipe Revision 与新资产，绝不覆盖原视频。</small>
        </footer>
      </aside>
    </main>
  </section>
</template>

<style scoped>
.video-editor { position: fixed; inset: 0; z-index: 1000; display: grid; grid-template-rows: 72px 1fr; color: #edf1f6; background: #101216; }
.editor-header { padding: 0 24px; display: flex; align-items: center; justify-content: space-between; background: #15181d; border-bottom: 1px solid #2b3038; }
.editor-header span, .section-label { color: #758197; font-size: 10px; font-weight: 800; letter-spacing: .13em; }
.editor-header h1 { margin: 3px 0 0; font-size: 17px; }
button { color: inherit; font: inherit; }
.icon-button { width: 38px; height: 38px; padding: 10px; background: #242932; border: 1px solid #39414d; border-radius: 10px; cursor: pointer; }
.editor-layout { min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 390px; }
.preview-column { min-width: 0; min-height: 0; padding: 22px; display: grid; grid-template-rows: minmax(280px, 1fr) auto auto; gap: 14px; }
.video-stage { position: relative; min-height: 0; overflow: hidden; display: grid; place-items: center; background: #090a0c; border: 1px solid #2e333b; border-radius: 14px; }
.video-stage video { width: 100%; height: 100%; object-fit: contain; }
.video-stage canvas { position: absolute; inset: 0; width: 100%; height: 100%; cursor: crosshair; pointer-events: auto; }
.annotation-tools { padding: 7px; display: flex; align-items: center; gap: 4px; background: #20242b; border: 1px solid #353b45; border-radius: 12px; }
.annotation-tools button { min-width: 42px; height: 40px; padding: 7px 10px; display: flex; align-items: center; gap: 6px; color: #aeb8c7; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }
.annotation-tools button :deep(svg) { width: 17px; }.annotation-tools button.active, .annotation-tools button:hover { color: #fff; background: #38404b; }.annotation-tools i { width: 1px; height: 24px; margin: 0 5px; background: #3b424d; }
.range-panel { padding: 12px; background: #181c22; border: 1px solid #303640; border-radius: 12px; }
.filmstrip { position: relative; height: 70px; overflow: hidden; display: grid; grid-template-columns: repeat(8, 1fr); border-radius: 8px; }
.filmstrip video { width: 100%; height: 70px; object-fit: cover; filter: brightness(.62); }
.range-selection { position: absolute; top: 0; bottom: 0; min-width: 8px; border: 3px solid #75a9f8; border-radius: 8px; box-shadow: 0 0 0 999px rgb(4 6 8 / 46%); }
.range-selection b { position: absolute; right: 5px; top: 5px; padding: 3px 5px; background: #101722; border-radius: 5px; font-size: 10px; }
.range-inputs { margin-top: 10px; display: flex; align-items: center; gap: 8px; }.range-inputs label { display: flex; align-items: center; gap: 6px; color: #808b9c; font-size: 11px; }.range-inputs input { width: 92px; padding: 7px; color: #d8e0eb; background: #111419; border: 1px solid #343b46; border-radius: 7px; }.range-inputs small { margin-left: auto; color: #7f8999; }.range-inputs small.error { color: #ef8e8e; }
.recipe-panel { min-height: 0; padding: 20px; overflow: auto; background: #171a20; border-left: 1px solid #2b3038; }
.recipe-panel > section { padding-bottom: 20px; margin-bottom: 20px; border-bottom: 1px solid #2c323b; }
textarea { width: 100%; margin-top: 10px; padding: 12px; resize: vertical; box-sizing: border-box; color: #e8edf4; background: #101318; border: 1px solid #343c48; border-radius: 10px; font: inherit; line-height: 1.6; }
.section-title { display: flex; align-items: center; justify-content: space-between; }.section-title b { color: #aab6c7; font-size: 11px; }
.reference-row { margin-top: 10px; padding: 8px; display: grid; grid-template-columns: auto 48px 1fr auto; align-items: center; gap: 9px; background: #20242b; border: 1px solid #323943; border-radius: 9px; cursor: pointer; }.reference-row img { width: 48px; height: 48px; object-fit: cover; border-radius: 7px; }.reference-row span { display: grid; }.reference-row small { color: #727e91; }.reference-row em { color: #74c79e; font-size: 9px; font-style: normal; }.empty-copy { color: #727e8f; font-size: 12px; }
.compile-plan { padding: 14px !important; background: #1d2429; border: 1px solid #395144 !important; border-radius: 11px; }.cost-grid { margin: 12px 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }.cost-grid span { padding: 8px; display: grid; color: #808c9d; background: #14181d; border-radius: 7px; font-size: 9px; }.cost-grid strong { color: #e4eaf2; font-size: 15px; }.compile-plan p { color: #8fb7a3; font-size: 11px; }
footer { display: grid; gap: 9px; }footer button { min-height: 42px; padding: 9px 12px; display: flex; align-items: center; justify-content: center; gap: 7px; background: #29303a; border: 1px solid #414c5a; border-radius: 9px; cursor: pointer; }footer button :deep(svg) { width: 16px; }footer button.primary { color: #10141a; background: #e7edf6; border-color: #e7edf6; font-weight: 800; }footer button:disabled { opacity: .38; cursor: not-allowed; }footer small { color: #6e798a; text-align: center; line-height: 1.5; }
@media (max-width: 980px) { .editor-layout { grid-template-columns: 1fr; overflow: auto; }.preview-column { min-height: 700px; }.recipe-panel { border: 1px solid #2b3038; }.annotation-tools span { display: none; } }
</style>
