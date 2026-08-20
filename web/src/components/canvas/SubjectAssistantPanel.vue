<script setup lang="ts">
import { computed, reactive, watch } from "vue";

import type { SubjectCompletionRunDto, SubjectInput } from "../../api/types";

const props = defineProps<{ subject: SubjectInput; run: SubjectCompletionRunDto | null; loading?: boolean }>();
const emit = defineEmits<{
  run: [instruction: string];
  apply: [payload: { acceptedFields: string[]; finalDraft: SubjectInput }];
  "inspect-prompt": [promptId: string];
}>();

const instruction = defineModel<string>("instruction", { default: "" });
const accepted = reactive<Record<string, boolean>>({});
const candidate = reactive<SubjectInput>({ ...props.subject });
const fields = [
  ["identityAnchors", "身份锚点"],
  ["immutableTraits", "不可变化特征"],
  ["relationshipNotes", "关系"],
  ["dramaticFunction", "戏剧功能"],
  ["visualRisks", "视觉风险"],
] as const;

const missing = computed(() => fields.filter(([key]) => {
  const value = props.subject[key];
  return value === "" || (Array.isArray(value) && value.length === 0);
}));
const acceptedFields = computed(() => fields.map(([key]) => key).filter((key) => accepted[key]));

watch(() => props.run, (run) => {
  Object.assign(candidate, props.subject);
  for (const [key] of fields) accepted[key] = false;
  if (!run?.proposal) return;
  for (const [key] of fields) {
    const value = run.proposal[key];
    if (value !== undefined) Object.assign(candidate, { [key]: value });
  }
}, { immediate: true });

function apply() {
  const finalDraft: SubjectInput = {
    ...props.subject,
    identityAnchors: [...props.subject.identityAnchors],
    immutableTraits: [...props.subject.immutableTraits],
    visualRisks: [...(props.subject.visualRisks ?? [])],
    references: (props.subject.references ?? []).map((item) => ({ ...item })),
  };
  for (const key of acceptedFields.value) Object.assign(finalDraft, { [key]: candidate[key] });
  emit("apply", { acceptedFields: acceptedFields.value, finalDraft });
}
</script>

<template>
  <section class="subject-assistant" aria-label="主体 AI 分析与补全">
    <header><div><b>主体完整度</b><small>Revision 不会被建议自动覆盖</small></div><strong>{{ missing.length ? `缺少 ${missing.length} 项` : '信息完整' }}</strong></header>
    <ul><li v-for="[, label] in missing" :key="label">{{ label }}</li></ul>
    <textarea v-model="instruction" aria-label="主体补全要求" placeholder="可选：说明画风、剧情功能或必须保持的细节" />
    <button data-action="run" type="button" :disabled="loading" @click="emit('run', instruction)">AI 分析并补全</button>

    <div v-if="run?.status === 'pending'" class="state">已进入持久任务队列，可安全刷新页面。</div>
    <div v-else-if="run?.status === 'awaiting_review' && run.proposal" class="proposal">
      <h3>建议修订 · 逐项选择</h3>
      <label v-for="[key, label] in fields" :key="key">
        <input v-model="accepted[key]" type="checkbox" :value="key" />
        <span><b>{{ label }}</b><small>当前：{{ subject[key] || '未填写' }}</small><small>建议：{{ candidate[key] || '保持为空' }}</small></span>
      </label>
      <div class="actions">
        <button v-if="run.promptId" type="button" @click="emit('inspect-prompt', run.promptId)">查看精确 Prompt</button>
        <button data-action="apply" type="button" :disabled="!acceptedFields.length || loading" @click="apply">应用为新 Revision</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.subject-assistant { color: #e6ebf3; } header { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 12px; background: #20242b; border: 1px solid #343b46; border-radius: 10px; } header div { display: grid; } header small { margin-top: 3px; color: #8792a3; } header strong { color: #e8c979; }
ul { display: flex; gap: 6px; flex-wrap: wrap; padding: 0; list-style: none; } li { padding: 4px 7px; color: #d3af67; background: #332b1d; border-radius: 6px; font-size: 11px; }
textarea { box-sizing: border-box; width: 100%; min-height: 86px; margin: 6px 0 10px; padding: 10px; color: #eef2f8; background: #171a1f; border: 1px solid #3a414c; border-radius: 8px; resize: vertical; }
button { padding: 9px 12px; color: #e8edf5; background: #2b3440; border: 1px solid #485567; border-radius: 8px; cursor: pointer; } button:disabled { opacity: .45; cursor: not-allowed; }
.state { margin-top: 12px; padding: 10px; color: #d8bd76; background: #2d281c; border-radius: 8px; }.proposal { margin-top: 14px; }.proposal label { display: flex; gap: 9px; margin: 7px 0; padding: 9px; background: #1d2026; border: 1px solid #303640; border-radius: 8px; }.proposal label span { display: grid; gap: 3px; }.proposal small { color: #8e99aa; }.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
