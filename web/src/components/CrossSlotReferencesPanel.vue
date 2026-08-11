<script setup lang="ts">
import { ElMessage } from "element-plus";
import { onMounted, ref, watch } from "vue";

import { api } from "../api/client";
import type {
  CrossSlotReferenceDto,
  CrossSlotReferenceRole,
  CrossSlotReferenceTarget,
  EligibleCrossSlotAssetDto,
  Slot,
} from "../api/types";

const props = defineProps<{
  runId: string;
  slot: Slot;
  disabled?: boolean;
}>();
const emit = defineEmits<{ saved: [] }>();
const loading = ref(false);
const eligible = ref<EligibleCrossSlotAssetDto[]>([]);
const selected = ref<CrossSlotReferenceDto[]>([]);

async function load() {
  loading.value = true;
  try {
    const result = await api.crossSlotReferences(props.runId, props.slot);
    eligible.value = result.eligibleAssets;
    selected.value = result.references;
  } finally {
    loading.value = false;
  }
}

function selection(assetId: string) {
  return selected.value.find((item) => item.assetId === assetId);
}

function toggle(assetId: string, enabled: boolean) {
  if (!enabled) {
    selected.value = selected.value.filter((item) => item.assetId !== assetId);
    return;
  }
  const asset = eligible.value.find((item) => item.assetId === assetId);
  selected.value.push({
    assetId,
    role: asset?.suggestedRole ?? "identity",
    applyTo: asset?.mediaType === "video" ? "video" : "opening_anchor",
  });
}

function updateRole(assetId: string, role: CrossSlotReferenceRole) {
  const item = selection(assetId);
  if (item) item.role = role;
}

function updateTarget(assetId: string, applyTo: CrossSlotReferenceTarget) {
  const item = selection(assetId);
  if (item) item.applyTo = applyTo;
}

async function save() {
  await api.saveCrossSlotReferences(props.runId, props.slot, selected.value);
  ElMessage.success("前序媒体引用已保存")
  emit("saved");
}

watch(() => [props.runId, props.slot], () => void load());
onMounted(() => void load());
</script>

<template>
  <el-card shadow="never" v-loading="loading" class="reference-card">
    <template #header><strong>可选前序媒体引用</strong></template>
    <el-alert
      type="info"
      :closable="false"
      title="默认不引用。系统只发送你勾选的素材，并按身份、道具、场景、构图或运动职责使用。"
      description="开场锚点已经生成后再修改引用，不会覆盖旧图；需要从锚点节点显式生成新attempt。"
    />
    <el-empty v-if="!eligible.length" description="暂无可用的前序已批准媒体" />
    <div v-for="asset in eligible" :key="asset.assetId" class="reference-row">
      <el-checkbox
        :model-value="Boolean(selection(asset.assetId))"
        :disabled="disabled"
        @change="(value) => toggle(asset.assetId, Boolean(value))"
      >
        {{ asset.sourceSlot }} · {{ asset.role }} · {{ asset.mediaType }}
      </el-checkbox>
      <div class="reference-suggestion">
        建议{{ asset.suggestedRole }}：{{ asset.recommendationReason }}
      </div>
      <template v-if="selection(asset.assetId)">
        <el-select
          :model-value="selection(asset.assetId)?.role"
          :disabled="disabled"
          @change="(value) => updateRole(asset.assetId, value as CrossSlotReferenceRole)"
        >
          <el-option label="身份" value="identity" /><el-option label="道具" value="prop" />
          <el-option label="场景" value="scene" /><el-option label="构图" value="composition" />
          <el-option label="运动" value="motion" />
        </el-select>
        <el-select
          :model-value="selection(asset.assetId)?.applyTo"
          :disabled="disabled"
          @change="(value) => updateTarget(asset.assetId, value as CrossSlotReferenceTarget)"
        >
          <el-option
            v-if="asset.mediaType === 'image'"
            label="开场锚点"
            value="opening_anchor"
          />
          <el-option label="视频" value="video" />
          <el-option v-if="asset.mediaType === 'image'" label="两者" value="both" />
        </el-select>
      </template>
    </div>
    <el-button type="primary" :disabled="disabled" @click="save">保存素材引用</el-button>
  </el-card>
</template>

<style scoped>
.reference-card { margin: 12px 0; }
.reference-row { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) 150px 150px; gap: 10px; align-items: center; margin: 10px 0; }
.reference-suggestion { color: #8a8f99; font-size: 12px; line-height: 1.4; }
</style>
