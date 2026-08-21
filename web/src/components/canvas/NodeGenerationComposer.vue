<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { ArrowUp, Location, Plus, User, VideoCamera } from "@element-plus/icons-vue";

import type {
  ActualReferenceBindingDto,
  GenerationCapabilityDto,
  GenerationReferenceAnnotationDto,
} from "../../api/types";

const props = withDefaults(defineProps<{
  nodeRevision: number;
  capabilities: GenerationCapabilityDto;
  actualReferences: ActualReferenceBindingDto[];
  referenceAnnotations?: GenerationReferenceAnnotationDto[];
  initialPrompt?: string;
  initialConfig?: Partial<{
    mode: string;
    aspectRatio: string;
    resolution: string;
    durationSeconds: number;
    candidateCount: number;
    audioEnabled: boolean;
    autoValidate: boolean;
    autoLink: boolean;
    cameraMotion: string;
  }>;
  embedded?: boolean;
}>(), {
  referenceAnnotations: () => [],
  initialPrompt: "",
  initialConfig: () => ({}),
  embedded: false,
});
const emit = defineEmits<{
  "save-config": [payload: Record<string, unknown>];
  generate: [payload: Record<string, unknown>];
  "select-references": [];
  "open-subject-library": [];
  "open-annotation": [assetIds: string[]];
}>();

const prompt = ref(props.initialPrompt);
const cameraMotionVisible = ref(false);
const cameraMotions = computed(() => props.capabilities.cameraMotions ?? []);
const settings = reactive({
  mode: props.capabilities.modes.includes(props.initialConfig.mode ?? "")
    ? props.initialConfig.mode : props.capabilities.modes[0],
  aspectRatio: props.capabilities.aspectRatios.includes(props.initialConfig.aspectRatio ?? "")
    ? props.initialConfig.aspectRatio : props.capabilities.aspectRatios[0],
  resolution: props.capabilities.resolutions.includes(props.initialConfig.resolution ?? "")
    ? props.initialConfig.resolution : props.capabilities.resolutions[0],
  durationSeconds: props.capabilities.durations.includes(props.initialConfig.durationSeconds ?? -1)
    ? props.initialConfig.durationSeconds : props.capabilities.durations[0],
  candidateCount: props.capabilities.candidateCounts.includes(props.initialConfig.candidateCount ?? -1)
    ? props.initialConfig.candidateCount : props.capabilities.candidateCounts[0],
  audioEnabled: props.capabilities.audio && (props.initialConfig.audioEnabled ?? props.capabilities.audio),
  autoValidate: props.initialConfig.autoValidate ?? true,
  autoLink: props.initialConfig.autoLink ?? true,
  cameraMotion: cameraMotions.value.some((item) => item.value === props.initialConfig.cameraMotion)
    ? props.initialConfig.cameraMotion
    : cameraMotions.value.find((item) => item.enabled !== false)?.value ?? "static",
});
const includedReferenceIds = computed(() => props.actualReferences
  .filter((item) => item.providerIncluded)
  .map((item) => item.assetId));
const blockers = computed(() => {
  const items: string[] = [];
  if (!prompt.value.trim()) items.push("请填写生成 Prompt");
  if (settings.mode === "image_to_video" && !includedReferenceIds.value.length) {
    items.push("图生视频需要至少一张实际进入请求的参考图");
  }
  return items;
});
const exactPayload = computed(() => ({
  provider: props.capabilities.provider,
  model: props.capabilities.model,
  draftPrompt: prompt.value,
  ...settings,
  actualReferences: props.actualReferences,
  referenceAnnotations: props.referenceAnnotations,
}));

let draftTimer: ReturnType<typeof setTimeout> | null = null;
watch(
  () => [prompt.value, ...Object.values(settings)],
  () => {
    if (draftTimer) clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      draftTimer = null;
      emit("save-config", exactPayload.value);
    }, 500);
  },
);
onBeforeUnmount(() => {
  if (draftTimer) {
    clearTimeout(draftTimer);
    draftTimer = null;
    emit("save-config", exactPayload.value);
  }
});

