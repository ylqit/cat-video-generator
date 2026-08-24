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
  if (props.node.type !== "StoryCandidateNode") return "";
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
  } else if (props.node.type === "StoryCandidateNode" && data.status === "approved") {
    const rules = data.episodeRules as Record<string, unknown> | undefined;
    add("剧情产物", data.artifactLabel ?? "剧情脚本定稿");
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
    <div class="content-summary">
      <p>{{ summary }}</p>
      <p v-if="detailText" class="detail-text">{{ detailText }}</p>
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
      <h3>执行过程</h3>
      <article v-for="item in executions.slice(0, 5)" :key="item.key">
        <span :class="`status-${item.status}`" /><b>{{ item.label }}</b><small>{{ executionStatus(item.status) }}</small>
        <p v-if="item.progress?.message">{{ item.progress.message }}</p>
        <p v-else-if="item.error">{{ String(item.error.message ?? '任务失败') }}</p>
      </article>
    </div>
    <div v-if="node.blocker" class="blocker" role="status">当前阻塞：{{ node.blocker }}</div>
    <div v-if="disabledReason" class="disabled-reason" role="status">{{ disabledReason }}</div>
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
.context-panel { box-sizing: border-box; width: min(520px, calc(100vw - 32px)); padding: 16px; color: #e9eef5; background: #202329; border: 1px solid #3b424d; border-radius: 14px; box-shadow: 0 22px 70px rgb(0 0 0 / 48%); }
.context-panel.embedded { width: 100%; height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 12px; padding: 20px; overflow: auto; background: #252525; border: 0; border-radius: 0; box-shadow: none; }
header { display: flex; align-items: start; justify-content: space-between; gap: 16px; padding-right: 48px; } header > div:first-child { min-width: 0; } h2 { margin: 3px 0 0; overflow: hidden; color: #f6f6f6; font-size: 20px; text-overflow: ellipsis; white-space: nowrap; } small { color: #9aa3af; font-size: 11px; letter-spacing: .06em; } .node-meta { display: flex; justify-content: flex-end; gap: 6px; flex-wrap: wrap; } .node-meta span { padding: 5px 9px; color: #aab4c3; background: #343434; border-radius: 999px; font-size: 10px; } .node-meta .status-chip { color: #c8dcf7; }
.content-summary { min-height: 0; padding: 14px 16px; overflow: auto; background: #1d2025; border: 1px solid #3a3f47; border-radius: 12px; } .content-summary > small { display: block; margin-top: 10px; color: #8fb6dc; } p { margin: 0; color: #d5d9df; line-height: 1.62; } .detail-text { margin-top: 10px; color: #aeb6c1; } strong { display: block; margin-top: 10px; color: #f0cb73; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 12px 0 0; } .detail-grid div { min-width: 0; padding: 8px 10px; background: #252a30; border-radius: 8px; } dt { color: #8792a2; font-size: 10px; } dd { margin: 3px 0 0; overflow: hidden; color: #d7dde6; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.workflow-list { display: flex; gap: 6px; overflow-x: auto; } .workflow-list span { flex: none; padding: 6px 9px; color: #aeb8c6; background: #30343a; border-radius: 999px; font-size: 10px; } .workflow-list .step-succeeded { color: #8ee4b0; background: #183526; } .workflow-list .step-running,.workflow-list .step-queued { color: #a9ccff; background: #1b304b; } .workflow-list .step-failed { color: #f1a08f; background: #49241f; }
footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: auto; overflow-x: auto; } footer button { flex: none; min-height: 44px; padding: 8px 13px; color: #d8e0ea; background: #30343a; border: 1px solid #4a515d; border-radius: 9px; cursor: pointer; } footer button:hover,footer button:focus-visible { border-color: #7eafff; outline: 2px solid #7eafff; outline-offset: 1px; } .primary { color: #10151b; background: #e5edf7; border-color: #e5edf7; font-weight: 700; }
.execution-list { display: grid; gap: 7px; padding-top: 10px; border-top: 1px solid #383d45; } .execution-list h3 { margin: 0 0 3px; font-size: 12px; } .execution-list article { display: grid; grid-template-columns: 9px 1fr auto; align-items: center; gap: 8px; } .execution-list article > span { width: 8px; height: 8px; border-radius: 50%; background: #717b89; } .execution-list article > span.status-running,.execution-list article > span.status-queued { background: #69a5f6; } .execution-list article > span.status-succeeded,.execution-list article > span.status-awaiting_review { background: #60d08d; } .execution-list article > span.status-failed,.execution-list article > span.status-submission_unknown { background: #e97868; } .execution-list article p { grid-column: 2 / 4; color: #b9c4d2; font-size: 11px; } .execution-list small { color: #8994a3; }
.blocker,.disabled-reason { padding: 9px 11px; color: #e6bd7c; background: #382d1c; border-radius: 8px; font-size: 12px; } .disabled-reason { color: #b9c4d2; background: #30343a; } footer button[aria-disabled="true"] { opacity: .48; cursor: not-allowed; }
@media (max-width: 720px) { .detail-grid { grid-template-columns: 1fr; } header { padding-right: 42px; } }
</style>
