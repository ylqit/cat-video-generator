<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, reactive, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { EpisodeDto } from "../api/types";

const props = defineProps<{
  episode: EpisodeDto;
  /** 仅planned/failed状态可编辑，由父级按状态传入 */
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
  draft.ending = script.ending;
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
  return `事件键 ${script.event_key} · 地点键 ${script.location_key} · ` +
    `服饰 ${script.appearance.description} · 输入模式 ${script.video_input_mode}`;
});

/** 以读出的完整剧本合并编辑字段整体提交，嵌套结构原样保留。 */
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
      ending: draft.ending,
      duration_seconds: draft.duration_seconds,
      style_context: draft.style_context,
      actions: script.actions.map((item) => {
        const edited = draft.actions.find((a) => a.order === item.order);
        return edited ? { ...item, ...edited } : item;
      }),
      shots: script.shots.map((item) => {
        const edited = draft.shots.find((s) => s.order === item.order);
        return edited ? { ...item, ...edited } : item;
      }),
    };
    const result = await api.updateScript(props.episode.id, payload);
    ElMessage.success(
      result.promptOverridesKept
        ? "剧本已保存；注意原Prompt覆盖基于旧剧本，请检查"
        : "剧本已保存，下游Prompt将按新版重新编译",
    );
    emit("saved");
  } catch (error) {
    if (error instanceof ApiError) {
      const detail = error.detail as { errors?: Array<{ msg?: string }> };
      if (Array.isArray(detail?.errors)) {
        errors.value = detail.errors.map((item) => String(item.msg ?? item));
      } else {
        errors.value = [error.message];
      }
    } else {
      errors.value = [String(error)];
    }
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <el-form label-width="90px" :disabled="!editable" size="small">
    <el-form-item label="标题">
      <el-input v-model="draft.title" />
    </el-form-item>
    <el-form-item label="主事件">
      <el-input v-model="draft.main_event" :rows="2" type="textarea" />
    </el-form-item>
    <el-form-item label="场景">
      <el-input v-model="draft.scene" :rows="2" type="textarea" />
    </el-form-item>
    <el-form-item label="情境/时长">
      <el-radio-group v-model="draft.style_context">
        <el-radio value="indoor">室内</el-radio>
        <el-radio value="outdoor">室外</el-radio>
      </el-radio-group>
      <el-input-number
        v-model="draft.duration_seconds"
        :min="8"
        :max="15"
        style="margin-left: 12px"
      />
      <span class="muted" style="margin-left: 6px">秒</span>
    </el-form-item>
    <el-form-item
      v-for="action in draft.actions"
      :key="action.order"
      :label="`动作${action.order}`"
    >
      <el-input v-model="action.action" :rows="1" type="textarea" />
      <el-input
        v-model="action.visible_result"
        :rows="1"
        type="textarea"
        placeholder="可见结果"
        style="margin-top: 4px"
      />
    </el-form-item>
    <el-form-item
      v-for="shot in draft.shots"
      :key="shot.order"
      :label="`镜头${shot.order}`"
    >
      <el-input v-model="shot.framing" placeholder="景别" style="width: 120px" />
      <el-input
        v-model="shot.direction"
        :rows="1"
        type="textarea"
        placeholder="镜头指引"
        style="margin-top: 4px"
      />
    </el-form-item>
    <el-form-item label="结尾">
      <el-input v-model="draft.ending" :rows="2" type="textarea" />
    </el-form-item>
    <el-form-item label="只读锚点">
      <span class="muted">{{ readonlyMeta }}</span>
    </el-form-item>
    <el-alert
      v-if="errors.length"
      type="error"
      :closable="false"
      style="margin-bottom: 10px"
    >
      <div v-for="(item, index) in errors" :key="index">{{ item }}</div>
    </el-alert>
    <el-form-item v-if="editable">
      <el-button type="primary" :loading="saving" @click="save">
        保存剧本编辑
      </el-button>
      <span class="muted" style="margin-left: 10px">
        保存后已生成的首末帧将因内容变化需要重新生成
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
