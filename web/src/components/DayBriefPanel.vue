<script setup lang="ts">
import { ElMessage } from "element-plus";
import { reactive, ref, toRaw, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { DayBriefDto } from "../api/types";

const props = defineProps<{ runId: string; dayBrief: DayBriefDto; editable: boolean }>();
const emit = defineEmits<{ saved: [] }>();

const SLOT_LABEL: Record<string, string> = {
  morning: "上午",
  noon: "中午",
  evening: "傍晚",
};

const draft = reactive<DayBriefDto>(structuredClone(toRaw(props.dayBrief)));
watch(
  () => props.dayBrief,
  (brief) => Object.assign(draft, structuredClone(toRaw(brief))),
  { deep: true },
);
const saving = ref(false);

async function save() {
  saving.value = true;
  try {
    await api.updateDayBrief(props.runId, structuredClone(toRaw(draft)));
    ElMessage.success("总导演边界已保存并确认；当前已解锁时段可以开始规划");
    emit("saved");
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <el-form label-width="104px" :disabled="!editable" size="small">
    <el-form-item label="日主题"><el-input v-model="draft.theme" /></el-form-item>
    <el-form-item label="全天生活弧">
      <el-input v-model="draft.day_arc" type="textarea" :rows="4" />
    </el-form-item>

    <el-card
      v-for="brief in draft.slot_briefs"
      :key="brief.slot"
      shadow="never"
      class="slot-card"
    >
      <template #header><strong>{{ SLOT_LABEL[brief.slot] }}</strong></template>
      <el-form-item label="时段作用" label-width="86px">
        <el-input v-model="brief.narrative_role" type="textarea" :rows="1" />
      </el-form-item>
      <el-form-item label="事件方向" label-width="86px">
        <el-input v-model="brief.event_direction" type="textarea" :rows="2" />
      </el-form-item>
      <el-form-item label="外观意图" label-width="86px">
        <el-input v-model="brief.appearance_intent" type="textarea" :rows="1" />
      </el-form-item>
      <el-form-item label="活动焦点" label-width="86px">
        <el-select v-model="brief.activity_focus" style="width: 180px">
          <el-option label="猫咪主活动" value="cat_lead" />
          <el-option label="人物主活动" value="person_lead" />
          <el-option label="人猫平衡" value="balanced" />
        </el-select>
      </el-form-item>
      <el-form-item label="时长档" label-width="86px">
        <el-select v-model="brief.duration_band" style="width: 180px">
          <el-option label="短片 8～15秒" value="short" />
          <el-option label="中片 16～30秒" value="medium" />
          <el-option label="长片 31～45秒" value="long" />
        </el-select>
      </el-form-item>
      <el-form-item label="决策理由" label-width="86px">
        <el-input v-model="brief.decision_reason" type="textarea" :rows="1" />
      </el-form-item>
    </el-card>

    <el-divider content-position="left">跨时段交接</el-divider>
    <el-descriptions
      v-for="(handoff, index) in draft.handoffs"
      :key="`${handoff.name}-${index}`"
      :column="1"
      border
      size="small"
      class="handoff"
    >
      <el-descriptions-item label="交接对象">{{ handoff.name }}</el-descriptions-item>
      <el-descriptions-item label="时段">{{ handoff.from_slot }} → {{ handoff.to_slot }}</el-descriptions-item>
      <el-descriptions-item label="连续性">{{ handoff.continuity }}</el-descriptions-item>
    </el-descriptions>

    <el-form-item v-if="editable">
      <el-button type="primary" :loading="saving" @click="save">保存并确认总导演边界</el-button>
      <span class="muted" style="margin-left: 10px">保存会清空尚未定稿的三时段草稿。</span>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.slot-card { margin-bottom: 10px; background: #16181d; border-color: #2b2d33; }
.handoff { margin-bottom: 10px; }
.muted { color: #8a8f99; font-size: 12px; }
</style>
