<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, ref, watch } from "vue";

import { api } from "../api/client";
import type {
  CreativeWorkflowDto,
  JobDto,
  SceneDto,
  StoryDiagnosisOutput,
  StoryRewriteOutput,
  StoryRewriteStrategy,
} from "../api/types";

const props = defineProps<{ scene: SceneDto }>();
const emit = defineEmits<{ changed: []; storyboard: [] }>();

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

const acceptedDiagnosis = computed(() => workflow.value?.stages.diagnosis.find(
  (item) => Boolean(item.acceptedAt),
) ?? null);
const activeDiagnosis = computed(() => workflow.value?.stages.diagnosis.find(
  (item) => Boolean(item.acceptedAt) && item.sourceHash === workflow.value?.currentStoryHash,
) ?? null);
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

async function load() {
  workflow.value = await api.creativeWorkflow(props.scene.id);
  const health = await api.health();
  planningModel.value = health.arkPlanningModel ?? "未配置";
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
    diagnosisDraft.value = structuredClone(result.diagnosis);
    selectedStrategy.value = "balanced";
    preserveOriginal.value = false;
    await load();
  });
}

function editDiagnosisHistory() {
  const latest = workflow.value?.stages.diagnosis[0];
  if (!latest?.providerOutput) return;
  diagnosisStepId.value = latest.stepId;
  diagnosisDraft.value = structuredClone(latest.providerOutput as unknown as StoryDiagnosisOutput);
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
    diagnosisDraft.value = null;
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
      <div class="actions"><el-button type="primary" plain @click="diagnose">{{ workflow?.stages.diagnosis.length ? '重新诊断' : '运行剧情医生' }}</el-button><el-button v-if="workflow?.stages.diagnosis[0] && !acceptedDiagnosis" @click="editDiagnosisHistory">编辑最近结果</el-button></div>
      <template v-if="diagnosisDraft">
        <el-input v-model="diagnosisDraft.overallAssessment" type="textarea" :rows="3" />
        <article v-for="(issue, index) in diagnosisDraft.issues" :key="index" class="issue">
          <b>{{ issue.category }}</b>
          <el-input v-model="issue.evidence" type="textarea" :rows="2" placeholder="原文证据" />
          <el-input v-model="issue.impact" type="textarea" :rows="2" placeholder="可能影响" />
          <el-input v-model="issue.suggestion" type="textarea" :rows="2" placeholder="修改建议" />
        </article>
        <el-radio-group v-model="selectedStrategy" :disabled="preserveOriginal">
          <el-radio-button v-for="option in diagnosisDraft.rewriteOptions" :key="option.strategy" :value="option.strategy">{{ option.title }}</el-radio-button>
        </el-radio-group>
        <div v-for="option in diagnosisDraft.rewriteOptions" :key="`detail-${option.strategy}`" class="option"><b>{{ option.title }}</b><span>{{ option.summary }}</span><small>{{ option.tradeoffs }}</small></div>
        <el-input v-model="diagnosisInstructions" type="textarea" :rows="2" placeholder="补充口述或人工修改要求" />
        <el-checkbox v-model="preserveOriginal">保留原稿并允许直接进入分镜设计</el-checkbox>
        <el-button type="success" @click="acceptDiagnosis">接受人工编辑后的诊断</el-button>
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
      <div class="stage-head"><b>4. 分镜导演</b><el-tag :type="workflow?.stages.storyboard.some(item => item.acceptedAt) ? 'success' : 'info'">{{ workflow?.stages.storyboard.some(item => item.acceptedAt) ? '已建立片段' : '待执行' }}</el-tag></div>
      <p>只读取当前批准剧情，严格按场景目标数量生成可编辑视频片段。</p>
      <el-button type="primary" :disabled="!storyReady" @click="emit('storyboard')">运行分镜导演</el-button>
      <el-alert v-if="!storyReady" type="info" title="先接受剧情重写，或在剧情诊断中明确选择保留原稿。" :closable="false" />
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
</style>
