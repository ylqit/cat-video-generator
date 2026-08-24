<script setup lang="ts">
import { computed, ref } from "vue";

import type { CanvasNodeActionDto, CanvasNodeDto, CanvasNodeType } from "../../api/types";
import type { TaskCenterItem, TaskCenterStatus } from "../../tasks/taskCenter";

const props = withDefaults(defineProps<{
  node: CanvasNodeDto;
  embedded?: boolean;
  executions?: TaskCenterItem[];
}>(), { embedded: false, executions: () => [] });
const emit = defineEmits<{
  action: [action: CanvasNodeActionDto];
}>();

const NODE_LABELS: Record<CanvasNodeType, string> = {
  RecipeGroupNode: "创作组合包",
  BriefNode: "创意输入",
  SubjectNode: "固定主体",
  StylePresetNode: "画风预设",
  StoryPlannerNode: "剧情策划",
  StoryEventNode: "事件方案",
  StoryScriptNode: "剧情脚本",
  StoryCandidateNode: "原创故事候选",
  StoryCriticNode: "剧情评审",
  ApprovalGateNode: "人工审核",
  StoryboardDirectorNode: "分镜导演",
  SceneNode: "场景规划",
  ShotBeatNode: "分镜镜头",
  CharacterDesignNode: "角色设计",
  ImageGenerationNode: "图片生成",
  VideoGenerationNode: "视频生成",
  ReviewNode: "资产审核",
  TimelineNode: "最终音画",
  ReferenceAssetNode: "参考素材",
  GenerationBatchNode: "生成批次",
  ImageAssetNode: "图片成品",
  VideoAssetNode: "视频成品",
  VideoEditNode: "视频编辑",
  VideoSegmentNode: "重拍片段",
  PromptArtifactNode: "提示词审计",
  AudioGenerationNode: "音频生成",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  ready: "已就绪",
  candidate: "待选择",
  approved: "已批准",
  queued: "排队中",
  submitting: "提交中",
  running: "执行中",
  generating: "生成中",
  submitted: "供应商处理中",
  awaiting_review: "等待人工审核",
  succeeded: "已完成",
  complete: "已完成",
  completed: "已完成",
  failed: "失败",
  stale: "上游已变更",
  submission_unknown: "提交状态待对账",
  restart_pending: "等待恢复",
  cancelled: "已取消",
};

const title = computed(() => String(props.node.data.title ?? NODE_LABELS[props.node.type]));
const category = computed(() => String(props.node.data.artifactLabel ?? NODE_LABELS[props.node.type]));
const status = computed(() => {
  const raw = String(props.node.status ?? props.node.data.status ?? "pending");
  return STATUS_LABELS[raw] ?? raw;
});
const summary = computed(() => {
  const data = props.node.data;
  if (props.node.type === "BriefNode") {
    return String(data.theme ?? "填写一句话主题，补全本集的情绪、场景与核心事件。");
  }
  if (props.node.type === "SubjectNode") {
    const anchors = Array.isArray(data.identityAnchors) ? data.identityAnchors.join("；") : "待补充身份锚点";
    return `${String(data.kind ?? "主体")} · ${anchors}`;
  }
  if (props.node.type === "CharacterDesignNode") {
    return String(data.description ?? data.slotDescription ?? "为本集生成造型、姿态和同框比例参考，不替代 Canon 身份。");
  }
  if (props.node.type === "StoryEventNode") {
    return String(data.premise ?? data.logline ?? "等待生成可拍摄事件方案");
  }
  if (props.node.type === "StoryScriptNode") {
    return String(data.logline ?? data.synopsis ?? "等待把已选事件扩写成完整剧情脚本");
  }
  if (props.node.type === "StoryCandidateNode") return String(data.logline ?? "等待故事候选");
  if (props.node.type === "StoryboardDirectorNode") {
    return String(data.description ?? "选择从故事、固定角色或手工方式建立镜头表。");
  }
  if (props.node.type === "ShotBeatNode") return String(data.action ?? "待填写镜头动作");
  if (props.node.type === "ReferenceAssetNode") {
    const count = Array.isArray(data.assets) ? data.assets.length : Number(Boolean(data.assetId));
    return count ? `已绑定 ${count} 个素材 · ${String(data.semanticRole ?? "通用参考")}` : "尚未绑定素材；可上传或从历史素材中选择。";
  }
  if (props.node.type === "VideoEditNode" || props.node.type === "VideoSegmentNode") {
    return String(data.instruction ?? "设置需要修改的区间、动作、画面和参考素材。");
  }
  if (props.node.type === "ApprovalGateNode") {
    return String(data.description ?? data.blocker ?? "检查当前阶段内容并作出人工决定。");
  }
  return String(data.description ?? data.synopsis ?? "打开节点查看当前内容和可执行操作。");
});

