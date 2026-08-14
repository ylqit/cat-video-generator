<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";

import { assetContentUrl } from "../api/client";
import type {
  AnchorMode,
  AssetDto,
  AttemptDto,
  PreviousTailStatus,
  ReferenceBinding,
  ReferenceRole,
  ReferenceSlotDto,
  ReferenceTarget,
  SceneDto,
  SceneLookUsage,
  ShotAssistContext,
  ShotAssistPatch,
  ShotAssistRecord,
  ShotDto,
  ShotPromptPreview,
} from "../api/types";
import ShotAssistancePanel from "./ShotAssistancePanel.vue";
import ShotMediaVersions from "./ShotMediaVersions.vue";

type OpeningStrategy = "text" | "previous_tail" | "existing" | "derived" | "direct_scene";

const props = defineProps<{
  shot: ShotDto;
  scene: SceneDto;
  allAssets: AssetDto[];
  selectableAssets: AssetDto[];
  anchorPreview: ShotPromptPreview | null;
  videoPreview: ShotPromptPreview | null;
  referenceSlots?: { anchor: ReferenceSlotDto[]; video: ReferenceSlotDto[] } | null;
  previousTail?: PreviousTailStatus | null;
  activeTasks?: AttemptDto[];
  assistContext: ShotAssistContext | null;
  assistRecords: ShotAssistRecord[];
}>();

const emit = defineEmits<{
  close: [];
  edit: [];
  generate: [target: "anchor" | "video"];
  review: [asset: AssetDto, decision: "approved" | "rejected", stale: boolean];
  selectVersion: [asset: AssetDto, stale: boolean];
  resume: [stepId: string];
  reconcile: [stepId: string];
  adoptTail: [];
  bindReference: [binding: ReferenceBinding];
  removeBinding: [assetId: string];
  saveSettings: [settings: {
    anchorMode: AnchorMode;
    sceneLookUsage: SceneLookUsage;
    inheritProjectReferences: boolean;
  }];
  applyAssistance: [record: ShotAssistRecord, patch: ShotAssistPatch];
}>();

const activeTab = ref("opening");
const previewKind = ref<"anchor" | "video">("anchor");
const referenceTarget = ref<"anchor" | "video">("video");
const previewAssetId = ref("");
const settings = reactive({
  anchorMode: props.shot.anchorMode,
  sceneLookUsage: props.shot.sceneLookUsage,
  inheritProjectReferences: props.shot.inheritProjectReferences,
});
const binding = reactive({
  assetId: "",
  role: "prop" as ReferenceRole,
  applyTo: "video" as ReferenceTarget,
});
const existingAnchorAssetId = ref("");

watch(() => props.shot.id, () => {
  activeTab.value = "opening";
  previewKind.value = props.shot.selectedVideoAssetId ? "video" : "anchor";
  previewAssetId.value = "";
}, { immediate: true });
watch(
  () => [props.shot.anchorMode, props.shot.sceneLookUsage, props.shot.inheritProjectReferences] as const,
  ([anchorMode, sceneLookUsage, inheritProjectReferences]) => {
    Object.assign(settings, { anchorMode, sceneLookUsage, inheritProjectReferences });
  },
);

const openingStrategy = computed<OpeningStrategy>({
  get() {
    if (settings.anchorMode === "generate") return "derived";
    if (settings.anchorMode === "existing") {
      return props.previousTail?.boundAssetId ? "previous_tail" : "existing";
    }
    if (settings.sceneLookUsage === "full_reference") return "direct_scene";
    return "text";
  },
  set(value) {
    if (value === "derived") {
      settings.anchorMode = "generate";
      settings.sceneLookUsage = "derive_anchor";
    } else if (value === "direct_scene") {
      settings.anchorMode = "text_only";
      settings.sceneLookUsage = "full_reference";
    } else if (value === "previous_tail" || value === "existing") {
      settings.anchorMode = "existing";
      if (settings.sceneLookUsage === "derive_anchor") settings.sceneLookUsage = "off";
    } else {
      settings.anchorMode = "text_only";
      settings.sceneLookUsage = "appearance_only";
    }
  },
});

