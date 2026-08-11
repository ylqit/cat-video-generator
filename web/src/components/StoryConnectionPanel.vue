<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { reactive, ref, watch } from "vue";

import { api } from "../api/client";
import type { JobAccepted, Slot, StoryConnectionDto, StoryConnectionMode } from "../api/types";

const props = defineProps<{
  runId: string;
  slot: Slot;
  value?: StoryConnectionDto | null;
  suggestion?: { mode: StoryConnectionMode; brief: string } | null;
  disabled?: boolean;
}>();
const emit = defineEmits<{ saved: []; job: [job: JobAccepted] }>();

type SourceMode = "none" | "manual" | "ai";
const sourceMode = ref<SourceMode>(props.value?.brief ? "manual" : "none");
const form = reactive<StoryConnectionDto>({
  useForDirector: props.value?.useForDirector ?? false,
  mode: props.value?.mode ?? "independent",
  brief: props.value?.brief ?? "",
  confirmedAt: props.value?.confirmedAt ?? null,
});

watch(
  [() => props.slot, () => props.value] as const,
  ([, value]) => {
    Object.assign(form, {
      useForDirector: value?.useForDirector ?? false,
      mode: value?.mode ?? "independent",
      brief: value?.brief ?? "",
      confirmedAt: value?.confirmedAt ?? null,
    });
    sourceMode.value = value?.brief ? "manual" : "none";
  },
  { immediate: true },
);
watch(
  () => props.suggestion,
  (value) => {
    if (!value || sourceMode.value !== "ai") return;
    form.mode = value.mode;
    form.brief = value.brief;
  },
);

async function generateSuggestion() {
  await ElMessageBox.confirm(
    "AI关联建议会调用一次付费导演模型；建议只形成草稿，不会自动加载给后续导演。",
    "确认生成关联建议",
    { type: "warning", confirmButtonText: "确认付费生成" },
  );
  sourceMode.value = "ai";
  const job = await api.suggestStoryConnection(props.runId, props.slot);
  emit("job", job);
  ElMessage.info("关联建议正在生成；完成后仍需编辑、保存并决定是否加载")
}

async function save() {
  const payload: StoryConnectionDto = sourceMode.value === "none"
    ? { useForDirector: false, mode: "independent", brief: "" }
    : { ...form };
  await api.saveStoryConnection(props.runId, props.slot, payload);
  ElMessage.success("剧情关联卡已保存")
  emit("saved");
}
</script>

<template>
  <el-card shadow="never" class="connection-card">
    <template #header><strong>可选剧情关联</strong></template>
    <el-alert
      type="info"
      :closable="false"
      title="后续导演只会读取已确认且已开启“加载关联卡”的正文，不会读取完整前序结果。"
    />
    <el-radio-group v-model="sourceMode" :disabled="disabled" style="margin: 12px 0">
      <el-radio-button value="none">不生成</el-radio-button>
      <el-radio-button value="manual">手工填写</el-radio-button>
      <el-radio-button value="ai">AI生成建议</el-radio-button>
    </el-radio-group>
    <el-button v-if="sourceMode === 'ai'" :disabled="disabled" @click="generateSuggestion">
      生成一版AI建议
    </el-button>
    <template v-if="sourceMode !== 'none'">
      <el-form label-position="top" style="margin-top: 12px">
        <el-form-item label="关联强度">
          <el-select v-model="form.mode" :disabled="disabled">
            <el-option label="独立成篇" value="independent" />
            <el-option label="选择性关联" value="selected_link" />
            <el-option label="直接续接" value="direct_continue" />
          </el-select>
        </el-form-item>
        <el-form-item label="关联卡正文">
          <el-input v-model="form.brief" type="textarea" :rows="4" :disabled="disabled" />
        </el-form-item>
        <el-form-item label="导演是否加载">
          <el-switch
            v-model="form.useForDirector"
            :disabled="disabled || form.mode === 'independent'"
            active-text="加载关联卡"
            inactive-text="不加载"
          />
        </el-form-item>
      </el-form>
    </template>
    <el-button type="primary" :disabled="disabled" @click="save">保存关联决定</el-button>
  </el-card>
</template>

<style scoped>
.connection-card { margin-bottom: 14px; }
</style>