const score = computed(() => {
  const scorecard = props.node.data.scorecard;
  if (!scorecard || typeof scorecard !== "object" || Array.isArray(scorecard)) return null;
  const average = (scorecard as Record<string, unknown>).average;
  return typeof average === "number" ? average : null;
});
const detailText = computed(() => {
  if (!["StoryCandidateNode", "StoryScriptNode"].includes(props.node.type)) return "";
  const synopsis = String(props.node.data.synopsis ?? "");
  return synopsis === summary.value ? "" : synopsis;
});

interface DetailItem { label: string; value: string }
const details = computed<DetailItem[]>(() => {
  const data = props.node.data;
  const rows: DetailItem[] = [];
  const add = (label: string, value: unknown, suffix = "") => {
    if (value === undefined || value === null || value === "") return;
    rows.push({ label, value: `${String(value)}${suffix}` });
  };
  if (props.node.type === "BriefNode") {
    add("总时长", data.targetDurationSeconds, " 秒");
    add("画幅", data.aspectRatio);
    add("情绪", data.emotion ?? data.mood);
    add("场景", data.scene ?? data.primaryScene);
  } else if (props.node.type === "CharacterDesignNode") {
    add("设计槽位", data.slotLabel ?? data.slot);
    add("候选数量", Array.isArray(data.candidates) ? data.candidates.length : data.candidateCount);
    add("引用职责", data.referenceRole ?? "造型 / 姿态 / 比例 / 构图");
  } else if (props.node.type === "StoryboardDirectorNode") {
    add("镜头数量", data.shotCount);
    add("分镜状态", data.storyboardApproved ? "已人工批准" : "等待生成或审核");
    add("脚本流程", "确认镜头 → 准备资产 → 合成提示词");
  } else if (props.node.type === "ShotBeatNode") {
    add("时长", data.durationSeconds, " 秒");
    add("景别", data.shotSize ?? data.framing);
    add("运镜", data.camera);
    add("音效", data.sound ?? data.soundEffect);
  } else if (props.node.type === "ApprovalGateNode") {
    add("审核阶段", data.phaseLabel ?? data.phase);
    add("审核结论", data.decision ?? status.value);
  } else if (props.node.type === "StoryEventNode") {
    add("儿童行动", data.childAction);
    add("猫咪参与", data.catParticipation);
    add("小变化", data.smallChange);
    add("温暖收尾", data.warmEnding);
    add("时长适配", data.durationFitSummary);
    add("换场建议", data.requiresSceneChange ? data.sceneChangePurpose ?? "需要换场" : "单场景完成");
  } else if (["StoryCandidateNode", "StoryScriptNode"].includes(props.node.type)) {
    const rules = data.episodeRules as Record<string, unknown> | undefined;
    add("剧情产物", data.artifactLabel ?? (data.status === "approved" ? "剧情脚本定稿" : "剧情脚本草稿"));
    add("来源事件", data.sourceEventTitle ?? data.sourceEventCandidateId);
    add("儿童服饰", rules?.personWardrobe);
    add("时间天气", rules?.timeWeather);
    add("猫咪模式", rules?.catBehaviorMode);
    add("场景数量", Array.isArray(data.scenes) ? data.scenes.length : 0);
  } else if (props.node.type === "ImageGenerationNode" || props.node.type === "VideoGenerationNode") {
    add("模型", data.model);
    add("候选数量", data.candidateCount);
    add("预计费用", data.estimatedCostLabel);
  }
  if (props.node.revision) add("版本", props.node.revision);
  return rows;
});

const actions = computed(() => props.node.availableActions ?? []);
const primaryActionKey = computed(() => actions.value.find((action) => action.enabled)?.key);
const disabledReason = ref("");
const plannerCanonDependencies = computed(() => (
  props.node.type === "StoryPlannerNode" && Array.isArray(props.node.data.canonDependencies)
    ? props.node.data.canonDependencies.map(String)
    : []
));
const plannerCandidateRules = computed(() => (
  props.node.type === "StoryPlannerNode" && Array.isArray(props.node.data.candidateRules)
    ? props.node.data.candidateRules.map(String)
    : []
));