const currentPrompt = computed(() => referenceTarget.value === "anchor"
  ? props.anchorPreview
  : props.videoPreview);
const slots = computed(() => props.referenceSlots?.[referenceTarget.value]
  ?? groupPreviewReferences(currentPrompt.value, referenceTarget.value));
const mediaCandidates = computed(() => props.shot.assets.filter((item) => (
  previewKind.value === "anchor"
    ? item.role === "shot_anchor"
    : ["shot_video", "shot_video_edit"].includes(item.role)
)));
const selectedMedia = computed(() => {
  const explicit = mediaCandidates.value.find((item) => item.id === previewAssetId.value);
  if (explicit) return explicit;
  const selectedId = previewKind.value === "anchor"
    ? props.shot.selectedAnchorAssetId
    : props.shot.selectedVideoAssetId;
  return mediaCandidates.value.find((item) => item.id === selectedId)
    ?? [...mediaCandidates.value].reverse().find((item) => item.status === "candidate")
    ?? [...mediaCandidates.value].reverse()[0]
    ?? null;
});
const selectedSceneLook = computed(() => props.allAssets.find(
  (item) => item.id === props.scene.selectedLookAssetId,
) ?? null);
const previousTailAsset = computed(() => props.allAssets.find(
  (item) => item.id === (props.previousTail?.assetId ?? props.previousTail?.boundAssetId),
) ?? null);
const tailState = computed(() => {
  if (props.previousTail?.stale) return { label: "来源已过期", type: "warning" as const };
  if (props.previousTail?.boundAssetId) return { label: "已采用", type: "success" as const };
  if (props.previousTail?.available) return { label: "可以采用", type: "info" as const };
  return { label: "建议独立开场", type: "info" as const };
});
const activeAnchorTask = computed(() => props.activeTasks?.some(
  (item) => item.operationKey === "image:anchor"
    && ["queued", "pending", "submitting", "running", "restart_pending"].includes(item.status),
) ?? false);
const activeVideoTask = computed(() => props.activeTasks?.some(
  (item) => ["video:shot", "video:range-edit"].includes(item.operationKey)
    && ["queued", "pending", "submitting", "running", "restart_pending"].includes(item.status),
) ?? false);
const needsAnchor = computed(() => settings.anchorMode === "generate"
  && !props.shot.selectedAnchorAssetId);
const primaryAction = computed(() => {
  if (activeAnchorTask.value) return { label: "开场图生成中", target: "anchor" as const, disabled: true };
  if (activeVideoTask.value) return { label: "视频生成中", target: "video" as const, disabled: true };
  if (needsAnchor.value) return {
    label: "生成片段开场图",
    target: "anchor" as const,
    disabled: !props.anchorPreview?.ready,
  };
  return {
    label: props.shot.selectedVideoAssetId ? "重新生成视频片段" : "生成视频片段",
    target: "video" as const,
    disabled: !props.videoPreview?.ready,
  };
});
const currentBlockers = computed(() => primaryAction.value.target === "anchor"
  ? props.anchorPreview?.blockers ?? []
  : props.videoPreview?.blockers ?? []);
const subshots = computed(() => {
  const matches = [...props.shot.direction.matchAll(/(?:^|\n)\s*(\d+)\s*[.、．]\s*([\s\S]*?)(?=(?:\n\s*\d+\s*[.、．])|$)/g)];
  if (!matches.length) return [{ ordinal: 1, text: props.shot.direction }];
  return matches.map((item) => ({ ordinal: Number(item[1]), text: item[2].trim() }));
});

