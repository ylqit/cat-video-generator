<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, ref } from "vue";

import { api, ApiError } from "../api/client";
import type {
  JobAccepted,
  ReconciliationCandidateDto,
  StepActionDto,
  StepDto,
} from "../api/types";
import StatusBadge from "./StatusBadge.vue";

const props = defineProps<{ steps: StepDto[] }>();
const emit = defineEmits<{
  changed: [];
  job: [job: JobAccepted];
  review: [step: StepDto];
}>();

const KIND_LABEL: Record<string, string> = {
  director: "导演规划",
  image: "视觉图片生成",
  video: "视频生成",
};

const activeStepId = ref<string | null>(null);
const retryTarget = ref<StepDto | null>(null);
const retryAction = ref<StepActionDto | null>(null);
const reason = ref("");
const paidConfirmed = ref(false);
const duplicateBillingConfirmed = ref(false);
const restartFromBeginning = ref(false);
const reconcileTarget = ref<StepDto | null>(null);
const candidates = ref<ReconciliationCandidateDto[]>([]);
const selectedTaskId = ref("");
const loadingCandidates = ref(false);

const retryTitle = computed(() =>
  retryAction.value?.type === "retry_unknown_image"
    ? "接受风险并重新生成图片"
    : "重试失败节点",
);

function askRetry(step: StepDto, action: StepActionDto) {
  retryTarget.value = step;
  retryAction.value = action;
  reason.value = "";
  paidConfirmed.value = false;
  duplicateBillingConfirmed.value = false;
  restartFromBeginning.value = false;
}

function closeRetry() {
  retryTarget.value = null;
  retryAction.value = null;
}

/** 显式重试创建新attempt；submission_unknown图片还需单独接受重复计费风险。 */
async function confirmRetry() {
  const step = retryTarget.value;
  const action = retryAction.value;
  if (!step || !action || reason.value.trim().length < 4) {
    return;
  }
  activeStepId.value = step.id;
  try {
    const job = await api.retryStep(
      step.id,
      reason.value.trim(),
      action.paid,
      duplicateBillingConfirmed.value,
      restartFromBeginning.value,
    );
    ElMessage.success("新attempt已提交；旧任务和错误记录保持不变");
    closeRetry();
    emit("job", job);
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    activeStepId.value = null;
  }
}

/** 只查询已有Seedance task ID，不创建新的供应商POST。 */
async function resumeStep(step: StepDto) {
  activeStepId.value = step.id;
  try {
    const job = await api.resumeStep(step.id);
    ElMessage.success("已恢复监看原Ark任务，不会重复收费");
    emit("job", job);
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    activeStepId.value = null;
  }
}

async function openReconciliation(step: StepDto) {
  reconcileTarget.value = step;
  candidates.value = [];
  selectedTaskId.value = "";
  loadingCandidates.value = true;
  try {
    candidates.value = await api.reconciliationCandidates(step.id);
    if (candidates.value.length === 1) {
      selectedTaskId.value = candidates.value[0].taskId;
    }
  } catch (error) {
    reconcileTarget.value = null;
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    loadingCandidates.value = false;
  }
}

async function confirmReconciliation() {
  const step = reconcileTarget.value;
  if (!step || !selectedTaskId.value) {
    return;
  }
  activeStepId.value = step.id;
  try {
    const job = await api.reconcileStep(step.id, selectedTaskId.value);
    ElMessage.success("已绑定所选Ark任务并恢复查询");
    reconcileTarget.value = null;
    emit("job", job);
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    activeStepId.value = null;
  }
}

function runAction(step: StepDto, action: StepActionDto) {
  if (action.type === "retry" || action.type === "retry_unknown_image") {
    askRetry(step, action);
  } else if (action.type === "continue_query") {
    void resumeStep(step);
  } else if (action.type === "reconcile") {
    void openReconciliation(step);
  } else if (action.type === "review") {
    emit("review", step);
  }
}
</script>