function handleAction(action: CanvasNodeActionDto) {
  if (!action.enabled) {
    disabledReason.value = action.disabledReason ?? "当前条件尚未满足";
    return;
  }
  disabledReason.value = "";
  emit("action", action);
}

function executionStatus(statusValue: TaskCenterStatus) {
  return STATUS_LABELS[statusValue] ?? statusValue;
}

function progressPercent(item: TaskCenterItem): number {
  return Math.max(0, Math.min(100, Number(item.progress?.percent ?? 0)));
}

function resultMessage(item: TaskCenterItem): string {
  const message = item.resultSummary?.message;
  return typeof message === "string" ? message : "";
}

function taskDuration(item: TaskCenterItem): string {
  const start = Date.parse(item.createdAt ?? item.updatedAt);
  const end = Date.parse(item.completedAt ?? item.updatedAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function childTaskSummary(item: TaskCenterItem): string {
  const children = props.executions.filter((candidate) => (
    candidate.parentStepId === item.stepId
    || Boolean(candidate.stepId && item.childStepIds?.includes(candidate.stepId))
  ));
  if (!children.length && !item.childStepIds?.length) return "";
  const total = Math.max(children.length, item.childStepIds?.length ?? 0);
  const completed = children.filter((candidate) => candidate.status === "succeeded").length;
  return `子任务 ${completed}/${total}`;
}

function executionStages(item: TaskCenterItem) {
  const running = ["submitting", "running", "awaiting_review", "succeeded", "failed", "submission_unknown"].includes(item.status);
  const terminal = ["awaiting_review", "succeeded", "failed", "submission_unknown", "cancelled"].includes(item.status);
  return [
    { key: "queued", label: "任务已入队", state: "done" },
    { key: "worker", label: "Worker 已领取", state: running ? "done" : "pending" },
    {
      key: "current",
      label: item.progress?.message || (item.providerTaskId ? "等待 Provider 返回结果" : "准备任务上下文与结构化产物"),
      state: terminal ? "done" : running ? "active" : "pending",
    },
    {
      key: "result",
      label: item.status === "failed"
        ? `失败：${String(item.error?.message ?? "查看错误原因")}`
        : item.status === "submission_unknown"
          ? "供应商提交状态待对账"
          : item.status === "awaiting_review"
            ? "等待人工审核"
            : item.status === "succeeded"
              ? resultMessage(item) || "产物已写入并同步画布"
              : "等待写入产物",
      state: terminal ? (item.status === "failed" || item.status === "submission_unknown" ? "error" : "done") : "pending",
    },
  ];
}
</script>

<template>
  <section class="context-panel" :class="{ embedded }" aria-label="节点内容">
    <header>
      <div><small>{{ category }}</small><h2>{{ title }}</h2></div>
      <div class="node-meta" aria-label="节点版本与状态">
        <span v-if="node.revision">版本 {{ node.revision }}</span>
        <span class="status-chip">{{ status }}</span>
      </div>
    </header>
    <div class="content-scroll">
      <div class="content-summary">
        <p>{{ summary }}</p>
        <p v-if="detailText" class="detail-text">{{ detailText }}</p>
        <section v-if="node.type === 'StoryPlannerNode'" class="planner-brief" aria-label="剧情策划输入与规则">
          <div><small>当前创意</small><p>{{ String(node.data.briefSummary ?? '等待已批准的创意简报') }}</p></div>
          <div><small>固定视觉依赖</small><p>{{ plannerCanonDependencies.join('、') || '等待儿童、猫咪和画风预设' }}</p></div>
          <div><small>候选规则 · {{ Number(node.data.candidateCount ?? 3) }} 案</small><ul><li v-for="rule in plannerCandidateRules" :key="rule">{{ rule }}</li></ul></div>
        </section>
        <dl v-if="details.length" class="detail-grid">
          <div v-for="item in details" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div>
        </dl>
        <strong v-if="score !== null">综合评分 {{ score }}</strong>
        <small v-if="node.outputs?.length">已产生 {{ node.outputs.length }} 个可追溯产物</small>
      </div>
      <div v-if="node.workflowSteps?.length" class="workflow-list" aria-label="工作流进度">
        <span v-for="step in node.workflowSteps" :key="step.key" :class="`step-${step.status}`">
          {{ step.label }} · {{ STATUS_LABELS[step.status] ?? step.status }}
        </span>
      </div>
      <div v-if="executions.length" class="execution-list" aria-label="节点执行过程">
        <h3>完整执行时间线 · {{ executions.length }} 次运行</h3>
        <article v-for="item in executions" :key="item.key" class="execution-card">
          <header class="execution-heading">
            <span :class="`status-${item.status}`" />
            <b>{{ item.label }}</b>
            <small>{{ executionStatus(item.status) }}</small>
          </header>
          <div class="execution-meta">
            <span v-if="item.progress?.totalSteps">步骤 {{ item.progress.currentStep ?? 0 }}/{{ item.progress.totalSteps }}</span>
            <span v-if="item.progress?.percent !== undefined">{{ progressPercent(item) }}%</span>
            <span v-if="childTaskSummary(item)">{{ childTaskSummary(item) }}</span>
            <span v-if="item.progress?.providerStatus">Provider · {{ item.progress.providerStatus }}</span>
            <span v-if="taskDuration(item)">耗时 {{ taskDuration(item) }}</span>
          </div>
          <div v-if="item.progress?.percent !== undefined" class="execution-progress" role="progressbar" :aria-valuenow="progressPercent(item)" aria-valuemin="0" aria-valuemax="100">
            <i :style="{ width: `${progressPercent(item)}%` }" />
          </div>
          <ol class="execution-timeline">
            <li v-for="stage in executionStages(item)" :key="stage.key" :class="`timeline-${stage.state}`">
              <span />{{ stage.label }}
            </li>
          </ol>
          <p v-if="resultMessage(item)" class="result-summary">结果：{{ resultMessage(item) }}</p>
          <p v-if="item.error" class="execution-error">{{ String(item.error.failedStep ?? '失败步骤') }} · {{ String(item.error.message ?? '任务失败') }}</p>
        </article>
      </div>
      <div v-if="node.blocker" class="blocker" role="status">当前阻塞：{{ node.blocker }}</div>
      <div v-if="disabledReason" class="disabled-reason" role="status">{{ disabledReason }}</div>
    </div>
    <footer>
      <button
        v-for="action in actions"
        :key="action.key"
        :data-action="action.key"
        :class="{ primary: action.key === primaryActionKey }"
        type="button"
        :aria-disabled="!action.enabled"
        :title="action.enabled ? action.label : action.disabledReason"
        @focus="!action.enabled && (disabledReason = action.disabledReason ?? '当前条件尚未满足')"
        @click="handleAction(action)"
      >{{ action.label }}</button>
    </footer>
  </section>
</template>

<style scoped>
.context-panel { box-sizing: border-box; width: min(520px, calc(100vw - 32px)); max-height: min(620px, calc(100vh - 32px)); display: grid; grid-template-rows: auto minmax(0, 1fr) auto; gap: 12px; padding: 16px; color: #e9eef5; background: #202329; border: 1px solid #3b424d; border-radius: 14px; box-shadow: 0 22px 70px rgb(0 0 0 / 48%); }
.context-panel.embedded { width: 100%; height: 100%; max-height: none; min-height: 0; padding: 20px; overflow: hidden; background: #252525; border: 0; border-radius: 0; box-shadow: none; }
header { display: flex; align-items: start; justify-content: space-between; gap: 16px; padding-right: 48px; } header > div:first-child { min-width: 0; } h2 { margin: 3px 0 0; overflow: hidden; color: #f6f6f6; font-size: 20px; text-overflow: ellipsis; white-space: nowrap; } small { color: #9aa3af; font-size: 11px; letter-spacing: .06em; } .node-meta { display: flex; justify-content: flex-end; gap: 6px; flex-wrap: wrap; } .node-meta span { padding: 5px 9px; color: #aab4c3; background: #343434; border-radius: 999px; font-size: 10px; } .node-meta .status-chip { color: #c8dcf7; }
.content-scroll { min-height: 0; display: grid; align-content: start; gap: 12px; overflow-y: auto; overflow-x: hidden; scrollbar-gutter: stable; scrollbar-color: #707780 #252525; }
.content-summary { min-height: 0; padding: 14px 16px; background: #1d2025; border: 1px solid #3a3f47; border-radius: 12px; } .content-summary > small { display: block; margin-top: 10px; color: #8fb6dc; } p { margin: 0; color: #d5d9df; line-height: 1.62; } .detail-text { margin-top: 10px; color: #aeb6c1; } strong { display: block; margin-top: 10px; color: #f0cb73; }
.planner-brief { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }.planner-brief > div { min-width: 0; padding: 10px; background: #252a30; border-radius: 8px; }.planner-brief p,.planner-brief li { color: #cbd3dd; font-size: 11px; line-height: 1.48; }.planner-brief ul { margin: 6px 0 0; padding-left: 16px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 12px 0 0; } .detail-grid div { min-width: 0; padding: 8px 10px; background: #252a30; border-radius: 8px; } dt { color: #8792a2; font-size: 10px; } dd { margin: 3px 0 0; overflow: hidden; color: #d7dde6; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.workflow-list { display: flex; gap: 6px; overflow-x: auto; } .workflow-list span { flex: none; padding: 6px 9px; color: #aeb8c6; background: #30343a; border-radius: 999px; font-size: 10px; } .workflow-list .step-succeeded { color: #8ee4b0; background: #183526; } .workflow-list .step-running,.workflow-list .step-queued { color: #a9ccff; background: #1b304b; } .workflow-list .step-failed { color: #f1a08f; background: #49241f; }
footer { min-height: 44px; display: flex; justify-content: flex-end; gap: 8px; overflow-x: auto; overflow-y: hidden; scrollbar-gutter: stable; } footer button { flex: none; min-height: 44px; padding: 8px 13px; color: #d8e0ea; background: #30343a; border: 1px solid #4a515d; border-radius: 9px; cursor: pointer; } footer button:hover,footer button:focus-visible { border-color: #7eafff; outline: 2px solid #7eafff; outline-offset: 1px; } .primary { color: #10151b; background: #e5edf7; border-color: #e5edf7; font-weight: 700; }
.execution-list { display: grid; gap: 10px; padding-top: 10px; border-top: 1px solid #383d45; } .execution-list h3 { margin: 0 0 3px; font-size: 12px; }
.execution-card { display: grid; gap: 8px; padding: 11px 12px; background: #20242a; border: 1px solid #353b44; border-radius: 10px; }
.execution-heading { display: grid; grid-template-columns: 9px minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 0; }
.execution-heading > span { width: 8px; height: 8px; border-radius: 50%; background: #717b89; }
.execution-heading > span.status-running,.execution-heading > span.status-queued,.execution-heading > span.status-submitting { background: #69a5f6; }
.execution-heading > span.status-succeeded,.execution-heading > span.status-awaiting_review { background: #60d08d; }
.execution-heading > span.status-failed,.execution-heading > span.status-submission_unknown { background: #e97868; }
.execution-heading b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.execution-heading small { color: #8994a3; }
.execution-meta { display: flex; flex-wrap: wrap; gap: 5px; }.execution-meta span { padding: 3px 6px; color: #9eabb9; background: #2a3038; border-radius: 999px; font-size: 9px; }
.execution-progress { height: 4px; overflow: hidden; background: #313944; border-radius: 999px; }.execution-progress i { display: block; height: 100%; background: #6ca8f7; border-radius: inherit; transition: width .16s ease; }
.execution-timeline { display: grid; gap: 5px; margin: 0; padding: 0; list-style: none; }.execution-timeline li { display: grid; grid-template-columns: 9px minmax(0, 1fr); align-items: start; gap: 7px; color: #7f8996; font-size: 10px; line-height: 1.45; }.execution-timeline li > span { width: 7px; height: 7px; margin-top: 4px; border: 1px solid #596472; border-radius: 50%; }.execution-timeline .timeline-done { color: #b7c3d0; }.execution-timeline .timeline-done > span { background: #5d89bd; border-color: #79a9df; }.execution-timeline .timeline-active { color: #bad7ff; }.execution-timeline .timeline-active > span { background: #79aefa; border-color: #a4cbff; box-shadow: 0 0 0 3px rgb(88 153 235 / 16%); }.execution-timeline .timeline-error { color: #ef9f92; }.execution-timeline .timeline-error > span { background: #e97868; border-color: #f29b8e; }
.execution-card .result-summary,.execution-card .execution-error { color: #9fd2ae; font-size: 10px; }.execution-card .execution-error { color: #ef9f92; }
.blocker,.disabled-reason { padding: 9px 11px; color: #e6bd7c; background: #382d1c; border-radius: 8px; font-size: 12px; } .disabled-reason { color: #b9c4d2; background: #30343a; } footer button[aria-disabled="true"] { opacity: .48; cursor: not-allowed; }
@media (max-width: 720px) { .detail-grid,.planner-brief { grid-template-columns: 1fr; } header { padding-right: 42px; } }
</style>