function groupPreviewReferences(
  preview: ShotPromptPreview | null,
  target: "anchor" | "video",
): ReferenceSlotDto[] {
  if (!preview) return [];
  const groups = new Map<ReferenceSlotDto["key"], ReferenceSlotDto>();
  const labels: Record<ReferenceSlotDto["key"], string> = {
    person: "人物身份",
    cat: "猫咪身份",
    style: "系列画风",
    scene: "场景视觉基准",
    prop: "道具与构图",
    opening: "批准开场图",
    custom: "片段专用素材",
  };
  for (const item of preview.references) {
    const asset = props.allAssets.find((candidate) => candidate.id === item.assetId);
    if (!asset) continue;
    let key: ReferenceSlotDto["key"] = "prop";
    if (item.assetId === props.shot.selectedAnchorAssetId) key = "opening";
    else if (item.sourceLayer === "scene_look") key = "scene";
    else if (asset.semanticKey?.startsWith("person:")) key = "person";
    else if (asset.semanticKey?.startsWith("cat:")) key = "cat";
    else if (asset.semanticKey?.startsWith("style:")) key = "style";
    else if (item.sourceLayer === "shot") key = "custom";
    const group = groups.get(key) ?? { key, label: labels[key], target, items: [] };
    group.items.push({ ...item, asset });
    groups.set(key, group);
  }
  return [...groups.values()];
}

function anchorModeLabel(value: AnchorMode): string {
  return {
    text_only: "纯文字开场",
    existing: "采用已批准开场图",
    generate: "根据场景视觉基准生成独立开场图",
  }[value];
}

function sceneLookLabel(value: SceneLookUsage): string {
  return {
    off: "本片段不使用场景基准",
    appearance_only: "只继承造型与环境",
    full_reference: "按当前场景画面直接起镜",
    derive_anchor: "仅用于生成片段开场图",
  }[value];
}

function bindCustomReference() {
  if (!binding.assetId) return;
  emit("bindReference", {
    assetId: binding.assetId,
    usage: "generation_reference",
    role: binding.role,
    applyTo: binding.applyTo,
  });
  binding.assetId = "";
}

function useExistingOpening() {
  if (!existingAnchorAssetId.value) return;
  emit("bindReference", {
    assetId: existingAnchorAssetId.value,
    usage: "approved_anchor",
    role: "composition",
    applyTo: "both",
  });
  existingAnchorAssetId.value = "";
}

function saveCurrentSettings() {
  emit("saveSettings", {
    anchorMode: settings.anchorMode,
    sceneLookUsage: settings.sceneLookUsage,
    inheritProjectReferences: settings.inheritProjectReferences,
  });
}

function forwardAssistance(record: ShotAssistRecord, patch: ShotAssistPatch) {
  emit("applyAssistance", record, patch);
}

function forwardReview(asset: AssetDto, decision: "approved" | "rejected", stale: boolean) {
  emit("review", asset, decision, stale);
}

function forwardSelection(asset: AssetDto, stale: boolean) {
  emit("selectVersion", asset, stale);
}
</script>

