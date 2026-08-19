<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { assetContentUrl } from "../api/client";
import type {
  ShotAssistContext,
  ShotAssistPatch,
  ShotAssistRecord,
  ShotDto,
} from "../api/types";

const props = defineProps<{
  context: ShotAssistContext | null;
  records: ShotAssistRecord[];
  shot?: ShotDto | null;
}>();
const emit = defineEmits<{
  apply: [
    record: ShotAssistRecord,
    patch: ShotAssistPatch | null,
    acceptedAnchorBrief: string | null,
  ];
  adoptTail: [];
}>();

const selectedFields = ref<string[]>([]);
const selectedCreativeBody = ref("");
const selectedStepId = ref("");
const selectedAnchorBrief = ref("");
const failedImages = ref<Set<string>>(new Set());
const record = computed(() => (
  props.records.find((item) => item.stepId === selectedStepId.value)
  ?? props.records[0]
  ?? null
));
const anchorBriefAlreadyAccepted = computed(() => Boolean(
  record.value?.acceptedAnchorBriefAt || record.value?.acceptedAnchorBrief,
));
const patchAlreadyAccepted = computed(() => Boolean(
  record.value?.acceptedPatchAt
  || (record.value?.acceptedOutput && Object.keys(record.value.acceptedOutput).length),
));
const creativeBodies = computed(() => {
  const analysis = record.value?.analysis;
  if (!analysis) return [];
  const values = [
    ...(analysis.creativeBody ? [{ label: "推荐正文", body: analysis.creativeBody, rationale: "LLM 主推荐" }] : []),
    ...(analysis.creativeAlternatives ?? []).map((item) => ({
      label: item.label === "stable" ? "稳定版" : "保守版",
      body: item.body,
      rationale: item.rationale,
    })),
  ];
  return values.filter((item, index) => values.findIndex((value) => value.body === item.body) === index);
});
const proposedPatch = computed<ShotAssistPatch>(() => {
  const analysis = record.value?.analysis;
  const patch = { ...(analysis?.patch ?? {}) };
  if (analysis && props.shot) {
    if (
      patch.durationSeconds === undefined
      && analysis.pacingPlan.recommendedDurationSeconds !== props.shot.durationSeconds
    ) patch.durationSeconds = analysis.pacingPlan.recommendedDurationSeconds;
    if (
      patch.sceneLookUsage === undefined
      && analysis.recommendedSceneLookUsage !== props.shot.sceneLookUsage
    ) patch.sceneLookUsage = analysis.recommendedSceneLookUsage;
    if (
      patch.anchorMode === undefined
      && analysis.recommendedAnchorMode !== props.shot.anchorMode
    ) patch.anchorMode = analysis.recommendedAnchorMode;
  }
  if (selectedCreativeBody.value) patch.direction = selectedCreativeBody.value;
  return patch;
});
const patchEntries = computed(() => Object.entries(proposedPatch.value));

const fieldLabels: Record<string, string> = {
  title: "片段标题",
  direction: "完整分镜描述",
  durationSeconds: "目标时长",
  sceneLookUsage: "场景视觉基准策略",
  anchorMode: "锚点方式",
  referenceBindings: "片段参考绑定",
};

watch(
  () => props.records.map((item) => item.stepId),
  (ids) => {
    const selected = props.records.find((item) => item.stepId === selectedStepId.value);
    const latestActionable = props.records.find((item) => (
      item.analysis
      && !item.stale
      && (
        !Boolean(item.acceptedAnchorBriefAt || item.acceptedAnchorBrief)
        || !Boolean(
          item.acceptedPatchAt
          || (item.acceptedOutput && Object.keys(item.acceptedOutput).length),
        )
      )
    ));
    if (latestActionable && latestActionable.stepId !== selectedStepId.value) {
      selectedStepId.value = latestActionable.stepId;
    } else if (!selected || selected.stale) {
      selectedStepId.value = props.records.find((item) => item.analysis && !item.stale)?.stepId
        ?? ids[0]
        ?? "";
    }
  },
  { immediate: true },
);

watch(
  () => record.value?.stepId,
  () => {
    selectedCreativeBody.value = record.value?.analysis?.patch?.direction
      ?? record.value?.analysis?.creativeBody
      ?? creativeBodies.value[0]?.body
      ?? "";
    selectedAnchorBrief.value = record.value?.analysis?.anchorBrief ?? "";
    selectedFields.value = [];
  },
  { immediate: true },
);

