<script setup lang="ts">
import { ElMessage } from "element-plus";
import { ref } from "vue";

import { api } from "../api/client";
import type { PromptDto } from "../api/types";

const props = defineProps<{
  /** 同一用途的Prompt记录，按创建时间新旧排列 */
  prompts: PromptDto[];
  title: string;
}>();

const expanded = ref(false);
const loading = ref(false);
const text = ref("");
const loadedId = ref("");

const latest = () => props.prompts[props.prompts.length - 1];

/** 展开时才拉取完整Prompt文本，避免graph响应过大。 */
async function toggle() {
  expanded.value = !expanded.value;
  const prompt = latest();
  if (!expanded.value || !prompt || loadedId.value === prompt.id) {
    return;
  }
  loading.value = true;
  try {
    const full = await api.prompt(prompt.id);
    text.value = full.text;
    loadedId.value = full.id;
  } catch {
    ElMessage.error("Prompt 加载失败");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div v-if="prompts.length" style="margin-top: 8px">
    <el-button size="small" text type="info" @click="toggle">
      {{ expanded ? "收起" : "查看" }}{{ title }}（{{ latest()?.charCount ?? 0 }}字）
    </el-button>
    <div v-if="expanded" v-loading="loading" class="prompt-text">
      <pre>{{ text }}</pre>
    </div>
  </div>
</template>

<style scoped>
.prompt-text {
  margin-top: 6px;
  max-height: 320px;
  overflow-y: auto;
  background: #17191f;
  border-radius: 6px;
  padding: 10px 12px;
}
.prompt-text pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
  color: #cbd5e1;
}
</style>
