<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, ref, watch } from "vue";

import { api, assetContentUrl } from "../api/client";
import type {
  AssetDto,
  LookReferenceBinding,
  LookReferencePurpose,
  SceneDto,
  SceneLookDraftEnvelope,
  SceneLookPromptPreview,
  SceneLookVersion,
  VisualProfileRevisionDto,
} from "../api/types";
import { registerTask, useTaskCenter } from "../tasks/taskCenter";

const props = defineProps<{
  projectId: string;
  scene: SceneDto;
  assets: AssetDto[];
}>();
const emit = defineEmits<{ refreshed: [] }>();

const visible = ref(false);
const busy = ref(false);
const errorText = ref("");
const healthModel = ref("doubao-seedream-5-0-260128");
const profile = ref<VisualProfileRevisionDto | null>(null);
const envelope = ref<SceneLookDraftEnvelope | null>(null);
const preview = ref<SceneLookPromptPreview | null>(null);
const versions = ref<SceneLookVersion[]>([]);
const selectedVersion = ref<SceneLookVersion | null>(null);
const imageErrors = ref<Record<string, string>>({});
const taskCenter = useTaskCenter();
const versionGallery = ref<HTMLElement | null>(null);

const usableAssets = computed(() => props.assets.filter(
  (item) => item.mediaType === "image"
    && item.contentReady
    && ["approved", "ready"].includes(item.status),
));
const groups = computed(() => [
  { key: "person", title: "人物身份与体型", items: usableAssets.value.filter((item) => item.referencePurpose === "person_identity" || item.referencePurpose === "person_body") },
  { key: "cat", title: "猫咪身份", items: usableAssets.value.filter((item) => item.referencePurpose === "cat_identity") },
  { key: "style", title: "画风", items: usableAssets.value.filter((item) => item.referencePurpose === "style") },
  { key: "other", title: "服装、道具与构图", items: usableAssets.value.filter((item) => !item.referencePurpose) },
]);
const selectedIds = computed(() => new Set(
  envelope.value?.draft.referenceBindings.map((item) => item.assetId) ?? [],
));
const selectedAsset = computed(() => props.assets.find(
  (item) => item.id === props.scene.selectedLookAssetId,
) ?? null);
const sceneLookAssets = computed(() => props.assets.filter(
  (item) => item.sceneId === props.scene.id && item.role === "scene_look",
));
const activeTask = computed(() => taskCenter.items.value.find(
  (item) => item.sceneId === props.scene.id
    && item.operationKey === "image:scene-look"
    && ["queued", "pending", "submitting", "running", "restart_pending"].includes(item.status),
) ?? null);

async function open() {
  visible.value = true;
  await run(async () => {
    const [loadedProfile, loadedDraft, loadedVersions, health] = await Promise.all([
      api.visualProfile(props.projectId),
      api.sceneLookDraft(props.scene.id),
      api.sceneLookVersions(props.scene.id),
      api.health(),
    ]);
    profile.value = loadedProfile;
    envelope.value = loadedDraft;
    versions.value = loadedVersions;
    healthModel.value = health.arkImageModel ?? healthModel.value;
    selectedVersion.value = loadedVersions.find((item) => item.selected)
      ?? loadedVersions[0]
      ?? null;
    preview.value = null;
  });
}

