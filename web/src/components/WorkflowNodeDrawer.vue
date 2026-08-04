<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { RunGraph, StepDto, WorkflowNodeDto } from "../api/types";
import AssetThumb from "./AssetThumb.vue";
import PromptCollapse from "./PromptCollapse.vue";
import StatusBadge from "./StatusBadge.vue";
import StepList from "./StepList.vue";

const props = defineProps<{
  modelValue: boolean;
  node: WorkflowNodeDto | null;
  graph: RunGraph;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  changed: [];
  review: [step: StepDto];
}>();

const activePanel = ref("content");
watch(
  () => props.node?.id,
  () => {
    activePanel.value = "content";
  },
);

const prompts = computed(() => {
  const ids = new Set(props.node?.promptIds ?? []);
  return props.graph.prompts.filter((item) => ids.has(item.id));
});
const assets = computed(() => {
  const ids = new Set(props.node?.assetIds ?? []);
  return props.graph.assets.filter((item) => ids.has(item.id));
});
const reviews = computed(() => {
  const ids = new Set(props.node?.reviewIds ?? []);
  return props.graph.reviews.filter((item) => ids.has(item.id));
});
const episode = computed(() =>
  props.graph.episodes.find((item) => item.slot === props.node?.slot),
);
const materialIds = computed(() => {
  const result: string[] = [];
  for (const attempt of props.node?.attempts ?? []) {
    for (const key of ["reference_asset_ids", "input_asset_ids"]) {
      const value = attempt.inputSnapshot[key];
      if (Array.isArray(value)) {
        result.push(...value.map(String));
      }
    }
  }
  return [...new Set(result)];
});

function close() {
  emit("update:modelValue", false);
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    size="min(760px, 92vw)"
    direction="rtl"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      <div v-if="node" class="drawer-title">
        <strong>{{ node.label }}</strong>
        <StatusBadge :status="node.status" />
        <span v-if="node.slot" class="muted">{{ node.slot }}</span>
      </div>
    </template>

    <template v-if="node">
      <el-alert
        v-if="node.error?.message"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
      >
        <template #title>
          {{ node.error.code ?? "workflow_error" }}：{{ node.error.message }}
        </template>
        <div v-if="node.nextAction">下一步：{{ node.nextAction }}</div>
      </el-alert>

      <el-tabs v-model="activePanel">
        <el-tab-pane label="内容结果" name="content">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="Provider">
              {{ node.providerStatus }}
            </el-descriptions-item>
            <el-descriptions-item label="契约">
              {{ node.contractStatus }}
            </el-descriptions-item>
            <el-descriptions-item label="语义审核">
              {{ node.semanticReviewStatus }}
            </el-descriptions-item>
            <el-descriptions-item label="Step">
              <span class="mono">{{ node.stepId ?? "—" }}</span>
            </el-descriptions-item>
          </el-descriptions>

          <template v-if="node.id === 'director:day'">
            <h4>全天导演结果</h4>
            <pre class="json-view">{{ JSON.stringify(graph.run.dayBrief, null, 2) }}</pre>
          </template>
          <template v-else-if="node.type === 'director' && episode">
            <h4>{{ episode.title }}</h4>
            <p>{{ episode.script.main_event }}</p>
            <div class="muted">{{ episode.script.scene }}</div>
          </template>
          <div v-if="assets.length" class="asset-grid">
            <AssetThumb
              v-for="asset in assets"
              :key="asset.id"
              :asset-id="asset.id"
              :label="`${asset.role} · ${asset.status}`"
              :size="150"
            />
          </div>
          <el-empty
            v-if="!assets.length && node.type !== 'director'"
            description="该节点尚未产生媒体资产"
          />
        </el-tab-pane>

        <el-tab-pane label="实际Prompt" name="prompt">
          <PromptCollapse
            v-if="prompts.length"
            :prompts="prompts"
            title="该attempt实际调用Prompt"
          />
          <el-empty v-else description="该节点尚未持久化实际Prompt" />
          <el-alert
            type="info"
            :closable="false"
            style="margin-top: 12px"
            title="实际调用Prompt按attempt永久保存且不可覆盖"
            description="工作台中的当前编译Prompt只有在尚未提交时才可编辑并影响下一次生成。"
          />
        </el-tab-pane>

        <el-tab-pane label="输入素材" name="inputs">
          <div v-if="materialIds.length" class="asset-grid">
            <AssetThumb
              v-for="assetId in materialIds"
              :key="assetId"
              :asset-id="assetId"
              :size="140"
            />
          </div>
          <el-empty v-else description="该节点没有媒体输入素材" />
          <el-collapse v-if="node.attempts.length" style="margin-top: 12px">
            <el-collapse-item title="类型化输入快照" name="snapshot">
              <pre class="json-view">{{
                JSON.stringify(node.attempts.at(-1)?.inputSnapshot, null, 2)
              }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-tab-pane>

        <el-tab-pane label="Provider任务" name="provider">
          <StepList
            :steps="node.attempts"
            @changed="emit('changed')"
            @review="emit('review', $event)"
          />
        </el-tab-pane>

        <el-tab-pane label="审核证据" name="reviews">
          <div v-for="review in reviews" :key="review.id" class="review-card">
            <div class="drawer-title">
              <StatusBadge :status="review.decision" />
              <span>{{ review.source }}</span>
            </div>
            <p v-if="review.reason">{{ review.reason }}</p>
            <el-collapse>
              <el-collapse-item title="结构化证据" name="evidence">
                <pre class="json-view">{{ JSON.stringify(review.evidence, null, 2) }}</pre>
              </el-collapse-item>
            </el-collapse>
          </div>
          <el-empty v-if="!reviews.length" description="该节点尚无审核证据" />
        </el-tab-pane>

        <el-tab-pane label="尝试历史" name="attempts">
          <StepList
            :steps="node.attempts"
            @changed="emit('changed')"
            @review="emit('review', $event)"
          />
        </el-tab-pane>
      </el-tabs>
    </template>

    <template #footer>
      <el-button @click="close">关闭</el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.drawer-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.muted {
  color: #8a8f99;
  font-size: 12px;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}
.json-view {
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  background: #17191f;
  border-radius: 6px;
  padding: 10px;
  color: #cbd5e1;
  font-size: 12px;
}
.asset-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}
.review-card {
  border: 1px solid #2b2d33;
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 10px;
}
</style>