<template>
  <div class="generation-workspace">
    <header class="workspace-header">
      <div>
        <span class="eyebrow">片段 {{ shot.order }} · {{ scene.title }}</span>
        <h2>{{ shot.title }}</h2>
        <p>{{ shot.durationSeconds }} 秒 · {{ anchorModeLabel(shot.anchorMode) }} · {{ sceneLookLabel(shot.sceneLookUsage) }}</p>
      </div>
      <div class="header-actions">
        <el-button @click="emit('edit')">编辑片段</el-button>
        <el-button @click="emit('close')">返回制作看板</el-button>
      </div>
    </header>

    <div class="workspace-body">
      <section class="preview-stage">
        <div class="preview-toolbar">
          <el-radio-group v-model="previewKind" size="small">
            <el-radio-button value="anchor">开场图</el-radio-button>
            <el-radio-button value="video">视频</el-radio-button>
          </el-radio-group>
          <el-tag v-if="selectedMedia" :type="selectedMedia.status === 'approved' ? 'success' : 'info'">
            {{ selectedMedia.status === "approved" ? "批准版本" : selectedMedia.status }}
          </el-tag>
        </div>

        <div class="media-canvas">
          <template v-if="selectedMedia?.contentReady">
            <img v-if="selectedMedia.mediaType === 'image'" :src="assetContentUrl(selectedMedia.id)" />
            <video v-else controls preload="metadata" :src="assetContentUrl(selectedMedia.id)" />
          </template>
          <template v-else-if="previewKind === 'anchor' && selectedSceneLook?.contentReady">
            <div class="baseline-watermark">场景视觉基准预览 · 不是视频首帧</div>
            <img :src="assetContentUrl(selectedSceneLook.id)" />
          </template>
          <div v-else class="empty-preview">
            <b>{{ previewKind === "anchor" ? "尚无片段开场图" : "尚无视频版本" }}</b>
            <span>{{ currentBlockers[0] || "完成右侧设置后即可生成。" }}</span>
          </div>
        </div>

        <div v-if="mediaCandidates.length" class="candidate-strip">
          <button
            v-for="(asset, index) in mediaCandidates"
            :key="asset.id"
            type="button"
            :class="{ active: selectedMedia?.id === asset.id }"
            @click="previewAssetId = asset.id"
          >
            <img v-if="asset.mediaType === 'image' && asset.contentReady" :src="assetContentUrl(asset.id)" />
            <video v-else-if="asset.contentReady" muted preload="metadata" :src="assetContentUrl(asset.id)" />
            <span>V{{ index + 1 }} · {{ asset.status }}</span>
          </button>
        </div>

        <div class="continuity-card">
          <img
            v-if="previousTailAsset?.contentReady"
            :src="assetContentUrl(previousTailAsset.id)"
            alt="上一片段批准尾帧"
          />
          <div v-else class="tail-empty">尾帧</div>
          <b>连续性衔接</b>
          <el-tag :type="tailState.type" size="small">{{ tailState.label }}</el-tag>
          <template v-if="previousTail?.available || previousTail?.stale">
            <span>{{ previousTail.stale ? "上一片段尾帧来源已变化，需要重新确认。" : "上一片段已有可用的批准尾帧。" }}</span>
            <el-button size="small" :type="previousTail.stale ? 'warning' : 'primary'" plain @click="emit('adoptTail')">
              {{ previousTail.stale || previousTail.boundAssetId ? "重新采用上一尾帧" : "采用上一片段尾帧" }}
            </el-button>
          </template>
          <span v-else>没有可采用的上一片段尾帧，可改用独立开场图。</span>
        </div>
      </section>

      <section class="setup-panel">
        <el-tabs v-model="activeTab" stretch>
          <el-tab-pane label="开场设计" name="opening">
            <div class="tab-stack">
              <div class="section-title">
                <div><b>本片段如何起镜</b><small>场景视觉基准只提供共同造型与环境，不自动等于首帧。</small></div>
              </div>
              <el-radio-group v-model="openingStrategy" class="mode-cards">
                <el-radio value="text" border>纯文字开场（不生成首帧，只继承场景造型）</el-radio>
                <el-radio
                  value="previous_tail"
                  border
                  :disabled="!previousTail?.available && !previousTail?.stale"
                >采用上一片段尾帧</el-radio>
                <el-radio value="existing" border>上传或选择已有开场图</el-radio>
                <el-radio value="derived" border>根据场景视觉基准生成独立开场图</el-radio>
                <el-radio value="direct_scene" border>按当前场景画面直接起镜</el-radio>
              </el-radio-group>

              <el-alert
                v-if="openingStrategy === 'direct_scene'"
                type="warning"
                :closable="false"
                title="仅当片段起始状态与场景视觉基准高度一致时使用；否则请选择独立开场图。"
              />
              <el-button
                v-if="openingStrategy === 'previous_tail'"
                type="primary"
                plain
                :disabled="!previousTail?.available && !previousTail?.stale"
                @click="emit('adoptTail')"
              >确认采用上一片段批准尾帧</el-button>
              <template v-if="openingStrategy === 'existing'">
                <div class="inline-controls">
                  <el-select v-model="existingAnchorAssetId" filterable placeholder="选择项目或片段图片">
                    <el-option v-for="asset in selectableAssets" :key="asset.id" :label="asset.displayName" :value="asset.id" />
                  </el-select>
                  <el-button :disabled="!existingAnchorAssetId" @click="useExistingOpening">设为开场图</el-button>
                </div>
              </template>
              <el-button @click="saveCurrentSettings">保存开场策略</el-button>

              <el-divider content-position="left">开场目标与锚点 Prompt</el-divider>
              <p class="creative-copy">{{ anchorPreview?.creativeBody || shot.direction }}</p>
              <el-alert v-for="blocker in anchorPreview?.blockers ?? []" :key="blocker" type="warning" :title="blocker" :closable="false" />
              <details v-if="anchorPreview" class="expert-panel">
                <summary>查看锚点完整 Prompt 与输入哈希</summary>
                <pre>{{ anchorPreview.prompt }}</pre>
                <small>输入哈希：{{ anchorPreview.inputHash }}</small>
              </details>
            </div>
          </el-tab-pane>

          <el-tab-pane label="参考素材" name="references">
            <div class="tab-stack">
              <div class="target-switch">
                <el-radio-group v-model="referenceTarget" size="small">
                  <el-radio-button value="anchor">开场图输入</el-radio-button>
                  <el-radio-button value="video">视频输入</el-radio-button>
                </el-radio-group>
                <el-tag :type="currentPrompt?.ready ? 'success' : 'warning'">
                  {{ currentPrompt?.ready ? `${currentPrompt.references.length} 张实际提交` : "输入未就绪" }}
                </el-tag>
              </div>
              <el-alert v-for="blocker in currentPrompt?.blockers ?? []" :key="blocker" type="warning" :title="blocker" :closable="false" />
              <section v-for="slot in slots" :key="`${slot.target}:${slot.key}`" class="reference-slot">
                <div class="slot-heading"><b>{{ slot.label }}</b><span>{{ slot.items.length }} 张</span></div>
                <div class="contact-sheet">
                  <article v-for="item in slot.items" :key="item.assetId">
                    <img v-if="item.contentReady" :src="assetContentUrl(item.assetId)" />
                    <div v-else class="missing-image">图片不可读</div>
                    <div>
                      <b>{{ item.promptAlias }}</b>
                      <span>{{ item.displayName }}</span>
                      <small>{{ item.sourceLayer }} · {{ item.responsibility }}</small>
                    </div>
                  </article>
                </div>
              </section>

              <el-divider content-position="left">添加片段专用素材</el-divider>
              <div class="binding-grid">
                <el-select v-model="binding.assetId" filterable placeholder="选择项目素材">
                  <el-option v-for="asset in selectableAssets" :key="asset.id" :label="asset.displayName" :value="asset.id" />
                </el-select>
                <el-select v-model="binding.role">
                  <el-option label="画风补充" value="style" />
                  <el-option label="道具" value="prop" />
                  <el-option label="构图" value="composition" />
                </el-select>
                <el-select v-model="binding.applyTo">
                  <el-option label="开场图" value="anchor" />
                  <el-option label="视频" value="video" />
                  <el-option label="两者" value="both" />
                </el-select>
                <el-button :disabled="!binding.assetId" @click="bindCustomReference">添加</el-button>
              </div>
              <div v-if="shot.referenceBindings.length" class="binding-list">
                <span v-for="item in shot.referenceBindings" :key="`${item.assetId}:${item.applyTo}`">
                  {{ item.role }} · {{ item.applyTo }}
                  <button type="button" @click="emit('removeBinding', item.assetId)">移除</button>
                </span>
              </div>
            </div>
          </el-tab-pane>

          <el-tab-pane label="分镜与 Prompt" name="prompt">
            <div class="tab-stack">
              <div class="section-title">
                <div><b>LLM / 人工确认创作正文</b><small>默认只展示可编辑的创作内容。</small></div>
                <el-button size="small" @click="emit('edit')">编辑完整分镜</el-button>
              </div>
              <div class="subshot-list">
                <article v-for="item in subshots" :key="item.ordinal"><span>镜头 {{ item.ordinal }}</span><p>{{ item.text }}</p></article>
              </div>
              <ShotAssistancePanel
                :context="assistContext"
                :records="assistRecords"
                :shot="shot"
                @apply="forwardAssistance"
                @adopt-tail="emit('adoptTail')"
              />
              <details class="expert-panel">
                <summary>专家调试：真实素材映射、系统技术外壳、Provider Prompt 与输入哈希</summary>
                <template v-if="videoPreview">
                  <h4>真实素材映射</h4>
                  <ol><li v-for="item in videoPreview.references" :key="item.assetId">{{ item.promptAlias }} · {{ item.displayName }} · {{ item.sourceLayer }} · {{ item.responsibility }}</li></ol>
                  <h4>系统技术外壳</h4><pre>{{ videoPreview.systemShell }}</pre>
                  <h4>最终 Provider Prompt</h4><pre>{{ videoPreview.prompt }}</pre>
                  <small>输入哈希：{{ videoPreview.inputHash }} · 内容版本：{{ videoPreview.sourceRevisionHash }}</small>
                </template>
              </details>
            </div>
          </el-tab-pane>

          <el-tab-pane label="生成设置" name="settings">
            <div class="tab-stack">
              <el-descriptions :column="1" border>
                <el-descriptions-item label="片段时长">{{ shot.durationSeconds }} 秒</el-descriptions-item>
                <el-descriptions-item label="输出">480p · 9:16 竖屏</el-descriptions-item>
                <el-descriptions-item label="开场方式">{{ anchorModeLabel(settings.anchorMode) }}</el-descriptions-item>
                <el-descriptions-item label="场景视觉基准">{{ sceneLookLabel(settings.sceneLookUsage) }}</el-descriptions-item>
                <el-descriptions-item label="项目身份与画风">{{ settings.inheritProjectReferences ? "继承" : "关闭" }}</el-descriptions-item>
              </el-descriptions>
              <el-checkbox v-model="settings.inheritProjectReferences">继承项目人物、猫咪和画风参考</el-checkbox>
              <el-button @click="saveCurrentSettings">保存生成设置</el-button>
              <el-alert type="info" :closable="false" title="保存和 Prompt 预览不会调用 Ark；点击底部生成按钮时才进行付费确认。" />
            </div>
          </el-tab-pane>

          <el-tab-pane label="版本历史" name="versions">
            <ShotMediaVersions
              :shot="shot"
              :anchor-preview="anchorPreview"
              :video-preview="videoPreview"
              @review="forwardReview"
              @select="forwardSelection"
              @resume="emit('resume', $event)"
              @reconcile="emit('reconcile', $event)"
              @retry="emit('generate', $event)"
            />
          </el-tab-pane>
        </el-tabs>
      </section>
    </div>

    <footer class="workspace-footer">
      <div>
        <b>{{ primaryAction.disabled ? currentBlockers[0] || primaryAction.label : "当前输入已完成付费前预检" }}</b>
        <span>
          {{ primaryAction.target === "anchor" ? anchorPreview?.references.length ?? 0 : videoPreview?.references.length ?? 0 }} 张实际参考图
          · 点击生成将产生一次 Ark 费用
        </span>
      </div>
      <el-button type="primary" size="large" :disabled="primaryAction.disabled" @click="emit('generate', primaryAction.target)">{{ primaryAction.label }}</el-button>
    </footer>
  </div>
