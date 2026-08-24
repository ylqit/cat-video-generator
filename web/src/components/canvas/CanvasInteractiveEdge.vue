<script setup lang="ts">
import { EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@vue-flow/core";
import { Scissor } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, ref } from "vue";

import type { CanvasEdgeActionDto, CanvasEdgeDto } from "../../api/types";

export interface CanvasInteractiveEdgeData {
  edge: CanvasEdgeDto;
  incident: boolean;
  onDisconnect: (edge: CanvasEdgeDto) => void | Promise<void>;
  onUnavailable: (reason: string) => void;
}

const props = defineProps<EdgeProps<CanvasInteractiveEdgeData>>();
const hovered = ref(false);
const focused = ref(false);
const pointerPosition = ref<{ x: number; y: number } | null>(null);
let leaveTimer: ReturnType<typeof setTimeout> | undefined;

const edgePath = computed(() => getBezierPath({
  sourceX: props.sourceX,
  sourceY: props.sourceY,
  sourcePosition: props.sourcePosition,
  targetX: props.targetX,
  targetY: props.targetY,
  targetPosition: props.targetPosition,
}));
const path = computed(() => edgePath.value[0]);
const labelX = computed(() => edgePath.value[1]);
const labelY = computed(() => edgePath.value[2]);
const active = computed(() => Boolean(props.selected || props.data.incident || hovered.value || focused.value));
const disconnectAction = computed<CanvasEdgeActionDto | undefined>(() => (
  props.data.edge.availableActions?.find((action) => action.key === "disconnect_edge")
));
const actionPosition = computed(() => pointerPosition.value ?? { x: labelX.value, y: labelY.value });

function recordPointer(event: PointerEvent) {
  const svg = (event.currentTarget as SVGPathElement).ownerSVGElement;
  if (!svg) return;
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const transform = svg.getScreenCTM()?.inverse();
  if (!transform) return;
  const projected = point.matrixTransform(transform);
  pointerPosition.value = { x: projected.x, y: projected.y };
}

function keepActionsVisible() {
  if (leaveTimer) clearTimeout(leaveTimer);
  leaveTimer = undefined;
  hovered.value = true;
}

function scheduleActionsClose() {
  if (leaveTimer) clearTimeout(leaveTimer);
  leaveTimer = setTimeout(() => {
    hovered.value = false;
    pointerPosition.value = null;
    leaveTimer = undefined;
  }, 120);
}

function runDisconnect() {
  const action = disconnectAction.value;
  if (!action?.enabled) {
    props.data.onUnavailable(action?.disabledReason ?? "该连线由系统规则管理，不能直接剪断");
    return;
  }
  void props.data.onDisconnect(props.data.edge);
}

onBeforeUnmount(() => {
  if (leaveTimer) clearTimeout(leaveTimer);
});
</script>

<template>
  <g
    class="interactive-edge"
    :class="{ active, selected: props.selected }"
    @pointerenter="keepActionsVisible"
    @pointerleave="scheduleActionsClose"
  >
    <path class="edge-base" :d="path" :marker-end="markerEnd" :marker-start="markerStart" />
    <path v-if="active" class="edge-flow" :d="path" />
    <path
      class="edge-hit"
      :d="path"
      tabindex="0"
      role="button"
      :aria-label="`连接 ${source} 到 ${target}`"
      @pointermove="recordPointer"
      @focus="focused = true"
      @blur="focused = false"
    />
    <EdgeLabelRenderer v-if="hovered || focused">
      <button
        class="edge-scissors nodrag nopan"
        type="button"
        :aria-disabled="disconnectAction?.enabled !== true"
        :title="disconnectAction?.enabled ? disconnectAction.label : (disconnectAction?.disabledReason ?? '该连线受系统保护')"
        :style="{ transform: `translate(-50%, -50%) translate(${actionPosition.x}px, ${actionPosition.y}px)` }"
        @pointerenter="keepActionsVisible"
        @pointerleave="scheduleActionsClose"
        @click.stop="runDisconnect"
      ><Scissor /><span>{{ disconnectAction?.enabled ? '剪断' : '已锁定' }}</span></button>
    </EdgeLabelRenderer>
  </g>
</template>

<style scoped>
.edge-base,.edge-flow,.edge-hit { fill: none; vector-effect: non-scaling-stroke; }
.edge-base { stroke: #657080; stroke-width: 1.4; transition: stroke 140ms ease, stroke-width 140ms ease; }
.edge-flow { stroke: #55b5ff; stroke-width: 2.4; stroke-linecap: round; stroke-dasharray: 32 138; filter: drop-shadow(0 0 4px rgb(59 164 255 / 70%)); animation: edge-flow 1.25s linear infinite; pointer-events: none; }
.edge-hit { stroke: transparent; stroke-width: 20; pointer-events: stroke; cursor: pointer; }
.interactive-edge.active .edge-base { stroke: #91c8f4; stroke-width: 1.8; }
.edge-hit:focus-visible { outline: none; stroke: rgb(85 181 255 / 12%); }
.edge-scissors { position: absolute; z-index: 1150; display: inline-flex; align-items: center; gap: 5px; min-width: 44px; min-height: 44px; padding: 7px 10px; color: #f4f7fb; background: #20242a; border: 1px solid #596370; border-radius: 22px; box-shadow: 0 8px 24px rgb(0 0 0 / 34%); pointer-events: all; cursor: pointer; }
.edge-scissors svg { width: 16px; height: 16px; }
.edge-scissors span { font-size: 11px; white-space: nowrap; }
.edge-scissors[aria-disabled="true"] { color: #9ca5b1; border-color: #454c55; cursor: not-allowed; }
.edge-scissors:focus-visible { outline: 2px solid #72b8ff; outline-offset: 2px; }
@keyframes edge-flow { to { stroke-dashoffset: -170; } }
@media (prefers-reduced-motion: reduce) {
  .edge-flow { stroke-dasharray: none; animation: none; }
}
</style>
