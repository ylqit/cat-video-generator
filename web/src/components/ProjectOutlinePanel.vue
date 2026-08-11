<script setup lang="ts">
import { ElMessage } from "element-plus";
import { ref, toRaw, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { ProjectOutlineDto, Slot, StoryProjectInputDto } from "../api/types";

const props = defineProps<{
  runId: string;
  projectInput: StoryProjectInputDto;
  projectOutline: ProjectOutlineDto | null;
  editable: boolean;
}>();
const emit = defineEmits<{ saved: [] }>();
const SLOT_LABEL: Record<Slot, string> = { morning: "上午", noon: "中午", evening: "傍晚" };
const draft = ref<ProjectOutlineDto | null>(
  props.projectOutline ? structuredClone(toRaw(props.projectOutline)) : null,
);

watch(
  () => props.projectOutline,
  (value) => {
    draft.value = value ? structuredClone(toRaw(value)) : null;
  },
  { deep: true },
);

async function save() {
  if (!draft.value) return;
  try {
    await api.updateProjectOutline(props.runId, structuredClone(toRaw(draft.value)));
    ElMessage.success("项目大纲已确认");
    emit("saved");
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}
</script>

<template>
  <div>
    <el-alert
      v-if="projectInput.input_mode === 'episode_scripts'"
      type="success"
      :closable="false"
      title="已有剧本项目无需总导演"
      description="项目已在本地确认；三个时段会按顺序镜头化，用户原文是事件与场景边界。"
      style="margin-bottom: 12px"
    />
    <el-descriptions :column="2" border>
      <el-descriptions-item label="主题">{{ projectInput.theme }}</el-descriptions-item>
      <el-descriptions-item label="场景路线">{{ projectInput.scene_route }}</el-descriptions-item>
      <el-descriptions-item label="剧情来源">{{ projectInput.input_mode }}</el-descriptions-item>
      <el-descriptions-item label="总导演">{{ projectOutline ? "已生成" : "不适用" }}</el-descriptions-item>
    </el-descriptions>

    <template v-if="draft">
      <el-form label-position="top" style="margin-top: 14px">
        <el-form-item label="全天生活弧">
          <el-input v-model="draft.day_arc" type="textarea" :rows="4" :readonly="!editable" />
        </el-form-item>
        <el-card
          v-for="slot in (['morning', 'noon', 'evening'] as Slot[])"
          :key="slot"
          shadow="never"
          class="outline-card"
        >
          <template #header><strong>{{ SLOT_LABEL[slot] }}</strong></template>
          <el-form-item label="主要场景">
            <el-input v-model="draft.episodes[slot].scene" :readonly="!editable" />
          </el-form-item>
          <el-form-item label="时段剧情方向">
            <el-input v-model="draft.episodes[slot].direction" type="textarea" :rows="4" :readonly="!editable" />
          </el-form-item>
        </el-card>
      </el-form>
      <el-collapse v-if="draft.handoffs.length">
        <el-collapse-item title="跨时段交接" name="handoffs">
          <el-descriptions v-for="(handoff, index) in draft.handoffs" :key="index" :column="3" border size="small">
            <el-descriptions-item label="对象">{{ handoff.name }}</el-descriptions-item>
            <el-descriptions-item label="时段">{{ handoff.from_slot }} → {{ handoff.to_slot }}</el-descriptions-item>
            <el-descriptions-item label="连续性">{{ handoff.continuity }}</el-descriptions-item>
          </el-descriptions>
        </el-collapse-item>
      </el-collapse>
      <el-button v-if="editable" type="primary" style="margin-top: 12px" @click="save">
        确认项目大纲
      </el-button>
    </template>
    <template v-else>
      <h4>用户逐集原文</h4>
      <el-descriptions :column="1" border>
        <el-descriptions-item
          v-for="slot in (['morning', 'noon', 'evening'] as Slot[])"
          :key="slot"
          :label="SLOT_LABEL[slot]"
        >
          <span class="source-text">{{ projectInput.episode_sources[slot] || "尚未填写" }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </template>
  </div>
</template>

<style scoped>
.outline-card { margin-bottom: 10px; background: #16181d; border-color: #2b2d33; }
.source-text { white-space: pre-wrap; line-height: 1.6; }
</style>
