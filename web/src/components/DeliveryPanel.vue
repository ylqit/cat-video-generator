<script setup lang="ts">
import { ElMessage } from "element-plus";
import { onMounted, ref } from "vue";

import { api, assetContentUrl, ApiError } from "../api/client";
import type { DeliveryPackageDto } from "../api/types";
import StatusBadge from "./StatusBadge.vue";

const props = defineProps<{
  runId: string;
  /** Run已就绪（三集均通过审核）时才允许交付 */
  canDeliver: boolean;
}>();

const packages = ref<DeliveryPackageDto[]>([]);
const delivering = ref(false);
const manifest = ref<Record<string, unknown> | null>(null);
const manifestVisible = ref(false);

async function refresh() {
  packages.value = await api.listDeliveries(props.runId);
}

/** 构建交付包；前置不满足时后端返回422。 */
async function deliver() {
  delivering.value = true;
  try {
    const result = await api.deliver(props.runId);
    ElMessage.success(`交付包 r${result.revision} 已构建`);
    await refresh();
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    delivering.value = false;
  }
}

async function showManifest(packageId: string) {
  try {
    manifest.value = await api.deliveryManifest(packageId);
    manifestVisible.value = true;
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  }
}

onMounted(refresh);
defineExpose({ refresh });
</script>

<template>
  <el-card shadow="never" style="background: #16181d; border-color: #26282e; margin-top: 16px">
    <template #header>
      <div style="display: flex; align-items: center; gap: 10px">
        <strong>交付</strong>
        <div style="flex: 1" />
        <el-button
          type="success"
          size="small"
          :disabled="!canDeliver"
          :loading="delivering"
          @click="deliver"
        >
          构建交付包
        </el-button>
      </div>
    </template>
    <el-table :data="packages" size="small">
      <el-table-column prop="revision" label="版本" width="70" align="center">
        <template #default="{ row }">r{{ row.revision }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <StatusBadge :status="row.status" />
        </template>
      </el-table-column>
      <el-table-column label="文件" min-width="260">
        <template #default="{ row }">
          <div v-for="item in row.items" :key="item.id">
            <a :href="assetContentUrl(item.assetId)" :download="item.filename">
              {{ item.filename }}
            </a>
            <span class="muted"> · {{ item.sha256.slice(0, 12) }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="createdAt" label="构建时间" width="180">
        <template #default="{ row }">
          {{ new Date(row.createdAt).toLocaleString() }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="showManifest(row.id)">
            manifest
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <span class="muted">尚未交付；三集视频都审核通过后可构建交付包</span>
      </template>
    </el-table>
    <el-dialog v-model="manifestVisible" title="manifest.json" width="560px">
      <pre style="max-height: 400px; overflow: auto; font-size: 12px">{{
        JSON.stringify(manifest, null, 2)
      }}</pre>
    </el-dialog>
  </el-card>
</template>
