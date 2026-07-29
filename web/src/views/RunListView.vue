<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import PlanCreateDialog from "../components/PlanCreateDialog.vue";
import StatusBadge from "../components/StatusBadge.vue";
import { useCanonStore } from "../stores/canon";
import { useRunsStore } from "../stores/runs";

const runs = useRunsStore();
const canon = useCanonStore();
const router = useRouter();
const dialog = ref<InstanceType<typeof PlanCreateDialog>>();

onMounted(() => {
  void runs.fetchRuns();
  void canon.fetch();
});
</script>

<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; margin-bottom: 16px">
      <h2 style="margin: 0">生产运行</h2>
      <el-button type="primary" @click="dialog?.open()">新建计划</el-button>
    </div>
    <el-table
      v-loading="runs.loading"
      :data="runs.runs"
      @row-click="(row: { id: string }) => router.push(`/runs/${row.id}`)"
    >
      <el-table-column prop="contentDate" label="内容日期" width="120" />
      <el-table-column prop="theme" label="主题" min-width="240" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <StatusBadge :status="row.status" />
        </template>
      </el-table-column>
      <el-table-column prop="selectedCandidate" label="选中候选" width="90" align="center" />
      <el-table-column prop="createdAt" label="创建时间" width="200">
        <template #default="{ row }">
          {{ new Date(row.createdAt).toLocaleString() }}
        </template>
      </el-table-column>
      <template #empty>
        <span class="muted">暂无运行，点击右上角"新建计划"开始一天的生产</span>
      </template>
    </el-table>
    <PlanCreateDialog ref="dialog" @created="runs.fetchRuns()" />
  </div>
</template>
