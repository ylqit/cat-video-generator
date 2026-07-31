<script setup lang="ts">
import { ElMessage } from "element-plus";
import { ref } from "vue";

import { api, ApiError } from "../api/client";
import type { StepDto } from "../api/types";
import StatusBadge from "./StatusBadge.vue";

const props = defineProps<{ steps: StepDto[] }>();
const emit = defineEmits<{ changed: [] }>();

const KIND_LABEL: Record<string, string> = {
  director: "导演规划",
  image: "图像生成",
  video: "视频生成",
  technical_qc: "技术QC",
  ark_visual: "语义审核",
  delivery: "交付",
};

const retrying = ref<string | null>(null);
const confirming = ref<StepDto | null>(null);
const reason = ref("");
const paidConfirmed = ref(false);

function askRetry(step: StepDto) {
  confirming.value = step;
  reason.value = "";
  paidConfirmed.value = false;
}

/** 失败步骤显式重试：创建新attempt，付费许可逐次透传。 */
async function confirmRetry() {
  const step = confirming.value;
  if (!step) {
    return;
  }
  if (reason.value.trim().length < 4) {
    ElMessage.warning("重试理由至少 4 个字符");
    return;
  }
  retrying.value = step.id;
  try {
    await api.retryStep(step.id, reason.value.trim(), true);
    ElMessage.success("重试任务已提交");
    confirming.value = null;
    emit("changed");
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    retrying.value = null;
  }
}
</script>

<template>
  <el-table :data="steps" size="small" style="width: 100%">
    <el-table-column label="类型" width="110">
      <template #default="{ row }">
        {{ KIND_LABEL[row.kind] ?? row.kind }}
      </template>
    </el-table-column>
    <el-table-column label="状态" width="130">
      <template #default="{ row }">
        <StatusBadge :status="row.status" />
      </template>
    </el-table-column>
    <el-table-column prop="attempt" label="尝试" width="60" />
    <el-table-column label="供应商任务" min-width="140">
      <template #default="{ row }">
        <span class="muted" style="font-size: 12px">
          {{ row.providerTaskId ?? "—" }}
        </span>
      </template>
    </el-table-column>
    <el-table-column label="错误 / 下一步" min-width="220">
      <template #default="{ row }">
        <div v-if="row.error" style="color: #f56c6c; font-size: 12px">
          {{ row.error.code ?? "error" }}：{{ row.error.message }}
        </div>
        <div v-if="row.nextAction" class="muted" style="font-size: 12px">
          下一步：{{ row.nextAction }}
        </div>
        <span v-if="!row.error && !row.nextAction" class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="操作" width="90">
      <template #default="{ row }">
        <el-button
          v-if="row.status === 'failed'"
          size="small"
          type="warning"
          :loading="retrying === row.id"
          @click="askRetry(row)"
        >
          重试
        </el-button>
      </template>
    </el-table-column>
  </el-table>

  <el-dialog
    :model-value="confirming !== null"
    title="重试失败步骤"
    width="440px"
    @update:model-value="confirming = null"
  >
    <p class="muted" style="margin-top: 0">
      将创建新的 attempt 重新执行该步骤；图像/视频步骤会产生 Ark 付费调用。
    </p>
    <el-input
      v-model="reason"
      type="textarea"
      :rows="2"
      placeholder="重试理由（至少 4 个字符，会记录到审计）"
    />
    <div style="margin-top: 10px">
      <el-checkbox v-model="paidConfirmed">
        <span style="color: #f56c6c">我已知晓重试可能产生付费调用</span>
      </el-checkbox>
    </div>
    <template #footer>
      <el-button @click="confirming = null">取消</el-button>
      <el-button
        type="primary"
        :disabled="!paidConfirmed || reason.trim().length < 4"
        :loading="retrying !== null"
        @click="confirmRetry"
      >
        确认重试
      </el-button>
    </template>
  </el-dialog>
</template>