async function run(action: () => Promise<void>) {
  busy.value = true;
  errorText.value = "";
  try {
    await action();
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

async function syncProjectProfile() {
  if (!envelope.value) return;
  await run(async () => {
    const current = await api.visualProfile(props.projectId);
    profile.value = current;
    const extras = envelope.value!.draft.referenceBindings.filter(
      (item) => ["wardrobe", "prop", "composition"].includes(item.purpose),
    );
    const environmentKey = `style:${envelope.value!.draft.lookPlan.environmentStyle}`;
    const profileBindings = current.referenceBindings.filter((binding) => {
      const semanticKey = props.assets.find((asset) => asset.id === binding.assetId)?.semanticKey;
      return !["style:outdoor", "style:indoor"].includes(semanticKey ?? "")
        || semanticKey === environmentKey;
    });
    envelope.value!.draft.visualProfileRevisionId = current.id;
    envelope.value!.draft.referenceBindings = [
      ...profileBindings,
      ...extras.filter((extra) => !profileBindings.some(
        (item) => item.assetId === extra.assetId,
      )),
    ];
    preview.value = null;
    ElMessage.success(`本场视觉基准草稿已引用项目视觉档案 Revision ${current.revision}，保存草稿后生效`);
  });
}

function defaultPurpose(asset: AssetDto): LookReferencePurpose {
  return asset.referencePurpose ?? "composition";
}

function setEnvironment(value: unknown) {
  if (!envelope.value) return;
  const environment = String(value) === "indoor" ? "indoor" : "outdoor";
  envelope.value.draft.lookPlan.environmentStyle = environment;
  const expectedKey = `style:${environment}`;
  const bindings = envelope.value.draft.referenceBindings.filter((binding) => {
    const semanticKey = props.assets.find((asset) => asset.id === binding.assetId)?.semanticKey;
    return !["style:outdoor", "style:indoor"].includes(semanticKey ?? "");
  });
  const environmentAsset = usableAssets.value.find(
    (asset) => asset.semanticKey === expectedKey,
  );
  if (environmentAsset && !bindings.some((item) => item.assetId === environmentAsset.id)) {
    bindings.push({
      assetId: environmentAsset.id,
      purpose: "style",
      instruction: environment === "indoor"
        ? "只参考室内材质、窗光和低对比色彩"
        : "只参考户外色彩、自然光和空气透视",
    });
  }
  envelope.value.draft.referenceBindings = bindings;
  preview.value = null;
}

function toggleAsset(asset: AssetDto, selected: boolean) {
  if (!envelope.value) return;
  const bindings = envelope.value.draft.referenceBindings.filter(
    (item) => item.assetId !== asset.id,
  );
  if (selected) {
    bindings.push({
      assetId: asset.id,
      purpose: defaultPurpose(asset),
      instruction: "",
    });
  }
  envelope.value.draft.referenceBindings = bindings;
  preview.value = null;
}

function bindingFor(assetId: string): LookReferenceBinding | undefined {
  return envelope.value?.draft.referenceBindings.find((item) => item.assetId === assetId);
}

function versionReferences(version: SceneLookVersion): AssetDto[] {
  const references = Array.isArray(version.inputSnapshot.references)
    ? version.inputSnapshot.references
    : [];
  const ids = references.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const id = (item as Record<string, unknown>).assetId;
    return typeof id === "string" ? [id] : [];
  });
  return ids.flatMap((id) => {
    const asset = props.assets.find((item) => item.id === id);
    return asset ? [asset] : [];
  });
}

function updateBinding(
  assetId: string,
  field: "purpose" | "instruction",
  value: string,
) {
  const binding = bindingFor(assetId);
  if (!binding) return;
  if (field === "purpose") binding.purpose = value as LookReferencePurpose;
  else binding.instruction = value;
  preview.value = null;
}

async function saveDraft(showMessage = true): Promise<SceneLookDraftEnvelope | null> {
  if (!envelope.value) return null;
  const saved = await api.saveSceneLookDraft(
    props.scene.id,
    envelope.value.revision,
    envelope.value.draft,
  );
  envelope.value = saved;
  if (showMessage) ElMessage.success(`场景视觉基准草稿 Revision ${saved.revision} 已保存`);
  emit("refreshed");
  return saved;
}

async function previewPrompt() {
  await run(async () => {
    await saveDraft(false);
    preview.value = await api.previewSceneLookPrompt(props.scene.id);
  });
}

