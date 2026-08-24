<script setup lang="ts">
import { computed, reactive, watch } from "vue";

import type {
  CanvasNodeDto,
  EpisodeVisualProfileDto,
  LookReferenceBinding,
  SubjectReferenceDto,
  VisualProfileDraft,
} from "../../api/types";

const props = defineProps<{
  node: CanvasNodeDto;
  profile?: EpisodeVisualProfileDto | null;
  loading?: boolean;
  saving?: boolean;
}>();

const emit = defineEmits<{
  "open-library": [];
  save: [draft: VisualProfileDraft];
}>();

const form = reactive<VisualProfileDraft>({
  personIdentity: "",
  personHair: "",
  personBody: "",
  catIdentity: "",
  stylePositive: [],
  styleNegative: [],
  referenceBindings: [],
});
const positiveText = reactive({ value: "" });
const negativeText = reactive({ value: "" });

const references = computed<SubjectReferenceDto[]>(() => {
  const nodeRefs = Array.isArray(props.node.data.references)
    ? props.node.data.references as unknown as SubjectReferenceDto[]
    : [];
  return nodeRefs.length ? nodeRefs : props.profile?.references ?? [];
});
const isStyle = computed(() => props.node.type === "StylePresetNode");
const nodeTitle = computed(() => String(props.node.data.title ?? (isStyle.value ? "线条材质" : "固定主体")));
const hasOptionalEvidence = computed(() => references.value.some((reference) => !reference.required));

watch(() => props.profile, (profile) => {
  if (!profile) return;
  form.personIdentity = profile.personIdentity;
  form.personHair = profile.personHair;
  form.personBody = profile.personBody;
  form.catIdentity = profile.catIdentity;
  form.stylePositive = [...profile.stylePositive];
  form.styleNegative = [...profile.styleNegative];
  form.referenceBindings = profile.referenceBindings.map((binding): LookReferenceBinding => ({ ...binding }));
  positiveText.value = profile.stylePositive.join("\n");
  negativeText.value = profile.styleNegative.join("\n");
}, { immediate: true });

function lines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function saveProfile() {
  emit("save", {
    ...form,
    stylePositive: lines(positiveText.value),
    styleNegative: lines(negativeText.value),
    referenceBindings: form.referenceBindings.map((binding) => ({ ...binding })),
  });
}
</script>

<template>
  <section class="visual-evidence-console">
    <header>
      <div><span>{{ isStyle ? 'STYLE PRESET' : 'CANON EVIDENCE' }}</span><b>{{ nodeTitle }}</b><small>Revision {{ profile?.revision ?? node.revision ?? 1 }} · 必需身份槽位锁定</small></div>
      <button type="button" @click="emit('open-library')">打开素材与预设库</button>
    </header>

    <div v-if="loading" class="console-state">正在读取本集视觉档案…</div>
    <template v-else>
      <div class="evidence-grid">
        <figure v-for="(reference, index) in references" :key="reference.semanticKey || reference.assetId">
          <img :src="reference.thumbnailUrl || reference.contentUrl" :alt="reference.title" />
          <figcaption>
            <i>{{ index + 1 }}</i><b>{{ reference.title }}</b><small>{{ reference.semanticKey }}</small>
            <span>{{ reference.approvalStatus }} · {{ reference.required ? '必需并锁定' : '可选' }}</span>
            <p>{{ reference.instruction }}</p>
          </figcaption>
        </figure>
      </div>
      <p v-if="!isStyle && references.length && !hasOptionalEvidence" class="supplement-note">
        当前仅显示已批准的必需证据；表情、多角度或背面证据可从素材库继续补充，不会伪造占位图。
      </p>
      <p v-if="!references.length" class="console-state">当前节点没有真实资产引用；不会生成伪造占位图。</p>

      <details v-if="profile" class="profile-editor" open>
        <summary>编辑本集视觉描述 <small>保存会创建新版本并使下游资产过期</small></summary>
        <div class="profile-fields">
          <label v-if="!isStyle"><span>人物身份不变量</span><textarea v-model="form.personIdentity" rows="2" /></label>
          <label v-if="!isStyle"><span>猫咪身份不变量</span><textarea v-model="form.catIdentity" rows="2" /></label>
          <label><span>正向画风（每行一项）</span><textarea v-model="positiveText.value" rows="3" /></label>
          <label><span>排除项（每行一项）</span><textarea v-model="negativeText.value" rows="3" /></label>
        </div>
        <div class="binding-editor">
          <b>引用职责说明</b>
          <label v-for="binding in form.referenceBindings" :key="binding.assetId">
            <span>{{ binding.purpose }} · 必需资产锁定</span>
            <input v-model="binding.instruction" type="text" />
          </label>
        </div>
        <footer>
          <small>图片提供视觉证据；文字声明职责、不变量与排除项，不会被简单拼成一个 Prompt。</small>
          <button type="button" :disabled="saving" @click="saveProfile">保存本集视觉档案</button>
        </footer>
      </details>
    </template>
  </section>
