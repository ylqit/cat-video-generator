<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { RunGraph, StepDto, StepTraceDto, WorkflowNodeDto } from "../api/types";
import AssetThumb from "./AssetThumb.vue";
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
const trace = ref<StepTraceDto | null>(null);
const traceLoading = ref(false);
const traceError = ref("");

async function loadTrace() {
  const stepId = props.node?.stepId;
  trace.value = null;
  traceError.value = "";
  if (!props.modelValue || !stepId) return;
  traceLoading.value = true;
  try {
    trace.value = await api.stepTrace(stepId);
  } catch (error) {
    traceError.value = error instanceof ApiError ? error.message : String(error);
  } finally {
    traceLoading.value = false;
  }
}

watch(
  [
    () => props.node?.id,
    () => props.node?.stepId,
    () => props.node?.status,
    () => props.node?.attempts.length,
    () => props.modelValue,
  ],
  () => {
    if (!props.modelValue) {
      return;
    }
    // 失败节点优先展示可执行的恢复操作，避免用户只看到错误却找不到重试入口。
    // 重试、继续查询和对账仍由 StepList 统一处理，不在抽屉中复制付费确认逻辑。
    activePanel.value = props.node?.status === "failed" || props.node?.error
      ? "provider"
      : "content";
    void loadTrace();
  },
);