async function generate() {
  errorText.value = "";
  try {
    const saved = await saveDraft(false);
    if (!saved) return;
    preview.value = await api.previewSceneLookPrompt(props.scene.id);
    if (preview.value.warnings.length) {
      throw new Error(`生成前预检未通过：${preview.value.warnings.join("；")}`);
    }
    const regenerate = versions.value.length > 0;
    let reason: string | undefined;
    if (regenerate) {
      const answer = await ElMessageBox.prompt(
        "只填写本次需要修正的一项，其他角色与画风锁定保持不变。",
        "视觉基准图重做目标",
        { inputPlaceholder: "例如：围巾改为米白色，猫咪身份和其余构图保持不变" },
      );
      reason = answer.value.trim();
      if (!reason) throw new Error("重新生成必须填写单项修正目标");
    }
    await ElMessageBox.confirm(
      `模型：${healthModel.value}\n参考图：${preview.value.referenceCount} 张\n`
      + `${regenerate ? "重新生成并保留旧版本" : "生成新候选"}会产生一次 Seedream 费用。`,
      "生成确认",
    );
    const submitted = await api.generateSceneLook(
      props.scene.id,
      saved.revision,
      regenerate,
      reason,
    );
    registerTask(submitted.jobId, {
      kind: "generate_scene_look",
      label: regenerate ? `场景视觉基准 V${versions.value.length + 1}` : "场景视觉基准 V1",
      projectId: props.projectId,
      sceneId: props.scene.id,
      operationKey: "image:scene-look",
    });
    ElMessage.success("场景视觉基准任务已提交，可关闭工作台继续操作");
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : String(error);
  }
}

async function refreshVersions() {
  versions.value = await api.sceneLookVersions(props.scene.id);
  selectedVersion.value = versions.value.find((item) => item.selected)
    ?? versions.value[0]
    ?? null;
  emit("refreshed");
}

