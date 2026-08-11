<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { api, ApiError } from "../api/client";
import type {
  ActivityFocusMode,
  DurationMode,
  HealthStatus,
  JobAccepted,
  PlanningMode,
  SceneRoute,
  Slot,
  StoryInputMode,
  StoryProjectPreview,
} from "../api/types";
import { useJobsStore } from "../stores/jobs";

const emit = defineEmits<{ created: [] }>();
const router = useRouter();
const jobs = useJobsStore();
const SLOT_LABEL: Record<Slot, string> = { morning: "上午", noon: "中午", evening: "傍晚" };

const visible = ref(false);
const submitting = ref(false);
const previewing = ref(false);
const health = ref<HealthStatus | null>(null);
const preview = ref<StoryProjectPreview | null>(null);
const form = reactive({
  contentDate: "",
  theme: "",
  inputMode: "theme_expand" as StoryInputMode,
  sceneRoute: "adaptive" as SceneRoute,
  scriptInputLayout: "batch" as "batch" | "separate",
  fullScriptText: "",
  episodeSources: { morning: "", noon: "", evening: "" } as Record<Slot, string>,
  personPersonality: "",
  catPersonality: "",
  humorStyle: "",
  allowPaidGeneration: false,
  planningMode: "guided_sequential" as PlanningMode,
  autoVisual: false,
  autoVideo: false,
  defaultActivityFocus: "cat_lead" as Exclude<ActivityFocusMode, "inherit">,
  slotControls: (["morning", "noon", "evening"] as Slot[]).map((slot) => ({
    slot,
    activityFocus: "inherit" as ActivityFocusMode,
    durationMode: "adaptive" as DurationMode,
  })),
});

const requiresPaidAuthorization = computed(
  () => form.inputMode === "theme_expand" || form.planningMode === "auto_day",
);
const populatedSources = computed(() =>
  (Object.keys(form.episodeSources) as Slot[]).filter(
    (slot) => form.episodeSources[slot].trim().length >= 4,
  ),
);
const scriptsValid = computed(() => {
  if (form.inputMode !== "episode_scripts") return true;
  if (form.planningMode === "auto_day") return populatedSources.value.length === 3;
  return populatedSources.value.length >= 1;
});
const batchPreviewValid = computed(
  () => form.inputMode !== "episode_scripts"
    || form.scriptInputLayout === "separate"
    || preview.value?.canConfirm === true,
);
const canSubmit = computed(() =>
  Boolean(form.contentDate && form.theme.trim().length >= 2)
  && scriptsValid.value
  && batchPreviewValid.value
  && (!requiresPaidAuthorization.value || form.allowPaidGeneration),
);

async function open() {
  form.allowPaidGeneration = false;
  preview.value = null;
  visible.value = true;
  try {
    health.value = await api.health();
  } catch {
    health.value = null;
  }
}