function generate() {
  if (blockers.value.length) return;
  if (draftTimer) {
    clearTimeout(draftTimer);
    draftTimer = null;
  }
  emit("generate", { prompt: prompt.value.trim(), config: exactPayload.value });
}

const modeLabels: Record<string, string> = {
  text_to_image: "文生图",
  all_reference: "全能参考",
  text_to_video: "文生视频",
  image_to_video: "图生视频",
  audio: "音频生成",
};
</script>

<template>
  <section class="node-composer" :class="{ embedded }" aria-label="节点生成器">
    <div class="chips">
      <button data-action="select-references" type="button" @click="emit('select-references')"><Plus />参考</button>
      <button
        data-action="annotate"
        type="button"
        :disabled="!includedReferenceIds.length"
        :title="includedReferenceIds.length ? '标记参考图中的修改区域' : '请先选择一张实际进入请求的参考图'"
        @click="emit('open-annotation', includedReferenceIds)"
      ><Location />标记</button>
      <button data-action="effect" type="button" disabled title="当前 Ark ProviderCapability 尚未配置视频特效执行器">特效</button>
      <button data-action="open-subject-library" type="button" @click="emit('open-subject-library')"><User />主体库</button>
      <button data-action="camera-motion" type="button" :disabled="!cameraMotions.length" :title="cameraMotions.length ? '选择供应商支持的结构化运镜' : '当前媒体类型或 ProviderCapability 不支持运镜'" @click="cameraMotionVisible = !cameraMotionVisible"><VideoCamera />运镜</button>
    </div>
    <div v-if="cameraMotionVisible" class="camera-motion-menu" aria-label="运镜预设">
      <button v-for="item in cameraMotions" :key="item.value" type="button" :data-camera-motion="item.value" :class="{ active: settings.cameraMotion === item.value }" :disabled="item.enabled === false" :title="item.enabled === false ? item.disabledReason : item.label" @click="settings.cameraMotion = item.value; cameraMotionVisible = false">{{ item.label }}</button>
    </div>
    <textarea v-model="prompt" aria-label="生成 Prompt" placeholder="描述你想要生成的画面内容，@ 引用画布素材" />
    <div class="composer-footer">
      <b>{{ capabilities.provider }} · {{ capabilities.model }}</b>
      <div class="settings">
        <label><span>模式</span><select v-model="settings.mode"><option v-for="item in capabilities.modes" :key="item" :value="item">{{ modeLabels[item] ?? item }}</option></select></label>
        <label><span>比例</span><select v-model="settings.aspectRatio"><option v-for="item in capabilities.aspectRatios" :key="item">{{ item }}</option></select></label>
        <label><span>清晰度</span><select v-model="settings.resolution"><option v-for="item in capabilities.resolutions" :key="item">{{ item }}</option></select></label>
        <label><span>时长</span><select v-model.number="settings.durationSeconds"><option v-for="item in capabilities.durations" :key="item" :value="item">{{ item }}s</option></select></label>
        <label><span>数量</span><select v-model.number="settings.candidateCount"><option v-for="item in capabilities.candidateCounts" :key="item" :value="item">{{ item }}</option></select></label>
      </div>
      <button class="generate-button" data-action="generate" type="button" :disabled="Boolean(blockers.length)" aria-label="确认并生成" @click="generate"><ArrowUp /></button>
    </div>
    <ul v-if="blockers.length" class="blockers" aria-label="生成阻塞原因"><li v-for="item in blockers" :key="item">{{ item }}</li></ul>
    <details class="advanced-settings">
      <summary>高级信息 · Revision {{ nodeRevision }} · {{ capabilities.estimatedCostMicros ? `预计 ¥${(capabilities.estimatedCostMicros / 1_000_000).toFixed(3)}` : '费用提交前确认' }}</summary>
      <div class="toggles"><label><input v-model="settings.audioEnabled" type="checkbox" :disabled="!capabilities.audio" />生成音频</label><label><input v-model="settings.autoValidate" type="checkbox" />自动校验素材</label><label><input v-model="settings.autoLink" type="checkbox" />智能引用 AutoLink</label></div>
      <div v-if="actualReferences.length" class="reference-audit"><b>实际供应商输入</b><ul><li v-for="item in actualReferences" :key="item.assetId" :class="{ omitted: !item.providerIncluded }"><b>{{ item.semanticRole }}</b><span>{{ item.providerIncluded ? `已进入供应商请求 · ${item.providerSlot ?? '已分配槽位'}` : '未进入供应商请求' }}</span><small v-if="item.omissionReason">{{ item.omissionReason }}</small></li></ul></div>
      <p v-if="referenceAnnotations.length" class="annotation-count">已保存 {{ referenceAnnotations.length }} 项归一化参考标注，将进入调用输入快照。</p>
      <details><summary>查看应用将发送的精确调用内容</summary><pre>{{ JSON.stringify(exactPayload, null, 2) }}</pre></details>
    </details>
  </section>
