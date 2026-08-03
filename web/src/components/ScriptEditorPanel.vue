<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, reactive, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { EpisodeDto } from "../api/types";

const props = defineProps<{
  episode: EpisodeDto;
  /** 只有尚未进入收费生产的脚本才允许编辑。 */
  editable: boolean;
}>();
const emit = defineEmits<{ saved: [] }>();

interface ActionDraft {
  order: number;
  action: string;
  visible_result: string;
}

interface ShotDraft {
  order: number;
  framing: string;
  direction: string;
}

const draft = reactive({
  title: "",
  main_event: "",
  scene: "",
  ending: "",
  ending_visual_critical: false,
  duration_seconds: 9,
  style_context: "indoor" as "indoor" | "outdoor",
  actions: [] as ActionDraft[],
  shots: [] as ShotDraft[],
});

function reset() {
  const script = props.episode.script;
  draft.title = script.title;
  draft.main_event = script.main_event;
  draft.scene = script.scene;
  draft.ending = script.ending.result;
  draft.ending_visual_critical = script.ending.visual_critical;
  draft.duration_seconds = script.duration_seconds;
  draft.style_context = script.style_context;
  draft.actions = script.actions.map((item) => ({
    order: item.order,
    action: item.action,
    visible_result: item.visible_result,
  }));
  draft.shots = script.shots.map((item) => ({
    order: item.order,
    framing: item.framing,
    direction: item.direction,
  }));
}
watch(() => props.episode.id, reset, { immediate: true });

const saving = ref(false);
const errors = ref<string[]>([]);

const readonlyMeta = computed(() => {
  const script = props.episode.script;
  return `事件键 ${script.event_key} · 地点键 ${script.location_key} · 服饰 ${script.appearance.description}`;
});

/** 合并可编辑字段后整体提交，连续性账本等未展示字段保持原样。 */
async function save() {
  saving.value = true;
  errors.value = [];
  try {
    const script = props.episode.script;
    const payload = {
      ...script,
      title: draft.title,
      main_event: draft.main_event,
      scene: draft.scene,
      ending: {
        ...script.ending,
        result: draft.ending,
        visual_critical: draft.ending_visual_critical,
      },
      duration_seconds: draft.duration_seconds,
      style_context: draft.style_context,
      actions: script.actions.map((item) => {
        const edited = draft.actions.find((action) => action.order === item.order);
        return edited ? { ...item, ...edited } : item;
      }),
      shots: script.shots.map((item) => {
        const edited = draft.shots.find((shot) => shot.order === item.order);
        return edited ? { ...item, ...edited } : item;
      }),
    };
    const result = await api.updateScript(props.episode.id, payload);
    ElMessage.success(
      result.promptOverridesKept
        ? "剧本已保存；现有 Prompt 覆盖仍然保留，请确认其是否仍适用"
        : "剧本已保存，下游故事板和视频 Prompt 将按新版本重新编译",
    );
    emit("saved");
  } catch (error) {
    if (error instanceof ApiError) {
      const detail = error.detail as { errors?: Array<{ msg?: string }> };
      errors.value = Array.isArray(detail?.errors)
        ? detail.errors.map((item) => String(item.msg ?? item))
        : [error.message];
    } else {
      errors.value = [String(error)];
    }
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <el-form label-width="96px" :disabled="!editable" size="small">
    <el-form-item label="标题"><el-input v-model="draft.title" /></el-form-item>
    <el-form-item label="主事件">
      <el-input v-model="draft.main_event" :rows="2" type="textarea" />
    </el-form-item>
    <el-form-item label="场景">
      <el-input v-model="draft.scene" :rows="2" type="textarea" />
    </el-form-item>
    <el-form-item label="场景/时长">
      <el-radio-group v-model="draft.style_context">
        <el-radio value="indoor">室内</el-radio>
        <el-radio value="outdoor">室外</el-radio>
      </el-radio-group>
      <el-input-number v-model="draft.duration_seconds" :min="8" :max="15" style="margin-left: 12px" />
      <span class="muted" style="margin-left: 6px">秒</span>
    </el-form-item>
    <el-form-item v-for="action in draft.actions" :key="action.order" :label="`动作${action.order}`">
      <el-input v-model="action.action" :rows="1" type="textarea" />
      <el-input v-model="action.visible_result" :rows="1" type="textarea" placeholder="可见结果" style="margin-top: 4px" />
    </el-form-item>
    <el-form-item v-for="shot in draft.shots" :key="shot.order" :label="`镜头${shot.order}`">
      <el-input v-model="shot.framing" placeholder="景别" style="width: 120px" />
      <el-input v-model="shot.direction" :rows="1" type="textarea" placeholder="镜头指引" style="margin-top: 4px" />
    </el-form-item>
    <el-form-item label="结尾结果">
      <el-input v-model="draft.ending" :rows="2" type="textarea" />
      <el-checkbox v-model="draft.ending_visual_critical" style="margin-top: 6px">
        结尾画面必须精确（视频仅使用首张和末张故事板作为严格帧输入）
      </el-checkbox>
    </el-form-item>
    <el-form-item label="只读信息"><span class="muted">{{ readonlyMeta }}</span></el-form-item>
    <el-alert v-if="errors.length" type="error" :closable="false" style="margin-bottom: 10px">
      <div v-for="(item, index) in errors" :key="index">{{ item }}</div>
    </el-alert>
    <el-form-item v-if="editable">
      <el-button type="primary" :loading="saving" @click="save">保存剧本编辑</el-button>
      <span class="muted" style="margin-left: 10px">保存后已有故事板将因输入哈希变化而不再复用。</span>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.muted { color: #8a8f99; font-size: 12px; }
</style>