async function previewScripts() {
  if (form.fullScriptText.trim().length < 8) return;
  previewing.value = true;
  try {
    const result = await api.previewStoryProject({
      text: form.fullScriptText.trim(),
    });
    preview.value = result;
    form.theme = result.theme;
    for (const slot of ["morning", "noon", "evening"] as Slot[]) {
      form.episodeSources[slot] = result.episodeSources[slot] ?? "";
    }
    if (result.canConfirm) {
      ElMessage.success("已拆分三集，请确认后创建项目");
    } else {
      ElMessage.warning("拆分结果需要补充");
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    previewing.value = false;
  }
}

async function submit() {
  if (!canSubmit.value) return;
  submitting.value = true;
  try {
    const allowPaid = requiresPaidAuthorization.value && form.allowPaidGeneration;
    const created = await api.createProject({
      contentDate: form.contentDate,
      projectInput: {
        theme: form.theme.trim(),
        input_mode: form.inputMode,
        scene_route: form.sceneRoute,
        episode_sources: form.inputMode === "theme_expand"
          ? { morning: null, noon: null, evening: null }
          : {
              morning: form.episodeSources.morning.trim() || null,
              noon: form.episodeSources.noon.trim() || null,
              evening: form.episodeSources.evening.trim() || null,
            },
      },
      creativeProfile: {
        personPersonality: form.personPersonality || undefined,
        catPersonality: form.catPersonality || undefined,
        humorStyle: form.humorStyle || undefined,
      },
      allowPaidGeneration: allowPaid,
      pipelineSettings: {
        planningMode: form.planningMode,
        allowPaidGeneration: allowPaid,
        projectOutline: form.inputMode === "theme_expand" && form.planningMode === "guided_sequential"
          ? "manual"
          : "auto",
        script: form.planningMode === "guided_sequential" ? "manual" : "auto",
        visual: form.planningMode === "guided_sequential"
          ? "manual"
          : form.autoVisual ? "auto" : "manual",
        video: form.planningMode === "guided_sequential"
          ? "manual"
          : form.autoVisual && form.autoVideo ? "auto" : "manual",
        review: "manual",
      },
      creativeControls: {
        default_activity_focus: form.defaultActivityFocus,
        slot_controls: form.slotControls.map((item) => ({
          slot: item.slot,
          activity_focus: item.activityFocus,
          duration_mode: item.durationMode,
        })),
      },
    });
    jobs.track(created);
    ElMessage.info("项目创建任务已提交…");
    const final = await waitJob(created);
    const runId = typeof final.result?.runId === "string" ? final.result.runId : undefined;
    if (!runId) throw new Error(final.error?.message ?? "项目创建失败");
    visible.value = false;
    emit("created");
    ElMessage.success("生活故事项目已创建");
    const stage = form.inputMode === "episode_scripts" ? "script" : "projectOutline";
    await router.push(`/studio?run=${runId}&stage=${stage}`);
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    submitting.value = false;
  }
}

async function waitJob(accepted: JobAccepted) {
  for (;;) {
    const job = await api.job(accepted.jobId);
    jobs.byDedupKey[job.dedupKey] = job;
    if (job.status === "succeeded" || job.status === "failed") return job;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

defineExpose({ open });
</script>

<template>
  <el-dialog v-model="visible" title="新建生活故事项目" width="760px">
    <el-form label-position="top">
      <div class="form-grid">
        <el-form-item label="内容日期" required>
          <el-date-picker v-model="form.contentDate" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="规划方式">
          <el-radio-group v-model="form.planningMode">
            <el-radio-button value="guided_sequential">顺序逐集确认</el-radio-button>
            <el-radio-button value="auto_day">全自动三集</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </div>

      <el-form-item label="剧情来源">
        <el-radio-group v-model="form.inputMode" @change="preview = null">
          <el-radio-button value="theme_expand">只给主题，由AI扩写</el-radio-button>
          <el-radio-button value="episode_scripts">输入已有剧本</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="全天主题" required>
        <el-input v-model="form.theme" placeholder="例如：出去钓鱼、春日放风筝" />
      </el-form-item>
      <el-form-item label="场景路线">
        <el-select v-model="form.sceneRoute" style="width: 100%">
          <el-option label="自适应（出游主题优先关联多场景）" value="adaptive" />
          <el-option label="关联多场景：准备 → 主活动 → 收束/归家" value="progressive_locations" />
          <el-option label="单地点阶段递进" value="single_location" />
        </el-select>
      </el-form-item>

      <template v-if="form.inputMode === 'episode_scripts'">
        <el-form-item label="剧本输入方式">
          <el-radio-group v-model="form.scriptInputLayout">
            <el-radio-button value="batch">一次粘贴三集</el-radio-button>
            <el-radio-button value="separate">分别编辑</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.scriptInputLayout === 'batch'" label="完整三集文本">
          <el-input
            v-model="form.fullScriptText"
            type="textarea"
            :rows="8"
            placeholder="粘贴主题与剧本1/剧本2/剧本3；预览只做本地结构拆分，不调用Ark。"
            @input="preview = null"
          />
          <el-button :loading="previewing" style="margin-top: 8px" @click="previewScripts">
            拆分并预览
          </el-button>
        </el-form-item>
        <el-alert
          v-if="preview?.issues.length"
          type="warning"
          :closable="false"
          :title="preview.issues.join('；')"
          style="margin-bottom: 12px"
        />
        <el-form-item
          v-for="slot in (['morning', 'noon', 'evening'] as Slot[])"
          :key="slot"
          :label="`${SLOT_LABEL[slot]}原始剧本`"
        >
          <el-input
            v-model="form.episodeSources[slot]"
            type="textarea"
            :rows="5"
            :placeholder="form.planningMode === 'guided_sequential' ? '可暂时留空，后续在该时段节点补充' : '全自动模式必须填写'"
          />
        </el-form-item>
      </template>
      <el-alert
        v-else
        type="info"
        :closable="false"
        title="主题扩写会调用一次总导演"
        description="总导演只规划三时段场景方向；具体剧情与镜头仍由各时段导演生成。"
        style="margin-bottom: 12px"
      />

      <el-form-item label="全天活动焦点">
        <el-select v-model="form.defaultActivityFocus" style="width: 100%">
          <el-option label="猫咪主活动（默认）" value="cat_lead" />
          <el-option label="人物主活动" value="person_lead" />
          <el-option label="人猫平衡" value="balanced" />
          <el-option label="自适应" value="adaptive" />
        </el-select>
      </el-form-item>
      <el-form-item label="时段焦点与时长">
        <div class="slot-controls">
          <div v-for="control in form.slotControls" :key="control.slot" class="slot-control">
            <span>{{ SLOT_LABEL[control.slot] }}</span>
            <el-select v-model="control.activityFocus">
              <el-option label="继承全天" value="inherit" />
              <el-option label="猫咪主活动" value="cat_lead" />
              <el-option label="人物主活动" value="person_lead" />
              <el-option label="人猫平衡" value="balanced" />
              <el-option label="自适应" value="adaptive" />
            </el-select>
            <el-select v-model="control.durationMode">
              <el-option label="自适应" value="adaptive" />
              <el-option label="短 8–15秒" value="short" />
              <el-option label="中 16–30秒" value="medium" />
              <el-option label="长 31–45秒" value="long" />
            </el-select>
          </div>
        </div>
      </el-form-item>
      <el-alert
        v-if="health && !health.supportsVideoExtension && form.slotControls.some((item) => ['medium', 'long'].includes(item.durationMode))"
        type="warning"
        :closable="false"
        title="当前模型不支持视频延展；中长视频会在收费前被阻断。"
        style="margin-bottom: 12px"
      />

      <el-collapse>
        <el-collapse-item title="性格与自动推进（可选）" name="advanced">
          <el-input v-model="form.personPersonality" placeholder="人物性格" />
          <el-input v-model="form.catPersonality" placeholder="猫咪性格" style="margin-top: 8px" />
          <el-input v-model="form.humorStyle" placeholder="幽默方式" style="margin-top: 8px" />
          <template v-if="form.planningMode === 'auto_day'">
            <el-checkbox v-model="form.autoVisual" style="margin-top: 10px">自动生成视觉锚点</el-checkbox>
            <el-checkbox v-model="form.autoVideo" :disabled="!form.autoVisual">自动生成视频</el-checkbox>
          </template>
        </el-collapse-item>
      </el-collapse>

      <el-form-item v-if="requiresPaidAuthorization" style="margin-top: 14px">
        <el-checkbox v-model="form.allowPaidGeneration">
          <span style="color: #f56c6c">
            我确认本次{{ form.inputMode === "theme_expand" ? "总导演" : "三集时段导演" }}会产生Ark费用
          </span>
        </el-checkbox>
      </el-form-item>
      <el-alert
        v-else
        type="success"
        :closable="false"
        title="创建项目不调用Ark"
        description="已有剧本的顺序模式只保存项目和原文；规划当前时段时再单独确认费用。"
        style="margin-top: 14px"
      />
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="!canSubmit" :loading="submitting" @click="submit">
        创建生活故事项目
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.slot-controls { display: grid; width: 100%; gap: 8px; }
.slot-control { display: grid; grid-template-columns: 56px 1fr 1fr; align-items: center; gap: 8px; }
</style>
