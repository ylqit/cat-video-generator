<script setup lang="ts">
import { computed } from "vue";

import type { PromptRunDto } from "../../api/types";

const props = defineProps<{
  modelValue: boolean;
  prompt: PromptRunDto | null;
  loading?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [visible: boolean] }>();
const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});

function pretty(value: unknown): string {
  return value === null || value === undefined ? "暂无" : JSON.stringify(value, null, 2);
}
</script>

<template>
  <el-drawer v-model="visible" title="Prompt 调用详情" size="min(760px, 92vw)">
    <div v-if="loading" class="drawer-loading">正在读取审计记录…</div>
    <div v-else-if="prompt" class="prompt-trace">
      <el-alert
        title="应用发送的精确 Prompt 可完整审计；供应商内部可能发生的改写不可见，也不会被伪装成平台记录。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-descriptions :column="2" border>
        <el-descriptions-item label="调用目的">{{ prompt.purpose }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag>{{ prompt.status }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="模板">{{ prompt.templateName }} · {{ prompt.templateVersion }}</el-descriptions-item>
        <el-descriptions-item label="模型">{{ prompt.provider }} / {{ prompt.model }}</el-descriptions-item>
      </el-descriptions>
      <el-tabs>
        <el-tab-pane label="精确 Prompt">
          <h3>应用发送的精确 Prompt</h3>
          <pre>{{ prompt.finalPrompt }}</pre>
          <h4>System Prompt</h4><pre>{{ prompt.systemPrompt || 'legacy_unavailable' }}</pre>
          <h4>User Prompt</h4><pre>{{ prompt.userPrompt || 'legacy_unavailable' }}</pre>
        </el-tab-pane>
        <el-tab-pane label="输入与请求">
          <h3>输入快照</h3><pre>{{ pretty(prompt.inputSnapshot) }}</pre>
          <h3>供应商请求快照</h3><pre>{{ pretty(prompt.providerRequestSnapshot) }}</pre>
        </el-tab-pane>
        <el-tab-pane label="响应与接受版本">
          <h3>原始响应</h3><pre>{{ pretty(prompt.rawResponse) }}</pre>
          <h3>结构化响应</h3><pre>{{ pretty(prompt.structuredResponse) }}</pre>
          <h3>人工接受版本与差异</h3><pre>{{ pretty({ accepted: prompt.acceptedResponse, diff: prompt.responseDiff }) }}</pre>
        </el-tab-pane>
        <el-tab-pane label="成本与重试">
          <pre>{{ pretty({ tokenUsage: prompt.tokenUsage, costMicros: prompt.costMicros, durationMs: prompt.durationMs, retryChain: prompt.retryChain, error: prompt.error }) }}</pre>
        </el-tab-pane>
      </el-tabs>
    </div>
    <el-empty v-else description="未选择 Prompt 调用" />
  </el-drawer>
</template>

<style scoped>
.prompt-trace { display: grid; gap: 16px; }
.drawer-loading { padding: 40px; color: #8c96a8; text-align: center; }
h3, h4 { margin: 12px 0 7px; }
pre { max-height: 360px; margin: 0; padding: 14px; overflow: auto; color: #cdd8e8; background: #10141a; border: 1px solid #29313c; border-radius: 9px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
</style>
