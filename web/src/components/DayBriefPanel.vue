<script setup lang="ts">
import { ElMessage } from "element-plus";
import { reactive, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { DayBriefDto } from "../api/types";

const props = defineProps<{ runId: string; dayBrief: DayBriefDto; editable: boolean }>();
const emit = defineEmits<{ saved: [] }>();

const SLOT_LABEL: Record<string, string> = {
  morning: "上午",
  noon: "中午",
  evening: "傍晚",
};

const draft = reactive<DayBriefDto>(structuredClone(props.dayBrief));
watch(
  () => props.dayBrief,
  (brief) => Object.assign(draft, structuredClone(brief)),
  { deep: true },
);
const saving = ref(false);

function durationChanged(brief: DayBriefDto["slot_briefs"][number]) {
  if (brief.duration_intent.requested_mode !== "adaptive") {
    brief.duration_intent.resolved_band = brief.duration_intent.requested_mode;
    brief.duration_intent.resolution_reason = "用户在DayBrief确认阶段固定时长档";
  }
}

async function save() {
  saving.value = true;
  try {
    await api.updateDayBrief(props.runId, structuredClone(draft));
    ElMessage.success("总导演方向已保存；未生成的时段导演将读取新设置");
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
    <el-form-item label="全天目标">
      <el-input v-model="draft.day_objective" type="textarea" :rows="2" />
    </el-form-item>
    <el-form-item label="全天环境">
      <el-input v-model="draft.day_context" type="textarea" :rows="2" />
    </el-form-item>
    <el-form-item label="共享视觉母题">
      <el-input v-model="draft.shared_motif" type="textarea" :rows="1" />
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
      <el-form-item label="场景方向" label-width="86px">
        <el-input v-model="brief.scene_direction" type="textarea" :rows="1" />
      </el-form-item>
      <el-form-item label="事件方向" label-width="86px">
        <el-input v-model="brief.event_direction" type="textarea" :rows="1" />
      </el-form-item>
      <el-form-item label="外观意图" label-width="86px">
        <el-input v-model="brief.appearance_intent" type="textarea" :rows="1" />
      </el-form-item>
      <el-form-item label="活动焦点" label-width="86px">
        <el-select v-model="brief.resolved_activity_focus" style="width: 180px">
          <el-option label="猫咪主活动" value="cat_lead" />
          <el-option label="人物主活动" value="person_lead" />
          <el-option label="人猫平衡" value="balanced" />
        </el-select>
      </el-form-item>
      <el-form-item label="关系方向" label-width="86px">
        <el-input v-model="brief.relationship_direction" type="textarea" :rows="1" />
      </el-form-item>
      <el-form-item label="时长意图" label-width="86px">
        <el-select
          v-model="brief.duration_intent.requested_mode"
          style="width: 160px"
          @change="durationChanged(brief)"
        >
          <el-option label="自适应" value="adaptive" />
          <el-option label="短 8–15秒" value="short" />
          <el-option label="中 16–30秒" value="medium" />
          <el-option label="长 31–45秒" value="long" />
        </el-select>
        <el-select
          v-if="brief.duration_intent.requested_mode === 'adaptive'"
          v-model="brief.duration_intent.resolved_band"
          style="width: 160px; margin-left: 8px"
        >
          <el-option label="解析为短" value="short" />
          <el-option label="解析为中" value="medium" />
          <el-option label="解析为长" value="long" />
        </el-select>
        <span class="muted" style="margin-left: 8px">
          {{ brief.duration_intent.resolution_reason }}
        </span>
      </el-form-item>
    </el-card>

    <el-form-item v-if="editable">
      <el-button type="primary" :loading="saving" @click="save">保存总导演编辑</el-button>
      <span class="muted" style="margin-left: 10px">保存会清空尚未定稿的三时段草稿。</span>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.slot-card { margin-bottom: 10px; background: #16181d; border-color: #2b2d33; }
.muted { color: #8a8f99; font-size: 12px; }
</style>