const assets = computed(() => {
  const ids = new Set(props.node?.assetIds ?? []);
  return props.graph.assets.filter((item) => ids.has(item.id));
});
const reviews = computed(() => {
  if (trace.value) return trace.value.reviews;
  const ids = new Set(props.node?.reviewIds ?? []);
  return props.graph.reviews.filter((item) => ids.has(item.id));
});
const outputAssets = computed(() => trace.value?.assets ?? assets.value);
const attempts = computed(() => trace.value?.attempts ?? props.node?.attempts ?? []);
const episode = computed(() =>
  props.graph.episodes.find((item) => item.slot === props.node?.slot),
);
const materialIds = computed(() => {
  if (trace.value) {
    const ids = trace.value.inputBindings
      .map((item) => item.assetId)
      .filter((value): value is string => typeof value === "string");
    return [...new Set(ids)];
  }
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

function promptAttempt(stepId: string) {
  return attempts.value.find((item) => item.id === stepId)?.attempt ?? "?";
}

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
            <p>{{ episode.script.story_text }}</p>
            <div class="muted">{{ episode.script.visual_context }} · {{ episode.script.appearance }}</div>
            <el-descriptions :column="1" border size="small" style="margin-top: 12px">
              <el-descriptions-item label="活动焦点">{{ episode.activityFocus }}</el-descriptions-item>
              <el-descriptions-item label="关系弧">{{ episode.relationshipArc }}</el-descriptions-item>
              <el-descriptions-item label="精确时长">{{ episode.script.duration_seconds }}秒</el-descriptions-item>
              <el-descriptions-item label="结尾回报">{{ episode.script.ending }}</el-descriptions-item>
            </el-descriptions>
          </template>
          <div v-if="outputAssets.length" class="asset-grid">
            <AssetThumb
              v-for="asset in outputAssets"
              :key="asset.id"
              :asset-id="asset.id"
              :label="`${asset.role} · ${asset.status}`"
              :size="150"
            />
          </div>
          <el-empty
            v-if="!outputAssets.length && node.type !== 'director'"
            description="该节点尚未产生媒体资产"
          />
        </el-tab-pane>

        <el-tab-pane label="Prompt链路" name="prompt">
          <el-alert v-if="traceError" type="error" :closable="false" :title="traceError" />
          <div v-loading="traceLoading">
            <template v-if="trace">
              <h4>上游输入摘要</h4>
              <pre class="json-view">{{ JSON.stringify(trace.inputSummary, null, 2) }}</pre>

              <el-descriptions :column="3" border size="small" style="margin: 12px 0">
                <el-descriptions-item label="Provider">{{ node.providerStatus }}</el-descriptions-item>
                <el-descriptions-item label="契约校验">{{ node.contractStatus }}</el-descriptions-item>
                <el-descriptions-item label="语义审核">{{ node.semanticReviewStatus }}</el-descriptions-item>
              </el-descriptions>

              <h4>当前结构化结果</h4>
              <pre class="json-view">{{ JSON.stringify(trace.currentStructuredOutput, null, 2) }}</pre>

              <h4>当前编译Prompt</h4>
              <el-collapse v-if="trace.currentCompiledPrompts.length">
                <el-collapse-item
                  v-for="prompt in trace.currentCompiledPrompts"
                  :key="prompt.label"
                  :title="`${prompt.label} · ${prompt.text.length}字`"
                  :name="prompt.label"
                >
                  <pre class="prompt-text">{{ prompt.text }}</pre>
                </el-collapse-item>
              </el-collapse>
              <el-empty v-else description="导演节点的当前编译Prompt就是已持久化的调用Prompt" />

              <h4>本节点各attempt实际调用Prompt</h4>
              <el-collapse v-if="trace.actualPrompts.length">
                <el-collapse-item
                  v-for="prompt in trace.actualPrompts"
                  :key="prompt.id"
                  :title="`attempt ${promptAttempt(prompt.stepId)} · ${prompt.purpose} · ${prompt.charCount}字`"
                  :name="prompt.id"
                >
                  <pre class="prompt-text">{{ prompt.text }}</pre>
                </el-collapse-item>
              </el-collapse>
              <el-empty v-else description="该节点尚未持久化实际Prompt" />

              <el-collapse style="margin-top: 12px">
                <el-collapse-item title="Ark原始结构化JSON" name="provider-output">
                  <pre class="json-view">{{ JSON.stringify(trace.providerOutput, null, 2) }}</pre>
                </el-collapse-item>
                <el-collapse-item v-if="trace.normalizedOutput" title="标准化业务对象" name="normalized-output">
                  <pre class="json-view">{{ JSON.stringify(trace.normalizedOutput, null, 2) }}</pre>
                </el-collapse-item>
              </el-collapse>
              <el-alert
                v-if="trace.normalizationWarnings.length"
                type="warning"
                :closable="false"
                title="归一化警告"
                style="margin-top: 12px"
              >
                <div v-for="warning in trace.normalizationWarnings" :key="warning">{{ warning }}</div>
              </el-alert>
            </template>
          </div>
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
          <el-collapse v-if="attempts.length" style="margin-top: 12px">
            <el-collapse-item title="类型化输入快照" name="snapshot">
              <pre class="json-view">{{
                JSON.stringify(attempts.at(-1)?.inputSnapshot, null, 2)
              }}</pre>
            </el-collapse-item>
            <el-collapse-item v-if="trace?.inputBindings.length" title="有序素材绑定" name="bindings">
              <pre class="json-view">{{ JSON.stringify(trace.inputBindings, null, 2) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-tab-pane>

        <el-tab-pane label="Provider任务" name="provider">
          <StepList
            :steps="attempts"
            @changed="emit('changed')"
            @review="emit('review', $event)"
          />
        </el-tab-pane>

        <el-tab-pane label="媒体输出" name="media">
          <div v-if="outputAssets.length" class="asset-grid">
            <AssetThumb
              v-for="asset in outputAssets"
              :key="asset.id"
              :asset-id="asset.id"
              :label="`${asset.role} · ${asset.status}`"
              :size="180"
            />
          </div>
          <el-empty v-else description="该节点尚未产生媒体输出" />
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
            :steps="attempts"
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
.prompt-text {
  margin: 0;
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  background: #17191f;
  border-radius: 6px;
  padding: 10px;
  color: #cbd5e1;
  font-size: 12px;
  line-height: 1.6;
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