</template>

<style scoped>
.generation-workspace { min-height: calc(100vh - 36px); display: grid; grid-template-rows: auto 1fr auto; color: #e8edf5; background: #0c1016; }
.workspace-header, .workspace-footer, .preview-toolbar, .header-actions, .target-switch, .section-title, .slot-heading, .inline-controls { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.workspace-header { padding: 18px 24px; border-bottom: 1px solid #29313d; background: #111721; }
.workspace-header h2 { margin: 4px 0; }
.workspace-header p, .eyebrow { margin: 0; color: #8d9aaf; }
.eyebrow { font-size: 12px; }
.workspace-body { min-height: 0; display: grid; grid-template-columns: minmax(480px, 3fr) minmax(420px, 2fr); }
.preview-stage { min-width: 0; padding: 18px 22px; display: grid; grid-template-rows: auto minmax(420px, 1fr) auto auto; gap: 12px; border-right: 1px solid #29313d; }
.setup-panel { min-width: 0; padding: 10px 18px 18px; overflow: auto; max-height: calc(100vh - 176px); }
.media-canvas { position: relative; min-height: 420px; display: grid; place-items: center; overflow: hidden; border: 1px solid #2b3544; border-radius: 12px; background: #070a0f; }
.media-canvas img, .media-canvas video { width: 100%; height: 100%; max-height: 68vh; object-fit: contain; }
.baseline-watermark { position: absolute; top: 12px; left: 12px; z-index: 1; padding: 6px 9px; border-radius: 6px; color: #d9e8ff; background: #182438e6; font-size: 12px; }
.empty-preview { display: grid; gap: 7px; place-items: center; color: #8c98aa; text-align: center; }
.empty-preview b { color: #dce4ef; font-size: 18px; }
.candidate-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.candidate-strip button { width: 112px; flex: 0 0 auto; display: grid; gap: 5px; padding: 6px; color: #9ea9ba; background: #101722; border: 1px solid #2d3746; border-radius: 8px; cursor: pointer; }
.candidate-strip button.active { border-color: #409eff; }
.candidate-strip img, .candidate-strip video { width: 100%; height: 82px; object-fit: cover; background: #080b10; }
.continuity-card { display: grid; grid-template-columns: auto auto auto minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 11px; border: 1px solid #304057; border-radius: 9px; background: #111a27; }
.continuity-card img { width: 72px; height: 56px; object-fit: cover; border-radius: 6px; background: #080b10; }
.tail-empty { display: grid; place-items: center; width: 72px; height: 56px; color: #68768a; border-radius: 6px; background: #080b10; font-size: 11px; }
.continuity-card span { color: #91a0b4; }
.tab-stack { display: grid; gap: 12px; }
.section-title { align-items: flex-start; }
.section-title > div { display: grid; gap: 4px; }
.section-title small { color: #8491a4; }
.mode-cards { display: grid; grid-template-columns: 1fr; gap: 8px; }
.mode-cards :deep(.el-radio) { margin: 0; width: 100%; }
.inline-controls > .el-select { flex: 1; min-width: 230px; }
.creative-copy { white-space: pre-wrap; line-height: 1.7; color: #c7cfdb; }
.reference-slot { display: grid; gap: 8px; }
.slot-heading span { color: #7f8ca1; font-size: 12px; }
.contact-sheet { display: grid; grid-template-columns: repeat(auto-fill, minmax(145px, 1fr)); gap: 8px; }
.contact-sheet article { display: grid; gap: 7px; padding: 7px; border: 1px solid #2d3746; border-radius: 8px; background: #101722; }
.contact-sheet img, .missing-image { width: 100%; height: 112px; object-fit: cover; border-radius: 6px; background: #090c11; }
.missing-image { display: grid; place-items: center; color: #d49a5c; }
.contact-sheet article > div { display: grid; gap: 3px; }
.contact-sheet span, .contact-sheet small { color: #8794a7; font-size: 11px; }
.binding-grid { display: grid; grid-template-columns: minmax(180px, 1fr) 130px 110px auto; gap: 7px; }
.binding-list { display: flex; gap: 7px; flex-wrap: wrap; }
.binding-list span { padding: 5px 8px; border-radius: 20px; background: #1a2432; font-size: 11px; }
.binding-list button { margin-left: 5px; color: #f08d8d; background: transparent; border: 0; cursor: pointer; }
.subshot-list { display: grid; gap: 8px; }
.subshot-list article { display: grid; grid-template-columns: 74px 1fr; gap: 10px; padding: 11px; border: 1px solid #2d3746; border-radius: 8px; background: #101722; }
.subshot-list article > span { color: #71aef7; font-weight: 700; }
.subshot-list p { margin: 0; color: #c7cfdb; line-height: 1.65; }
.expert-panel { padding: 12px; border: 1px solid #303b4b; border-radius: 8px; }
.expert-panel summary { color: #91a8c8; cursor: pointer; }
.expert-panel pre { max-height: 300px; overflow: auto; white-space: pre-wrap; color: #cbd4e1; background: #080b10; padding: 10px; border-radius: 7px; }
.workspace-footer { position: sticky; bottom: 0; z-index: 2; padding: 13px 24px; border-top: 1px solid #2b3544; background: #111721f2; backdrop-filter: blur(10px); }
.workspace-footer > div { display: grid; gap: 4px; }
.workspace-footer span { color: #8795aa; font-size: 12px; }
@media (max-width: 1100px) {
  .workspace-body { grid-template-columns: 1fr; }
  .preview-stage { border-right: 0; border-bottom: 1px solid #29313d; }
  .setup-panel { max-height: none; }
  .binding-grid { grid-template-columns: 1fr 1fr; }
}
</style>