</template>

<style scoped>
.visual-evidence-console { display: grid; gap: 14px; color: #e8ebf0; }.visual-evidence-console > header { display: flex; align-items: center; justify-content: space-between; gap: 18px; }.visual-evidence-console header > div { display: grid; gap: 3px; }.visual-evidence-console header span { color: #8390a1; font-size: 10px; font-weight: 800; letter-spacing: .12em; }.visual-evidence-console header b { font-size: 18px; }.visual-evidence-console header small { color: #838d9a; }.visual-evidence-console button { min-height: 44px; padding: 0 14px; color: #e9edf3; background: #30343a; border: 1px solid #474c55; border-radius: 9px; cursor: pointer; }.visual-evidence-console button:hover,.visual-evidence-console button:focus-visible { background: #3a4048; outline: 2px solid #78aef0; outline-offset: -2px; }.visual-evidence-console button:disabled { opacity: .45; cursor: wait; }
.evidence-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }.evidence-grid figure { min-width: 0; margin: 0; overflow: hidden; background: #181a1e; border: 1px solid #343840; border-radius: 10px; }.evidence-grid img { display: block; width: 100%; height: 160px; object-fit: cover; background: #101216; }.evidence-grid figcaption { position: relative; display: grid; gap: 3px; padding: 9px; }.evidence-grid i { position: absolute; top: -30px; left: 8px; display: grid; width: 24px; height: 24px; place-items: center; color: #fff; background: rgb(15 17 20 / 88%); border-radius: 50%; font-size: 11px; font-style: normal; }.evidence-grid figcaption b,.evidence-grid figcaption small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.evidence-grid figcaption small,.evidence-grid figcaption span { color: #7f8997; font-size: 10px; }.evidence-grid figcaption p { margin: 3px 0 0; color: #aab2bd; font-size: 10px; line-height: 1.4; }
.supplement-note { margin: -4px 0 0; padding: 9px 11px; color: #c9a96f; background: #2a241b; border: 1px solid #4d402c; border-radius: 8px; font-size: 11px; }
.profile-editor { padding: 12px; background: #1b1d21; border: 1px solid #343840; border-radius: 10px; }.profile-editor summary { color: #dfe4eb; cursor: pointer; }.profile-editor summary small { margin-left: 9px; color: #916f43; }.profile-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 12px; }.profile-fields label { display: grid; gap: 5px; color: #9aa4b2; font-size: 11px; }.profile-fields textarea { resize: vertical; min-height: 58px; padding: 9px; color: #e6eaf0; background: #121417; border: 1px solid #353a43; border-radius: 8px; font: inherit; line-height: 1.45; }.profile-editor footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 10px; }.profile-editor footer small { color: #808a98; line-height: 1.4; }.profile-editor footer button { flex: 0 0 auto; color: #15202b; font-weight: 700; background: #dceaf9; }.console-state { display: grid; min-height: 160px; place-items: center; color: #87919e; border: 1px dashed #393e46; border-radius: 9px; }
.binding-editor { display: grid; gap: 7px; margin-top: 12px; padding-top: 12px; border-top: 1px solid #32363d; }.binding-editor > b { color: #cfd5de; font-size: 11px; }.binding-editor label { display: grid; grid-template-columns: 160px minmax(0, 1fr); align-items: center; gap: 10px; color: #818b98; font-size: 10px; }.binding-editor input { min-height: 36px; padding: 0 9px; color: #e2e7ed; background: #121417; border: 1px solid #353a43; border-radius: 7px; }
@media (max-width: 720px) { .profile-fields { grid-template-columns: 1fr; }.visual-evidence-console > header,.profile-editor footer { align-items: stretch; flex-direction: column; } }
</style>
