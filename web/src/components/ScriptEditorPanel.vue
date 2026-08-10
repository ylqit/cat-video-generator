<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onBeforeUnmount, reactive, ref, toRaw, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { EpisodeDto, EpisodePromptPreview, EpisodeScript } from "../api/types";

const props = defineProps<{ episode: EpisodeDto; editable: boolean }>();
const emit = defineEmits<{ saved: [] }>();
// Pinia中的Episode会被Vue代理；structuredClone不能直接复制Proxy。
// 编辑器只处理脱离响应式系统的业务快照，保存时再显式提交该快照。
const draft = reactive<EpisodeScript>(structuredClone(toRaw(props.episode.script)));
const loaded = ref("");
const saving = ref(false);
const errors = ref<string[]>([]);
const promptPreview = ref<EpisodePromptPreview | null>(null);
const previewLoading = ref(false);
const previewError = ref("");
let previewTimer: number | undefined;

function snapshot() { return JSON.stringify(draft); }
function reset() {
  Object.assign(draft, structuredClone(toRaw(props.episode.script)));
  loaded.value = snapshot();
}

function addShot() {
  if (draft.shots.length >= 3) return;
  draft.shots.push({
    order: draft.shots.length + 1,
    direction: "说明本镜头的景别、机位、唯一运镜、角色站位、动作路径、可见结果和稳定切点。",
  });
}

function removeShot(index: number) {
  if (draft.shots.length <= 1) return;
  const removedOrder = draft.shots[index].order;
  draft.shots.splice(index, 1);
  draft.shots.forEach((shot, shotIndex) => { shot.order = shotIndex + 1; });
  draft.hard_constraints.forEach((constraint) => {
    constraint.shot_orders = constraint.shot_orders
      .filter((order) => order !== removedOrder)
      .map((order) => order > removedOrder ? order - 1 : order);
  });
}

function addConstraint() {
  draft.hard_constraints.push({
    shot_orders: [],
    text: "只写一个会直接影响成片正确性的关系事实，以及不得发生的错误连接。",
  });
}

function removeConstraint(index: number) {
  draft.hard_constraints.splice(index, 1);
}

watch(
  () => props.episode.script,
  () => {
    if (!loaded.value || snapshot() === loaded.value) reset();
  },
  { deep: true, immediate: true },
);

function schedulePreview() {
  window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(() => void refreshPromptPreview(), 600);
}

async function refreshPromptPreview() {
  previewLoading.value = true;
  previewError.value = "";
  try {
    promptPreview.value = await api.previewScriptPrompts(
      props.episode.id,
      structuredClone(toRaw(draft)),
    );
  } catch (error) {
    promptPreview.value = null;
    previewError.value = error instanceof ApiError
      ? "当前内容尚未满足编译条件；补全必填文本或镜头后会自动刷新"
      : String(error);
  } finally {
    previewLoading.value = false;
  }
}

watch(draft, schedulePreview, { deep: true, immediate: true });
onBeforeUnmount(() => window.clearTimeout(previewTimer));

const durationBand = computed(() =>
  draft.duration_seconds <= 15 ? "短片" : draft.duration_seconds <= 30 ? "中片·一次延展" : "长片·两次延展",
);

