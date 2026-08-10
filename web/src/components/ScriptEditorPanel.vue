<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, reactive, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { EpisodeDto, EpisodeScript } from "../api/types";

const props = defineProps<{ episode: EpisodeDto; editable: boolean }>();
const emit = defineEmits<{ saved: [] }>();
const draft = reactive<EpisodeScript>(structuredClone(props.episode.script));
const loaded = ref("");
const saving = ref(false);
const errors = ref<string[]>([]);

function snapshot() { return JSON.stringify(draft); }
function reset() {
  Object.assign(draft, structuredClone(props.episode.script));
  loaded.value = snapshot();
}
watch(
  () => props.episode.script,
  () => {
    if (!loaded.value || snapshot() === loaded.value) reset();
  },
  { deep: true, immediate: true },
);

const durationBand = computed(() =>
  draft.duration_seconds <= 15 ? "短片" : draft.duration_seconds <= 30 ? "中片·一次延展" : "长片·两次延展",
);

async function save() {
  saving.value = true;
  errors.value = [];
  try {
    const result = await api.updateScript(props.episode.id, structuredClone(draft));
    loaded.value = snapshot();
    ElMessage.success(
      result.promptOverridesKept
        ? "剧本已保存；请确认既有Prompt覆盖仍适用"
        : "剧本已保存，视觉锚点和视频Prompt将按新脚本编译",
    );
    emit("saved");
  } catch (error) {
    if (error instanceof ApiError) {
      const detail = error.detail as { errors?: Array<{ msg?: string }> };
      errors.value = Array.isArray(detail?.errors)
        ? detail.errors.map((item) => String(item.msg ?? item))
        : [error.message];
    } else errors.value = [String(error)];
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <el-form label-width="96px" :disabled="!editable" size="small">
    <el-form-item label="标题"><el-input v-model="draft.title" /></el-form-item>
    <el-form-item label="观众问题">
      <el-input v-model="draft.episode_question" type="textarea" :rows="1" />
    </el-form-item>
    <el-form-item label="主事件">
      <el-input v-model="draft.main_event" type="textarea" :rows="2" />
    </el-form-item>
    <el-form-item label="场景">
      <el-input v-model="draft.scene" type="textarea" :rows="2" />
    </el-form-item>
    <el-form-item label="活动焦点">
      <el-select v-model="draft.activity_focus" style="width: 180px">
        <el-option label="猫咪主活动" value="cat_lead" />
        <el-option label="人物主活动" value="person_lead" />
        <el-option label="人猫平衡" value="balanced" />
      </el-select>
      <el-input-number v-model="draft.duration_seconds" :min="8" :max="45" style="margin-left: 12px" />
      <span class="muted" style="margin-left: 8px">秒 · {{ durationBand }}</span>
    </el-form-item>
    <el-form-item label="主活动">
      <el-input v-model="draft.relationship_arc.lead_activity" type="textarea" :rows="1" />
    </el-form-item>
    <el-form-item label="副活动">
      <el-input v-model="draft.relationship_arc.secondary_activity" type="textarea" :rows="1" />
    </el-form-item>
    <el-form-item label="关系汇合">
      <el-input v-model="draft.relationship_arc.convergence" type="textarea" :rows="1" />
    </el-form-item>
    <el-form-item label="人物定妆">
      <el-input v-model="draft.appearance.description" type="textarea" :rows="2" />
    </el-form-item>

    <el-divider content-position="left">动作阶段</el-divider>
    <el-form-item v-for="action in draft.actions" :key="action.order" :label="`动作${action.order}`">
      <el-select v-model="action.actor_id" style="width: 105px">
        <el-option label="猫咪" value="cat" />
        <el-option label="人物" value="person" />
        <el-option label="环境" value="environment" />
        <el-option v-if="draft.guest" :label="draft.guest.name" :value="draft.guest.id" />
      </el-select>
      <el-input v-model="action.action" type="textarea" :rows="1" style="margin-top: 5px" />
      <el-input v-model="action.visible_result" type="textarea" :rows="1" placeholder="动作后的可见结果" style="margin-top: 5px" />
    </el-form-item>

    <el-divider content-position="left">文字镜头设计</el-divider>
    <el-form-item v-for="shot in draft.shots" :key="shot.order" :label="`镜头${shot.order}`">
      <div style="display: flex; gap: 8px; width: 100%">
        <el-input v-model="shot.framing" placeholder="景别/机位" style="width: 180px" />
        <el-select v-model="shot.camera_move" style="width: 130px">
          <el-option label="固定" value="fixed" />
          <el-option label="跟拍" value="follow" />
          <el-option label="推近" value="push" />
          <el-option label="拉远" value="pull" />
          <el-option label="摇摄" value="pan" />
          <el-option label="横移" value="track" />
        </el-select>
        <span class="muted">动作 {{ shot.action_orders.join(", ") }}</span>
      </div>
      <el-input v-model="shot.direction" type="textarea" :rows="2" placeholder="主体位置、动作路径、结果与稳定切点" style="margin-top: 5px" />
    </el-form-item>

    <el-form-item label="结尾回报">
      <el-input v-model="draft.ending.result" type="textarea" :rows="2" />
    </el-form-item>
    <el-form-item label="声音设计">
      <el-input v-model="draft.sound_design" type="textarea" :rows="2" />
    </el-form-item>
    <el-alert v-if="errors.length" type="error" :closable="false" style="margin-bottom: 10px">
      <div v-for="(item, index) in errors" :key="index">{{ item }}</div>
    </el-alert>
    <el-form-item v-if="editable">
      <el-button type="primary" :loading="saving" @click="save">保存时段脚本</el-button>
      <span class="muted" style="margin-left: 10px">长片会按8–15秒区段使用官方视频延展。</span>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.muted { color: #8a8f99; font-size: 12px; line-height: 32px; }
</style>
