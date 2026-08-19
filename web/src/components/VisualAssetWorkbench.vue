<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, ref, watch } from "vue";

import { api, assetContentUrl } from "../api/client";
import type {
  AssetDto,
  CreativeStepRecord,
  LookReferencePurpose,
  SceneDto,
  SceneVisualAssetsDto,
  VisualAssetPlanOutput,
  VisualAssetPlanSelection,
  VisualAssetPurpose,
  VisualAssetVersion,
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
const data = ref<SceneVisualAssetsDto | null>(null);
const selectedPlanId = ref("");
const selections = ref<VisualAssetPlanSelection[]>([]);
const imageErrors = ref<Record<string, string>>({});
const uploadFiles = ref<Record<string, File | undefined>>({});
const taskCenter = useTaskCenter();
const runtimeStatus = useRuntimeStatus();
const paidReady = computed(() => runtimeStatus.settings.value?.arkReady === true);
let pendingRunCount = 0;
let refreshSequence = 0;

const selectedPlan = computed<CreativeStepRecord | null>(() => (
  data.value?.plans.find((item) => item.stepId === selectedPlanId.value)
  ?? data.value?.plans[0]
  ?? null
));
const planOutput = computed<VisualAssetPlanOutput | null>(() => {
  const value = selectedPlan.value?.providerOutput;
  return value ? value as unknown as VisualAssetPlanOutput : null;
});
const versions = computed(() => [
  ...(data.value?.scene ?? []),
  ...(data.value?.project ?? []),
].filter((item) => (
  ["generated_reference", "external_reference"].includes(item.role)
  && Boolean(item.referencePurpose ?? item.metadata.referencePurpose)
)));
const usableAssets = computed(() => {
  const merged = new Map<string, AssetDto>();
  for (const asset of [
    ...props.assets,
    ...(data.value?.canon ?? []),
    ...(data.value?.project ?? []),
    ...(data.value?.scene ?? []),
  ]) merged.set(asset.id, asset);
  return [...merged.values()].filter((item) => (
    item.mediaType === "image"
    && item.contentReady
    && ["approved", "ready"].includes(item.status)
    && (item.scope === "canon" || item.projectId === props.projectId)
  ));
});
const activePlanTask = computed(() => taskCenter.items.value.some((item) => (
  item.sceneId === props.scene.id
  && item.operationKey === "director:visual-asset-plan"
  && ["queued", "running", "pending", "submitting", "restart_pending"].includes(item.status)
)));
const activeReferenceTasks = computed(() => taskCenter.items.value.filter((item) => (
  item.projectId === props.projectId
  && item.operationKey?.startsWith("image:reference:")
  && ["queued", "running", "pending", "submitting", "restart_pending"].includes(item.status)
)));
const selectedReferenceCount = computed(() => new Set(
  selections.value.flatMap((item) => item.referenceAssetIds),
).size);
const currentSceneLook = computed(() => props.assets.find(
  (item) => item.id === props.scene.selectedLookAssetId,
) ?? null);
const approvedByPurpose = computed(() => {
  const grouped: Record<VisualAssetPurpose, VisualAssetVersion[]> = {
    wardrobe: [],
    environment: [],
    prop: [],
    composition: [],
  };
  for (const asset of versions.value) {
    const purpose = asset.referencePurpose ?? asset.metadata.referencePurpose;
    if (["approved", "ready"].includes(asset.status)
      && typeof purpose === "string"
      && purpose in grouped) {
      grouped[purpose as VisualAssetPurpose].push(asset);
    }
  }
  return grouped;
});
const sceneAnchors = computed(() => props.assets.filter((item) => (
  item.sceneId === props.scene.id && ["shot_anchor", "shot_tail_frame"].includes(item.role)
)));

const purposeLabels: Record<VisualAssetPurpose, string> = {
  wardrobe: "服装与配件",
  environment: "环境参考",
  prop: "关键道具",
  composition: "构图参考",
};

async function run(action: () => Promise<void>) {
  pendingRunCount += 1;
  busy.value = true;
  errorText.value = "";
  try {
    await action();
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : String(error);
  } finally {
    pendingRunCount -= 1;
    busy.value = pendingRunCount > 0;
  }
}

async function open() {
  visible.value = true;
  await refresh();
}

async function refresh() {
  const sequence = ++refreshSequence;
  await run(async () => {
    const nextData = await api.sceneVisualAssets(props.scene.id);
    if (sequence !== refreshSequence) return;
    data.value = nextData;
    if (!data.value.plans.some((item) => item.stepId === selectedPlanId.value)) {
      selectedPlanId.value = data.value.plans[0]?.stepId ?? "";
    }
    loadSelectionDraft();
  });
}

function loadSelectionDraft() {
  const accepted = selectedPlan.value?.acceptedOutput?.selections;
  if (Array.isArray(accepted)) {
    selections.value = accepted.map((item) => ({
      ...(item as unknown as VisualAssetPlanSelection),
      referenceAssetIds: [...((item as unknown as VisualAssetPlanSelection).referenceAssetIds ?? [])],
    }));
    return;
  }
  selections.value = (planOutput.value?.suggestions ?? []).map((item) => ({
    suggestionKey: item.suggestionKey,
    displayName: item.displayName,
    purpose: item.purpose,
    targetScope: item.targetScope,
    prompt: item.prompt,
    referenceAssetIds: [...item.referenceAssetIds],
    action: "generate",
    existingAssetId: null,
  }));
}

async function planAssets() {
  try {
    const runtimeRevision = runtimeStatus.settings.value?.current.revision;
    if (runtimeRevision === undefined) throw new Error("运行配置尚未加载");
    await ElMessageBox.confirm(
      `规划模型会读取已批准剧情、分镜与当前资产，只提出环境、服装和关键道具建议，不会自动生成图片。配置 revision ${runtimeStatus.settings.value?.current.revision ?? "未加载"}，此操作会产生一次 Ark LLM 费用。`,
      "确认运行视觉资产规划",
      { type: "warning" },
    );
    const result = await api.planVisualAssets(props.scene.id, runtimeRevision);
    registerTask(result.jobId, {
      kind: "visual_asset_plan",
      label: `视觉资产规划 · ${props.scene.title}`,
      projectId: props.projectId,
      sceneId: props.scene.id,
      operationKey: "director:visual-asset-plan",
    });
    ElMessage.success("规划任务已提交，可以继续操作其他场景或片段");
  } catch (error) {
    if (error !== "cancel") errorText.value = error instanceof Error ? error.message : String(error);
  }
}

async function acceptPlan() {
  if (!selectedPlan.value || !planOutput.value) return;
  await run(async () => {
    await api.acceptVisualAssetPlan(selectedPlan.value!.stepId, {
      selections: selections.value,
    });
    await refresh();
    ElMessage.success("视觉资产选择稿已保存；生成项仍需逐张确认费用");
  });
}

async function generate(item: VisualAssetPlanSelection, regenerate = false) {
  if (item.action !== "generate") return;
  if (item.referenceAssetIds.length > 14) {
    errorText.value = "图片生成最多允许 14 张去重后的参考图";
    return;
  }
  let reason: string | undefined;
  try {
    if (regenerate) {
      const answer = await ElMessageBox.prompt(
        "填写本次只需要修正的一项；旧候选和输入快照会保留。",
        `重试生成 · ${item.displayName}`,
      );
      reason = answer.value.trim();
      if (!reason) return;
    }
    const runtimeRevision = runtimeStatus.settings.value?.current.revision;
    if (runtimeRevision === undefined) throw new Error("运行配置尚未加载");
    await ElMessageBox.confirm(
      `${purposeLabels[item.purpose]} · ${item.targetScope === "project" ? "项目复用" : "当前场景"}\n参考图 ${item.referenceAssetIds.length} 张\n模型 ${runtimeStatus.settings.value?.current.imageModel ?? "未加载"} · revision ${runtimeStatus.settings.value?.current.revision ?? "未加载"}\n将产生一次 Seedream 费用。`,
      `生成 ${item.displayName}`,
      { type: "warning" },
    );
    const result = await api.generateReferenceImage(
      { projectId: props.projectId, sceneId: item.targetScope === "scene" ? props.scene.id : null },
      {
        displayName: item.displayName,
        purpose: item.purpose,
        prompt: item.prompt,
        referenceAssetIds: item.referenceAssetIds,
        sourceRevision: selectedPlan.value?.stepId ?? `manual:${props.scene.id}`,
      },
      regenerate,
      reason,
      runtimeRevision,
    );
    registerTask(result.jobId, {
      kind: "generate_reference_image",
      label: `${purposeLabels[item.purpose]} · ${item.displayName}`,
      projectId: props.projectId,
      sceneId: item.targetScope === "scene" ? props.scene.id : undefined,
      operationKey: result.operationKey,
    });
    ElMessage.success("图片候选任务已提交，旧版本不会被覆盖");
  } catch (error) {
    if (error !== "cancel") errorText.value = error instanceof Error ? error.message : String(error);
  }
}

function selectUpload(event: Event, item: VisualAssetPlanSelection) {
  const input = event.target as HTMLInputElement;
  uploadFiles.value[item.suggestionKey] = input.files?.[0];
}

async function saveToScenePackage(asset: AssetDto) {
  const purpose = (asset.referencePurpose ?? asset.metadata.referencePurpose) as LookReferencePurpose;
  if (!["wardrobe", "environment", "prop", "composition"].includes(purpose)) {
    throw new Error("该资产没有可加入场景参考包的固定职责");
  }
  const envelope = await api.sceneLookDraft(props.scene.id);
  const bindings = envelope.draft.referenceBindings.filter((item) => item.assetId !== asset.id);
  bindings.push({
    assetId: asset.id,
    purpose,
    instruction: `只影响${purposeLabels[purpose as VisualAssetPurpose]}，不得改写长期身份`,
  });
  await api.saveSceneLookDraft(props.scene.id, envelope.revision, {
    ...envelope.draft,
    referenceBindings: bindings,
  });
}

async function upload(item: VisualAssetPlanSelection) {
  const file = uploadFiles.value[item.suggestionKey];
  if (!file) {
    errorText.value = "请先选择要上传的图片";
    return;
  }
  await run(async () => {
    const asset = await api.uploadVisualReference(
      {
        projectId: props.projectId,
        sceneId: item.targetScope === "scene" ? props.scene.id : null,
      },
      item.purpose,
      item.displayName,
      file,
    );
    await saveToScenePackage(asset);
    item.action = "existing";
    item.existingAssetId = asset.id;
    data.value = await api.sceneVisualAssets(props.scene.id);
    emit("refreshed");
    ElMessage.success(`${item.displayName} 已上传并加入当前场景参考包`);
  });
}

async function decide(asset: VisualAssetVersion, decision: "approved" | "rejected") {
  await run(async () => {
    await api.reviewAsset(
      asset.id,
      decision,
      decision === "approved" ? "人工确认资产职责和视觉设计" : "人工拒绝参考图候选",
    );
    await refresh();
    emit("refreshed");
  });
}

async function addToScenePackage(asset: VisualAssetVersion) {
  if (!["approved", "ready"].includes(asset.status)) return;
  await run(async () => {
    await saveToScenePackage(asset);
    await refresh();
    emit("refreshed");
    ElMessage.success(`${asset.displayName} 已加入场景参考包`);
  });
}

function versionFor(item: VisualAssetPlanSelection): VisualAssetVersion[] {
  return versions.value.filter((asset) => (
    asset.displayName === item.displayName
    && asset.referencePurpose === item.purpose
    && asset.metadata.sourceRevision === (selectedPlan.value?.stepId ?? `manual:${props.scene.id}`)
  ));
}

function selectedAssetName(item: VisualAssetPlanSelection): string {
  return usableAssets.value.find((asset) => asset.id === item.existingAssetId)?.displayName
    ?? "尚未选择";
}

function imageFailed(asset: VisualAssetVersion) {
  imageErrors.value[asset.id] = `资产 ${asset.id} 内容读取失败`;
}

watch(selectedPlanId, loadSelectionDraft);
watch(() => props.scene.id, () => void refresh(), { immediate: true });
watch(() => taskCenter.projectSignals.value[props.projectId]?.revision ?? 0, () => {
  if (visible.value) void refresh();
  else emit("refreshed");
});
</script>

<template>
  <article class="visual-asset-card">
    <div class="asset-card-icon">资产</div>
    <div>
      <b>视觉资产准备</b>
      <p>按需规划服装、环境和关键道具；人物、猫咪与画风继续继承全局 Canon。</p>
      <small>{{ data?.plans.length ?? 0 }} 个规划版本 · {{ versions.length }} 个图片候选</small>
    </div>
    <el-button type="primary" plain @click="open">打开资产工作台</el-button>
  </article>

  <el-dialog
    v-model="visible"
    title="视觉资产准备（按需生成）"
    width="min(1500px, 98vw)"
    top="2vh"
    destroy-on-close
    class="visual-asset-dialog"
  >
    <div class="visual-workbench" :aria-busy="busy">
      <el-alert v-if="errorText" type="error" :closable="false" :title="errorText" show-icon />
      <header class="asset-toolbar">
        <div>
          <b>场景 {{ scene.order }} · {{ scene.title }}</b>
          <small>规划不会生成图片；每张候选都需要单独确认 Ark 费用。</small>
        </div>
        <div>
          <el-button :disabled="activePlanTask || !paidReady" @click="planAssets">
            {{ data?.plans.length ? "生成新规划版本" : "AI 规划视觉资产" }}
          </el-button>
          <el-tag v-if="activePlanTask" type="warning">规划中</el-tag>
          <el-tag v-if="activeReferenceTasks.length" type="warning">{{ activeReferenceTasks.length }} 张生成中</el-tag>
          <el-tag v-if="busy" type="info">正在同步</el-tag>
        </div>
      </header>

      <section class="canon-strip">
        <div><b>全局身份与画风</b><small>只读继承，不在项目内重复生成身份包</small></div>
        <div class="canon-assets">
          <span v-for="asset in data?.canon ?? []" :key="asset.id">
            <img v-if="asset.contentReady" :src="assetContentUrl(asset.id)" />
            <small>{{ asset.displayName }}</small>
          </span>
        </div>
      </section>

      <section class="layer-board">
        <article>
          <header><b>当场造型</b><el-tag size="small">{{ approvedByPurpose.wardrobe.length }}</el-tag></header>
          <p>人物服装和猫咪配件；不承担脸、发型、毛色或体型。</p>
          <div class="layer-thumbs">
            <el-image v-for="asset in approvedByPurpose.wardrobe" :key="asset.id" :src="assetContentUrl(asset.id)" fit="cover" :preview-src-list="[assetContentUrl(asset.id)]" />
          </div>
        </article>
        <article>
          <header><b>环境参考</b><el-tag size="small">{{ approvedByPurpose.environment.length }}</el-tag></header>
          <p>空场景、主要家具、出入口、光线和色调。</p>
          <div class="layer-thumbs">
            <el-image v-for="asset in approvedByPurpose.environment" :key="asset.id" :src="assetContentUrl(asset.id)" fit="cover" :preview-src-list="[assetContentUrl(asset.id)]" />
          </div>
        </article>
        <article>
          <header><b>关键道具</b><el-tag size="small">{{ approvedByPurpose.prop.length }}</el-tag></header>
          <p>只为跨片段或结构影响动作的道具建图，小物继续文字描述。</p>
          <div class="layer-thumbs">
            <el-image v-for="asset in approvedByPurpose.prop" :key="asset.id" :src="assetContentUrl(asset.id)" fit="cover" :preview-src-list="[assetContentUrl(asset.id)]" />
          </div>
        </article>
        <article>
          <header><b>场景视觉基准</b><el-tag size="small">{{ currentSceneLook ? 1 : 0 }}</el-tag></header>
          <p>汇总本场共同造型和环境，不是视频首帧。</p>
          <div v-if="currentSceneLook" class="layer-thumbs">
            <el-image :src="assetContentUrl(currentSceneLook.id)" fit="cover" :preview-src-list="[assetContentUrl(currentSceneLook.id)]" />
          </div>
        </article>
        <article>
          <header><b>片段首帧</b><el-tag size="small">{{ sceneAnchors.length }}</el-tag></header>
          <p>每个片段动作开始前的静态状态；在片段生成台中生成和批准。</p>
          <div class="layer-thumbs">
            <el-image v-for="asset in sceneAnchors" :key="asset.id" :src="assetContentUrl(asset.id)" fit="cover" :preview-src-list="[assetContentUrl(asset.id)]" />
          </div>
        </article>
      </section>

      <section v-if="data?.plans.length" class="plan-section">
        <div class="plan-version-row">
          <b>资产规划版本</b>
          <el-radio-group v-model="selectedPlanId" size="small">
            <el-radio-button v-for="item in data.plans" :key="item.stepId" :value="item.stepId">
              V{{ item.attempt }} · {{ item.acceptedAt ? "已采用" : item.status }}
            </el-radio-button>
          </el-radio-group>
        </div>
        <el-alert
          v-if="planOutput"
          type="info"
          :closable="false"
          :title="planOutput.overallAssessment"
        />
        <div v-if="planOutput?.textOnlyItems.length" class="text-only-items">
          <b>只需文字描述的小物</b>
          <span v-for="item in planOutput.textOnlyItems" :key="item">{{ item }}</span>
        </div>

        <div class="suggestion-grid">
          <article v-for="item in selections" :key="item.suggestionKey" class="suggestion-card">
            <header>
              <div><b>{{ item.displayName }}</b><small>{{ purposeLabels[item.purpose] }} · {{ item.targetScope === 'project' ? '项目复用' : '当前场景' }}</small></div>
              <el-select v-model="item.action" size="small" style="width: 130px">
                <el-option label="生成" value="generate" />
                <el-option label="上传" value="upload" />
                <el-option label="选择已有" value="existing" />
                <el-option label="跳过" value="skip" />
              </el-select>
            </header>
            <p>{{ planOutput?.suggestions.find(value => value.suggestionKey === item.suggestionKey)?.rationale }}</p>
            <template v-if="item.action === 'generate'">
              <el-input v-model="item.prompt" type="textarea" :rows="5" maxlength="6000" show-word-limit />
              <label>实际参考图片</label>
              <el-select v-model="item.referenceAssetIds" multiple filterable collapse-tags>
                <el-option v-for="asset in usableAssets" :key="asset.id" :label="`${asset.displayName} · ${asset.scope}`" :value="asset.id" />
              </el-select>
              <el-button type="primary" :disabled="activeReferenceTasks.length > 0 || !paidReady" @click="generate(item)">生成新候选</el-button>
            </template>
            <template v-else-if="item.action === 'upload'">
              <el-alert type="info" :closable="false" title="上传图按规划归属保存并加入当前场景参考包，不会进入全局 Canon。" />
              <input type="file" accept="image/*" @change="selectUpload($event, item)" />
              <el-button type="primary" :disabled="!uploadFiles[item.suggestionKey]" @click="upload(item)">上传并使用</el-button>
            </template>
            <template v-else-if="item.action === 'existing'">
              <el-select v-model="item.existingAssetId" filterable clearable placeholder="选择当前项目或 Canon 中的已有图片">
                <el-option v-for="asset in usableAssets" :key="asset.id" :label="`${asset.displayName} · ${asset.scope}`" :value="asset.id" />
              </el-select>
              <small>当前：{{ selectedAssetName(item) }}</small>
            </template>

            <div v-if="versionFor(item).length" class="candidate-strip">
              <div v-for="asset in versionFor(item)" :key="asset.id" class="candidate-version">
                <el-image
                  v-if="asset.contentReady && !imageErrors[asset.id]"
                  :src="assetContentUrl(asset.id)"
                  fit="cover"
                  :preview-src-list="[assetContentUrl(asset.id)]"
                  @error="imageFailed(asset)"
                />
                <span v-else>{{ imageErrors[asset.id] || '内容不可用' }}</span>
                <small>V{{ asset.attempt ?? '?' }} · {{ asset.status }}</small>
                <div>
                  <el-button v-if="asset.status === 'candidate'" size="small" type="success" @click="decide(asset, 'approved')">批准</el-button>
                  <el-button v-if="asset.status === 'candidate'" size="small" type="danger" @click="decide(asset, 'rejected')">拒绝</el-button>
                  <el-button v-if="asset.status === 'approved'" size="small" @click="addToScenePackage(asset)">加入场景参考包</el-button>
                  <el-button v-if="asset.status !== 'candidate'" size="small" :disabled="!paidReady" @click="generate(item, true)">按此设计重试</el-button>
                </div>
              </div>
            </div>
          </article>
        </div>
        <footer class="plan-footer">
          <span>本规划当前选择 {{ selections.length }} 项 · 共选 {{ selectedReferenceCount }} 张参考图</span>
          <el-button type="primary" :disabled="Boolean(selectedPlan?.acceptedAt)" @click="acceptPlan">
            {{ selectedPlan?.acceptedAt ? "本版本已采用" : "保存人工选择稿" }}
          </el-button>
        </footer>
      </section>
      <el-empty v-else description="运行一次 AI 资产规划，或继续使用现有场景视觉基准与片段首帧。" />
    </div>
  </el-dialog>
</template>

<style scoped>
.visual-asset-card { min-height: 142px; display: grid; grid-template-columns: 84px 1fr auto; gap: 16px; align-items: center; padding: 18px; border: 1px solid #2b3545; border-radius: 12px; background: #111722; }
.visual-asset-card p { margin: 6px 0; color: #aeb8c8; }.visual-asset-card small { color: #7f8ba0; }.asset-card-icon { display: grid; place-items: center; width: 74px; height: 96px; border-radius: 10px; background: linear-gradient(150deg, #22486e, #15253b); color: #91c9ff; font-weight: 700; }
.visual-workbench { display: grid; gap: 14px; min-height: 70vh; }.asset-toolbar, .plan-version-row, .suggestion-card header, .plan-footer { display: flex; justify-content: space-between; gap: 12px; align-items: center; }.asset-toolbar small, .canon-strip small, .suggestion-card small { display: block; color: #8491a5; margin-top: 4px; }
.canon-strip, .plan-section { padding: 14px; border: 1px solid #293445; border-radius: 10px; background: #0f141d; }.canon-strip { display: grid; grid-template-columns: 190px 1fr; gap: 12px; }.canon-assets { display: flex; gap: 8px; overflow-x: auto; }.canon-assets span { display: grid; gap: 3px; min-width: 70px; text-align: center; }.canon-assets img { width: 70px; height: 76px; object-fit: cover; border-radius: 6px; background: #080b10; }
.layer-board { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }.layer-board article { min-height: 156px; padding: 12px; border: 1px solid #293445; border-radius: 10px; background: #0f141d; }.layer-board header { display: flex; justify-content: space-between; align-items: center; }.layer-board p { min-height: 42px; margin: 8px 0; color: #8794a7; font-size: 12px; }.layer-thumbs { display: flex; gap: 5px; overflow-x: auto; }.layer-thumbs :deep(.el-image) { flex: 0 0 58px; width: 58px; height: 72px; border-radius: 5px; background: #080b10; }
.plan-section { display: grid; gap: 12px; }.text-only-items { display: flex; gap: 7px; flex-wrap: wrap; align-items: center; }.text-only-items span { padding: 5px 8px; border: 1px solid #38445a; border-radius: 999px; color: #aab5c7; }
.suggestion-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.suggestion-card { display: grid; gap: 9px; padding: 13px; border: 1px solid #303b4f; border-radius: 9px; background: #121923; }.suggestion-card p { color: #a7b1c1; margin: 0; }.suggestion-card label { color: #8794a7; font-size: 12px; }
.candidate-strip { display: flex; gap: 9px; overflow-x: auto; padding-top: 7px; border-top: 1px solid #283244; }.candidate-version { display: grid; gap: 5px; min-width: 150px; }.candidate-version :deep(.el-image) { width: 150px; height: 200px; border-radius: 7px; background: #080b10; }.candidate-version > span { display: grid; place-items: center; width: 150px; height: 200px; color: #d99090; background: #190f12; }.candidate-version > div { display: flex; gap: 4px; flex-wrap: wrap; }
@media (max-width: 1180px) { .layer-board { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 980px) { .suggestion-grid, .layer-board { grid-template-columns: 1fr; }.canon-strip { grid-template-columns: 1fr; }.visual-asset-card { grid-template-columns: 64px 1fr; }.visual-asset-card > .el-button { grid-column: 1 / -1; } }
</style>
