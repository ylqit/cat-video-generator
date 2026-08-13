<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref, toRaw, watch } from "vue";

import { api } from "../api/client";
import type {
  CreativeWorkflowDto,
  CreativeStepRecord,
  JobDto,
  SceneDto,
  ShotSuggestionOutput,
  StoryDiagnosisOutput,
  StoryRewriteOutput,
  StoryRewriteStrategy,
} from "../api/types";

const props = defineProps<{ scene: SceneDto }>();
const emit = defineEmits<{ changed: [] }>();

const workflow = ref<CreativeWorkflowDto | null>(null);
const busy = ref(false);
const error = ref("");
const planningModel = ref("");
const diagnosisStepId = ref("");
const diagnosisDraft = ref<StoryDiagnosisOutput | null>(null);
const selectedStrategy = ref<StoryRewriteStrategy>("balanced");
const diagnosisInstructions = ref("");
const preserveOriginal = ref(false);
const rewriteStepId = ref("");
const rewriteDraft = ref<StoryRewriteOutput | null>(null);
const storyboardStepId = ref("");
const storyboardDraft = ref<ShotSuggestionOutput | null>(null);

const diagnosisVersions = computed(() => workflow.value?.stages.diagnosis ?? []);

const acceptedDiagnosis = computed(() => workflow.value?.stages.diagnosis.find(
  (item) => Boolean(item.acceptedAt),
) ?? null);
const activeDiagnosis = computed(() => workflow.value?.stages.diagnosis.find(
  (item) => Boolean(item.acceptedAt) && item.sourceHash === workflow.value?.currentStoryHash,
) ?? null);
const selectedDiagnosis = computed(() => diagnosisVersions.value.find(
  (item) => item.stepId === diagnosisStepId.value,
) ?? null);
const selectedDiagnosisAccepted = computed(() => Boolean(selectedDiagnosis.value?.acceptedAt));
const rewriteEligibleDiagnosis = computed(() => {
  const accepted = activeDiagnosis.value?.acceptedOutput as {
    preserveOriginal?: boolean;
    selectedStrategy?: StoryRewriteStrategy | null;
  } | undefined;
  return accepted?.preserveOriginal === false && Boolean(accepted.selectedStrategy)
    ? activeDiagnosis.value
    : null;
});
const acceptedRewrite = computed(() => workflow.value?.stages.rewrite.find(
  (item) => Boolean(item.acceptedAt)
    && (item.acceptedOutput as { acceptedStoryHash?: string } | undefined)?.acceptedStoryHash
      === workflow.value?.currentStoryHash,
) ?? null);
const storyReady = computed(() => Boolean(acceptedRewrite.value)
  || Boolean((activeDiagnosis.value?.acceptedOutput as { preserveOriginal?: boolean } | undefined)?.preserveOriginal));
const storyboardVersions = computed(() => workflow.value?.stages.storyboard ?? []);
const selectedStoryboard = computed(() => storyboardVersions.value.find(
  (item) => item.stepId === storyboardStepId.value,
) ?? null);
const storyboardDuration = computed(() => storyboardDraft.value?.shots.reduce(
  (total, item) => total + item.suggestedDurationSeconds,
  0,
) ?? 0);
const hasShotHistory = computed(() => props.scene.shots.some(
  (shot) => shot.assets.length > 0 || shot.attempts.length > 0,
));

async function load() {
  workflow.value = await api.creativeWorkflow(props.scene.id);
  const selected = diagnosisVersions.value.find(
    (item) => item.stepId === diagnosisStepId.value,
  ) ?? diagnosisVersions.value.find(
    (item) => Boolean(item.providerOutput || item.acceptedOutput),
  ) ?? activeDiagnosis.value;
  selectDiagnosisVersion(selected ?? null);
  const storyboard = storyboardVersions.value.find(
    (item) => item.stepId === storyboardStepId.value,
  ) ?? storyboardVersions.value.find(
    (item) => Boolean(item.providerOutput || item.acceptedOutput),
  ) ?? null;
  selectStoryboardVersion(storyboard);
  const health = await api.health();
  planningModel.value = health.arkPlanningModel ?? "未配置";
}

