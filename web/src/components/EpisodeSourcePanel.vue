<script setup lang="ts">
import { ElMessage } from "element-plus";
import { ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { Slot } from "../api/types";

const props = defineProps<{
  runId: string;
  slot: Slot;
  sourceText: string | null;
  planned: boolean;
  unlocked: boolean;
  readOnly?: boolean;
}>();
const emit = defineEmits<{
  saved: [sourceText: string];
  plan: [generateFromTheme: boolean];
}>();

const draft = ref(props.sourceText ?? "");
const saving = ref(false);
watch(() => props.sourceText, (value) => { draft.value = value ?? ""; });

async function save() {
  const value = draft.value.trim();
  if (value.length < 4) return;
  saving.value = true;
  try {
    await api.updateEpisodeSource(props.runId, props.slot, value);
    ElMessage.success("本时段原始剧本已保存");
    emit("saved", value);
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="source-panel">
    <div class="source-header">
      <strong>用户原始剧本</strong>
      <el-tag v-if="planned" type="info">已镜头化</el-tag>
      <el-tag v-else-if="sourceText" type="success">已保存</el-tag>
      <el-tag v-else>未填写</el-tag>
    </div>
    <el-input
      v-model="draft"
      type="textarea"
      :rows="6"
      :readonly="planned || readOnly"
      placeholder="输入本时段完整剧情；顺序模式允许先保存后续时段，但未解锁前不会创建收费Step。"
    />
    <div v-if="!planned && !readOnly" class="source-actions">
      <el-button :loading="saving" :disabled="draft.trim().length < 4" @click="save">
        保存原始剧本
      </el-button>
      <el-button
        v-if="unlocked && sourceText"
        type="primary"
        :disabled="draft.trim() !== (sourceText ?? '').trim()"
        @click="emit('plan', false)"
      >按原文镜头化</el-button>
      <el-button
        v-if="unlocked && !sourceText && !draft.trim()"
        type="primary"
        @click="emit('plan', true)"
      >AI根据主题生成（仅在启用关联卡时参考前序）</el-button>
      <span v-if="unlocked && draft.trim() && draft.trim() !== (sourceText ?? '').trim()" class="muted">
        请先保存当前原文，再调用时段导演。
      </span>
      <span v-if="!unlocked" class="muted">可以提前保存，但需先完成前一时段结果卡。</span>
    </div>
  </div>
</template>

<style scoped>
.source-panel { margin-bottom: 14px; }
.source-header, .source-actions { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.source-actions { margin-top: 8px; }
.muted { color: #8a8f99; font-size: 12px; }
</style>