<template>
  <el-table :data="props.steps" size="small" style="width: 100%">
    <el-table-column label="类型" width="120">
      <template #default="{ row }">
        {{ KIND_LABEL[row.kind] ?? row.kind }}
      </template>
    </el-table-column>
    <el-table-column label="状态" width="130">
      <template #default="{ row }">
        <StatusBadge :status="row.status" />
      </template>
    </el-table-column>
    <el-table-column prop="attempt" label="尝试" width="65" />
    <el-table-column label="Provider任务" min-width="180">
      <template #default="{ row }">
        <div class="task-id">{{ row.providerTaskId ?? "—" }}</div>
        <div v-if="row.submittedAt" class="muted">
          {{ new Date(row.submittedAt).toLocaleString() }}
        </div>
      </template>
    </el-table-column>
    <el-table-column label="错误 / 下一步" min-width="260">
      <template #default="{ row }">
        <div v-if="row.error" class="error-text">
          {{ row.error.code ?? "error" }}：{{ row.error.message }}
        </div>
        <div v-if="row.nextAction" class="muted">下一步：{{ row.nextAction }}</div>
        <span v-if="!row.error && !row.nextAction" class="muted">—</span>
      </template>
    </el-table-column>
    <el-table-column label="节点操作" min-width="150" fixed="right">
      <template #default="{ row }">
        <el-button
          v-for="action in row.availableActions"
          :key="action.type"
          size="small"
          :type="action.paid ? 'warning' : 'primary'"
          :loading="activeStepId === row.id"
          @click="runAction(row, action)"
        >
          {{ action.label }}
        </el-button>
        <span v-if="!row.availableActions.length" class="muted">—</span>
      </template>
    </el-table-column>
  </el-table>

  <el-dialog
    :model-value="retryTarget !== null"
    :title="retryTitle"
    width="500px"
    @update:model-value="closeRetry"
  >
    <el-alert
      v-if="retryAction?.type === 'retry_unknown_image'"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
      title="Seedream是同步接口，原超时请求无法通过Task ID找回"
      description="供应商可能已经完成并计费；再次生成会创建新attempt，并可能产生重复费用。"
    />
    <p v-else class="muted" style="margin-top: 0">
      将创建新的attempt重新执行该节点；原Prompt、错误和Provider任务记录不会覆盖。
    </p>
    <el-input
      v-model="reason"
      type="textarea"
      :rows="3"
      placeholder="填写重试原因（至少4个字符，写入审计快照）"
    />
    <div style="margin-top: 10px">
      <el-checkbox v-model="paidConfirmed">
        <span style="color: #f56c6c">我已知晓本次操作会产生新的Ark付费调用</span>
      </el-checkbox>
    </div>
    <div v-if="retryAction?.requiresDuplicateBillingAck" style="margin-top: 6px">
      <el-checkbox v-model="duplicateBillingConfirmed">
        我接受原未知请求可能已计费，本次可能重复计费
      </el-checkbox>
    </div>
    <div
      v-if="retryTarget?.kind === 'video' && retryTarget.operationKey.startsWith('video:extend:')"
      style="margin-top: 6px"
    >
      <el-checkbox v-model="restartFromBeginning">
        错误已存在于前段：从开场锚点重新生成全部区段
      </el-checkbox>
      <div v-if="restartFromBeginning" class="error-text">
        将为首段和全部延展分别创建新attempt，产生多次新的Ark视频费用。
      </div>
    </div>
    <template #footer>
      <el-button @click="closeRetry">取消</el-button>
      <el-button
        type="primary"
        :disabled="
          reason.trim().length < 4 ||
          !paidConfirmed ||
          (!!retryAction?.requiresDuplicateBillingAck && !duplicateBillingConfirmed)
        "
        :loading="activeStepId !== null"
        @click="confirmRetry"
      >
        确认并创建新attempt
      </el-button>
    </template>
  </el-dialog>

  <el-dialog
    :model-value="reconcileTarget !== null"
    title="Ark视频任务对账"
    width="680px"
    @update:model-value="reconcileTarget = null"
  >
    <el-alert
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
      title="对账只绑定已经存在的Ark任务，不会重新提交Seedance"
      description="候选按模型、创建时间、分辨率、比例、时长和音频设置筛选；即使只有一个候选也需要人工确认。"
    />
    <el-table v-loading="loadingCandidates" :data="candidates" size="small">
      <el-table-column width="48">
        <template #default="{ row }">
          <el-radio v-model="selectedTaskId" :value="row.taskId">
            <span />
          </el-radio>
        </template>
      </el-table-column>
      <el-table-column prop="taskId" label="Task ID" min-width="210" />
      <el-table-column prop="status" label="状态" width="90" />
      <el-table-column label="规格" min-width="150">
        <template #default="{ row }">
          {{ row.resolution ?? "?" }} · {{ row.ratio ?? "?" }} ·
          {{ row.durationSeconds ?? "?" }}s
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">
          {{ row.createdAt ? new Date(row.createdAt).toLocaleString() : "—" }}
        </template>
      </el-table-column>
      <template #empty>
        <span class="muted">没有找到满足当前Step输入快照的可绑定任务</span>
      </template>
    </el-table>
    <template #footer>
      <el-button @click="reconcileTarget = null">取消</el-button>
      <el-button
        type="primary"
        :disabled="!selectedTaskId"
        :loading="activeStepId !== null"
        @click="confirmReconciliation"
      >
        确认绑定并继续查询
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.muted {
  color: #8a8f99;
  font-size: 12px;
}
.task-id {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}
.error-text {
  color: #f56c6c;
  font-size: 12px;
}
</style>
