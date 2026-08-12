<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import { api, assetContentUrl } from "../api/client";
import type {
  AssetDto,
  ProjectGraph,
  ProjectSummary,
  ReferenceBinding,
  ReferenceRole,
} from "../api/types";

const assets = ref<AssetDto[]>([]);
const projects = ref<ProjectSummary[]>([]);
const projectId = ref("");
const graph = ref<ProjectGraph | null>(null);
const selectedIds = ref<string[]>([]);
const previewAsset = ref<AssetDto | null>(null);
const loading = ref(false);

const displayNames: Record<string, string> = {
  "person:headshot": "人物大头照",
  "person:fullbody": "人物全身",
  "person:front": "人物正面",
  "person:side": "人物侧面",
  "person:back": "人物背面",
  "cat:front": "猫咪正面",
  "cat:side": "猫咪侧面",
  "cat:back": "猫咪背面",
  "style:line_texture": "线条材质",
  "style:outdoor": "室外画风",
  "style:indoor": "室内画风",
};

const recommendedKeys = new Set([
  "person:headshot",
  "person:fullbody",
  "cat:front",
  "cat:side",
  "style:line_texture",
]);

const groups = computed(() => [
  { key: "person", title: "人物", items: assets.value.filter((item) => item.semanticKey?.startsWith("person:")) },
  { key: "cat", title: "猫咪", items: assets.value.filter((item) => item.semanticKey?.startsWith("cat:")) },
  { key: "style", title: "画风", items: assets.value.filter((item) => item.semanticKey?.startsWith("style:")) },
]);

function assetName(asset: AssetDto): string {
  return displayNames[asset.semanticKey ?? ""] ?? asset.semanticKey ?? asset.role;
}

function referenceRole(asset: AssetDto): ReferenceRole {
  return asset.semanticKey?.startsWith("style:") ? "style" : "identity";
}

async function loadProject(id: string) {
  graph.value = id ? await api.project(id) : null;
  selectedIds.value = graph.value?.project.defaultReferenceBindings.map((item) => item.assetId) ?? [];
}

function initializeRecommended() {
  if (!graph.value || graph.value.project.defaultReferenceBindings.length) {
    ElMessage.info("该项目已有默认参考，不会覆盖现有选择");
    return;
  }
  selectedIds.value = assets.value
    .filter((item) => item.contentReady && recommendedKeys.has(item.semanticKey ?? ""))
    .map((item) => item.id);
}

async function saveDefaults() {
  if (!graph.value) return;
  loading.value = true;
  try {
    const selected = assets.value.filter((item) => selectedIds.value.includes(item.id));
    const references: ReferenceBinding[] = selected.map((item) => ({
      assetId: item.id,
      usage: "generation_reference",
      role: referenceRole(item),
      applyTo: "both",
    }));
    await api.updateProjectDefaultReferences(graph.value.project.id, references);
    await loadProject(graph.value.project.id);
    ElMessage.success("项目默认参考已保存");
  } finally {
    loading.value = false;
  }
}

watch(projectId, (id) => { void loadProject(id); });

onMounted(async () => {
  [assets.value, projects.value] = await Promise.all([api.canon(), api.projects()]);
  if (projects.value.length) projectId.value = projects.value[0].id;
});
</script>

<template>
  <div class="page">
    <header>
      <div>
        <h1>Canon 资产</h1>
        <p>查看当前批准的人物、猫咪和定稿画风，并保存为项目默认参考。</p>
      </div>
      <div class="project-actions">
        <el-select v-model="projectId" placeholder="选择项目" filterable>
          <el-option v-for="project in projects" :key="project.id" :label="project.title" :value="project.id" />
        </el-select>
        <el-button :disabled="!graph" @click="initializeRecommended">初始化推荐 5 张</el-button>
        <el-button type="primary" :loading="loading" :disabled="!graph" @click="saveDefaults">保存项目默认</el-button>
      </div>
    </header>

    <el-alert
      v-if="graph && !graph.project.defaultReferenceBindings.length"
      type="info"
      :closable="false"
      title="该项目还没有默认参考；可初始化人物大头照、人物全身、猫咪正面、猫咪侧面和线条材质。"
    />

    <section v-for="group in groups" :key="group.key" class="canon-group">
      <h2>{{ group.title }} <small>{{ group.items.length }} 张</small></h2>
      <div class="grid">
        <article v-for="asset in group.items" :key="asset.id" :class="{ selected: selectedIds.includes(asset.id), missing: !asset.contentReady }">
          <button class="preview" type="button" :disabled="!asset.contentReady" @click="previewAsset = asset">
            <img v-if="asset.contentReady" :src="assetContentUrl(asset.id)" :alt="assetName(asset)" />
            <div v-else class="missing-placeholder">文件缺失<br /><small>请运行 canon-repair 修复</small></div>
          </button>
          <div class="asset-caption">
            <el-checkbox v-model="selectedIds" :label="asset.id" :disabled="!asset.contentReady">
              {{ assetName(asset) }}
            </el-checkbox>
            <span>{{ asset.contentReady ? asset.sha256.slice(0, 12) : "内容不可用" }}</span>
          </div>
        </article>
      </div>
    </section>

    <el-dialog
      :model-value="Boolean(previewAsset)"
      :title="previewAsset ? assetName(previewAsset) : 'Canon 预览'"
      width="min(860px, 92vw)"
      @update:model-value="(visible) => { if (!visible) previewAsset = null; }"
    >
      <img v-if="previewAsset?.contentReady" class="large-preview" :src="assetContentUrl(previewAsset.id)" :alt="assetName(previewAsset)" />
    </el-dialog>
  </div>
</template>

<style scoped>
.page { padding: 28px; color: #e8ebf2; }
header { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 18px; }
.page p { color: #929aaa; }
.project-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.project-actions .el-select { width: 210px; }
.canon-group { margin-top: 26px; }
.canon-group h2 { font-size: 18px; }
.canon-group small { color: #818a9a; font-weight: 400; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 14px; }
.grid article { background: #161b23; border: 1px solid #2b323f; border-radius: 10px; padding: 10px; display: grid; gap: 8px; }
.grid article.selected { border-color: #409eff; box-shadow: 0 0 0 1px #409eff55; }
.grid article.missing { border-color: #8b5e2b; }
.preview { border: 0; padding: 0; background: #0a0d12; cursor: zoom-in; width: 100%; }
.preview:disabled { cursor: default; }
.preview img, .missing-placeholder { width: 100%; aspect-ratio: 1; object-fit: contain; }
.missing-placeholder { display: grid; place-content: center; color: #d6a15e; line-height: 1.6; }
.asset-caption { display: grid; gap: 3px; }
.asset-caption span { color: #818a9a; font-size: 11px; }
.large-preview { display: block; max-width: 100%; max-height: 72vh; margin: 0 auto; object-fit: contain; }
@media (max-width: 800px) { header { flex-direction: column; } }
</style>