function storyboardState(item: CreativeStepRecord): string {
  if (item.sourceHash && item.sourceHash !== workflow.value?.currentStoryHash) return "剧情已变化";
  const acceptedHash = (item.acceptedOutput as {
    appliedShotSnapshotHash?: string;
  } | null | undefined)?.appliedShotSnapshotHash;
  if (acceptedHash && acceptedHash === workflow.value?.currentShotSnapshotHash) return "当前采用";
  if (item.acceptedAt) return "历史已采用";
  if (item.status === "succeeded") return "待确认";
  if (item.status === "failed") return "生成失败";
  return item.status;
}

function selectStoryboardVersion(item: CreativeStepRecord | null) {
  storyboardStepId.value = item?.stepId ?? "";
  if (!item) {
    storyboardDraft.value = null;
    return;
  }
  const accepted = item.acceptedOutput as unknown as ShotSuggestionOutput | null | undefined;
  const provider = item.providerOutput as unknown as ShotSuggestionOutput | null | undefined;
  const output = accepted?.shots ? accepted : provider;
  storyboardDraft.value = output ? structuredClone(toRaw(output)) : null;
}

function diagnosisState(item: CreativeStepRecord): string {
  if (item.sourceHash && item.sourceHash !== workflow.value?.currentStoryHash) return "剧情已变化";
  if (item.stepId === activeDiagnosis.value?.stepId) return "当前采用";
  if (item.acceptedAt) return "历史已接受";
  if (item.status === "succeeded") return "待确认";
  if (item.status === "failed") return "生成失败";
  return item.status;
}

function selectDiagnosisVersion(item: CreativeStepRecord | null) {
  diagnosisStepId.value = item?.stepId ?? "";
  diagnosisInstructions.value = "";
  preserveOriginal.value = false;
  selectedStrategy.value = "balanced";
  if (!item) {
    diagnosisDraft.value = null;
    return;
  }
  const accepted = item.acceptedOutput as {
    diagnosis?: StoryDiagnosisOutput;
    selectedStrategy?: StoryRewriteStrategy | null;
    additionalInstructions?: string;
    preserveOriginal?: boolean;
  } | null | undefined;
  const output = accepted?.diagnosis
    ?? item.providerOutput as unknown as StoryDiagnosisOutput | null | undefined;
  diagnosisDraft.value = output ? structuredClone(toRaw(output)) : null;
  diagnosisInstructions.value = accepted?.additionalInstructions ?? "";
  preserveOriginal.value = accepted?.preserveOriginal ?? false;
  selectedStrategy.value = accepted?.selectedStrategy
    ?? output?.rewriteOptions.find((option) => option.strategy === "balanced")?.strategy
    ?? output?.rewriteOptions[0]?.strategy
    ?? "balanced";
}

async function waitJob(id: string): Promise<JobDto> {
  for (;;) {
    const job = await api.job(id);
    if (job.status === "succeeded" || job.status === "failed") return job;
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
}

async function perform(action: () => Promise<void>) {
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : String(caught);
  } finally {
    busy.value = false;
  }
}

async function diagnose() {
  await ElMessageBox.confirm(
    `剧情医生将把当前原始剧情和项目角色档案发送给 ${planningModel.value}。本次不分析图片，但会产生一次 Ark 规划模型费用。`,
    "剧情诊断付费确认",
  );
  await perform(async () => {
    const submitted = await api.diagnoseStory(props.scene.id);
    const job = await waitJob(submitted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "剧情诊断失败"));
    const result = job.result as { stepId: string; diagnosis: StoryDiagnosisOutput };
    diagnosisStepId.value = result.stepId;
    await load();
  });
}

async function acceptDiagnosis() {
  if (!diagnosisDraft.value || !diagnosisStepId.value) return;
  await perform(async () => {
    await api.acceptStoryDiagnosis(
      diagnosisStepId.value,
      diagnosisDraft.value!,
      preserveOriginal.value ? null : selectedStrategy.value,
      diagnosisInstructions.value,
      preserveOriginal.value,
    );
    await load();
    emit("changed");
    ElMessage.success(preserveOriginal.value ? "已确认保留原稿" : "已接受剧情诊断方案");
  });
}

