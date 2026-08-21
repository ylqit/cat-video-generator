<script setup lang="ts">
import { ElMessage, ElNotification } from "element-plus";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "./api/client";
import {
  startRuntimeStatus,
  stopRuntimeStatus,
  useRuntimeStatus,
} from "./runtimeStatus";
import {
  clearCompletedTasks,
  registerTask,
  requestWorkspaceRefresh,
  startTaskCenter,
  stopTaskCenter,
  useTaskCenter,
} from "./tasks/taskCenter";
import type { TaskCenterItem } from "./tasks/taskCenter";

const route = useRoute();
const router = useRouter();
const taskDrawerVisible = ref(false);
const taskCenter = useTaskCenter();
const runtimeStatus = useRuntimeStatus();
const health = runtimeStatus.health;
const unreachable = runtimeStatus.unreachable;
const statusText = computed(() => {
  if (unreachable.value) return "后端不可达";
  if (!health.value) return "检查中…";
  return health.value.ready
    ? `数据库已就绪 · ${health.value.alembicRevision}`
    : `迁移不一致 · ${health.value.alembicRevision}`;
});
const statusColor = computed(() => unreachable.value ? "#f56c6c" : health.value?.ready ? "#67c23a" : "#e6a23c");

function taskStatusText(task: Pick<TaskCenterItem, "status" | "kind" | "providerTaskId">): string {
  if (task.status === "running" && [
    "story_diagnosis",
    "story_rewrite",
    "shot_suggestions",
    "shot_assistance",
  ].includes(task.kind)) return "Ark 分析中";
  if (task.status === "queued" && task.providerTaskId) return "已提交 Provider";
  return ({
    queued: "排队中",
    pending: "等待提交",
    submitting: "提交 Provider 中",
    running: "Provider 生成中",
    awaiting_review: "等待人工审核",
    succeeded: "成功",
    failed: "失败",
    submission_unknown: "提交状态待对账",
    restart_pending: "服务重启后待恢复",
    cancelled: "已取消",
  } as Record<string, string>)[task.status] ?? task.status;
}

function taskStatusType(status: string): "success" | "warning" | "danger" | "info" {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "submission_unknown") return "danger";
  if (["awaiting_review", "restart_pending"].includes(status)) return "warning";
  return "info";
}

async function openTask(projectId?: string, shotId?: string, canvasNodeId?: string) {
  if (!projectId) return;
  taskDrawerVisible.value = false;
  if (canvasNodeId) {
    await router.push({
      name: "aigc-canvas",
      params: { projectId },
      query: { node: canvasNodeId, focus: String(Date.now()) },
    });
    requestWorkspaceRefresh(projectId);
    return;
  }
  if (shotId) {
    await router.push({
      name: "shot-generation-workspace",
      params: { projectId, shotId },
    });
    requestWorkspaceRefresh(projectId, shotId);
    return;
  }
  await router.push({ name: "aigc-canvas", params: { projectId } });
  requestWorkspaceRefresh(projectId);
}