function scrollToVersions() {
  versionGallery.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

watch(() => taskCenter.revision.value, () => {
  const event = taskCenter.lastEvent.value;
  if (event?.item.sceneId !== props.scene.id) return;
  if (visible.value) void refreshVersions();
  else emit("refreshed");
});

async function decide(version: SceneLookVersion, decision: "approved" | "rejected") {
  await run(async () => {
    await api.reviewAsset(
      version.id,
      decision,
      decision === "approved" ? "人工确认角色、画风与本场景造型" : "人工拒绝视觉基准候选",
    );
    versions.value = await api.sceneLookVersions(props.scene.id);
    selectedVersion.value = versions.value.find((item) => item.id === version.id)
      ?? versions.value[0]
      ?? null;
    emit("refreshed");
  });
}

async function selectVersion(version: SceneLookVersion) {
  await run(async () => {
    await api.selectSceneLook(props.scene.id, version.id);
    versions.value = await api.sceneLookVersions(props.scene.id);
    emit("refreshed");
  });
}

async function recordImageFailure(asset: AssetDto) {
  try {
    const response = await fetch(assetContentUrl(asset.id), { method: "HEAD" });
    imageErrors.value[asset.id] = `HTTP ${response.status} · 资产 ${asset.id}`;
  } catch (error) {
    imageErrors.value[asset.id] = `网络读取失败 · 资产 ${asset.id}`;
  }
}
</script>

<template>
  <div class="look-entry">
    <el-button size="small" type="primary" plain @click="open">打开视觉基准工作台</el-button>
    <el-tag v-if="activeTask" type="info">{{ activeTask.status }}</el-tag>
    <span v-if="selectedAsset">当前已选：{{ selectedAsset.displayName }} · 共 {{ sceneLookAssets.length }} 个版本</span>
    <span v-else>尚未选择批准版本 · 共 {{ sceneLookAssets.length }} 个版本</span>
    <el-image
      v-if="selectedAsset?.contentReady && !imageErrors[selectedAsset.id]"
      class="selected-thumb"
      :src="assetContentUrl(selectedAsset.id)"
      :preview-src-list="[assetContentUrl(selectedAsset.id)]"
      fit="contain"
      @error="recordImageFailure(selectedAsset)"
    />
  </div>

  <el-dialog v-model="visible" title="场景视觉基准工作台" width="min(1180px, 96vw)" destroy-on-close>
    <div v-loading="busy" class="look-workbench">
      <el-alert v-if="errorText" type="error" :closable="false" :title="errorText" show-icon />
      <section class="workbench-toolbar">
        <div>
          <b>{{ selectedVersion ? `当前查看 V${selectedVersion.attempt ?? '?'}` : '尚无视觉基准版本' }}</b>
          <small>{{ selectedAsset ? `场景已选择 ${selectedAsset.displayName}` : '场景尚未选择批准版本' }} · {{ versions.length }} 个历史版本</small>
          <small v-if="preview">当前 Prompt {{ preview.charCount }} 字 · {{ preview.referenceCount }} 张参考图</small>
        </div>
        <div>
          <el-button @click="scrollToVersions">查看全部版本</el-button>
          <el-button type="primary" :disabled="!envelope || Boolean(activeTask)" @click="generate">{{ versions.length ? '重试生成新候选' : '生成新候选' }}</el-button>
        </div>
      </section>

      <el-collapse v-if="profile" model-value="profile">
        <el-collapse-item name="profile" title="1. 角色与画风锁定（项目视觉档案，只读）">
          <div class="revision-line">
            <span>当前 Revision {{ profile.revision }} · {{ profile.profileHash.slice(0, 16) }}</span>
            <el-button type="primary" plain @click="syncProjectProfile">同步当前项目档案到本场视觉基准草稿</el-button>
          </div>
          <el-alert type="info" :closable="false" title="项目视觉档案只在“项目设置”中编辑；这里确认本场视觉基准实际引用的不可变 Revision。" />
          <div class="profile-summary">
            <p><b>人物：</b>{{ profile.personIdentity }}；{{ profile.personHair }}；{{ profile.personBody }}</p>
            <p><b>猫咪：</b>{{ profile.catIdentity }}</p>
            <p><b>画风：</b>{{ profile.stylePositive.join('、') }}</p>
          </div>
        </el-collapse-item>
      </el-collapse>

      <section v-if="envelope" class="section">
        <h3>2. 场景视觉基准草稿 · Revision {{ envelope.revision }}</h3>
        <div class="form-grid">
          <el-form-item label="人物服装"><el-input v-model="envelope.draft.lookPlan.personWardrobe" /></el-form-item>
          <el-form-item label="人物配件"><el-input v-model="envelope.draft.lookPlan.personAccessories" /></el-form-item>
          <el-form-item label="猫咪外观与可选配件"><el-input v-model="envelope.draft.lookPlan.catAppearance" placeholder="默认保持 Canon 外观，不增加帽子或背包" /></el-form-item>
          <el-form-item label="关键道具"><el-input v-model="envelope.draft.lookPlan.keyProps" /></el-form-item>
          <el-form-item label="人物姿态"><el-input v-model="envelope.draft.lookPlan.personPose" /></el-form-item>
          <el-form-item label="猫咪姿态"><el-input v-model="envelope.draft.lookPlan.catPose" /></el-form-item>
          <el-form-item label="场景环境"><el-radio-group :model-value="envelope.draft.lookPlan.environmentStyle" @update:model-value="setEnvironment"><el-radio-button value="outdoor">户外</el-radio-button><el-radio-button value="indoor">室内</el-radio-button></el-radio-group></el-form-item>
          <el-form-item label="视觉基准建议"><el-switch v-model="envelope.draft.lookPlan.imageRecommended" active-text="建议生成（不阻断视频）" /></el-form-item>
        </div>
        <el-form-item label="构图与人猫空间关系"><el-input v-model="envelope.draft.lookPlan.composition" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="补充生成要求"><el-input v-model="envelope.draft.lookPlan.additionalInstructions" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="建议原因"><el-input v-model="envelope.draft.lookPlan.recommendationReason" type="textarea" :rows="2" /></el-form-item>
      </section>

      <section v-if="envelope" class="section">
        <h3>3. 本次参考资产</h3>
        <p class="muted">至少包含人物身份、猫咪身份和画风。后端固定顺序、按资产 ID 与内容 SHA 去重，最多 14 张。</p>
        <div v-for="group in groups" :key="group.key" class="asset-group">
          <b>{{ group.title }}</b>
          <div class="asset-grid">
            <article v-for="asset in group.items" :key="asset.id" :class="{ chosen: selectedIds.has(asset.id) }">
              <el-checkbox :model-value="selectedIds.has(asset.id)" @change="toggleAsset(asset, Boolean($event))">{{ asset.displayName }}</el-checkbox>
              <el-image v-if="!imageErrors[asset.id]" :src="assetContentUrl(asset.id)" fit="contain" :preview-src-list="[assetContentUrl(asset.id)]" @error="recordImageFailure(asset)" />
              <div v-else class="image-failure">{{ imageErrors[asset.id] }}<br />请检查 contentReady 或运行 Canon repair</div>
              <template v-if="bindingFor(asset.id)">
                <el-select :model-value="bindingFor(asset.id)?.purpose" size="small" @update:model-value="updateBinding(asset.id, 'purpose', String($event))">
                  <el-option v-for="purpose in ['person_identity','person_body','cat_identity','style','wardrobe','prop','composition']" :key="purpose" :label="purpose" :value="purpose" />
                </el-select>
                <el-input :model-value="bindingFor(asset.id)?.instruction" size="small" placeholder="可选：该图只负责什么" @update:model-value="updateBinding(asset.id, 'instruction', String($event))" />
              </template>
              <small v-if="imageErrors[asset.id]">{{ imageErrors[asset.id] }} · 请检查 contentReady 或运行 Canon repair</small>
            </article>
          </div>
        </div>
      </section>

      <section class="section prompt-section">
        <div class="section-heading"><h3>4. 最终 Prompt 与付费前预检</h3><div><el-button :disabled="!envelope" @click="run(() => saveDraft().then(() => undefined))">保存草稿</el-button><el-button :disabled="!envelope" @click="previewPrompt">编译预览</el-button><el-button type="primary" :disabled="!preview || preview.warnings.length > 0 || Boolean(activeTask)" @click="generate">确认并生成</el-button></div></div>
        <template v-if="preview">
          <el-alert v-for="warning in preview.warnings" :key="warning" type="warning" :closable="false" :title="warning" />
          <p>{{ healthModel }} · {{ preview.referenceCount }} 张参考 · {{ preview.charCount }} 字</p>
          <ol><li v-for="item in preview.references" :key="item.assetId">@图片{{ item.index }} · {{ item.purpose }} · {{ item.semanticKey }} · {{ item.instruction || '使用系统职责' }}</li></ol>
          <pre>{{ preview.prompt }}</pre>
        </template>
      </section>

      <section ref="versionGallery" class="section">
        <h3>5. 视觉基准版本画廊</h3>
        <div class="gallery">
          <div class="version-list">
            <button v-for="version in versions" :key="version.id" type="button" :class="{ active: selectedVersion?.id === version.id }" @click="selectedVersion = version">
              <span>#{{ version.attempt ?? '?' }} · {{ version.status }}</span><small>{{ version.selected ? '当前场景视觉基准' : version.displayName }}</small><small>{{ version.createdAt ? new Date(version.createdAt).toLocaleString() : '时间未记录' }}</small>
            </button>
          </div>
          <div v-if="selectedVersion" class="version-detail">
            <el-image v-if="selectedVersion.contentReady && !imageErrors[selectedVersion.id]" class="large-image" :src="assetContentUrl(selectedVersion.id)" fit="contain" :preview-src-list="[assetContentUrl(selectedVersion.id)]" @error="recordImageFailure(selectedVersion)" />
            <el-alert v-else type="error" :closable="false" :title="imageErrors[selectedVersion.id] || `资产 ${selectedVersion.id} 内容缺失，请检查 storage_key 或执行修复`" />
            <div class="gallery-actions">
              <el-button v-if="selectedVersion.status === 'candidate'" type="success" @click="decide(selectedVersion, 'approved')">批准并选择</el-button>
              <el-button v-if="selectedVersion.status === 'candidate'" type="danger" @click="decide(selectedVersion, 'rejected')">拒绝</el-button>
              <el-button v-if="selectedVersion.status === 'approved' && !selectedVersion.selected" @click="selectVersion(selectedVersion)">选择此历史版本</el-button>
            </div>
            <div class="version-references">
              <div v-for="asset in versionReferences(selectedVersion)" :key="asset.id">
                <el-image v-if="asset.contentReady && !imageErrors[asset.id]" :src="assetContentUrl(asset.id)" fit="contain" :preview-src-list="[assetContentUrl(asset.id)]" @error="recordImageFailure(asset)" />
                <span>{{ asset.displayName }} · {{ asset.referencePurpose || asset.role }}</span>
              </div>
            </div>
            <details v-if="selectedVersion.prompt"><summary>本次 Prompt</summary><pre>{{ selectedVersion.prompt.text }}</pre></details>
            <details><summary>参考图与审计快照</summary><pre>{{ JSON.stringify(selectedVersion.inputSnapshot, null, 2) }}</pre></details>
          </div>
          <p v-else class="muted">尚无视觉基准图版本。</p>
        </div>
      </section>
    </div>
  </el-dialog>
</template>

<style scoped>
.look-entry { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 7px; }.look-entry span { color: #91a2ba; }.selected-thumb { width: 64px; height: 72px; background: #090c11; }
.look-workbench { min-height: 500px; display: grid; gap: 14px; }.section { padding: 14px; border: 1px solid #2b323f; border-radius: 9px; background: #11161e; }.section h3 { margin: 0 0 12px; }.section-heading,.revision-line { display: flex; align-items: center; justify-content: space-between; gap: 14px; }.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }.muted { color: #8d97a8; font-size: 12px; }.asset-group { margin-top: 14px; }.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 9px; margin-top: 7px; }.asset-grid article { display: grid; gap: 6px; padding: 8px; border: 1px solid #2c3441; border-radius: 7px; }.asset-grid article.chosen { border-color: #409eff; }.asset-grid .el-image,.image-failure { width: 100%; height: 150px; background: #090c11; }.image-failure { display: grid; place-content: center; box-sizing: border-box; padding: 8px; color: #dc9d62; font-size: 11px; }.asset-grid small { color: #dc9d62; }.prompt-section pre,.version-detail pre { white-space: pre-wrap; max-height: 360px; overflow: auto; background: #090c11; padding: 12px; border-radius: 7px; }.gallery { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 12px; }.version-list { display: grid; align-content: start; gap: 6px; }.version-list button { display: grid; gap: 3px; text-align: left; color: #dce2ec; background: #0d1219; border: 1px solid #2b3442; border-radius: 7px; padding: 9px; cursor: pointer; }.version-list button.active { border-color: #409eff; }.version-list small { color: #8490a2; }.large-image { width: 100%; height: min(66vh, 720px); background: #080b0f; }.gallery-actions { display: flex; gap: 8px; margin: 10px 0; }.version-references { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 7px; margin: 10px 0; }.version-references > div { display: grid; gap: 4px; color: #8d97a8; font-size: 10px; }.version-references .el-image { width: 100%; height: 100px; background: #090c11; }
.workbench-toolbar { position: sticky; top: 0; z-index: 3; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 14px; border: 1px solid #3b4a60; border-radius: 9px; background: #101722ee; backdrop-filter: blur(8px); }.workbench-toolbar > div { display: grid; gap: 4px; }.workbench-toolbar > div:last-child { display: flex; flex-wrap: wrap; }.workbench-toolbar small { color: #8d9ab0; }
.profile-summary { margin-top: 10px; color: #aeb8c8; line-height: 1.6; }.profile-summary p { margin: 5px 0; }
@media (max-width: 800px) { .form-grid,.gallery { grid-template-columns: 1fr; }.section-heading,.revision-line { align-items: flex-start; flex-direction: column; } }
</style>
