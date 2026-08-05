<script setup lang="ts">
import { ElMessage } from "element-plus";
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { api, ApiError } from "../api/client";
import { useJobsStore } from "../stores/jobs";

const emit = defineEmits<{ created: [] }>();
const router = useRouter();
const jobs = useJobsStore();

const visible = ref(false);
const submitting = ref(false);
const form = reactive({
  targetDate: "",
  planningContext: "",
  personPersonality: "",
  catPersonality: "",
  humorStyle: "",
  allowPaidGeneration: false,
});

function open() {
  form.allowPaidGeneration = false;
  visible.value = true;
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
    });
    jobs.track(accepted);
    visible.value = false;
    ElMessage.info("规划任务已提交，正在等待导演输出…");
    const final = await waitJob(accepted.jobId);
    if (final.status === "succeeded" && final.result?.runId) {
      ElMessage.success("全天方案已生成");
      emit("created");
      router.push(`/runs/${final.result.runId}`);
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