function applySelectedFields() {
  if (!record.value?.analysis) return;
  const patch = Object.fromEntries(
    patchEntries.value.filter(([key]) => selectedFields.value.includes(key)),
  ) as ShotAssistPatch;
  const selectedPatch = Object.keys(patch).length ? patch : null;
  if (selectedPatch) emit("apply", record.value, selectedPatch, null);
}

function applyAnchorBrief() {
  if (!record.value?.analysis || !selectedAnchorBrief.value.trim()) return;
  emit("apply", record.value, null, selectedAnchorBrief.value.trim());
}

function currentValue(key: string): unknown {
  if (!props.shot) return "未载入当前稿";
  return {
    title: props.shot.title,
    direction: props.shot.direction,
    durationSeconds: props.shot.durationSeconds,
    sceneLookUsage: props.shot.sceneLookUsage,
    anchorMode: props.shot.anchorMode,
    referenceBindings: props.shot.referenceBindings,
  }[key];
}

function displayValue(value: unknown): string {
  if (Array.isArray(value)) {
    if (!value.length) return "无";
    return value.map((item) => {
      if (typeof item !== "object" || item === null) return String(item);
      const binding = item as { assetId?: string; usage?: string; role?: string; applyTo?: string };
      return `${binding.assetId ?? "?"} · ${binding.usage ?? "?"}/${binding.role ?? "?"}/${binding.applyTo ?? "?"}`;
    }).join("\n");
  }
  if (typeof value === "object" && value !== null) return JSON.stringify(value, null, 2);
  return String(value ?? "无");
}

function imageFailed(assetId: string) {
  failedImages.value = new Set([...failedImages.value, assetId]);
}
</script>

