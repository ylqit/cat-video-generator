<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, onMounted, provide, ref } from "vue";
import { useRouter } from "vue-router";

import { creatorApi } from "./api/client";
import type { CreatorTaskDto, TaskCancellationPolicyDto } from "./api/types";
import ProjectRail from "./components/director/ProjectRail.vue";

const router = useRouter();
const taskDrawerVisible = ref(false);
const tasks = ref<CreatorTaskDto[]>([]);
const loadingTasks = ref(false);
const taskError = ref("");
let timer: number | undefined;

const activeStatuses = new Set(["local_queued", "submitting", "provider_queued", "provider_running"]);
const taskCount = computed(() => tasks.value.filter((task) =>
  activeStatuses.has(task.status) || task.status === "awaiting_selection"
).length);

provide("openGlobalTasks", () => {
  taskDrawerVisible.value = true;
  void loadTasks();
});

function statusText(status: CreatorTaskDto["status"]) {
  return ({
    local_queued: "本地排队",
    submitting: "正在提交 Provider",
    provider_queued: "Provider 排队",
    provider_running: "Provider 生成中",
    awaiting_selection: "等待选择版本",
    succeeded: "成功",
    failed: "失败",
    cancelled: "已取消",
    submission_unknown: "提交状态未知",
    cancellation_unknown: "取消状态未知",
  } as const)[status];
}

async function loadTasks() {
  if (loadingTasks.value) return;
  loadingTasks.value = true;
  try {
    const next = await creatorApi.tasks();
    const policies = await Promise.all(next.map(async (task) => {
      if (!["local_queued", "submitting", "provider_queued", "provider_running", "submission_unknown", "cancellation_unknown"].includes(task.status)) return task;
      try {
        const cancellation = await creatorApi.cancellation(task.taskId);
        return { ...task, cancellation };
      } catch {
        return task;
      }
    }));
    tasks.value = policies;
    taskError.value = "";
  } catch (error) {
    taskError.value = error instanceof Error ? error.message : String(error);
  } finally {
    loadingTasks.value = false;
  }
}

async function cancelTask(task: CreatorTaskDto) {
  const policy = task.cancellation as TaskCancellationPolicyDto | undefined;
  if (!policy?.allowed) return;
  try {
    await ElMessageBox.confirm(
      policy.mode === "local_before_provider"
        ? "该任务尚未提交 Provider。取消后 Provider 调用为 0 次。"
        : "系统会先核对 Provider；只有仍在排队时才发送远端取消。费用以 Provider 账单为准。",
      policy.label,
      { confirmButtonText: policy.label, cancelButtonText: "返回", type: "warning" },
    );
    await creatorApi.cancelTask(task, "Web 全局任务中心人工取消");
    await loadTasks();
    ElMessage.success("任务取消状态已更新");
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof Error ? error.message : String(error));
    }
  }
}

function openTask(task: CreatorTaskDto) {
  taskDrawerVisible.value = false;
  void router.push({
    name: "project-production",
    params: { projectId: task.projectId },
    query: task.creatorShotId
      ? { workspace: "video", tab: "generate", shot: task.creatorShotId }
      : {},
  });
}

onMounted(() => {
  void loadTasks();
  timer = window.setInterval(loadTasks, 5_000);
});
onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <div class="shell">
    <ProjectRail :task-count="taskCount" />
    <main class="content"><router-view /></main>
    <el-drawer v-model="taskDrawerVisible" title="全局任务" size="min(430px, 100vw)">
      <div class="task-toolbar">
        <p>任务事实只来自服务器。隐藏面板不会删除任务或媒体。</p>
        <el-button :loading="loadingTasks" @click="loadTasks">刷新</el-button>
      </div>
      <el-alert v-if="taskError" type="warning" :closable="false" :title="taskError" />
      <el-empty v-if="!tasks.length && !loadingTasks" description="暂无任务" />
      <article v-for="task in tasks" :key="task.taskId" class="task-card">
        <header><b>{{ task.model || task.provider }}</b><el-tag>{{ statusText(task.status) }}</el-tag></header>
        <small>{{ task.createdAt ? new Date(task.createdAt).toLocaleString() : "刚刚" }}</small>
        <p>Provider：{{ task.providerStatus }}<template v-if="task.providerTaskId"> · {{ task.providerTaskId }}</template></p>
        <p v-if="task.error" class="error">{{ task.error.message || task.error.code || "任务失败" }}</p>
        <section v-if="task.cancellation" class="cancellation">
          <b>{{ task.cancellation.label }}</b>
          <span v-if="task.cancellation.costMayAlreadyApply">费用可能已产生</span>
          <small v-if="task.cancellation.disabledReason">{{ task.cancellation.disabledReason }}</small>
        </section>
        <footer>
          <el-button @click="openTask(task)">打开生产</el-button>
          <el-button v-if="task.cancellation" type="warning" :disabled="!task.cancellation.allowed" @click="cancelTask(task)">{{ task.cancellation.label }}</el-button>
        </footer>
      </article>
    </el-drawer>
  </div>
</template>

<style scoped>
.shell{width:100%;height:100%;display:grid;grid-template-columns:64px minmax(0,1fr);overflow:hidden}.content{min-width:0;min-height:0;overflow:hidden;background:#0d1016}.task-toolbar,.task-card{display:grid;gap:9px}.task-toolbar{margin-bottom:12px;grid-template-columns:minmax(0,1fr) auto;align-items:center;color:#8695a8}.task-toolbar p{margin:0}.task-card{margin-bottom:10px;padding:12px;color:#dbe6f2;background:#111824;border:1px solid #303c4c;border-radius:10px}.task-card header,.task-card footer{display:flex;align-items:center;justify-content:space-between;gap:8px}.task-card p,.task-card small{margin:0;color:#8493a6}.task-card .error{color:#e2a19c}.cancellation{padding:9px;display:grid;gap:4px;color:#dec598;background:#2a2218;border:1px solid #66502f;border-radius:8px}.task-card footer{justify-content:flex-end}@media(max-width:720px){.shell{display:block;padding-bottom:58px}.content{height:100%}}
</style>
