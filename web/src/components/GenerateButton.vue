<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, ref } from "vue";

import { api, ApiError } from "../api/client";
import { useJobsStore } from "../stores/jobs";

const props = defineProps<{
  runId: string;
  /** null 表示推进全天 */
  slot: "morning" | "noon" | "evening" | null;
  label?: string;
}>();
const emit = defineEmits<{ submitted: [] }>();

const jobs = useJobsStore();
const confirming = ref(false);
const paidConfirmed = ref(false);
const submitting = ref(false);

const dedupKey = computed(
  () => `run:${props.runId}:${props.slot ?? "all"}`,
);
const active = computed(() => jobs.isActive(dedupKey.value));

function ask() {
  paidConfirmed.value = false;
  confirming.value = true;
}

/** 付费确认后提交生成任务并登记到任务store。 */
async function confirm() {
  submitting.value = true;
  try {
    const accepted = await api.generate(props.runId, {
      slot: props.slot,
      allowPaidGeneration: true,
    });
    jobs.track(accepted);
    confirming.value = false;
    ElMessage.success("生成任务已提交");
    emit("submitted");
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      ElMessage.warning("相同任务正在执行，请等待完成");
    } else {
      ElMessage.error(
        error instanceof ApiError ? error.message : String(error),
      );
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <el-button
    type="primary"
    size="small"
    :loading="active"
    :disabled="active"
    @click="ask"
  >
    {{ active ? "生成中…" : (label ?? "生成视频") }}
  </el-button>
  <el-dialog v-model="confirming" title="确认付费生成" width="440px">
    <p>
      将为{{ slot === null ? "全天三个时段" : `时段「${slot}」` }}
      调用 Seedream / Seedance 生成媒体，产生 Ark 费用。
    </p>
    <el-checkbox v-model="paidConfirmed">
      <span style="color: #f56c6c">我已知晓并确认本次付费生成</span>
    </el-checkbox>
    <template #footer>
      <el-button @click="confirming = false">取消</el-button>
      <el-button
        type="primary"
        :disabled="!paidConfirmed"
        :loading="submitting"
        @click="confirm"
      >
        确认生成
      </el-button>
    </template>
  </el-dialog>
</template>