async function save() {
  saving.value = true;
  errors.value = [];
  try {
    const result = await api.updateScript(props.episode.id, structuredClone(toRaw(draft)));
    loaded.value = snapshot();
    ElMessage.success(
      result.promptOverrideStale
        ? "剧本已保存；旧的高级Prompt覆盖已失效，请重新检查后确认"
        : "剧本已保存，视觉锚点和视频Prompt会按新内容编译",
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
    <el-form-item label="事件键"><el-input v-model="draft.event_key" /></el-form-item>
    <el-form-item label="地点键"><el-input v-model="draft.location_key" /></el-form-item>
    <el-form-item label="视觉环境">
      <el-radio-group v-model="draft.visual_context">
        <el-radio-button value="indoor">室内</el-radio-button>
        <el-radio-button value="outdoor">户外</el-radio-button>
      </el-radio-group>
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
    <el-form-item label="本集外观">
      <el-input v-model="draft.appearance" type="textarea" :rows="3" />
    </el-form-item>
    <el-form-item label="完整剧情">
      <el-input
        v-model="draft.story_text"
        type="textarea"
        :rows="10"
        placeholder="用自然长文本完整描述本集发生什么、因果如何推进以及观众最终看到什么。"
      />
    </el-form-item>
    <el-form-item label="关系弧">
      <el-input
        v-model="draft.relationship_arc"
        type="textarea"
        :rows="3"
        placeholder="用一段文字说明猫咪主活动、人物回应以及两条活动线如何汇合。"
      />
    </el-form-item>

    <el-divider content-position="left">文字镜头设计</el-divider>
    <el-form-item v-for="(shot, index) in draft.shots" :key="shot.order" :label="`镜头${shot.order}`">
      <el-input
        v-model="shot.direction"
        type="textarea"
        :rows="5"
        placeholder="完整写出景别、机位、唯一运镜、角色站位、实际动作主体、肢体路径、接触对象、可见结果和稳定切点。"
      />
      <el-button v-if="editable && draft.shots.length > 1" type="danger" text @click="removeShot(index)">删除镜头</el-button>
    </el-form-item>
    <el-form-item v-if="editable && draft.shots.length < 3">
      <el-button plain @click="addShot">增加镜头</el-button>
    </el-form-item>

    <el-divider content-position="left">关键硬约束</el-divider>
    <el-form-item
      v-for="(constraint, index) in draft.hard_constraints"
      :key="index"
      :label="`约束${index + 1}`"
    >
      <div style="display: flex; gap: 8px; width: 100%">
        <el-select v-model="constraint.shot_orders" multiple collapse-tags placeholder="空=全片" style="width: 180px">
          <el-option v-for="shot in draft.shots" :key="shot.order" :label="`镜头${shot.order}`" :value="shot.order" />
        </el-select>
        <el-button type="danger" text @click="removeConstraint(index)">删除</el-button>
      </div>
      <el-input
        v-model="constraint.text"
        type="textarea"
        :rows="2"
        placeholder="只登记连接、承重、容器、穿戴或交接等真正影响正确性的关系。"
        style="margin-top: 5px"
      />
    </el-form-item>
    <el-form-item v-if="editable">
      <el-button plain @click="addConstraint">增加关键约束</el-button>
      <span class="muted" style="margin-left: 8px">普通走动、视线、姿势和背景变化不需要登记。</span>
    </el-form-item>

    <el-form-item label="声音设计">
      <el-input v-model="draft.sound_design" type="textarea" :rows="3" />
    </el-form-item>
    <el-form-item label="结尾回报">
      <el-input v-model="draft.ending" type="textarea" :rows="3" />
    </el-form-item>

    <el-form-item label="Prompt预览">
      <el-collapse v-loading="previewLoading" style="width: 100%">
        <el-collapse-item title="按当前长剧情和镜头实时编译（不保存、不调用Ark）" name="compiled-prompts">
          <el-alert
            v-if="previewError"
            type="info"
            :closable="false"
            :title="previewError"
            style="margin-bottom: 8px"
          />
          <template v-if="promptPreview">
            <div class="preview-title">定妆图 Prompt</div>
            <pre class="prompt-preview">{{ promptPreview.look }}</pre>
            <div class="preview-title">开场视觉锚点 Prompt</div>
            <pre class="prompt-preview">{{ promptPreview.openingAnchor }}</pre>
            <template v-for="section in promptPreview.videoSections" :key="section.order">
              <div class="preview-title">视频区段{{ section.order }} Prompt · {{ section.durationSeconds }}秒</div>
              <pre class="prompt-preview">{{ section.prompt }}</pre>
            </template>
          </template>
        </el-collapse-item>
      </el-collapse>
    </el-form-item>
    <el-alert v-if="errors.length" type="error" :closable="false" style="margin-bottom: 10px">
      <div v-for="(item, index) in errors" :key="index">{{ item }}</div>
    </el-alert>
    <el-form-item v-if="editable">
      <el-button type="primary" :loading="saving" @click="save">保存时段脚本</el-button>
      <span class="muted" style="margin-left: 10px">长片会按8～15秒区段使用官方视频延展。</span>
    </el-form-item>
  </el-form>
</template>

<style scoped>
.muted { color: #8a8f99; font-size: 12px; line-height: 32px; }
.preview-title { margin: 10px 0 4px; color: #aab2bf; font-size: 12px; }
.prompt-preview {
  margin: 0;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  background: #111318;
  border-radius: 6px;
  padding: 10px;
  color: #cbd5e1;
  font-size: 12px;
  line-height: 1.6;
}
</style>
