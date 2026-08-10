<script setup lang="ts">
import { ElMessage } from "element-plus";
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { api, ApiError } from "../api/client";
import type {
  ActivityFocusMode,
  DurationMode,
  HealthStatus,
  PlanningMode,
  Slot,
} from "../api/types";
import { useJobsStore } from "../stores/jobs";

const emit = defineEmits<{ created: [] }>();
const router = useRouter();
const jobs = useJobsStore();

const visible = ref(false);
const submitting = ref(false);
const health = ref<HealthStatus | null>(null);
const form = reactive({
  targetDate: "",
  planningContext: "",
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

async function open() {
  form.allowPaidGeneration = false;
  visible.value = true;
  try {
    health.value = await api.health();
  } catch {
    health.value = null;
  }
}

/** 提交规划并跟踪后台任务；成功后跳转到新Run详情。 */
async function submit() {
  if (!form.targetDate || !form.allowPaidGeneration) {
    return;
  }
  submitting.value = true;
  try {
    const accepted = await api.createPlan({
      targetDate: form.targetDate,
      planningContext: form.planningContext || undefined,
      creativeProfile: {
        personPersonality: form.personPersonality || undefined,
        catPersonality: form.catPersonality || undefined,
        humorStyle: form.humorStyle || undefined,
      },
      allowPaidGeneration: true,
      pipelineSettings: {
        planningMode: form.planningMode,
        allowPaidGeneration: true,
        dayBrief: form.planningMode === "guided_sequential" ? "manual" : "auto",
        script: form.planningMode === "guided_sequential" ? "manual" : "auto",
        visual:
          form.planningMode === "guided_sequential"
            ? "manual"
            : form.autoVisual
              ? "auto"
              : "manual",
        video:
          form.planningMode === "guided_sequential"
            ? "manual"
            : form.autoVisual && form.autoVideo
              ? "auto"
              : "manual",
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
    jobs.track(accepted);
    visible.value = false;
    ElMessage.info("规划任务已提交，正在等待导演输出…");
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded" && final.result?.runId) {
      ElMessage.success("全天方案已生成");
      emit("created");
      router.push(`/studio?run=${final.result.runId}&stage=dayBrief`);
    } else {
      ElMessage.error(final.error?.message ?? "规划任务失败");
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    submitting.value = false;
  }
}

async function waitJob(jobId: string) {
  for (;;) {
    const job = await api.job(jobId);
    jobs.byDedupKey[job.dedupKey] = job;
    if (job.status === "succeeded" || job.status === "failed") {
      return job;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

defineExpose({ open });
</script>

<template>
  <el-dialog v-model="visible" title="新建全天计划" width="520px">
    <el-form label-width="110px">
      <el-form-item label="内容日期" required>
        <el-date-picker
          v-model="form.targetDate"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="选择日期"
        />
      </el-form-item>
      <el-form-item label="规划上下文">
        <el-input
          v-model="form.planningContext"
          type="textarea"
          :rows="3"
          placeholder="留空则使用默认：根据日期、天气和角色习惯设计自然的一天。"
        />
      </el-form-item>
      <el-form-item>
        <el-collapse style="width: 100%">
          <el-collapse-item title="性格与幽默（可选）" name="creative-profile">
            <el-input v-model="form.personPersonality" placeholder="人物性格；留空使用系列默认" />
            <el-input v-model="form.catPersonality" placeholder="猫咪性格；留空使用系列默认" style="margin-top: 8px" />
            <el-input v-model="form.humorStyle" placeholder="幽默方式；留空使用系列默认" style="margin-top: 8px" />
          </el-collapse-item>
        </el-collapse>
      </el-form-item>
      <el-form-item label="全天活动焦点">
        <el-select v-model="form.defaultActivityFocus" style="width: 100%">
          <el-option label="猫咪主活动（默认）" value="cat_lead" />
          <el-option label="人物主活动" value="person_lead" />
          <el-option label="人猫平衡" value="balanced" />
          <el-option label="总导演自适应" value="adaptive" />
        </el-select>
      </el-form-item>
      <el-form-item label="规划方式">
        <el-radio-group v-model="form.planningMode">
          <el-radio-button value="guided_sequential">顺序人工确认</el-radio-button>
          <el-radio-button value="auto_day">全自动全天</el-radio-button>
        </el-radio-group>
        <div class="muted" style="width: 100%; margin-top: 6px">
          顺序模式会在每个时段成片批准并确认实际结果后，才解锁下一时段导演。
        </div>
      </el-form-item>
      <el-form-item label="时段覆盖">
        <div style="width: 100%">
          <div
            v-for="control in form.slotControls"
            :key="control.slot"
            style="display: grid; grid-template-columns: 58px 1fr 1fr; gap: 8px; margin-bottom: 8px"
          >
            <span class="muted" style="line-height: 32px">{{
              { morning: "上午", noon: "中午", evening: "傍晚" }[control.slot]
            }}</span>
            <el-select v-model="control.activityFocus">
              <el-option label="继承全天" value="inherit" />
              <el-option label="猫咪主活动" value="cat_lead" />
              <el-option label="人物主活动" value="person_lead" />
              <el-option label="人猫平衡" value="balanced" />
              <el-option label="自适应" value="adaptive" />
            </el-select>
            <el-select v-model="control.durationMode">
              <el-option label="自适应时长" value="adaptive" />
              <el-option label="短 8–15秒" value="short" />
              <el-option label="中 16–30秒" value="medium" />
              <el-option label="长 31–45秒" value="long" />
            </el-select>
          </div>
          <div class="muted">总导演只解析自适应档；时段导演在档内决定精确秒数。</div>
          <el-alert
            v-if="health && !health.supportsVideoExtension && form.slotControls.some((item) => ['medium', 'long'].includes(item.durationMode))"
            type="warning"
            :closable="false"
            title="当前视频模型不支持官方延展；中片或长片可以先规划，但在收费视频任务前会被阻断。"
            style="margin-top: 8px"
          />
        </div>
      </el-form-item>
      <el-form-item label="推进方式">
        <div style="width: 100%">
          <template v-if="form.planningMode === 'guided_sequential'">
            <el-alert
              type="info"
              :closable="false"
              title="总导演后暂停；上午→结果卡→中午→结果卡→傍晚按顺序人工推进。"
            />
          </template>
          <template v-else>
            <el-checkbox v-model="form.autoVisual">三集导演完成后自动生成视觉锚点</el-checkbox>
            <el-checkbox v-model="form.autoVideo" :disabled="!form.autoVisual">视觉锚点通过后自动生成视频</el-checkbox>
            <div class="muted">关闭自动项后，可在统一工作台逐节点确认并付费生成。</div>
          </template>
        </div>
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="form.allowPaidGeneration">
          <span style="color: #f56c6c">
            我已知晓本次规划将产生 Ark 付费模型调用
          </span>
        </el-checkbox>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary"
        :disabled="!form.targetDate || !form.allowPaidGeneration"
        :loading="submitting"
        @click="submit"
      >
        提交规划
      </el-button>
    </template>
  </el-dialog>
</template>