async function resumeTask(stepId: string, projectId?: string, shotId?: string) {
  try {
    const submitted = await api.resumeStep(stepId);
    registerTask(submitted.jobId, {
      kind: "resume_step",
      label: "恢复 Provider 任务查询",
      projectId,
      shotId,
      operationKey: "resume",
    });
    ElMessage.success("已登记恢复查询，页面可继续操作");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

onMounted(() => {
  startRuntimeStatus();
  startTaskCenter();
});
onBeforeUnmount(() => {
  stopRuntimeStatus();
  stopTaskCenter();
});

watch(() => taskCenter.lastNotification.value, (event) => {
  if (!event || !["awaiting_review", "succeeded", "failed", "submission_unknown"].includes(event.item.status)) return;
  const tagType = taskStatusType(event.item.status);
  const notificationType = tagType === "danger" ? "error" : tagType;
  ElNotification({
    title: event.item.label,
    message: taskStatusText(event.item),
    type: notificationType,
    duration: ["awaiting_review", "failed", "submission_unknown"].includes(event.item.status)
      ? 0
      : 4500,
    onClick: () => void openTask(
      event.item.projectId,
      event.item.shotId,
      event.item.canvasNodeId,
    ),
  });
});
</script>

<template>
  <el-container class="shell">
    <el-aside width="190px" class="sidebar">
      <div class="brand">AIGC 媒体工作台</div>
      <el-menu :default-active="route.path" router background-color="transparent">
        <el-menu-item index="/canvas">通用媒体画布</el-menu-item>
        <el-menu-item index="/studio">旧版镜头生产</el-menu-item>
        <el-menu-item index="/projects">项目列表</el-menu-item>
        <el-menu-item index="/canon">Canon 资产</el-menu-item>
        <el-menu-item index="/settings">系统设置</el-menu-item>
      </el-menu>
      <button class="task-trigger" type="button" @click="taskDrawerVisible = true">
        <span>全局任务</span>
        <el-badge :value="taskCenter.activeCount.value + taskCenter.attentionCount.value" :hidden="taskCenter.activeCount.value + taskCenter.attentionCount.value === 0" />
      </button>
      <div class="health"><i :style="{ background: statusColor }" />{{ statusText }}</div>
    </el-aside>
    <el-main class="content"><router-view /></el-main>
    <el-drawer v-model="taskDrawerVisible" title="全局任务中心" size="430px">
      <div class="task-actions">
        <span>长任务在后台继续执行，切换项目或关闭对话框不会中断。</span>
        <el-button size="small" @click="clearCompletedTasks">清理已完成</el-button>
      </div>
      <el-alert v-if="taskCenter.connectionError.value" type="warning" :title="`任务状态暂不可达：${taskCenter.connectionError.value}`" :closable="false" />
      <el-empty v-if="!taskCenter.items.value.length" description="暂无任务" />
      <article v-for="task in taskCenter.items.value" :key="task.key" class="task-card">
        <div class="task-head">
          <b>{{ task.label }}<template v-if="task.attempt"> V{{ task.attempt }}</template></b>
          <el-tag :type="taskStatusType(task.status)">{{ taskStatusText(task) }}</el-tag>
        </div>
        <small>{{ task.model || task.kind }} · {{ task.createdAt ? new Date(task.createdAt).toLocaleString() : '刚刚' }}</small>
        <el-progress
          v-if="task.progress && typeof task.progress.percent === 'number' && ['queued', 'pending', 'running', 'submitting'].includes(task.status)"
          :percentage="task.progress.percent"
        />
        <p v-if="task.progress?.message" class="task-progress-message">{{ task.progress.message }}</p>
        <p v-if="task.resultSummary?.message" class="task-result-message">{{ String(task.resultSummary.message) }}</p>
        <p v-if="task.error">{{ String(task.error.message ?? task.error.code ?? '任务失败') }}</p>
        <div class="task-card-actions">
          <el-button v-if="task.projectId" size="small" @click="openTask(task.projectId, task.shotId, task.canvasNodeId)">打开对应节点</el-button>
          <el-button v-if="task.stepId && task.providerTaskId && ['submission_unknown', 'restart_pending'].includes(task.status)" size="small" @click="resumeTask(task.stepId, task.projectId, task.shotId)">按 Provider ID 对账</el-button>
        </div>
      </article>
    </el-drawer>
  </el-container>
</template>

<style scoped>
.shell { height: 100%; }.sidebar { border-right: 1px solid #252a34; background: #101319; display: flex; flex-direction: column; }.brand { padding: 20px 16px; font-weight: 700; }.sidebar .el-menu { flex: 1; border-right: 0; }.health { padding: 12px; font-size: 11px; color: #8c95a6; display: flex; align-items: center; gap: 6px; }.health i { width: 8px; height: 8px; border-radius: 50%; }.content { padding: 0; background: #0d1016; }
.task-trigger { margin: 10px 12px 0; padding: 9px 10px; display: flex; align-items: center; justify-content: space-between; color: #dce7f5; background: #17202d; border: 1px solid #314055; border-radius: 7px; cursor: pointer; }
.task-actions, .task-card { display: grid; gap: 8px; }.task-actions { margin-bottom: 12px; color: #8d9ab0; }.task-card { padding: 12px; margin-bottom: 10px; border: 1px solid #303c4f; border-radius: 8px; background: #111824; }.task-head, .task-card-actions { display: flex; gap: 8px; align-items: center; justify-content: space-between; flex-wrap: wrap; }.task-card small { color: #8592a6; }.task-card p { margin: 0; color: #f3a6a6; white-space: pre-wrap; }
.task-card .task-progress-message { color: #9eb4cc; }.task-card .task-result-message { color: #8ed4ad; }
</style>
