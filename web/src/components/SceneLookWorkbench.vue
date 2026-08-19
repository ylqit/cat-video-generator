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
import { useRuntimeStatus } from "../runtimeStatus";
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
const workbenchTab = ref("look");
const taskCenter = useTaskCenter();
const runtimeStatus = useRuntimeStatus();
const paidReady = computed(() => runtimeStatus.settings.value?.arkReady === true);

const usableAssets = computed(() => props.assets.filter(
  (item) => item.mediaType === "image"
    && item.contentReady
    && ["approved", "ready"].includes(item.status),
));
const groups = computed(() => [
  {
    key: "person",
    title: "人物身份与体型",
    items: usableAssets.value.filter(
      (item) => item.referencePurpose === "person_identity" || item.referencePurpose === "person_body",
    ),
  },
  {
    key: "cat",
    title: "猫咪身份",
    items: usableAssets.value.filter((item) => item.referencePurpose === "cat_identity"),
  },
  {
    key: "style",
    title: "系列画风",
    items: usableAssets.value.filter((item) => item.referencePurpose === "style"),
  },
  {
    key: "other",
    title: "服装、道具与构图",
    items: usableAssets.value.filter(
      (item) => !["person_identity", "person_body", "cat_identity", "style"].includes(
        item.referencePurpose ?? "",
      ),
    ),
  },
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

async function open() {
  envelope.value = null;
  preview.value = null;
  errorText.value = "";
  visible.value = true;
  workbenchTab.value = "look";
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

async function skipSceneLook() {
  await open();
  if (!envelope.value) return;
  envelope.value.draft.lookPlan.imageRecommended = false;
  await saveDraft(false);
  visible.value = false;
  ElMessage.success("已标记为本场跳过；场景视觉基准不是片段生产的强制前置条件");
}

async function syncProjectProfile() {
  if (!envelope.value) return;
  await run(async () => {
    const current = await api.visualProfile(props.projectId);
    profile.value = current;
    const extras = envelope.value!.draft.referenceBindings.filter(
      (item) => ["wardrobe", "environment", "prop", "composition"].includes(item.purpose),
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
    ElMessage.success(`已引用项目视觉档案 Revision ${current.revision}；保存草稿后生效`);
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
  const environmentAsset = usableAssets.value.find((asset) => asset.semanticKey === expectedKey);
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
  const bindings = envelope.value.draft.referenceBindings.filter((item) => item.assetId !== asset.id);
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

function updateBinding(assetId: string, field: "purpose" | "instruction", value: string) {
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
    workbenchTab.value = "prompt";
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
    const runtimeRevision = runtimeStatus.settings.value?.current.revision;
    if (runtimeRevision === undefined) throw new Error("运行配置尚未加载");
    await ElMessageBox.confirm(
      `模型：${healthModel.value}\n参考图：${preview.value.referenceCount} 张\n`
      + `配置 revision：${runtimeStatus.settings.value?.current.revision ?? "未加载"}\n`
      + `${regenerate ? "重新生成并保留旧版本" : "生成新候选"}会产生一次 Seedream 费用。`,
      "生成确认",
    );
    const submitted = await api.generateSceneLook(
      props.scene.id,
      saved.revision,
      regenerate,
      reason,
      runtimeRevision,
    );
    registerTask(submitted.jobId, {
      kind: "generate_scene_look",
      label: regenerate ? `场景视觉基准 V${versions.value.length + 1}` : "场景视觉基准 V1",
      projectId: props.projectId,
      sceneId: props.scene.id,
      operationKey: "image:scene-look",
    });
    ElMessage.success("生成任务已提交；可以关闭工作台继续操作");
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : String(error);
  }
}

async function refreshVersions(notifyParent = true) {
  versions.value = await api.sceneLookVersions(props.scene.id);
  selectedVersion.value = versions.value.find((item) => item.selected)
    ?? versions.value[0]
    ?? null;
  if (notifyParent) emit("refreshed");
}

watch(() => taskCenter.sceneSignals.value[props.scene.id]?.revision ?? 0, () => {
  if (visible.value) void refreshVersions();
  else emit("refreshed");
});
watch(
  () => sceneLookAssets.value.map((asset) => `${asset.id}:${asset.status}`).join("|"),
  () => {
    if (visible.value) void refreshVersions(false);
  },
);

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
    selectedVersion.value = versions.value.find((item) => item.id === version.id)
      ?? versions.value[0]
      ?? null;
    emit("refreshed");
  });
}

async function recordImageFailure(asset: AssetDto) {
  try {
    const response = await fetch(assetContentUrl(asset.id), { method: "HEAD" });
    imageErrors.value[asset.id] = `HTTP ${response.status} · 资产 ${asset.id}`;
  } catch {
    imageErrors.value[asset.id] = `网络读取失败 · 资产 ${asset.id}`;
  }
}
</script>

<template>
  <article class="scene-look-card">
    <div class="scene-look-preview" @click="open">
      <el-image
        v-if="selectedAsset?.contentReady && !imageErrors[selectedAsset.id]"
        :src="assetContentUrl(selectedAsset.id)"
        fit="cover"
        @error="recordImageFailure(selectedAsset)"
      />
      <div v-else class="look-empty">场景共同造型<br />与环境基准</div>
      <el-tag v-if="activeTask" class="task-badge" type="warning" effect="dark">生成中</el-tag>
    </div>
    <div class="scene-look-copy">
      <div>
        <b>场景视觉基准</b>
        <el-tag size="small" type="info">按需可选 · 不是视频首帧</el-tag>
      </div>
      <p v-if="selectedAsset">当前：{{ selectedAsset.displayName }}</p>
      <p v-else>尚未选择已批准版本</p>
      <small>{{ sceneLookAssets.length }} 个历史版本 · 负责服饰、环境、共同道具与画风</small>
    </div>
    <div class="card-actions">
      <el-button v-if="!selectedAsset" text @click="skipSceneLook">本场跳过</el-button>
      <el-button type="primary" plain @click="open">
        {{ sceneLookAssets.length ? "查看版本与重试" : "开始设计" }}
      </el-button>
    </div>
  </article>

  <el-dialog
    v-model="visible"
    title="场景视觉基准（不是视频首帧）"
    width="min(1420px, 98vw)"
    top="2vh"
    destroy-on-close
    class="scene-look-dialog"
  >
    <div v-loading="busy" class="look-workbench">
      <el-alert v-if="errorText" type="error" :closable="false" :title="errorText" show-icon />

      <header class="workbench-toolbar">
        <div>
          <b>{{ selectedVersion ? `当前查看 V${selectedVersion.attempt ?? "?"}` : "尚无视觉基准版本" }}</b>
          <small>
            {{ selectedAsset ? `场景已选择 ${selectedAsset.displayName}` : "场景尚未选择批准版本" }}
            · 共 {{ versions.length }} 个历史版本
          </small>
          <small v-if="preview">Prompt {{ preview.charCount }} 字 · {{ preview.referenceCount }} 张参考图</small>
        </div>
        <div class="toolbar-actions">
          <el-button :disabled="!envelope" @click="previewPrompt">预览 Prompt</el-button>
          <el-button
            type="primary"
            :loading="Boolean(activeTask)"
            :disabled="!envelope || Boolean(activeTask) || !paidReady"
            @click="generate"
          >
            {{ versions.length ? "生成新候选 / 重试" : "生成首个候选" }}
          </el-button>
        </div>
      </header>

      <div class="look-workspace-layout">
        <section class="look-preview-panel">
          <div class="large-preview">
            <el-image
              v-if="selectedVersion?.contentReady && !imageErrors[selectedVersion.id]"
              :src="assetContentUrl(selectedVersion.id)"
              fit="contain"
              :preview-src-list="[assetContentUrl(selectedVersion.id)]"
              @error="recordImageFailure(selectedVersion)"
            />
            <div v-else-if="selectedVersion" class="look-empty error">
              {{ imageErrors[selectedVersion.id] || `资产 ${selectedVersion.id} 内容不可用` }}
            </div>
            <div v-else class="look-empty">生成候选后，可在这里大图查看、批准、拒绝和切换历史版本。</div>
          </div>

          <div v-if="versions.length" class="version-strip">
            <button
              v-for="version in versions"
              :key="version.id"
              type="button"
              :class="{ active: selectedVersion?.id === version.id }"
              @click="selectedVersion = version"
            >
              <el-image
                v-if="version.contentReady && !imageErrors[version.id]"
                :src="assetContentUrl(version.id)"
                fit="cover"
                @error="recordImageFailure(version)"
              />
              <span>V{{ version.attempt ?? "?" }}</span>
              <small>{{ version.selected ? "当前采用" : version.status }}</small>
            </button>
          </div>

          <div v-if="selectedVersion" class="version-summary">
            <div>
              <el-tag :type="selectedVersion.status === 'approved' ? 'success' : selectedVersion.status === 'rejected' ? 'danger' : 'warning'">
                {{ selectedVersion.selected ? "当前采用" : selectedVersion.status }}
              </el-tag>
              <span>{{ selectedVersion.createdAt ? new Date(selectedVersion.createdAt).toLocaleString() : "时间未记录" }}</span>
            </div>
            <div class="gallery-actions">
              <el-button v-if="selectedVersion.status === 'candidate'" type="success" @click="decide(selectedVersion, 'approved')">批准并选择</el-button>
              <el-button v-if="selectedVersion.status === 'candidate'" type="danger" plain @click="decide(selectedVersion, 'rejected')">拒绝</el-button>
              <el-button v-if="selectedVersion.status === 'approved' && !selectedVersion.selected" type="primary" plain @click="selectVersion(selectedVersion)">重新选择此版本</el-button>
            </div>
          </div>

          <div v-if="selectedVersion" class="version-references">
            <b>本版本实际参考</b>
            <div class="reference-strip">
              <div v-for="asset in versionReferences(selectedVersion)" :key="asset.id">
                <el-image
                  v-if="asset.contentReady && !imageErrors[asset.id]"
                  :src="assetContentUrl(asset.id)"
                  fit="cover"
                  :preview-src-list="[assetContentUrl(asset.id)]"
                  @error="recordImageFailure(asset)"
                />
                <span>{{ asset.displayName }}</span>
              </div>
            </div>
            <details v-if="selectedVersion.prompt"><summary>本次 Prompt</summary><pre>{{ selectedVersion.prompt.text }}</pre></details>
            <details><summary>输入快照与审计</summary><pre>{{ JSON.stringify(selectedVersion.inputSnapshot, null, 2) }}</pre></details>
          </div>
        </section>

        <section class="look-editor-panel">
          <el-tabs v-model="workbenchTab" stretch>
            <el-tab-pane label="造型设定" name="look">
              <div v-if="profile" class="profile-card">
                <div class="section-heading">
                  <div>
                    <b>角色与画风锁定 · Revision {{ profile.revision }}</b>
                    <small>项目人物和猫咪身份只在项目设置中修改。</small>
                  </div>
                  <el-button size="small" plain @click="syncProjectProfile">同步项目档案</el-button>
                </div>
                <p><b>人物：</b>{{ profile.personIdentity }}；{{ profile.personHair }}；{{ profile.personBody }}</p>
                <p><b>猫咪：</b>{{ profile.catIdentity }}</p>
                <p><b>画风：</b>{{ profile.stylePositive.join("、") }}</p>
              </div>

              <template v-if="envelope">
                <div class="draft-heading">
                  <b>本场共同视觉设定</b>
                  <span>草稿 Revision {{ envelope.revision }}</span>
                </div>
                <div class="form-grid">
                  <el-form-item label="人物服装"><el-input v-model="envelope.draft.lookPlan.personWardrobe" /></el-form-item>
                  <el-form-item label="人物配件"><el-input v-model="envelope.draft.lookPlan.personAccessories" /></el-form-item>
                  <el-form-item label="猫咪外观与可选配件"><el-input v-model="envelope.draft.lookPlan.catAppearance" placeholder="默认保持 Canon 外观" /></el-form-item>
                  <el-form-item label="共同道具"><el-input v-model="envelope.draft.lookPlan.keyProps" /></el-form-item>
                  <el-form-item label="人物展示姿态"><el-input v-model="envelope.draft.lookPlan.personPose" /></el-form-item>
                  <el-form-item label="猫咪展示姿态"><el-input v-model="envelope.draft.lookPlan.catPose" /></el-form-item>
                  <el-form-item label="场景环境">
                    <el-radio-group :model-value="envelope.draft.lookPlan.environmentStyle" @update:model-value="setEnvironment">
                      <el-radio-button value="outdoor">户外</el-radio-button>
                      <el-radio-button value="indoor">室内</el-radio-button>
                    </el-radio-group>
                  </el-form-item>
                  <el-form-item label="建议生成">
                    <el-switch v-model="envelope.draft.lookPlan.imageRecommended" active-text="作为本场共同视觉基准" />
                  </el-form-item>
                </div>
                <el-form-item label="构图与人猫空间关系"><el-input v-model="envelope.draft.lookPlan.composition" type="textarea" :rows="3" /></el-form-item>
                <el-form-item label="补充生成要求"><el-input v-model="envelope.draft.lookPlan.additionalInstructions" type="textarea" :rows="3" /></el-form-item>
                <el-form-item label="建议原因"><el-input v-model="envelope.draft.lookPlan.recommendationReason" type="textarea" :rows="2" /></el-form-item>
                <div class="editor-actions"><el-button type="primary" plain @click="run(() => saveDraft().then(() => undefined))">保存造型草稿</el-button></div>
              </template>
            </el-tab-pane>

            <el-tab-pane label="参考资产" name="references">
              <p class="muted">至少包含人物身份、猫咪身份和画风。系统按资产 ID 与内容 SHA 去重，最多 14 张。</p>
              <div v-for="group in groups" :key="group.key" class="asset-group">
                <b>{{ group.title }}</b>
                <div class="asset-grid">
                  <article v-for="asset in group.items" :key="asset.id" :class="{ chosen: selectedIds.has(asset.id) }">
                    <el-checkbox :model-value="selectedIds.has(asset.id)" @change="toggleAsset(asset, Boolean($event))">{{ asset.displayName }}</el-checkbox>
                    <el-image
                      v-if="!imageErrors[asset.id]"
                      :src="assetContentUrl(asset.id)"
                      fit="contain"
                      :preview-src-list="[assetContentUrl(asset.id)]"
                      @error="recordImageFailure(asset)"
                    />
                    <div v-else class="image-failure">{{ imageErrors[asset.id] }}<br />请检查 contentReady 或执行 Canon repair</div>
                    <template v-if="bindingFor(asset.id)">
                      <el-select :model-value="bindingFor(asset.id)?.purpose" size="small" @update:model-value="updateBinding(asset.id, 'purpose', String($event))">
                        <el-option v-for="purpose in ['person_identity','person_body','cat_identity','style','wardrobe','environment','prop','composition']" :key="purpose" :label="purpose" :value="purpose" />
                      </el-select>
                      <el-input :model-value="bindingFor(asset.id)?.instruction" size="small" placeholder="可选：该图只负责什么" @update:model-value="updateBinding(asset.id, 'instruction', String($event))" />
                    </template>
                  </article>
                </div>
              </div>
              <div class="editor-actions"><el-button type="primary" plain @click="run(() => saveDraft().then(() => undefined))">保存参考选择</el-button></div>
            </el-tab-pane>

            <el-tab-pane label="Prompt 预览" name="prompt">
              <div class="section-heading">
                <div>
                  <b>确定性编译结果</b>
                  <small>预览不会调用 Ark；真实生成复用相同草稿、素材顺序和 Prompt。</small>
                </div>
                <el-button type="primary" plain :disabled="!envelope" @click="previewPrompt">保存并重新编译</el-button>
              </div>
              <template v-if="preview">
                <el-alert v-for="warning in preview.warnings" :key="warning" type="warning" :closable="false" :title="warning" />
                <p>{{ healthModel }} · {{ preview.referenceCount }} 张参考 · {{ preview.charCount }} 字</p>
                <ol>
                  <li v-for="item in preview.references" :key="item.assetId">
                    @图片{{ item.index }} · {{ item.purpose }} · {{ item.semanticKey }} · {{ item.instruction || "使用系统职责" }}
                  </li>
                </ol>
                <pre>{{ preview.prompt }}</pre>
              </template>
              <div v-else class="prompt-empty">点击“保存并重新编译”查看最终 Prompt、参考图顺序和预检结果。</div>
            </el-tab-pane>
          </el-tabs>
        </section>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.scene-look-card {
  display: grid;
  grid-template-columns: 116px minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  padding: 12px;
  border: 1px solid #2d394a;
  border-radius: 12px;
  background: linear-gradient(145deg, #131a24, #0d1219);
}
.scene-look-preview { position: relative; height: 108px; overflow: hidden; border-radius: 9px; background: #080b10; cursor: pointer; }
.scene-look-preview .el-image { width: 100%; height: 100%; }
.task-badge { position: absolute; top: 7px; right: 7px; }
.scene-look-copy { min-width: 0; }
.scene-look-copy > div { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.scene-look-copy p { margin: 7px 0 3px; color: #dbe4f0; }
.scene-look-copy small, .muted { color: #8f9bad; font-size: 12px; }
.card-actions { display: flex; justify-content: flex-end; gap: 6px; flex-wrap: wrap; }
.look-empty { display: grid; place-content: center; width: 100%; height: 100%; box-sizing: border-box; padding: 20px; text-align: center; color: #8290a4; background: radial-gradient(circle at 50% 35%, #1a2636, #090d13 68%); }
.look-empty.error { color: #df9a71; }
.look-workbench { min-height: 620px; display: grid; gap: 12px; }
.workbench-toolbar { position: sticky; top: 0; z-index: 4; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 14px; border: 1px solid #3b4a60; border-radius: 10px; background: #101722f2; backdrop-filter: blur(8px); }
.workbench-toolbar > div:first-child { display: grid; gap: 4px; }
.workbench-toolbar small { color: #8d9ab0; }
.toolbar-actions, .gallery-actions, .editor-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.look-workspace-layout { display: grid; grid-template-columns: minmax(430px, .9fr) minmax(560px, 1.1fr); gap: 14px; min-height: 0; }
.look-preview-panel, .look-editor-panel { min-width: 0; padding: 14px; border: 1px solid #293442; border-radius: 11px; background: #10161e; }
.look-preview-panel { display: flex; flex-direction: column; gap: 12px; }
.large-preview { height: min(62vh, 720px); min-height: 420px; overflow: hidden; border-radius: 9px; background: #080b0f; }
.large-preview .el-image { width: 100%; height: 100%; }
.version-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 5px; }
.version-strip button { flex: 0 0 104px; display: grid; gap: 3px; padding: 6px; color: #dce2ec; text-align: left; border: 1px solid #2b3442; border-radius: 8px; background: #0c1118; cursor: pointer; }
.version-strip button.active { border-color: #409eff; box-shadow: 0 0 0 1px #409eff inset; }
.version-strip .el-image { width: 90px; height: 76px; border-radius: 5px; background: #070a0e; }
.version-strip small { color: #8290a3; }
.version-summary { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.version-summary > div:first-child { display: flex; align-items: center; gap: 8px; color: #929eb0; }
.version-references { display: grid; gap: 9px; border-top: 1px solid #263140; padding-top: 12px; }
.reference-strip { display: flex; gap: 8px; overflow-x: auto; }
.reference-strip > div { flex: 0 0 92px; display: grid; gap: 4px; font-size: 11px; color: #8f9bad; }
.reference-strip .el-image { width: 92px; height: 78px; border-radius: 6px; background: #080b10; }
.version-references pre, .look-editor-panel pre { max-height: 360px; overflow: auto; padding: 12px; white-space: pre-wrap; border-radius: 8px; background: #080b10; }
.look-editor-panel { max-height: calc(96vh - 150px); overflow-y: auto; }
.profile-card { padding: 12px; margin-bottom: 14px; border: 1px solid #2c3a4d; border-radius: 9px; background: #0d141d; }
.profile-card p { margin: 7px 0; color: #aeb8c8; line-height: 1.55; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.section-heading > div:first-child { display: grid; gap: 4px; }
.section-heading small { color: #8f9bad; }
.draft-heading { display: flex; justify-content: space-between; margin: 12px 0; color: #c7d2e2; }
.draft-heading span { color: #8492a6; font-size: 12px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }
.asset-group { margin-top: 16px; }
.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 9px; margin-top: 7px; }
.asset-grid article { display: grid; gap: 6px; padding: 8px; border: 1px solid #2c3441; border-radius: 8px; }
.asset-grid article.chosen { border-color: #409eff; }
.asset-grid .el-image, .image-failure { width: 100%; height: 126px; background: #090c11; }
.image-failure { display: grid; place-content: center; box-sizing: border-box; padding: 8px; color: #dc9d62; font-size: 11px; }
.editor-actions { justify-content: flex-end; margin-top: 12px; }
.prompt-empty { display: grid; place-content: center; min-height: 280px; color: #8391a4; text-align: center; border: 1px dashed #334054; border-radius: 9px; }
@media (max-width: 1050px) {
  .look-workspace-layout { grid-template-columns: 1fr; }
  .large-preview { min-height: 360px; height: 52vh; }
  .look-editor-panel { max-height: none; }
}
@media (max-width: 720px) {
  .scene-look-card { grid-template-columns: 86px 1fr; }
  .scene-look-card > .card-actions { grid-column: 1 / -1; }
  .scene-look-preview { height: 86px; }
  .workbench-toolbar, .section-heading, .version-summary { align-items: flex-start; flex-direction: column; }
  .form-grid { grid-template-columns: 1fr; }
}
</style>
