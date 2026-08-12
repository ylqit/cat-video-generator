<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus";

import { api, assetContentUrl } from "../api/client";
import type {
  AssetDto,
  LookReferenceBinding,
  LookReferencePurpose,
  ProjectGraph,
  ProjectSummary,
  VisualProfileDraft,
  VisualProfileRevisionDto,
} from "../api/types";

const assets = ref<AssetDto[]>([]);
const projects = ref<ProjectSummary[]>([]);
const projectId = ref("");
const graph = ref<ProjectGraph | null>(null);
const selectedIds = ref<string[]>([]);
const profile = ref<VisualProfileRevisionDto | null>(null);
const profileForm = ref<VisualProfileDraft | null>(null);
const previewAsset = ref<AssetDto | null>(null);
const loading = ref(false);
const imageErrors = ref<Record<string, string>>({});

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

const groups = computed(() => [
  { key: "person", title: "人物", items: assets.value.filter((item) => item.semanticKey?.startsWith("person:")) },
  { key: "cat", title: "猫咪", items: assets.value.filter((item) => item.semanticKey?.startsWith("cat:")) },
  { key: "style", title: "画风", items: assets.value.filter((item) => item.semanticKey?.startsWith("style:")) },
]);

function assetName(asset: AssetDto): string {
  return displayNames[asset.semanticKey ?? ""] ?? asset.semanticKey ?? asset.role;
}

function referencePurpose(asset: AssetDto): LookReferencePurpose {
  return asset.referencePurpose ?? (asset.semanticKey?.startsWith("style:") ? "style" : "person_identity");
}

async function loadProject(id: string) {
  if (!id) {
    graph.value = null;
    profile.value = null;
    profileForm.value = null;
    selectedIds.value = [];
    return;
  }
  [graph.value, profile.value] = await Promise.all([api.project(id), api.visualProfile(id)]);
  profileForm.value = cloneProfile(profile.value);
  selectedIds.value = profileForm.value.referenceBindings.map((item) => item.assetId);
}

function cloneProfile(value: VisualProfileDraft): VisualProfileDraft {
  return structuredClone({
    personIdentity: value.personIdentity,
    personHair: value.personHair,
    personBody: value.personBody,
    catIdentity: value.catIdentity,
    stylePositive: value.stylePositive,
    styleNegative: value.styleNegative,
    referenceBindings: value.referenceBindings,
  });
}

function buildBindings(): LookReferenceBinding[] {
  const previous = new Map(
    profileForm.value?.referenceBindings.map((item) => [item.assetId, item]) ?? [],
  );
  return assets.value
    .filter((item) => selectedIds.value.includes(item.id))
    .map((asset) => previous.get(asset.id) ?? {
      assetId: asset.id,
      purpose: referencePurpose(asset),
      instruction: "",
    });
}

function initializeRecommended() {
  if (!profileForm.value || !profile.value?.canonDefaults) return;
  if (profileForm.value.referenceBindings.length) {
    ElMessage.info("该项目已有默认参考，不会覆盖现有选择");
    return;
  }
  profileForm.value = cloneProfile(profile.value.canonDefaults);
  selectedIds.value = profileForm.value.referenceBindings.map((item) => item.assetId);
}

async function saveDefaults() {
  if (!graph.value || !profileForm.value) return;
  loading.value = true;
  try {
    profileForm.value.referenceBindings = buildBindings();
    const saved = await api.updateVisualProfile(graph.value.project.id, profileForm.value);
    await loadProject(graph.value.project.id);
    ElMessage.success(`视觉档案 Revision ${saved.revision} 已锁定`);
  } finally {
    loading.value = false;
  }
}

function restoreCanonDefaults() {
  if (!profile.value?.canonDefaults) return;
  profileForm.value = cloneProfile(profile.value.canonDefaults);
  selectedIds.value = profileForm.value.referenceBindings.map((item) => item.assetId);
  ElMessage.info("已恢复为 Canon 默认草稿，保存后才会创建/切换 revision");
}

function updateStyleList(kind: "positive" | "negative", value: unknown) {
  if (!profileForm.value) return;
  const items = String(value).split("\n").map((item) => item.trim()).filter(Boolean);
  if (kind === "positive") profileForm.value.stylePositive = items;
  else profileForm.value.styleNegative = items;
}

function closePreview(visible: boolean) {
  if (!visible) previewAsset.value = null;
}