async function rewrite() {
  if (!rewriteEligibleDiagnosis.value) return;
  await ElMessageBox.confirm(
    `剧本编辑将使用已接受诊断调用 ${planningModel.value}，重写完整剧情但不拆镜头。本次会产生一次 Ark 规划模型费用。`,
    "剧情重写付费确认",
  );
  await perform(async () => {
    const submitted = await api.rewriteStory(
      props.scene.id,
      rewriteEligibleDiagnosis.value!.stepId,
    );
    const job = await waitJob(submitted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "剧情重写失败"));
    const result = job.result as { stepId: string; rewrite: StoryRewriteOutput };
    rewriteStepId.value = result.stepId;
    rewriteDraft.value = structuredClone(result.rewrite);
    await load();
  });
}

function editRewriteHistory() {
  const latest = workflow.value?.stages.rewrite[0];
  if (!latest?.providerOutput) return;
  rewriteStepId.value = latest.stepId;
  rewriteDraft.value = structuredClone(latest.providerOutput as unknown as StoryRewriteOutput);
}

async function acceptRewrite() {
  if (!rewriteDraft.value || !rewriteStepId.value) return;
  await perform(async () => {
    await api.acceptStoryRewrite(rewriteStepId.value, rewriteDraft.value!);
    rewriteDraft.value = null;
    await load();
    emit("changed");
    ElMessage.success("人工编辑后的完整剧情已设为当前批准剧情");
  });
}

async function runStoryboard() {
  await ElMessageBox.confirm(
    `分镜导演将使用当前批准剧情调用 ${planningModel.value}，生成 ${props.scene.targetShotCount} 个可编辑视频片段。本次会产生一次 Ark 规划模型费用。`,
    "分镜导演付费确认",
  );
  await perform(async () => {
    const submitted = await api.suggestShots(props.scene.id);
    const job = await waitJob(submitted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "分镜生成失败"));
    const result = job.result as { stepId: string; output: ShotSuggestionOutput };
    storyboardStepId.value = result.stepId;
    await load();
  });
}

async function acceptStoryboard() {
  if (!storyboardDraft.value || !storyboardStepId.value) return;
  const applyMode = hasShotHistory.value ? "update_existing" : "replace";
  const sourceShotRevisions = applyMode === "update_existing"
    ? Object.fromEntries(props.scene.shots.map((shot) => [shot.id, shot.draftRevision]))
    : {};
  await perform(async () => {
    await api.acceptSuggestions(
      storyboardStepId.value,
      storyboardDraft.value!.lookPlan,
      storyboardDraft.value!.shots,
      applyMode,
      sourceShotRevisions,
    );
    await load();
    emit("changed");
    ElMessage.success(applyMode === "replace" ? "已建立视频片段" : "已更新现有视频片段并保留媒体历史");
  });
}

watch(() => [props.scene.id, props.scene.sourceText], () => void load());
onMounted(() => void load());
</script>