</template>

<style scoped>
.node-composer { width: min(760px, calc(100vw - 40px)); padding: 14px; color: #ededed; background: #252525; border: 1px solid #414141; border-radius: 14px; box-shadow: 0 20px 60px rgb(0 0 0 / 42%); }.node-composer.embedded { box-sizing: border-box; width: 100%; min-height: 100%; padding: 18px 24px 16px; background: #252525; border: 0; border-radius: 0; box-shadow: none; }.chips, .settings, .toggles, .camera-motion-menu,.composer-footer { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }.chips button, .camera-motion-menu button { min-height: 38px; display: flex; align-items: center; gap: 6px; padding: 7px 11px; color: #aaa; background: #333; border: 0; border-radius: 999px; cursor: pointer; }.chips svg { width: 16px; }.chips button:disabled,.camera-motion-menu button:disabled { opacity: .42; cursor: not-allowed; }.chips button:focus-visible,.camera-motion-menu button:focus-visible { outline: 2px solid #79aef8; outline-offset: 2px; }.camera-motion-menu { margin-top: 8px; padding: 8px; background: #1d1d1d; border-radius: 9px; }.camera-motion-menu button.active { color: #101010; background: #eee; } textarea { box-sizing: border-box; width: 100%; min-height: 170px; margin: 12px 0; padding: 16px 4px; color: #f4f4f4; background: transparent; border: 0; border-radius: 0; font-size: 16px; line-height: 1.6; resize: none; }textarea::placeholder { color: #666; }textarea:focus { outline: 0; }.composer-footer { min-height: 52px; padding-top: 10px; border-top: 1px solid #383838; }.composer-footer > b { margin-right: 6px; white-space: nowrap; }.settings { flex: 1; }.settings label { display: flex; align-items: center; gap: 5px; color: #858585; font-size: 10px; }.settings select { max-width: 112px; padding: 7px 22px 7px 8px; color: #ededed; background: #303030; border: 1px solid #454545; border-radius: 8px; }.generate-button { width: 48px; height: 48px; display: grid; place-items: center; padding: 12px; color: #111; background: #f2f2f2; border: 0; border-radius: 14px; cursor: pointer; }.generate-button svg { width: 22px; }.generate-button:disabled { opacity: .35; cursor: not-allowed; }.generate-button:focus-visible { outline: 2px solid #79aef8; outline-offset: 2px; }.toggles { margin: 11px 0; color: #adb6c4; font-size: 11px; }.advanced-settings { margin-top: 8px; padding: 9px; color: #999; background: #1d1d1d; border-radius: 8px; }.advanced-settings details { margin-top: 8px; padding: 8px; background: #171717; border-radius: 7px; }.reference-audit { margin-top: 10px; } pre { max-height: 160px; overflow: auto; color: #9cabc0; font-size: 10px; white-space: pre-wrap; } ul { padding-left: 18px; } li { margin: 5px 0; } li span, li small { margin-left: 8px; color: #7ed8a9; }.omitted span, .omitted small { color: #e3ad6d; }.annotation-count { margin: 8px 0 0; color: #85c7ec; font-size: 11px; }.blockers { margin: 4px 0 0; color: #e8af73; font-size: 11px; }
@media (max-width: 900px) { .settings label span { display: none; }.node-composer.embedded { padding-inline: 16px; }.composer-footer > b { width: 100%; }.settings select { max-width: 96px; } }
</style>