async function recordImageFailure(asset: AssetDto) {
  try {
    const response = await fetch(assetContentUrl(asset.id), { method: "HEAD" });
    imageErrors.value[asset.id] = `HTTP ${response.status} · 资产 ${asset.id}`;
  } catch {
    imageErrors.value[asset.id] = `网络读取失败 · 资产 ${asset.id}`;
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
        <p>锁定人物、猫咪和画风身份；保存会创建或复用不可变项目视觉档案 revision。</p>
      </div>
      <div class="project-actions">
        <el-select v-model="projectId" placeholder="选择项目" filterable>
          <el-option v-for="project in projects" :key="project.id" :label="project.title" :value="project.id" />
        </el-select>
        <el-button :disabled="!graph" @click="initializeRecommended">初始化 Canon 档案</el-button>
        <el-button :disabled="!profile?.canonDefaults" @click="restoreCanonDefaults">恢复 Canon 默认</el-button>
        <el-button type="primary" :loading="loading" :disabled="!profileForm" @click="saveDefaults">保存新 Revision</el-button>
      </div>
    </header>

    <el-alert
      v-if="profileForm && !profileForm.referenceBindings.length"
      type="info"
      :closable="false"
      title="该项目还没有视觉参考；可初始化人物、猫咪、线条材质和室内/户外画风参考。"
    />

    <section v-if="profileForm && profile" class="profile-editor">
      <div class="profile-heading">
        <div>
          <h2>项目视觉档案 · Revision {{ profile.revision }}</h2>
          <p>锁定表示自动进入后续定妆与视频 Prompt；可编辑，保存后旧生成历史仍引用原 revision。</p>
        </div>
        <code>{{ profile.profileHash.slice(0, 16) }}</code>
      </div>
      <div class="profile-grid">
        <el-form-item label="人物身份"><el-input v-model="profileForm.personIdentity" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="人物发型"><el-input v-model="profileForm.personHair" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="年龄、体型与比例"><el-input v-model="profileForm.personBody" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="猫咪身份"><el-input v-model="profileForm.catIdentity" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="画风正向（每行一项）">
          <el-input :model-value="profileForm.stylePositive.join('\n')" type="textarea" :rows="5" @update:model-value="updateStyleList('positive', $event)" />
        </el-form-item>
        <el-form-item label="画风排除（每行一项）">
          <el-input :model-value="profileForm.styleNegative.join('\n')" type="textarea" :rows="5" @update:model-value="updateStyleList('negative', $event)" />
        </el-form-item>
      </div>
      <el-alert type="warning" :closable="false" title="采茶叶.mp4 只定义二维水彩画风；样片马尾人物不是角色 Canon。当前固定角色仍为 5–7 岁短波波头儿童与灰白虎斑猫。" />
    </section>

    <section v-for="group in groups" :key="group.key" class="canon-group">
      <h2>{{ group.title }} <small>{{ group.items.length }} 张</small></h2>
      <div class="grid">
        <article v-for="asset in group.items" :key="asset.id" :class="{ selected: selectedIds.includes(asset.id), missing: !asset.contentReady }">
          <button class="preview" type="button" :disabled="!asset.contentReady" @click="previewAsset = asset">
            <img v-if="asset.contentReady && !imageErrors[asset.id]" :src="assetContentUrl(asset.id)" :alt="assetName(asset)" @error="recordImageFailure(asset)" />
            <div v-else class="missing-placeholder">{{ imageErrors[asset.id] || '文件缺失' }}<br /><small>请检查 storage_key 或运行 canon-repair 修复</small></div>
          </button>
          <div class="asset-caption">
            <el-checkbox v-model="selectedIds" :label="asset.id" :disabled="!asset.contentReady">
              {{ asset.displayName || assetName(asset) }}
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
      @update:model-value="closePreview"
    >
      <img v-if="previewAsset?.contentReady && !imageErrors[previewAsset.id]" class="large-preview" :src="assetContentUrl(previewAsset.id)" :alt="assetName(previewAsset)" @error="recordImageFailure(previewAsset)" />
      <el-alert v-else-if="previewAsset" type="error" :closable="false" :title="imageErrors[previewAsset.id] || `资产 ${previewAsset.id} 内容缺失，请执行 Canon repair`" />
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
.profile-editor { margin: 22px 0; padding: 18px; background: #161b23; border: 1px solid #2b323f; border-radius: 12px; }
.profile-heading { display: flex; justify-content: space-between; gap: 20px; align-items: start; }
.profile-heading h2 { margin: 0; }.profile-heading code { color: #7faeff; }
.profile-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; }
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
@media (max-width: 800px) { header { flex-direction: column; }.profile-grid { grid-template-columns: 1fr; } }
</style>