<template>
  <section class="creative-workflow" v-loading="busy">
    <header>
      <div><b>AI 创作流程</b><small>同一 Ark 规划模型串行执行；每一步单独确认和审计</small></div>
      <el-tag>{{ planningModel || '读取模型中' }}</el-tag>
    </header>
    <el-alert v-if="error" type="error" :title="error" :closable="false" />

    <div class="stage raw-stage">
      <b>1. 原始剧情</b>
      <p>{{ workflow?.originalStory || scene.sourceText }}</p>
      <template v-if="workflow && workflow.currentStory !== workflow.originalStory">
        <b>下一阶段实际采用的当前批准剧情</b>
        <p>{{ workflow.currentStory }}</p>
        <small>来源：{{ workflow.currentStorySource === 'accepted_rewrite' ? '人工接受的剧情重写稿' : '当前场景人工稿' }} · Step {{ workflow.currentStorySourceStepId || '无' }}</small>
      </template>
      <small>原稿保存在首个诊断输入快照；编辑或接受重写不会覆盖历史 Prompt 和媒体版本。</small>
    </div>

    <div class="stage">
      <div class="stage-head"><b>2. 剧情诊断</b><el-tag :type="activeDiagnosis ? 'success' : acceptedDiagnosis ? 'warning' : 'info'">{{ activeDiagnosis ? '当前稿已接受' : acceptedDiagnosis ? '旧稿已接受，需重跑' : '待确认' }}</el-tag></div>
      <div class="actions"><el-button type="primary" plain @click="diagnose">{{ workflow?.stages.diagnosis.length ? '生成新诊断版本' : '运行剧情医生' }}</el-button></div>
      <div v-if="diagnosisVersions.length" class="version-list" aria-label="剧情诊断版本">
        <button
          v-for="item in diagnosisVersions"
          :key="item.stepId"
          type="button"
          class="version-button"
          :class="{ selected: item.stepId === diagnosisStepId }"
          @click="selectDiagnosisVersion(item)"
        >
          <b>诊断 V{{ item.attempt }}</b>
          <span>{{ diagnosisState(item) }}</span>
          <small>{{ item.createdAt ? new Date(item.createdAt).toLocaleString() : '未记录时间' }}</small>
        </button>
      </div>
      <el-alert
        v-if="selectedDiagnosis?.sourceHash && selectedDiagnosis.sourceHash !== workflow?.currentStoryHash"
        type="warning"
        title="该版本基于旧剧情，只能查看审计记录；请基于当前剧情生成新版本。"
        :closable="false"
      />
      <el-alert
        v-else-if="selectedDiagnosis?.status === 'failed'"
        type="error"
        :title="String(selectedDiagnosis.error?.message ?? '该版本生成失败，可生成新版本重试。')"
        :closable="false"
      />
      <template v-if="diagnosisDraft">
        <el-alert v-if="selectedDiagnosisAccepted" type="success" :title="diagnosisState(selectedDiagnosis!)" :closable="false" />
        <el-input v-model="diagnosisDraft.overallAssessment" type="textarea" :rows="3" :readonly="selectedDiagnosisAccepted" />
        <article v-for="(issue, index) in diagnosisDraft.issues" :key="index" class="issue">
          <b>{{ issue.category }}</b>
          <el-input v-model="issue.evidence" type="textarea" :rows="2" placeholder="原文证据" :readonly="selectedDiagnosisAccepted" />
          <el-input v-model="issue.impact" type="textarea" :rows="2" placeholder="可能影响" :readonly="selectedDiagnosisAccepted" />
          <el-input v-model="issue.suggestion" type="textarea" :rows="2" placeholder="修改建议" :readonly="selectedDiagnosisAccepted" />
        </article>
        <el-radio-group v-model="selectedStrategy" :disabled="preserveOriginal || selectedDiagnosisAccepted">
          <el-radio-button v-for="option in diagnosisDraft.rewriteOptions" :key="option.strategy" :value="option.strategy">{{ option.title }}</el-radio-button>
        </el-radio-group>
        <div v-for="option in diagnosisDraft.rewriteOptions" :key="`detail-${option.strategy}`" class="option"><b>{{ option.title }}</b><span>{{ option.summary }}</span><small>{{ option.tradeoffs }}</small></div>
        <el-input v-model="diagnosisInstructions" type="textarea" :rows="2" placeholder="补充口述或人工修改要求" :readonly="selectedDiagnosisAccepted" />
        <el-checkbox v-model="preserveOriginal" :disabled="selectedDiagnosisAccepted">保留原稿并允许直接进入分镜设计</el-checkbox>
        <el-button
          v-if="!selectedDiagnosisAccepted"
          type="success"
          :disabled="selectedDiagnosis?.status !== 'succeeded' || selectedDiagnosis?.sourceHash !== workflow?.currentStoryHash"
          @click="acceptDiagnosis"
        >接受人工编辑后的诊断</el-button>
      </template>
    </div>

    <div class="stage">
      <div class="stage-head"><b>3. 剧情重写</b><el-tag :type="acceptedRewrite ? 'success' : 'info'">{{ acceptedRewrite ? '已接受' : '待确认' }}</el-tag></div>
      <div class="actions"><el-button :disabled="!rewriteEligibleDiagnosis" type="primary" plain @click="rewrite">{{ workflow?.stages.rewrite.length ? '重新重写' : '运行剧本编辑' }}</el-button><el-button v-if="workflow?.stages.rewrite[0] && !acceptedRewrite" @click="editRewriteHistory">编辑最近结果</el-button></div>
      <template v-if="rewriteDraft">
        <el-input v-model="rewriteDraft.rewrittenStory" type="textarea" :rows="9" />
        <b>LLM 修改摘要</b>
        <ul><li v-for="item in rewriteDraft.changeSummary" :key="item">{{ item }}</li></ul>
        <ul><li v-for="item in rewriteDraft.unresolvedQuestions" :key="item">待决定：{{ item }}</li></ul>
        <el-button type="success" @click="acceptRewrite">接受人工编辑后的完整剧情</el-button>
      </template>
    </div>

    <div class="stage">
      <div class="stage-head"><b>4. 分镜导演</b><el-tag :type="storyboardVersions.some(item => storyboardState(item) === '当前采用') ? 'success' : 'info'">{{ storyboardVersions.some(item => storyboardState(item) === '当前采用') ? '当前片段已同步' : '待执行' }}</el-tag></div>
      <p>只读取当前批准剧情，严格按场景目标数量生成可编辑视频片段。</p>
      <el-button type="primary" :disabled="!storyReady" @click="runStoryboard">{{ storyboardVersions.length ? '生成新分镜版本' : '运行分镜导演' }}</el-button>
      <el-alert v-if="!storyReady" type="info" title="先接受剧情重写，或在剧情诊断中明确选择保留原稿。" :closable="false" />
      <div v-if="storyboardVersions.length" class="version-list" aria-label="分镜版本">
        <button
          v-for="item in storyboardVersions"
          :key="item.stepId"
          type="button"
          class="version-button"
          :class="{ selected: item.stepId === storyboardStepId }"
          @click="selectStoryboardVersion(item)"
        >
          <b>分镜 V{{ item.attempt }}</b>
          <span>{{ storyboardState(item) }}</span>
          <small>{{ item.createdAt ? new Date(item.createdAt).toLocaleString() : '未记录时间' }}</small>
        </button>
      </div>
      <el-alert
        v-if="selectedStoryboard?.sourceHash && selectedStoryboard.sourceHash !== workflow?.currentStoryHash"
        type="warning"
        title="该分镜基于旧剧情，只能查看；请生成新版本。"
        :closable="false"
      />
      <el-alert
        v-else-if="selectedStoryboard?.status === 'failed'"
        type="error"
        :title="String(selectedStoryboard.error?.message ?? '该分镜版本生成失败。')"
        :closable="false"
      />
      <template v-if="storyboardDraft">
        <div class="suggestion-summary">
          <b>场景：{{ storyboardDraft.sceneTitle }}</b>
          <el-tag>{{ storyboardDraft.shots.length }} 个片段</el-tag>
          <el-tag type="info">累计 {{ storyboardDuration }} 秒</el-tag>
        </div>
        <el-divider content-position="left">场景造型方案</el-divider>
        <div class="look-grid">
          <el-form-item label="人物服装"><el-input v-model="storyboardDraft.lookPlan.personWardrobe" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
          <el-form-item label="人物配件"><el-input v-model="storyboardDraft.lookPlan.personAccessories" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
          <el-form-item label="猫咪外观/配件"><el-input v-model="storyboardDraft.lookPlan.catAppearance" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
          <el-form-item label="关键道具"><el-input v-model="storyboardDraft.lookPlan.keyProps" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
          <el-form-item label="人物姿态"><el-input v-model="storyboardDraft.lookPlan.personPose" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
          <el-form-item label="猫咪姿态"><el-input v-model="storyboardDraft.lookPlan.catPose" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
        </div>
        <el-form-item label="构图与人猫空间关系"><el-input v-model="storyboardDraft.lookPlan.composition" type="textarea" :rows="2" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
        <article v-for="(shot, index) in storyboardDraft.shots" :key="index" class="suggestion-shot">
          <div class="suggestion-shot-head"><b>{{ index + 1 }}. 视频片段</b><el-input-number v-model="shot.suggestedDurationSeconds" :min="8" :max="15" :disabled="Boolean(selectedStoryboard?.acceptedAt)" /></div>
          <el-form-item label="标题"><el-input v-model="shot.title" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
          <el-form-item label="完整分镜描述（2–4 个编号子镜头）"><el-input v-model="shot.direction" type="textarea" :rows="7" :readonly="Boolean(selectedStoryboard?.acceptedAt)" /></el-form-item>
        </article>
        <el-alert
          v-if="hasShotHistory && props.scene.shots.length !== storyboardDraft.shots.length"
          type="warning"
          title="当前已有媒体历史且片段数量不同，不能覆盖；请保留该版本作为历史或新建场景。"
          :closable="false"
        />
        <el-button
          v-if="!selectedStoryboard?.acceptedAt"
          type="success"
          :disabled="selectedStoryboard?.status !== 'succeeded' || selectedStoryboard?.sourceHash !== workflow?.currentStoryHash || (hasShotHistory && props.scene.shots.length !== storyboardDraft.shots.length)"
          @click="acceptStoryboard"
        >{{ hasShotHistory ? '接受编辑稿并更新现有片段' : '接受编辑稿并建立视频片段' }}</el-button>
      </template>
    </div>

    <div class="stage">
      <div class="stage-head"><b>5. 片段视觉与 Prompt 审稿</b><el-tag>{{ workflow?.reviews.length ?? 0 }} 次</el-tag></div>
      <p>在每个片段保存后逐次确认，LLM 实际查看所选图片；建议仍需在右侧逐项接受。</p>
    </div>

    <el-collapse v-if="workflow && (workflow.stages.diagnosis.length || workflow.stages.rewrite.length || workflow.stages.storyboard.length)">
      <el-collapse-item title="四阶段历史、原稿与接受稿" name="history">
        <template v-for="(records, stage) in workflow.stages" :key="stage">
          <article v-for="item in records" :key="item.stepId" class="history">
            <b>{{ stage }} #{{ item.attempt }} · {{ item.status }}{{ item.acceptedAt ? ' · 已接受' : '' }}</b>
            <details><summary>LLM 原稿</summary><pre>{{ JSON.stringify(item.providerOutput, null, 2) }}</pre></details>
            <details v-if="item.acceptedOutput"><summary>人工接受稿</summary><pre>{{ JSON.stringify(item.acceptedOutput, null, 2) }}</pre></details>
          </article>
        </template>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<style scoped>
