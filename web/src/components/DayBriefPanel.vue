<script setup lang="ts">
import { ElMessage } from "element-plus";
import { reactive, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { DayBriefDto } from "../api/types";

const props = defineProps<{
  runId: string;
  dayBrief: DayBriefDto;
  /** 方案未定稿且dayBrief阶段为manual时可编辑 */
  editable: boolean;
}>();
const emit = defineEmits<{ saved: [] }>();

const SLOT_LABEL: Record<string, string> = {
  morning: "早间",
  noon: "午间",
  evening: "晚间",
};

const draft = reactive({
  theme: props.dayBrief.theme,
  day_context: props.dayBrief.day_context,
  slots: props.dayBrief.slots.map((slot) => ({ ...slot })),
});
watch(
  () => props.dayBrief,
  (brief) => {
    draft.theme = brief.theme;
    draft.day_context = brief.day_context;
    draft.slots = brief.slots.map((slot) => ({ ...slot }));
  },
);

const saving = ref(false);

/** 保存人工编辑的日导演输出；后端会清空错配的时段草稿。 */
async function save() {
  saving.value = true;
  try {
    await api.updateDayBrief(props.runId, {
      content_date: props.dayBrief.content_date,
      theme: draft.theme,
      day_context: draft.day_context,
      slots: draft.slots,
    });
    ElMessage.success("日导演输出已保存，时段草稿已清空");
    emit("saved");
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <el-form label-width="110px" :disabled="!editable">
    <el-form-item label="日主题">
      <el-input v-model="draft.theme" :rows="1" type="textarea" />
    </el-form-item>
    <el-form-item label="全天上下文">
      <el-input v-model="draft.day_context" :rows="2" type="textarea" />
    </el-form-item>
    <el-card
      v-for="slot in draft.slots"
      :key="slot.slot"
      shadow="never"
      style="margin-bottom: 10px"
    >
      <template #header>
        <strong>{{ SLOT_LABEL[slot.slot] ?? slot.slot }}</strong>
      </template>
      <el-form-item label="叙事目的" label-width="90px">
        <el-input v-model="slot.narrative_purpose" :rows="1" type="textarea" />
      </el-form-item>
      <el-form-item label="场景方向" label-width="90px">
        <el-input v-model="slot.scene_direction" :rows="1" type="textarea" />
      </el-form-item>
      <el-form-item label="事件方向" label-width="90px">
        <el-input v-model="slot.event_direction" :rows="1" type="textarea" />
      </el-form-item>
      <el-form-item label="服饰意图" label-width="90px">
        <el-input v-model="slot.appearance_intent" :rows="1" type="textarea" />
      </el-form-item>
    </el-card>
    <el-form-item v-if="editable">
      <el-button type="primary" :loading="saving" @click="save">
        保存日导演编辑
      </el-button>
      <span class="muted" style="margin-left: 10px">
        保存后点「继续」重新生成三集剧本
      </span>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.muted {
  color: #8a8f99;
  font-size: 12px;
}
</style>
