<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { api } from "./api/client";
import type { HealthStatus } from "./api/types";

const route = useRoute();
const active = computed(() => route.path);

/** 后端健康状态：绿=数据库连通且迁移最新，黄=迁移落后，红=不可达。 */
const health = ref<HealthStatus | null>(null);
const unreachable = ref(false);
let timer: number | undefined;

const healthColor = computed(() => {
  if (unreachable.value) {
    return "#f56c6c";
  }
  if (!health.value) {
    return "#8a8f99";
  }
  return health.value.ready ? "#67c23a" : "#e6a23c";
});

const healthText = computed(() => {
  if (unreachable.value) {
    return "后端不可达";
  }
  if (!health.value) {
    return "检查中…";
  }
  const db = String(health.value.database ?? "");
  return health.value.ready ? `已连接 ${db}` : `${db} 迁移落后`;
});

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
  <el-container style="height: 100%">
    <el-aside
      width="200px"
      style="border-right: 1px solid #26282e; display: flex; flex-direction: column"
    >
      <div style="padding: 18px 16px; font-weight: 600">猫咪视频生产台</div>
      <el-menu
        :default-active="active"
        router
        background-color="transparent"
        style="flex: 1"
      >
        <el-menu-item index="/studio">生产工作台</el-menu-item>
        <el-menu-item index="/runs">生产运行</el-menu-item>
        <el-menu-item index="/canon">Canon 资产</el-menu-item>
      </el-menu>
      <div
        style="padding: 12px 16px; font-size: 12px; color: #8a8f99; display: flex; align-items: center; gap: 6px"
      >
        <span
          :style="{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            background: healthColor,
            display: 'inline-block',
          }"
        />
        {{ healthText }}
      </div>
    </el-aside>
    <el-main style="padding: 0; overflow-y: auto">
      <router-view />
    </el-main>
  </el-container>
</template>
