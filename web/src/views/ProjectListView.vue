<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import type { ProjectSummary } from "../api/types";

const router = useRouter();
const projects = ref<ProjectSummary[]>([]);
onMounted(async () => { projects.value = await api.projects(); });

function openProject(row: ProjectSummary) {
  void router.push({ path: "/studio", query: { project: row.id } });
}
</script>

<template>
  <div class="page">
    <h1>项目列表</h1>
    <p>每个项目可包含任意数量场景，每个视频片段对应一次 8–15 秒生成。</p>
    <el-table :data="projects" @row-click="openProject">
      <el-table-column prop="title" label="项目" />
      <el-table-column prop="contentDate" label="日期" width="140" />
      <el-table-column prop="status" label="状态" width="120" />
    </el-table>
  </div>
</template>

<style scoped>.page { padding: 28px; color: #e8ebf2; }.page p { color: #929aaa; }</style>
