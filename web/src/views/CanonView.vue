<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onMounted, ref, watch } from "vue";

import { ApiError } from "../api/client";
import AssetThumb from "../components/AssetThumb.vue";
import { useCanonStore } from "../stores/canon";

const canon = useCanonStore();

const ROLE_LABEL: Record<string, string> = {
  person: "定稿人物",
  cat: "定稿猫咪",
  style: "定稿画风",
};

/** 人物保留身份图与前侧后三视图；猫咪使用前侧后三视图。 */
const VIEW_LABEL: Record<string, string> = {
  headshot: "大头照",
  fullbody: "全身照",
  front: "正面",
  side: "侧面",
  back: "背面",
};

const STYLE_KEY_HINTS = [
  "style:line_texture",
  "style:indoor",
  "style:outdoor",
];

const role = ref("person");
const view = ref("headshot");
const styleKey = ref("style:line_texture");
const file = ref<File | null>(null);
const uploading = ref(false);

const needsView = computed(() => role.value === "person" || role.value === "cat");
const viewOptions = computed<Record<string, string>>(() =>
  role.value === "person"
    ? {
        headshot: VIEW_LABEL.headshot,
        fullbody: VIEW_LABEL.fullbody,
        front: VIEW_LABEL.front,
        side: VIEW_LABEL.side,
        back: VIEW_LABEL.back,
      }
    : { front: VIEW_LABEL.front, side: VIEW_LABEL.side, back: VIEW_LABEL.back },
);

watch(role, (value) => {
  if (value === "person") view.value = "headshot";
  if (value === "cat") view.value = "front";
});

/** 最终提交的 semantic_key：主体按视角组合，画风只能使用三个定稿键。 */
const semanticKey = computed(() =>
  needsView.value ? `${role.value}:${view.value}` : styleKey.value.trim(),
);

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
  if (!semanticKey.value) {
    ElMessage.warning("请填写画风语义键");
    return;
  }
  uploading.value = true;
  try {
    await canon.upload(
      role.value,
      semanticKey.value,
      needsView.value ? view.value : null,
      file.value,
    );
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
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
        <el-select v-model="role" style="width: 140px">
          <el-option
            v-for="(label, key) in ROLE_LABEL"
            :key="key"
            :label="label"
            :value="key"
          />
        </el-select>
        <el-select v-if="needsView" v-model="view" style="width: 110px">
          <el-option
            v-for="(label, key) in viewOptions"
            :key="key"
            :label="label"
            :value="key"
          />
        </el-select>
        <el-select
          v-else
          v-model="styleKey"
          style="width: 220px"
        >
          <el-option
            v-for="hint in STYLE_KEY_HINTS"
            :key="hint"
            :label="hint"
            :value="hint"
          />
        </el-select>
        <input type="file" accept="image/*" @change="onFileChange" />
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!file || !semanticKey"
          @click="upload"
        >
          导入
        </el-button>
        <el-tag size="small" type="info">{{ semanticKey || "未命名" }}</el-tag>
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
        :label="item.semanticKey ?? item.sha256.slice(0, 8)"
        :size="120"
      />
      <span v-if="!group.items.length" class="muted">
        尚未导入{{ group.label }}
      </span>
    </el-card>
  </div>
</template>
