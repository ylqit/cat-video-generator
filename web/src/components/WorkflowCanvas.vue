<script setup lang="ts">
import { Background } from "@vue-flow/background";
import { Controls } from "@vue-flow/controls";
import { MarkerType, VueFlow, type Edge, type Node } from "@vue-flow/core";
import { computed } from "vue";

import type { WorkflowNodeDto } from "../api/types";

import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";

const props = defineProps<{
  nodes: WorkflowNodeDto[];
  selectedId?: string;
}>();
const emit = defineEmits<{ select: [node: WorkflowNodeDto] }>();

const SLOT_Y: Record<string, number> = { morning: 70, noon: 250, evening: 430 };
const COLUMN_X: Record<string, number> = {
  director: 460,
  look: 680,
  opening_anchor: 900,
  video: 1140,
  content_review: 1380,
  accepted_outcome: 1600,
};

function position(item: WorkflowNodeDto) {
  if (item.semanticNodeId === "run:day-director") return { x: 20, y: 205 };
  if (item.semanticNodeId === "run:day-confirmation") return { x: 235, y: 205 };
  if (item.semanticNodeId === "run:delivery") return { x: 1830, y: 205 };
  return {
    x: COLUMN_X[item.type] ?? 460,
    y: SLOT_Y[item.slot ?? "morning"] ?? 70,
  };
}

const flowNodes = computed<Node[]>(() =>
  props.nodes.map((item) => ({
    id: item.semanticNodeId,
    position: position(item),
    draggable: false,
    selectable: true,
    data: { item },
    class: [
      `availability-${item.availability}`,
      `execution-${item.executionStatus}`,
      props.selectedId === item.semanticNodeId ? "is-selected" : "",
      item.stale ? "is-stale" : "",
    ],
  })),
);

const nodeIds = computed(() => new Set(props.nodes.map((item) => item.semanticNodeId)));
const edgePairs = computed(() => {
  const pairs: Array<[string, string]> = [["run:day-director", "run:day-confirmation"]];
  const slots = ["morning", "noon", "evening"];
  const suffixes = ["director", "look", "opening-anchor", "video", "review", "outcome"];
  slots.forEach((slot, slotIndex) => {
    const ids = suffixes.map((suffix) => `${slot}:${suffix}`);
    const source = slotIndex === 0 ? "run:day-confirmation" : `${slots[slotIndex - 1]}:outcome`;
    pairs.push([source, ids[0]]);
    ids.slice(0, -1).forEach((id, index) => pairs.push([id, ids[index + 1]]));
  });
  pairs.push(["evening:outcome", "run:delivery"]);
  return pairs;
});
const edges = computed<Edge[]>(() =>
  edgePairs.value
    .filter(([source, target]) => nodeIds.value.has(source) && nodeIds.value.has(target))
    .map(([source, target]) => ({
      id: `${source}->${target}`,
      source,
      target,
      type: "smoothstep",
      markerEnd: MarkerType.ArrowClosed,
      animated: props.nodes.find((item) => item.semanticNodeId === target)?.availability === "active",
    })),
);

function onNodeClick(event: { node: Node }) {
  const item = (event.node.data as { item: WorkflowNodeDto }).item;
  emit("select", item);
}
</script>

<template>
  <div class="workflow-canvas">
    <div class="lane-label lane-morning">上午</div>
    <div class="lane-label lane-noon">中午</div>
    <div class="lane-label lane-evening">傍晚</div>
    <VueFlow
      :nodes="flowNodes"
      :edges="edges"
      :min-zoom="0.35"
      :max-zoom="1.6"
      fit-view-on-init
      :nodes-connectable="false"
      :elements-selectable="true"
      @node-click="onNodeClick"
    >
      <template #node-default="{ data }">
        <div class="semantic-node">
          <div class="semantic-node__title">{{ data.item.label }}</div>
          <div class="semantic-node__meta">
            <span>{{ data.item.availability }}</span>
            <span>{{ data.item.executionStatus }}</span>
          </div>
          <div v-if="data.item.lockReason" class="semantic-node__reason">
            {{ data.item.lockReason }}
          </div>
          <div v-if="data.item.stale" class="semantic-node__stale">上游已变化</div>
        </div>
      </template>
      <Background :gap="18" :size="1" color="#30363d" />
      <Controls position="bottom-left" />
    </VueFlow>
  </div>
</template>

<style scoped>
.workflow-canvas {
  position: relative;
  height: 620px;
  overflow: hidden;
  border: 1px solid var(--el-border-color);
  border-radius: 12px;
  background: #0f1115;
}
.lane-label {
  position: absolute;
  left: 8px;
  z-index: 5;
  color: #8b949e;
  font-size: 12px;
  pointer-events: none;
}
.lane-morning { top: 58px; }
.lane-noon { top: 238px; }
.lane-evening { top: 418px; }
.semantic-node {
  width: 170px;
  min-height: 78px;
  padding: 10px 12px;
  text-align: left;
}
.semantic-node__title { font-weight: 700; color: #f0f3f6; }
.semantic-node__meta { display: flex; gap: 8px; margin-top: 8px; color: #8b949e; font-size: 11px; }
.semantic-node__reason { margin-top: 7px; color: #d29922; font-size: 11px; line-height: 1.35; }
.semantic-node__stale { margin-top: 6px; color: #ff7b72; font-size: 11px; }
:deep(.vue-flow__node) { border: 1px solid #3d444d; border-radius: 10px; background: #161b22; }
:deep(.vue-flow__node.availability-locked) { opacity: .54; border-style: dashed; }
:deep(.vue-flow__node.availability-ready) { border-color: #58a6ff; }
:deep(.vue-flow__node.availability-active) { border-color: #d29922; box-shadow: 0 0 0 2px #d2992233; }
:deep(.vue-flow__node.availability-completed) { border-color: #3fb950; }
:deep(.vue-flow__node.is-selected) { box-shadow: 0 0 0 3px #58a6ff55; }
:deep(.vue-flow__node.is-stale) { border-color: #f85149; }
:deep(.vue-flow__edge-path) { stroke: #484f58; stroke-width: 1.8; }
</style>
