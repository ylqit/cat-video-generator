<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import { api } from "./api/client";
import type { HealthDto } from "./api/types";

const route = useRoute();
const health = ref<HealthDto | null>(null);
const unreachable = ref(false);
let timer: number | undefined;
const statusText = computed(() => {
  if (unreachable.value) return "后端不可达";
  if (!health.value) return "检查中…";
  return health.value.ready
    ? `数据库已就绪 · ${health.value.alembicRevision}`
    : `迁移不一致 · ${health.value.alembicRevision}`;
});
const statusColor = computed(() => unreachable.value ? "#f56c6c" : health.value?.ready ? "#67c23a" : "#e6a23c");

async function checkHealth() {
  try {
    health.value = await api.health();
    unreachable.value = false;
  } catch {
    unreachable.value = true;
  }
}

onMounted(() => {
  void checkHealth();
  timer = window.setInterval(checkHealth, 30000);
});
onBeforeUnmount(() => window.clearInterval(timer));
</script>

<template>
  <el-container class="shell">
    <el-aside width="190px" class="sidebar">
      <div class="brand">猫咪视频工作台</div>
      <el-menu :default-active="route.path" router background-color="transparent">
        <el-menu-item index="/studio">镜头生产</el-menu-item>
        <el-menu-item index="/projects">项目列表</el-menu-item>
        <el-menu-item index="/canon">Canon 资产</el-menu-item>
      </el-menu>
      <div class="health"><i :style="{ background: statusColor }" />{{ statusText }}</div>
    </el-aside>
    <el-main class="content"><router-view /></el-main>
  </el-container>
</template>

<style scoped>
.shell { height: 100%; }.sidebar { border-right: 1px solid #252a34; background: #101319; display: flex; flex-direction: column; }.brand { padding: 20px 16px; font-weight: 700; }.sidebar .el-menu { flex: 1; border-right: 0; }.health { padding: 12px; font-size: 11px; color: #8c95a6; display: flex; align-items: center; gap: 6px; }.health i { width: 8px; height: 8px; border-radius: 50%; }.content { padding: 0; background: #0d1016; }
</style>