<template>
  <div class="shot-assistance-panel">
    <div v-if="context" class="context-block">
      <span>本地诊断：{{ context.localAnalysis.detectedSubshotCount }} 个子镜头；建议 {{ context.localAnalysis.suggestedSubshotMin }}–{{ context.localAnalysis.suggestedSubshotMax }} 个</span>
      <span>相邻：{{ context.previousShot?.title || '无上一片段' }} → 当前 → {{ context.nextShot?.title || '无下一片段' }}</span>
      <el-alert v-for="finding in context.localAnalysis.findings" :key="finding.code" :type="finding.severity === 'warning' ? 'warning' : 'info'" :title="finding.message" :closable="false" />
      <div v-if="context.previousTail.available || context.previousTail.stale" class="tail-row">
        <template v-if="context.previousTail.assetId && !failedImages.has(context.previousTail.assetId)">
          <img :src="assetContentUrl(context.previousTail.assetId)" @error="imageFailed(context.previousTail.assetId)" />
        </template>
        <span v-else-if="context.previousTail.assetId">加载失败 · {{ context.previousTail.assetId }}</span>
        <el-tag :type="context.previousTail.stale ? 'warning' : 'success'">{{ context.previousTail.stale ? '尾帧已过期' : '上一片段尾帧可用' }}</el-tag>
        <el-button size="small" @click="emit('adoptTail')">{{ context.previousTail.stale ? '重新抽取并采用' : '采用为唯一锚点' }}</el-button>
      </div>
    </div>

    <label v-if="records.length" class="history-select">
      分析历史
      <select v-model="selectedStepId">
        <option v-for="item in records" :key="item.stepId" :value="item.stepId">
          {{ item.createdAt || item.stepId }} · {{ item.status }}{{ item.stale ? ' · 已过期' : '' }}
        </option>
      </select>
    </label>

    <div v-if="record" class="analysis-block">
      <el-alert v-if="record.stale" title="该分析对应旧草稿，不能再接受" type="warning" :closable="false" />
      <el-alert v-if="record.error" :title="String(record.error.message ?? 'LLM 分析失败，已保存内容不受影响')" type="error" :closable="false" />
      <template v-if="record.analysis">
        <p>{{ record.analysis.actionDensityAssessment }}</p>
        <p v-if="record.analysis.assetCompatibilityAssessment"><b>素材适配：</b>{{ record.analysis.assetCompatibilityAssessment }}</p>
        <p><b>节奏：</b>{{ record.analysis.pacingPlan.rationale }}（建议 {{ record.analysis.pacingPlan.recommendedDurationSeconds }} 秒）</p>
        <ol><li v-for="beat in record.analysis.pacingPlan.beats" :key="beat.ordinal">{{ beat.description }} · {{ beat.rhythm }}</li></ol>
        <p><b>推荐策略：</b>{{ record.analysis.recommendedSceneLookUsage }}；锚点 {{ record.analysis.recommendedAnchorMode }}</p>
        <p><b>衔接：</b>{{ record.analysis.continuity.recommendation }}</p>
        <ul>
          <li v-for="issue in record.analysis.continuity.previousIssues" :key="`previous-${issue}`">上一片段：{{ issue }}</li>
          <li v-for="issue in record.analysis.continuity.nextIssues" :key="`next-${issue}`">下一片段：{{ issue }}</li>
          <li v-for="risk in record.analysis.promptRisks" :key="risk">Prompt 风险：{{ risk }}</li>
          <li v-for="decision in record.analysis.referenceDecisions" :key="decision.assetId">参考 {{ decision.assetId }}：{{ decision.decision }}{{ decision.recommendedRole ? ` → ${decision.recommendedRole}` : '' }}；{{ decision.reason }}</li>
        </ul>
        <div v-if="creativeBodies.length" class="creative-candidates">
          <b>Seedance 创作正文候选</b>
          <label v-for="item in creativeBodies" :key="item.body">
            <el-radio v-model="selectedCreativeBody" :value="item.body">{{ item.label }}</el-radio>
            <small>{{ item.rationale }}</small>
            <pre>{{ item.body }}</pre>
          </label>
        </div>
        <div v-if="record.analysis.anchorBrief" class="anchor-brief-block">
          <div>
            <b>开场静态画面稿</b>
            <small>只用于 Seedream 首帧；不得包含完整动作过程、声音或收尾结果。</small>
          </div>
          <el-input
            v-model="selectedAnchorBrief"
            type="textarea"
            :rows="6"
            :disabled="anchorBriefAlreadyAccepted"
            maxlength="4000"
            show-word-limit
          />
          <el-button
            type="primary"
            :disabled="record.stale || anchorBriefAlreadyAccepted || !selectedAnchorBrief.trim()"
            @click="applyAnchorBrief"
          >{{ anchorBriefAlreadyAccepted ? '静态稿已采用' : '采用该静态画面稿' }}</el-button>
        </div>
        <div class="patch-block">
          <b>选择要应用的字段</b>
          <el-checkbox-group v-model="selectedFields">
            <div v-for="([key, suggested]) in patchEntries" :key="key" class="diff-row">
              <el-checkbox :value="key">{{ fieldLabels[key] || key }}</el-checkbox>
              <div><small>当前人工稿</small><pre>{{ displayValue(currentValue(key)) }}</pre></div>
              <div><small>LLM 候选稿</small><pre>{{ displayValue(suggested) }}</pre></div>
            </div>
          </el-checkbox-group>
          <span v-if="!patchEntries.length">本次分析没有提出字段改写。</span>
          <el-button
            type="primary"
            :disabled="record.stale || patchAlreadyAccepted || selectedFields.length === 0"
            @click="applySelectedFields"
          >{{ patchAlreadyAccepted ? '字段修改已接受' : '接受所选字段修改' }}</el-button>
        </div>
      </template>
    </div>
    <div v-else class="empty">保存片段后可选择是否进行一次付费多模态分析。</div>
  </div>
</template>

<style scoped>
.shot-assistance-panel, .context-block, .analysis-block, .patch-block, .creative-candidates, .anchor-brief-block { display: grid; gap: 8px; }
.anchor-brief-block { padding: 10px; border: 1px solid #31577d; border-radius: 8px; background: #101a25; }.anchor-brief-block small { display: block; color: #8fa4bc; margin-top: 3px; }
.history-select { display: grid; gap: 5px; color: #9eabc0; }.history-select select { background: #111722; color: #e7eaf0; border: 1px solid #344056; padding: 7px; }
.diff-row { display: grid; grid-template-columns: 140px 1fr 1fr; gap: 8px; align-items: start; border: 1px solid #293344; border-radius: 7px; padding: 8px; }.diff-row small { color: #8791a2; }
.tail-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tail-row img { width: 90px; height: 120px; object-fit: contain; background: #090c11; }
.empty { color: #8791a2; font-size: 12px; }
pre { white-space: pre-wrap; max-height: 260px; overflow: auto; background: #0c0f15; padding: 8px; }
.creative-candidates label { display: grid; gap: 4px; padding: 8px; border: 1px solid #293344; border-radius: 7px; }.creative-candidates small { color: #8791a2; }
</style>
