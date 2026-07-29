<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onMounted, ref } from "vue";

import { ApiError } from "../api/client";
import AssetThumb from "../components/AssetThumb.vue";
import { useCanonStore } from "../stores/canon";

const canon = useCanonStore();

const ROLE_LABEL: Record<string, string> = {
  person: "人物本体",
  cat: "猫咪本体",
  style: "画风示例",
};

const role = ref("person");
const file = ref<File | null>(null);
const uploading = ref(false);

const grouped = computed(() =>
  Object.keys(ROLE_LABEL).map((key) => ({
    role: key,
    label: ROLE_LABEL[key],
    items: canon.items.filter((item) => item.role === key),
  })),
);

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  file.value = input.files?.[0] ?? null;
}

/** 上传Canon图并刷新列表。 */
async function upload() {
  if (!file.value) {
    ElMessage.warning("请先选择图片文件");
    return;
  }
  uploading.value = true;
  try {
    await canon.upload(role.value, file.value);
    ElMessage.success("Canon 已导入");
    file.value = null;
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    uploading.value = false;
  }
}

onMounted(() => canon.fetch(true));
</script>

<template>
  <div class="page">
    <h2 style="margin-top: 0">Canon 资产</h2>
    <el-card
      shadow="never"
      style="background: #16181d; border-color: #26282e; margin-bottom: 16px"
    >
      <div style="display: flex; align-items: center; gap: 12px">
        <el-select v-model="role" style="width: 160px">
          <el-option
            v-for="(label, key) in ROLE_LABEL"
            :key="key"
            :label="label"
            :value="key"
          />
        </el-select>
        <input type="file" accept="image/*" @change="onFileChange" />
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!file"
          @click="upload"
        >
          导入
        </el-button>
      </div>
    </el-card>

    <el-card
      v-for="group in grouped"
      :key="group.role"
      shadow="never"
      style="background: #16181d; border-color: #26282e; margin-bottom: 16px"
    >
      <template #header>
        <strong>{{ group.label }}</strong>
        <span class="muted" style="margin-left: 8px">
          {{ group.items.length }} 张
        </span>
      </template>
      <AssetThumb
        v-for="item in group.items"
        :key="item.id"
        :asset-id="item.id"
        :label="item.sha256.slice(0, 8)"
        :size="120"
      />
      <span v-if="!group.items.length" class="muted">
        尚未导入{{ group.label }}
      </span>
    </el-card>
  </div>
</template>