.creative-workflow { display: grid; gap: 10px; padding: 12px; margin: 12px 0; border: 1px solid #2c3b52; border-radius: 10px; background: #0d121b; }
header, .stage-head, .actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
header div, .option { display: grid; gap: 3px; }.stage { display: grid; gap: 8px; padding: 10px; border: 1px solid #293344; border-radius: 8px; }.raw-stage p { white-space: pre-wrap; }.issue, .history { display: grid; gap: 6px; padding: 8px; border-left: 3px solid #385a83; }.option small, header small, .stage > small { color: #8d9ab0; }pre { white-space: pre-wrap; max-height: 320px; overflow: auto; }
.version-list { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 3px; }
.version-button { min-width: 150px; display: grid; gap: 2px; text-align: left; padding: 8px 10px; color: #cbd5e1; background: #111827; border: 1px solid #344155; border-radius: 7px; cursor: pointer; }
.version-button.selected { color: #eaf3ff; border-color: #409eff; box-shadow: 0 0 0 1px #409eff44; }
.version-button span { color: #7fb2eb; }.version-button small { color: #7e8a9d; }
.look-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }
.suggestion-summary, .suggestion-shot-head { display: flex; gap: 10px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
.suggestion-shot { display: grid; gap: 7px; padding: 12px; border: 1px solid #2f3948; border-radius: 8px; background: #101722; }
</style>
