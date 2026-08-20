<script setup lang="ts">
import { computed, reactive, ref } from "vue";

import type { ActualReferenceBindingDto, GenerationCapabilityDto } from "../../api/types";

const props = defineProps<{
  nodeRevision: number;
  capabilities: GenerationCapabilityDto;
  actualReferences: ActualReferenceBindingDto[];
}>();
const emit = defineEmits<{
  "save-config": [payload: Record<string, unknown>];
  generate: [payload: Record<string, unknown>];
}>();

const prompt = ref("");
const settings = reactive({
  mode: props.capabilities.modes[0],
  aspectRatio: props.capabilities.aspectRatios[0],
  resolution: props.capabilities.resolutions[0],
  durationSeconds: props.capabilities.durations[0],
  candidateCount: props.capabilities.candidateCounts[0],
  audioEnabled: props.capabilities.audio,
  autoValidate: true,
  autoLink: true,
});
const exactPayload = computed(() => ({
  provider: props.capabilities.provider,
  model: props.capabilities.model,
  ...settings,
  actualReferences: props.actualReferences,
}));

function generate() {
  if (!prompt.value.trim()) return;
  emit("save-config", exactPayload.value);
  emit("generate", { prompt: prompt.value.trim(), config: exactPayload.value });
}
</script>

<template>
  <section class="node-composer" aria-label="节点生成器">
    <div class="chips"><button type="button">＋参考</button><button type="button">标记</button><button type="button">主体库</button><button type="button">运镜</button></div>
    <textarea v-model="prompt" aria-label="生成 Prompt" placeholder="描述要生成的画面；可使用主体与素材引用" />
    <div class="settings">
      <label>模式<select v-model="settings.mode"><option v-for="item in capabilities.modes" :key="item" :value="item">{{ item }}</option></select></label>
      <label>比例<select v-model="settings.aspectRatio"><option v-for="item in capabilities.aspectRatios" :key="item">{{ item }}</option></select></label>
      <label>清晰度<select v-model="settings.resolution"><option v-for="item in capabilities.resolutions" :key="item">{{ item }}</option></select></label>
      <label>时长<select v-model.number="settings.durationSeconds"><option v-for="item in capabilities.durations" :key="item" :value="item">{{ item }}s</option></select></label>
      <label>数量<select v-model.number="settings.candidateCount"><option v-for="item in capabilities.candidateCounts" :key="item" :value="item">{{ item }}</option></select></label>
    </div>
    <div class="toggles"><label><input v-model="settings.audioEnabled" type="checkbox" :disabled="!capabilities.audio" />生成音频</label><label><input v-model="settings.autoValidate" type="checkbox" />自动校验素材</label><label><input v-model="settings.autoLink" type="checkbox" />智能引用 AutoLink</label></div>
    <details v-if="actualReferences.length" open><summary>实际供应商输入</summary><ul><li v-for="item in actualReferences" :key="item.assetId" :class="{ omitted: !item.providerIncluded }"><b>{{ item.semanticRole }}</b><span>{{ item.providerIncluded ? '已进入供应商请求' : '未进入供应商请求' }}</span><small v-if="item.omissionReason">{{ item.omissionReason }}</small></li></ul></details>
    <footer><span>{{ capabilities.provider }} · {{ capabilities.model }} · Revision {{ nodeRevision }}</span><button data-action="generate" type="button" :disabled="!prompt.trim()" @click="generate">确认并生成</button></footer>
  </section>
</template>

<style scoped>
.node-composer { width: min(760px, calc(100vw - 40px)); padding: 14px; color: #e9edf4; background: #22252b; border: 1px solid #3c424c; border-radius: 14px; box-shadow: 0 20px 60px rgb(0 0 0 / 42%); }.chips, .settings, .toggles, footer { display: flex; gap: 7px; align-items: center; flex-wrap: wrap; }.chips button { padding: 6px 9px; color: #aeb8c8; background: #2d3037; border: 0; border-radius: 999px; } textarea { box-sizing: border-box; width: 100%; min-height: 110px; margin: 12px 0; padding: 12px; color: #f0f3f8; background: #1a1c20; border: 1px solid #3a4049; border-radius: 10px; resize: vertical; }.settings label { display: grid; gap: 4px; color: #8792a3; font-size: 10px; }.settings select { min-width: 92px; padding: 6px; color: #e3e8ef; background: #292d34; border: 1px solid #414955; border-radius: 6px; }.toggles { margin: 11px 0; color: #adb6c4; font-size: 11px; } details { padding: 9px; background: #1b1e23; border-radius: 8px; } ul { padding-left: 18px; } li { margin: 5px 0; } li span, li small { margin-left: 8px; color: #7ed8a9; }.omitted span, .omitted small { color: #e3ad6d; } footer { justify-content: space-between; margin-top: 12px; color: #7f8a9a; font-size: 11px; } footer button { padding: 9px 14px; color: #15181d; background: #e1e8f2; border: 0; border-radius: 8px; font-weight: 700; } footer button:disabled { opacity: .35; }
</style>
