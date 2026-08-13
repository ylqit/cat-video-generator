<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, assetContentUrl } from "../api/client";
import type { SuggestionJobResult } from "../api/client";
import type {
  AnchorMode,
  AssetDto,
  JobDto,
  ProjectGraph,
  ProjectSummary,
  ReferenceBinding,
  ReferenceRole,
  ReferenceTarget,
  ReferenceUsage,
  SceneDto,
  SceneLookPlan,
  SceneLookUsage,
  SequenceDto,
  ShotAssistContext,
  ShotAssistPatch,
  ShotAssistRecord,
  ShotDto,
  ShotPromptPreview,
  StoryMode,
} from "../api/types";
import VideoTimeline from "../components/VideoTimeline.vue";
import CreativeWorkflowPanel from "../components/CreativeWorkflowPanel.vue";
import SceneLookWorkbench from "../components/SceneLookWorkbench.vue";
import ShotAssistancePanel from "../components/ShotAssistancePanel.vue";

const route = useRoute();
const router = useRouter();
const projects = ref<ProjectSummary[]>([]);
const graph = ref<ProjectGraph | null>(null);
const selectedShotId = ref<string | null>(null);
const selectedSequenceId = ref<string | null>(null);
const busy = ref(false);
const persistentError = ref("");
const createVisible = ref(false);
const projectSettingsVisible = ref(false);
const sceneVisible = ref(false);
const shotVisible = ref(false);
const assistConfirmVisible = ref(false);
const suggestion = ref<SuggestionJobResult | null>(null);
const promptPreview = ref<ShotPromptPreview | null>(null);
const assistContext = ref<ShotAssistContext | null>(null);
const assistAnalyses = ref<ShotAssistRecord[]>([]);
const assistCandidateIds = ref<string[]>([]);
const shotOriginalDuration = ref(8);
const createReferenceFile = ref<File | null>(null);
let polling: number | undefined;

function emptyLookPlan(): SceneLookPlan {
  return {
    personWardrobe: "",
    personAccessories: "",
    catAppearance: "",
    keyProps: "",
    environmentStyle: "outdoor",
    personPose: "",
    catPose: "",
    composition: "",
    additionalInstructions: "",
    imageRecommended: false,
    recommendationReason: null,
  };
}

const createForm = reactive({ title: "", sceneTitle: "第一场景", sourceText: "" });
const projectSettingsForm = reactive({ title: "", contentDate: "" });
const sceneForm = reactive({
  id: "",
  title: "",
  sourceText: "",
  chapterLabel: "",
  contextNote: "",
  storyMode: "single" as StoryMode,
  targetShotCount: 1,
  lookPlan: emptyLookPlan(),
});
const shotForm = reactive({
  id: "",
  sceneId: "",
  title: "",
  direction: "",
  durationSeconds: 8,
  anchorMode: "text_only" as AnchorMode,
  referenceBindings: [] as ReferenceBinding[],
  inheritProjectReferences: true,
  sceneLookUsage: "appearance_only" as SceneLookUsage,
});
const referenceForm = reactive({
  assetId: "",
  usage: "generation_reference" as ReferenceUsage,
  role: "identity" as ReferenceRole,
  applyTo: "both" as ReferenceTarget,
});
const uploadForm = reactive({
  usage: "generation_reference" as ReferenceUsage,
  role: "prop" as ReferenceRole,
  file: null as File | null,
});

const selectedShot = computed<ShotDto | null>(() => {
  if (!graph.value || !selectedShotId.value) return null;
  for (const scene of graph.value.scenes) {
    const shot = scene.shots.find((item) => item.id === selectedShotId.value);
    if (shot) return shot;
  }
  return null;
});
const selectedVideo = computed(() => {
  const shot = selectedShot.value;
  if (!shot) return null;
  return shot.assets.find((item) => item.id === shot.selectedVideoAssetId)
    ?? [...shot.assets].reverse().find((item) => item.mediaType === "video")
    ?? null;
});
const selectableAssets = computed(() => graph.value?.assets.filter(
  (item) => item.mediaType === "image" && item.contentReady && item.role !== "scene_look",
) ?? []);
const selectedScene = computed<SceneDto | null>(() => {
  if (!graph.value || !selectedShot.value) return null;
  return graph.value.scenes.find((item) => item.id === selectedShot.value?.sceneId) ?? null;
});
const suggestionDuration = computed(() => suggestion.value?.output.shots.reduce(
  (total, item) => total + item.suggestedDurationSeconds,
  0,
) ?? 0);
const selectedVideoDurationMs = computed(() => {
  if (!selectedVideo.value || !selectedShot.value) return 0;
  const qc = selectedVideo.value.metadata.qc as Record<string, unknown> | undefined;
  return Number(qc?.durationMs ?? selectedShot.value.durationSeconds * 1000);
});
const selectedVideoFrames = computed(() => {
  if (!selectedShot.value || !selectedVideo.value) return [];
  return selectedShot.value.assets
    .filter(
      (item) => item.role === "review_frame"
        && item.metadata.sourceVideoAssetId === selectedVideo.value?.id,
    )
    .sort((left, right) => Number(left.metadata.ordinal) - Number(right.metadata.ordinal))
    .map((item) => ({
      src: assetContentUrl(item.id),
      label: `${String(item.metadata.ordinal)}/${String(item.metadata.frameCount)}`,
      timestampMs: Math.round(
        ((Number(item.metadata.ordinal) - 1)
          / Math.max(1, Number(item.metadata.frameCount) - 1))
          * selectedVideoDurationMs.value,
      ),
    }));
});
const selectedVideoMarkersMs = computed(() => {
  if (!selectedShot.value || !selectedVideo.value?.producingStepId) return [];
  const attempt = selectedShot.value.attempts.find(
    (item) => item.id === selectedVideo.value?.producingStepId,
  );
  const review = attempt?.reviews.find(
    (item) => Array.isArray(item.evidence.shotBoundariesSeconds),
  );
  if (!review) return [];
  const boundaries = Array.isArray(review.evidence.shotBoundariesSeconds)
    ? review.evidence.shotBoundariesSeconds.map((item) => Number(item) * 1000)
    : [];
  const findings = Array.isArray(review.evidence.evidence)
    ? review.evidence.evidence.flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const raw = String((item as Record<string, unknown>).timestamp ?? "").trim();
        const clock = raw.match(/^(?:(\d+):)?(\d+(?:\.\d+)?)s?$/);
        if (!clock) return [];
        return [((Number(clock[1] ?? 0) * 60) + Number(clock[2])) * 1000];
      })
    : [];
  return [...new Set([...boundaries, ...findings]
    .map((item) => Math.round(item))
    .filter((item) => Number.isFinite(item) && item >= 0 && item <= selectedVideoDurationMs.value))]
    .sort((left, right) => left - right);
});
const selectedSequence = computed<SequenceDto | null>(() => {
  if (!graph.value || !selectedSequenceId.value) return null;
  return graph.value.sequences.find((item) => item.id === selectedSequenceId.value) ?? null;
});
const selectedSequenceAsset = computed(() => {
  if (!graph.value || !selectedSequence.value?.renderedAssetId) return null;
  return graph.value.assets.find((item) => item.id === selectedSequence.value?.renderedAssetId) ?? null;
});
const shotFormSubshotCount = computed(() => Math.max(
  1,
  (shotForm.direction.match(/^\s*\d+\s*[.、．]/gm) ?? []).length,
));
const shotFormFindings = computed(() => {
  const findings: Array<{ type: "success" | "info" | "warning"; title: string }> = [];
  if (shotFormSubshotCount.value < 2 || shotFormSubshotCount.value > 4) findings.push({
    type: "warning",
    title: `当前正文检测到 ${shotFormSubshotCount.value} 个编号子镜头；V5 接受 2–4 个。是否拆分或重写由 LLM 审稿判断。`,
  });
  if (shotForm.durationSeconds !== shotOriginalDuration.value) findings.push({
    type: "info",
    title: "时长已变化。保存本身不会用代码改写剧情；保存后可显式确认一次 LLM 审稿，让模型结合当前与相邻片段提出新正文。",
  });
  return findings;
});

function sceneLookUsageLabel(value: SceneLookUsage): string {
  return {
    off: "关闭基础定妆",
    appearance_only: "只继承造型（默认）",
    full_reference: "完整参考姿态与构图",
    derive_anchor: "由基础定妆派生开场锚点",
  }[value];
}

function localDateText(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

async function loadProjects() {
  projects.value = await api.projects();
}

async function loadGraph(projectId?: string) {
  const id = projectId ?? String(route.query.project ?? "");
  if (!id) {
    graph.value = null;
    return;
  }
  graph.value = await api.project(id);
  const requestedSequence = String(route.query.sequence ?? "");
  if (graph.value.sequences.some((item) => item.id === requestedSequence)) {
    selectedSequenceId.value = requestedSequence;
    selectedShotId.value = null;
    await loadShotAssistance(null);
    return;
  }
  selectedSequenceId.value = null;
  const requestedShot = String(route.query.shot ?? "");
  const allShots = graph.value.scenes.flatMap((scene) => scene.shots);
  selectedShotId.value = allShots.some((item) => item.id === requestedShot)
    ? requestedShot
    : selectedShotId.value && allShots.some((item) => item.id === selectedShotId.value)
      ? selectedShotId.value
      : allShots[0]?.id ?? null;
  await loadShotAssistance(selectedShotId.value);
}

async function reloadGenerationInputs() {
  promptPreview.value = null;
  await loadGraph();
}

async function selectProject(id: string) {
  await router.replace({ path: "/studio", query: { project: id } });
  await loadGraph(id);
}

async function selectShot(id: string) {
  selectedShotId.value = id;
  selectedSequenceId.value = null;
  await router.replace({
    path: "/studio",
    query: { project: graph.value?.project.id, shot: id },
  });
  promptPreview.value = null;
  await loadShotAssistance(id);
}

async function loadShotAssistance(shotId?: string | null) {
  if (!shotId) {
    assistContext.value = null;
    assistAnalyses.value = [];
    return;
  }
  const [context, analyses] = await Promise.all([
    api.shotAssistContext(shotId),
    api.shotAssistAnalyses(shotId),
  ]);
  assistContext.value = context;
  assistAnalyses.value = analyses;
}

async function showSequence(id: string) {
  selectedSequenceId.value = id;
  selectedShotId.value = null;
  await loadShotAssistance(null);
  await router.replace({
    path: "/studio",
    query: { project: graph.value?.project.id, sequence: id },
  });
}

async function createProject() {
  if (!createForm.title.trim() || !createForm.sourceText.trim()) {
    ElMessage.warning("请填写项目标题和第一场景原始剧本");
    return;
  }
  await act(async () => {
    const result = await api.createProject({
      project: {
        title: createForm.title,
        firstSceneTitle: createForm.sceneTitle,
        firstSceneText: createForm.sourceText,
      },
      contentDate: localDateText(),
    });
    if (createReferenceFile.value) {
      await api.uploadReference(
        result.projectId,
        "generation_reference",
        "composition",
        createReferenceFile.value,
      );
    }
    createVisible.value = false;
    Object.assign(createForm, { title: "", sceneTitle: "第一场景", sourceText: "" });
    createReferenceFile.value = null;
    await loadProjects();
    await selectProject(result.projectId);
  });
}

function editProjectSettings() {
  if (!graph.value) return;
  Object.assign(projectSettingsForm, {
    title: graph.value.project.title,
    contentDate: graph.value.project.contentDate,
  });
  projectSettingsVisible.value = true;
}

async function saveProjectSettings() {
  if (!graph.value || !projectSettingsForm.title.trim() || !projectSettingsForm.contentDate) {
    ElMessage.warning("请填写项目标题和日期");
    return;
  }
  await act(async () => {
    await api.updateProject(graph.value!.project.id, {
      title: projectSettingsForm.title,
      contentDate: projectSettingsForm.contentDate,
    });
    projectSettingsVisible.value = false;
    await loadProjects();
    await loadGraph();
  });
}

function editScene(scene?: SceneDto) {
  Object.assign(sceneForm, scene
    ? {
        id: scene.id,
        title: scene.title,
        sourceText: scene.sourceText,
        chapterLabel: scene.chapterLabel ?? "",
        contextNote: scene.contextNote ?? "",
        storyMode: scene.storyMode,
        targetShotCount: scene.targetShotCount,
        lookPlan: scene.lookPlan ? { ...scene.lookPlan } : emptyLookPlan(),
      }
    : {
        id: "",
        title: "新场景",
        sourceText: "",
        chapterLabel: "",
        contextNote: "",
        storyMode: "single",
        targetShotCount: 1,
        lookPlan: emptyLookPlan(),
      });
  sceneVisible.value = true;
}

async function saveScene() {
  const payload = {
    title: sceneForm.title,
    sourceText: sceneForm.sourceText,
    chapterLabel: sceneForm.chapterLabel || null,
    contextNote: sceneForm.contextNote || null,
    storyMode: sceneForm.storyMode,
    targetShotCount: sceneForm.storyMode === "single" ? 1 : sceneForm.targetShotCount,
    lookPlan: sceneForm.lookPlan,
  };
  await act(async () => {
    if (sceneForm.id) await api.updateScene(sceneForm.id, payload);
    else if (graph.value) await api.addScene(graph.value.project.id, payload);
    sceneVisible.value = false;
    await loadGraph();
  });
}

async function removeScene(scene: SceneDto) {
  await ElMessageBox.confirm(`删除场景“${scene.title}”？已有 Provider 历史的场景不会被允许删除。`, "确认");
  await act(async () => {
    await api.deleteScene(scene.id);
    await loadGraph();
  });
}

function editShot(sceneId: string, shot?: ShotDto) {
  Object.assign(shotForm, shot
    ? {
        id: shot.id,
        sceneId,
        title: shot.title,
        direction: shot.direction,
        durationSeconds: shot.durationSeconds,
        anchorMode: shot.anchorMode,
        referenceBindings: shot.referenceBindings.map((item) => ({ ...item })),
        inheritProjectReferences: shot.inheritProjectReferences,
        sceneLookUsage: shot.sceneLookUsage,
      }
    : {
        id: "",
        sceneId,
        title: "新视频片段",
        direction: "1. 中景固定机位，交代人物与灰白猫的清晰相对位置，猫咪先观察目标。\n2. 近景跟随猫咪的自然四足动作，人物用手配合完成必要操作。\n3. 中景固定收尾，呈现人猫互动结果并停在稳定切点。",
        durationSeconds: 8,
        anchorMode: "text_only",
        referenceBindings: [],
        inheritProjectReferences: true,
        sceneLookUsage: "appearance_only",
      });
  shotOriginalDuration.value = shot?.durationSeconds ?? 8;
  shotVisible.value = true;
}

async function saveShot() {
  const payload = {
    title: shotForm.title,
    direction: shotForm.direction,
    durationSeconds: shotForm.durationSeconds,
    anchorMode: shotForm.anchorMode,
    referenceBindings: shotForm.referenceBindings,
    inheritProjectReferences: shotForm.inheritProjectReferences,
    sceneLookUsage: shotForm.sceneLookUsage,
  };
  await act(async () => {
    const saved = shotForm.id
      ? await api.updateShot(shotForm.id, payload)
      : await api.addShot(shotForm.sceneId, payload);
    shotVisible.value = false;
    await reloadGenerationInputs();
    await selectShot(saved.id);
    assistCandidateIds.value = [...(assistContext.value?.defaultCandidateAssetIds ?? [])];
    assistConfirmVisible.value = true;
  });
}

async function restoreCanonReferences() {
  if (!graph.value) return;
  await ElMessageBox.confirm(
    "这会恢复项目视觉档案中的 Canon 人物、猫咪和画风参考，并从片段自定义集合移除误绑的场景定妆；不会删除任何历史生成记录。",
    "恢复 Canon 默认引用",
  );
  await act(async () => {
    const result = await api.restoreProjectCanonReferences(graph.value!.project.id);
    await reloadGenerationInputs();
    ElMessage.success(`已恢复 ${result.referenceCount} 张 Canon 引用，清理 ${result.cleanedShotCount} 个片段`);
  });
}

async function runShotAssistance() {
  if (!selectedShot.value || !assistContext.value) return;
  assistConfirmVisible.value = false;
  await act(async () => {
    const accepted = await api.assistShot(
      selectedShot.value!.id,
      assistContext.value!.sourceDraftRevision,
      assistCandidateIds.value,
    );
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") {
      throw new Error(String(job.error?.message ?? "LLM 创作分析失败；已保存的片段不受影响"));
    }
    await loadShotAssistance(selectedShot.value!.id);
  });
}

async function applyShotAssistance(record: ShotAssistRecord, patch: ShotAssistPatch) {
  if (!selectedShot.value) return;
  await act(async () => {
    await api.acceptShotAssistance(record.stepId, record.sourceDraftRevision, patch);
    await reloadGenerationInputs();
    ElMessage.success("已应用勾选的 LLM 建议字段");
  });
}

async function adoptPreviousTail() {
  if (!selectedShot.value) return;
  await ElMessageBox.confirm(
    "采用上一片段尾帧会替换当前唯一锚点，并清除当前视频/成片选择；历史版本仍保留。",
    "采用尾帧锚点",
  );
  await act(async () => {
    await api.adoptPreviousTailAnchor(selectedShot.value!.id);
    await reloadGenerationInputs();
    await showPrompt();
  });
}

async function removeShot(shot: ShotDto) {
  await ElMessageBox.confirm(`删除视频片段“${shot.title}”？已有生成历史的片段不会被允许删除。`, "确认");
  await act(async () => {
    await api.deleteShot(shot.id);
    await loadGraph();
  });
}

async function moveScene(index: number, delta: number) {
  if (!graph.value) return;
  const ids = graph.value.scenes.map((item) => item.id);
  const target = index + delta;
  if (target < 0 || target >= ids.length) return;
  [ids[index], ids[target]] = [ids[target], ids[index]];
  await act(async () => {
    await api.reorderScenes(graph.value!.project.id, ids);
    await loadGraph();
  });
}

async function moveShot(scene: SceneDto, index: number, delta: number) {
  const ids = scene.shots.map((item) => item.id);
  const target = index + delta;
  if (target < 0 || target >= ids.length) return;
  [ids[index], ids[target]] = [ids[target], ids[index]];
  await act(async () => {
    await api.reorderShots(scene.id, ids);
    await loadGraph();
  });
}

async function suggest(scene: SceneDto) {
  await ElMessageBox.confirm("AI 造型与视频片段建议会产生一次规划模型费用，是否继续？", "付费确认");
  await act(async () => {
    const accepted = await api.suggestShots(scene.id);
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "视频片段建议失败"));
    suggestion.value = structuredClone(job.result as SuggestionJobResult);
  });
}

async function acceptSuggestion() {
  if (!suggestion.value) return;
  await act(async () => {
    await api.acceptSuggestions(
      suggestion.value!.stepId,
      suggestion.value!.output.lookPlan,
      suggestion.value!.output.shots,
    );
    suggestion.value = null;
    await loadGraph();
  });
}

async function showPrompt() {
  if (!selectedShot.value) return;
  await act(async () => { promptPreview.value = await api.promptPreview(selectedShot.value!.id); });
}

async function generate(kind: "anchor" | "video") {
  if (!selectedShot.value) return;
  const operationKey = kind === "anchor" ? "image:anchor" : "video:shot";
  const priorAttempts = selectedShot.value.attempts.filter(
    (item) => item.operationKey === operationKey,
  );
  const regenerate = priorAttempts.length > 0;
  const action = regenerate ? "重新生成并保留旧版本" : "生成";
  let reason = kind === "anchor" ? "生成片段开场锚点" : "生成完整视频片段";
  if (regenerate) {
    const answer = await ElMessageBox.prompt(
      "请只写本次需要修正的一项问题。该说明会进入本次实际调用Prompt，旧Prompt不会被覆盖。",
      "填写重做目标",
      { inputPlaceholder: "例如：保持当前角色身份，只修正本次生成中出现的单一问题" },
    );
    reason = answer.value.trim();
    if (!reason) {
      ElMessage.warning("重新生成必须填写修正目标");
      return;
    }
  }
  await ElMessageBox.confirm(
    kind === "anchor"
      ? `${action}锚点会产生一次 Seedream 费用，是否继续？`
      : `${action}当前视频片段会产生一次 Seedance 费用，是否继续？`,
    "付费确认",
  );
  await act(async () => {
    const accepted = kind === "anchor"
      ? await api.generateAnchor(selectedShot.value!.id, regenerate, reason)
      : await api.generateVideo(selectedShot.value!.id, regenerate, reason);
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "生成失败"));
    await loadGraph();
  });
}

async function resumeAttempt(stepId: string) {
  await act(async () => {
    const accepted = await api.resumeStep(stepId);
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "继续查询失败"));
    await loadGraph();
  });
}

async function reconcileAttempt(stepId: string) {
  await act(async () => {
    const candidates = await api.reconciliationCandidates(stepId);
    if (!candidates.length) throw new Error("Ark任务列表中没有匹配候选，请稍后再次查询");
    const lines = candidates.map((item) => String(item.taskId)).join("\n");
    const answer = await ElMessageBox.prompt(
      `候选Task ID：\n${lines}\n请输入确认绑定的Task ID`,
      "对账供应商任务",
      { inputValue: candidates.length === 1 ? String(candidates[0].taskId) : "" },
    );
    await api.reconcileStep(stepId, answer.value);
    await loadGraph();
  });
}

async function review(asset: AssetDto, decision: "approved" | "rejected") {
  const reason = decision === "approved" ? "人工观看通过" : "人工观看未通过";
  await act(async () => {
    await api.reviewAsset(asset.id, decision, reason);
    await loadGraph();
  });
}

async function uploadReference() {
  if (!graph.value || !uploadForm.file) return;
  await act(async () => {
    await api.uploadReference(graph.value!.project.id, uploadForm.usage, uploadForm.role, uploadForm.file!);
    uploadForm.file = null;
    await loadGraph();
  });
}

function isProjectDefault(assetId: string): boolean {
  return graph.value?.project.defaultReferenceBindings.some((item) => item.assetId === assetId) ?? false;
}

async function toggleProjectDefault(asset: AssetDto) {
  if (!graph.value || !asset.contentReady) return;
  if (asset.role === "scene_look") {
    ElMessage.warning("场景定妆只能通过场景定妆策略使用，不能保存为项目身份参考");
    return;
  }
  const references = graph.value.project.defaultReferenceBindings.filter(
    (item) => item.assetId !== asset.id,
  );
  if (!isProjectDefault(asset.id)) {
    references.push({
      assetId: asset.id,
      usage: "generation_reference",
      role: fixedReferenceRole(asset) ?? editableReferenceRole(asset),
      applyTo: "both",
    });
  }
  await act(async () => {
    await api.updateProjectDefaultReferences(graph.value!.project.id, references);
    await reloadGenerationInputs();
  });
}

function fixedReferenceRole(asset: AssetDto | undefined): ReferenceRole | null {
  if (!asset) return null;
  if (asset.role === "scene_look") return "scene";
  if (asset.semanticKey?.startsWith("style:")) return "style";
  if (asset.semanticKey?.startsWith("person:") || asset.semanticKey?.startsWith("cat:")) return "identity";
  return null;
}

function editableReferenceRole(asset: AssetDto | undefined): ReferenceRole {
  const role = String(asset?.metadata.referenceRole ?? "prop");
  return ["style", "prop", "composition"].includes(role)
    ? role as ReferenceRole
    : "prop";
}

const selectedBindingAsset = computed(() => selectableAssets.value.find(
  (item) => item.id === referenceForm.assetId,
));
const bindingRoleLocked = computed(() => fixedReferenceRole(selectedBindingAsset.value) !== null);

async function bindReference() {
  if (!selectedShot.value || !referenceForm.assetId) return;
  const fixedRole = fixedReferenceRole(selectedBindingAsset.value);
  if (fixedRole) referenceForm.role = fixedRole;
  const bindings = selectedShot.value.referenceBindings.filter((item) => item.assetId !== referenceForm.assetId);
  bindings.push({ ...referenceForm });
  const draft = {
    title: selectedShot.value.title,
    direction: selectedShot.value.direction,
    durationSeconds: selectedShot.value.durationSeconds,
    anchorMode: referenceForm.usage === "approved_anchor"
      ? "existing"
      : selectedShot.value.anchorMode,
    referenceBindings: bindings,
    inheritProjectReferences: selectedShot.value.inheritProjectReferences,
    sceneLookUsage: referenceForm.usage === "approved_anchor"
      && selectedShot.value.sceneLookUsage === "derive_anchor"
      ? "appearance_only"
      : selectedShot.value.sceneLookUsage,
  };
  await act(async () => {
    await api.updateShot(selectedShot.value!.id, draft);
    await reloadGenerationInputs();
  });
}

async function removeBinding(assetId: string) {
  if (!selectedShot.value) return;
  const removed = selectedShot.value.referenceBindings.find((item) => item.assetId === assetId);
  const references = selectedShot.value.referenceBindings.filter(
    (item) => item.assetId !== assetId,
  );
  await act(async () => {
    if (removed?.usage === "approved_anchor") {
      await api.updateShot(selectedShot.value!.id, {
        title: selectedShot.value!.title,
        direction: selectedShot.value!.direction,
        durationSeconds: selectedShot.value!.durationSeconds,
        anchorMode: "text_only",
        referenceBindings: references,
        inheritProjectReferences: selectedShot.value!.inheritProjectReferences,
        sceneLookUsage: selectedShot.value!.sceneLookUsage === "derive_anchor"
          ? "appearance_only"
          : selectedShot.value!.sceneLookUsage,
      });
    } else {
      await api.updateReferences(selectedShot.value!.id, references);
    }
    await reloadGenerationInputs();
  });
}

function chooseUploadFile(event: Event) {
  uploadForm.file = (event.target as HTMLInputElement).files?.[0] ?? null;
}

function chooseCreateReference(event: Event) {
  createReferenceFile.value = (event.target as HTMLInputElement).files?.[0] ?? null;
}

function closeSuggestion(value: boolean) {
  if (!value) suggestion.value = null;
}

async function buildSequence() {
  if (!graph.value) return;
  await act(async () => {
    const accepted = await api.buildSequence(graph.value!.project.id);
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "总片合成失败"));
    await loadGraph();
  });
}

async function decideSequence(sequence: SequenceDto, approve: boolean) {
  if (!graph.value) return;
  await act(async () => {
    await api.selectSequence(graph.value!.project.id, sequence.id, approve);
    await loadGraph();
    if (approve) await showSequence(sequence.id);
  });
}

async function selectVideoVersion(asset: AssetDto) {
  if (!selectedShot.value) return;
  await act(async () => {
    await api.selectVersion(selectedShot.value!.id, asset.id);
    await loadGraph();
  });
}

async function waitJob(id: string): Promise<JobDto> {
  for (;;) {
    const job = await api.job(id);
    if (job.status === "succeeded" || job.status === "failed") return job;
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
  }
}

async function act(fn: () => Promise<void>) {
  busy.value = true;
  persistentError.value = "";
  try {
    await fn();
  } catch (error) {
    persistentError.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

watch(() => route.query.project, () => void loadGraph());
watch(() => shotForm.sceneLookUsage, (value) => {
  if (value === "derive_anchor") shotForm.anchorMode = "generate";
});
watch(() => shotForm.anchorMode, (value) => {
  if (value !== "generate" && shotForm.sceneLookUsage === "derive_anchor") {
    shotForm.sceneLookUsage = "appearance_only";
  }
});
watch(() => referenceForm.assetId, () => {
  const fixedRole = fixedReferenceRole(selectedBindingAsset.value);
  if (fixedRole) referenceForm.role = fixedRole;
  else referenceForm.role = editableReferenceRole(selectedBindingAsset.value);
});
onMounted(async () => {
  await act(async () => {
    await loadProjects();
    if (route.query.project) await loadGraph();
  });
  polling = window.setInterval(() => {
    if (route.query.project && !busy.value) void loadGraph();
  }, 10000);
});
onBeforeUnmount(() => window.clearInterval(polling));
</script>

<template>
  <div class="studio" v-loading="busy">
    <header class="studio-header">
      <div>
        <h1>视频片段工作台</h1>
        <p>按场景规划视频片段、逐段确认、独立版本；生成前的造型、分镜文字和素材均可修改。</p>
      </div>
      <el-button type="primary" @click="createVisible = true">新建项目</el-button>
    </header>

    <el-alert v-if="persistentError" type="error" :closable="false" show-icon class="persistent-alert">
      <template #title>操作未完成</template>
      {{ persistentError }}
    </el-alert>

    <div class="workspace-grid">
      <aside class="project-rail panel">
        <div class="panel-title">项目</div>
        <button
          v-for="project in projects"
          :key="project.id"
          class="project-item"
          :class="{ active: graph?.project.id === project.id }"
          @click="selectProject(project.id)"
        >
          <strong>{{ project.title }}</strong>
          <span>{{ project.contentDate }}</span>
        </button>
        <template v-if="graph">
          <div class="panel-title reference-title">项目与片段参考素材</div>
          <el-select v-model="uploadForm.usage" size="small">
            <el-option label="生成参考" value="generation_reference" />
            <el-option label="最终锚点" value="approved_anchor" />
          </el-select>
          <el-select v-model="uploadForm.role" size="small">
            <el-option v-for="item in ['style','prop','composition']" :key="item" :label="item" :value="item" />
          </el-select>
          <input type="file" accept="image/*" @change="chooseUploadFile" />
          <el-button size="small" :disabled="!uploadForm.file" @click="uploadReference">上传</el-button>
          <p class="source-hint">点击 Canon 或上传图切换“项目默认”。场景定妆始终由场景策略管理，不能伪装成项目身份图。</p>
          <div class="asset-strip">
            <button
              v-for="asset in graph.assets.filter(item => item.mediaType === 'image')"
              :key="asset.id"
              type="button"
              :class="{ selected: isProjectDefault(asset.id), missing: !asset.contentReady }"
              :title="`${String(asset.metadata.referenceRole ?? asset.role)} / ${String(asset.metadata.usage ?? asset.status)}`"
              @click="toggleProjectDefault(asset)"
            >
              <img v-if="asset.contentReady" :src="assetContentUrl(asset.id)" />
              <span v-else>缺失<br />请修复</span>
              <small>{{ isProjectDefault(asset.id) ? '项目默认' : asset.scope }}</small>
            </button>
          </div>
        </template>
      </aside>

      <main class="queue panel">
        <div v-if="!graph" class="empty-state">
          <h2>从一段场景剧本开始</h2>
          <p>不需要先设计全天结构，也不会预建上午、中午、傍晚节点。</p>
          <el-button type="primary" @click="createVisible = true">创建第一个项目</el-button>
        </div>
        <template v-else>
          <div class="queue-heading">
            <div><h2>{{ graph.project.title }}</h2><span>V5 · {{ graph.project.contentDate }} · {{ graph.scenes.length }} 个场景</span></div>
            <div><el-button @click="editProjectSettings">项目设置</el-button><el-button @click="restoreCanonReferences">恢复 Canon 引用</el-button><el-button @click="editScene()">添加场景</el-button><el-button type="success" @click="buildSequence">合成已批准片段</el-button></div>
          </div>
          <section v-for="(scene, sceneIndex) in graph.scenes" :key="scene.id" class="scene-card">
            <header>
              <div>
                <span class="order-chip">场景 {{ scene.order }}</span>
                <strong>{{ scene.title }}</strong>
                <small v-if="scene.chapterLabel">{{ scene.chapterLabel }}</small>
                <el-tag size="small">{{ scene.storyMode === 'single' ? '单片段' : `${scene.targetShotCount} 个片段` }}</el-tag>
              </div>
              <div>
                <el-button text @click="moveScene(sceneIndex, -1)">上移</el-button>
                <el-button text @click="moveScene(sceneIndex, 1)">下移</el-button>
                <el-button text @click="editScene(scene)">编辑</el-button>
                <el-button text type="danger" @click="removeScene(scene)">删除</el-button>
              </div>
            </header>
            <p class="source-text">{{ scene.sourceText }}</p>
            <CreativeWorkflowPanel :scene="scene" @changed="loadGraph()" @storyboard="suggest(scene)" />
            <div v-if="scene.lookPlan" class="look-plan">
              <b>场景造型方案</b>
              <span>人物服装：{{ scene.lookPlan.personWardrobe || '沿用 Canon' }}</span>
              <span>人物配件：{{ scene.lookPlan.personAccessories || '无新增' }}</span>
              <span>猫咪外观：{{ scene.lookPlan.catAppearance || '保持 Canon 外观' }}</span>
              <span>关键道具：{{ scene.lookPlan.keyProps || '无新增' }}</span>
              <span>环境与姿态：{{ scene.lookPlan.environmentStyle === 'indoor' ? '室内' : '户外' }} · {{ scene.lookPlan.personPose || '人物自然准备姿态' }} · {{ scene.lookPlan.catPose || '猫咪自然四足姿态' }}</span>
              <span>构图：{{ scene.lookPlan.composition || '稳定展示人猫关系、服饰与道具' }}</span>
              <el-alert
                v-if="scene.lookPlan.imageRecommended"
                type="warning"
                :closable="false"
                :title="`建议生成定妆图：${scene.lookPlan.recommendationReason || '造型或互动关系需要视觉确认'}`"
              />
              <SceneLookWorkbench
                :project-id="graph.project.id"
                :scene="scene"
                :assets="graph.assets"
                @refreshed="reloadGenerationInputs()"
              />
            </div>
            <div class="scene-actions">
              <el-button @click="editShot(scene.id)">手工添加视频片段</el-button>
            </div>
            <el-collapse v-if="scene.attempts.length" class="scene-attempts">
              <el-collapse-item title="AI 片段建议历史与审计" name="suggestions">
                <div v-for="attempt in scene.attempts" :key="attempt.id" class="attempt">
                  <b>#{{ attempt.attempt }} {{ attempt.status }}</b>
                  <span>{{ attempt.model || "未记录模型" }}</span>
                  <pre v-if="attempt.prompt">{{ attempt.prompt.text }}</pre>
                  <details>
                    <summary>Provider 输入与原始输出</summary>
                    <pre>{{ JSON.stringify(attempt.inputSnapshot, null, 2) }}</pre>
                  </details>
                  <el-alert
                    v-if="attempt.error"
                    type="error"
                    :title="String(attempt.error.message ?? attempt.error.code)"
                    :closable="false"
                  />
                </div>
              </el-collapse-item>
            </el-collapse>
            <div class="shots">
              <article
                v-for="(shot, shotIndex) in scene.shots"
                :key="shot.id"
                class="shot-card"
                :class="{ selected: selectedShotId === shot.id }"
                @click="selectShot(shot.id)"
              >
                <div class="shot-head"><b>{{ shot.order }}. {{ shot.title }}</b><el-tag size="small">{{ shot.durationSeconds }}s</el-tag></div>
                <p>{{ shot.direction }}</p>
                <footer>
                  <span>
                    {{ shot.anchorMode }} · {{ shot.status }} ·
                    {{ shot.referenceBindings.length }} 自定义 / {{ shot.inheritProjectReferences ? '继承项目' : '不继承项目' }} /
                    {{ sceneLookUsageLabel(shot.sceneLookUsage) }}
                  </span>
                  <span>
                    <el-button text size="small" @click.stop="moveShot(scene, shotIndex, -1)">↑</el-button>
                    <el-button text size="small" @click.stop="moveShot(scene, shotIndex, 1)">↓</el-button>
                    <el-button text size="small" @click.stop="editShot(scene.id, shot)">编辑</el-button>
                    <el-button text size="small" type="danger" @click.stop="removeShot(shot)">删除</el-button>
                  </span>
                </footer>
              </article>
            </div>
          </section>
        </template>
      </main>

      <aside class="inspector panel">
        <template v-if="selectedShot">
          <div class="panel-title">视频片段详情</div>
          <h3>{{ selectedShot.title }}</h3>
          <p class="direction">{{ selectedShot.direction }}</p>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="时长">{{ selectedShot.durationSeconds }} 秒</el-descriptions-item>
            <el-descriptions-item label="锚点">{{ selectedShot.anchorMode }}</el-descriptions-item>
            <el-descriptions-item label="片段自定义">{{ selectedShot.referenceBindings.length }} 张（最高优先）</el-descriptions-item>
            <el-descriptions-item label="场景定妆策略">{{ sceneLookUsageLabel(selectedShot.sceneLookUsage) }}{{ selectedScene?.selectedLookAssetId ? '' : '（当前无批准定妆）' }}</el-descriptions-item>
            <el-descriptions-item label="项目默认">{{ selectedShot.inheritProjectReferences ? `${graph?.project.defaultReferenceBindings.length ?? 0} 张继承` : '继承关闭' }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ selectedShot.status }}</el-descriptions-item>
          </el-descriptions>
          <div class="inspector-actions">
            <el-button @click="showPrompt">查看最终 Prompt</el-button>
            <el-button
              v-if="assistContext?.previousTail.available || assistContext?.previousTail.stale"
              :type="assistContext.previousTail.stale ? 'warning' : 'default'"
              @click="adoptPreviousTail"
            >{{ assistContext.previousTail.stale ? '尾帧已过期，重新采用' : '采用上一片段尾帧' }}</el-button>
            <el-button v-if="selectedShot.anchorMode === 'generate'" @click="generate('anchor')">生成锚点</el-button>
            <el-button type="primary" @click="generate('video')">生成视频片段</el-button>
          </div>
          <el-collapse>
            <el-collapse-item title="片段自定义素材绑定" name="refs">
              <el-select v-model="referenceForm.assetId" filterable placeholder="选择素材">
                <el-option v-for="asset in selectableAssets" :key="asset.id" :label="`${String(asset.metadata.referenceRole ?? asset.role)} · ${String(asset.metadata.usage ?? asset.status)} · ${asset.semanticKey ?? asset.id.slice(0,8)}`" :value="asset.id" />
              </el-select>
              <div class="binding-row">
                <el-select v-model="referenceForm.usage"><el-option label="生成参考" value="generation_reference" /><el-option label="最终锚点" value="approved_anchor" /></el-select>
                <el-select v-model="referenceForm.role" :disabled="bindingRoleLocked"><el-option label="identity（来源锁定）" value="identity" disabled /><el-option label="scene（场景策略专用）" value="scene" disabled /><el-option v-for="item in ['style','prop','composition']" :key="item" :label="item" :value="item" /></el-select>
                <el-select v-model="referenceForm.applyTo"><el-option label="锚点" value="anchor" /><el-option label="视频" value="video" /><el-option label="两者" value="both" /></el-select>
              </div>
              <el-button size="small" @click="bindReference">加入片段自定义集合</el-button>
              <ul>
                <li v-for="item in selectedShot.referenceBindings" :key="item.assetId">
                  {{ item.usage }} / {{ item.role }} / {{ item.applyTo }}
                  <el-button text type="danger" size="small" @click="removeBinding(item.assetId)">移除</el-button>
                </li>
              </ul>
            </el-collapse-item>
            <el-collapse-item title="Prompt 与 Provider 尝试" name="trace">
              <div v-for="attempt in selectedShot.attempts" :key="attempt.id" class="attempt">
                <b>#{{ attempt.attempt }} {{ attempt.operationKey }}</b>
                <span>{{ attempt.status }} · {{ attempt.provider || '本地' }} · {{ attempt.providerTaskId || '未创建 Task' }}</span>
                <pre v-if="attempt.prompt">{{ attempt.prompt.text }}</pre>
                <details>
                  <summary>输入快照</summary>
                  <pre>{{ JSON.stringify(attempt.inputSnapshot, null, 2) }}</pre>
                </details>
                <details v-if="attempt.reviews.length">
                  <summary>AI建议与人工审核证据</summary>
                  <pre>{{ JSON.stringify(attempt.reviews, null, 2) }}</pre>
                </details>
                <el-alert v-if="attempt.error" type="error" :title="String(attempt.error.message ?? attempt.error.code)" :closable="false" />
                <el-button
                  v-if="['queued','running'].includes(attempt.status) && attempt.providerTaskId"
                  size="small"
                  @click="resumeAttempt(attempt.id)"
                >继续查询原任务</el-button>
                <el-button
                  v-if="attempt.status === 'submission_unknown' && attempt.kind === 'video'"
                  size="small"
                  type="warning"
                  @click="reconcileAttempt(attempt.id)"
                >查询候选并对账</el-button>
                <el-alert
                  v-else-if="attempt.status === 'submission_unknown'"
                  type="warning"
                  title="同步请求结果未知，不能查询原任务或直接重提；请先在供应商账单中人工核对。"
                  :closable="false"
                />
              </div>
            </el-collapse-item>
            <el-collapse-item title="LLM 创作建议与相邻片段诊断" name="assist">
              <ShotAssistancePanel
                :context="assistContext"
                :records="assistAnalyses"
                :shot="selectedShot"
                @apply="applyShotAssistance"
                @adopt-tail="adoptPreviousTail"
              />
            </el-collapse-item>
          </el-collapse>
          <div v-if="promptPreview" class="prompt-preview">
            <b>当前编译 Prompt · {{ promptPreview.charCount }} 字</b>
            <p>定性节奏：{{ promptPreview.qualitativePacing }}</p>
            <el-alert v-for="finding in promptPreview.localAnalysis.findings" :key="finding.code" :type="finding.severity === 'warning' ? 'warning' : 'info'" :title="finding.message" :closable="false" />
            <div v-for="item in promptPreview.references" :key="item.assetId" class="prompt-reference">
              <img v-if="item.contentReady" :src="assetContentUrl(item.assetId)" />
              <div><b>@图片{{ item.index }} · {{ item.displayName }}</b><span>{{ item.sourceLayer }}</span><small>{{ item.responsibility }}</small></div>
            </div>
            <el-divider content-position="left">LLM 创作正文</el-divider>
            <pre>{{ promptPreview.creativeBody }}</pre>
            <el-divider content-position="left">系统技术外壳</el-divider>
            <pre>{{ promptPreview.systemShell }}</pre>
            <el-divider content-position="left">最终 Provider Prompt</el-divider>
            <pre>{{ promptPreview.prompt }}</pre>
          </div>
          <div class="versions">
            <h4>媒体版本</h4>
            <div
              v-for="asset in selectedShot.assets.filter(item => ['shot_anchor','shot_video','shot_video_edit'].includes(item.role))"
              :key="asset.id"
              class="version-card"
            >
              <img v-if="asset.mediaType === 'image'" :src="assetContentUrl(asset.id)" />
              <video v-else controls :src="assetContentUrl(asset.id)" />
              <span>{{ asset.role }} · {{ asset.status }}</span>
              <div v-if="asset.status === 'candidate'">
                <el-button size="small" type="success" @click="review(asset, 'approved')">批准并选择</el-button>
                <el-button size="small" type="danger" @click="review(asset, 'rejected')">拒绝</el-button>
              </div>
              <el-button
                v-else-if="asset.mediaType === 'video' && asset.status === 'approved' && asset.id !== selectedShot.selectedVideoAssetId"
                size="small"
                @click="selectVideoVersion(asset)"
              >选择此历史版本</el-button>
            </div>
          </div>
        </template>
        <div v-else class="empty-state"><p>选择一个视频片段查看 Prompt、素材来源、任务和版本。</p></div>
      </aside>
    </div>

    <section v-if="graph?.sequences.length" class="sequence-panel panel">
      <div class="sequence-heading">
        <div>
          <h3>项目总片版本</h3>
          <p>总片只引用已批准视频片段；每次合成创建新的 EDL Revision，不覆盖片段原文件。</p>
        </div>
      </div>
      <div class="sequence-grid">
        <article
          v-for="sequence in graph.sequences"
          :key="sequence.id"
          class="sequence-card"
          :class="{ selected: selectedSequenceId === sequence.id || graph.project.selectedSequenceId === sequence.id }"
        >
          <button class="sequence-open" @click="showSequence(sequence.id)">
            <b>Revision {{ sequence.revision }}</b>
            <span>{{ (sequence.plan.duration_ms / 1000).toFixed(2) }}s · {{ sequence.status }}</span>
          </button>
          <div>
            <el-button
              v-if="sequence.status === 'content_review'"
              size="small"
              type="success"
              @click="decideSequence(sequence, true)"
            >批准并设为总片</el-button>
            <el-button
              v-if="sequence.status === 'content_review'"
              size="small"
              type="danger"
              @click="decideSequence(sequence, false)"
            >拒绝</el-button>
            <el-button
              v-if="sequence.status === 'approved' && graph.project.selectedSequenceId !== sequence.id"
              size="small"
              @click="decideSequence(sequence, true)"
            >回退到此版本</el-button>
          </div>
        </article>
      </div>
    </section>

    <VideoTimeline
      v-if="selectedShot && selectedVideo && !selectedSequence"
      :shot-id="selectedShot.id"
      :asset-id="selectedVideo.id"
      :src="assetContentUrl(selectedVideo.id)"
      :duration-ms="selectedVideoDurationMs"
      :frames="selectedVideoFrames"
      :markers-ms="selectedVideoMarkersMs"
      @completed="loadGraph()"
    />
    <section v-else-if="selectedSequence && selectedSequenceAsset" class="master-timeline panel">
      <div>
        <h3>总片 Revision {{ selectedSequence.revision }}</h3>
        <p>单轨 EDL 由已批准视频片段依序组成；需要修改某段时，请返回对应片段重做或区间重拍后重新合成。</p>
      </div>
      <video controls :src="assetContentUrl(selectedSequenceAsset.id)" />
      <details>
        <summary>查看 EDL</summary>
        <pre>{{ JSON.stringify(selectedSequence.plan, null, 2) }}</pre>
      </details>
    </section>

    <el-dialog v-model="createVisible" title="新建视频片段项目" width="620px">
      <el-form label-position="top">
        <el-form-item label="项目标题"><el-input v-model="createForm.title" placeholder="例如：池塘边钓鱼" /></el-form-item>
        <el-form-item label="第一场景标题"><el-input v-model="createForm.sceneTitle" /></el-form-item>
        <el-form-item label="第一段原始剧本"><el-input v-model="createForm.sourceText" type="textarea" :rows="8" placeholder="直接粘贴一段完整场景故事。创建项目本身不会调用 Ark。" /></el-form-item>
        <el-form-item label="可选通用视觉素材（初始按构图参考）"><input type="file" accept="image/*" @change="chooseCreateReference" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" @click="createProject">创建项目</el-button></template>
    </el-dialog>

    <el-dialog v-model="projectSettingsVisible" title="项目设置" width="520px">
      <el-form label-position="top">
        <el-form-item label="项目标题"><el-input v-model="projectSettingsForm.title" /></el-form-item>
        <el-form-item label="内容日期"><el-date-picker v-model="projectSettingsForm.contentDate" type="date" value-format="YYYY-MM-DD" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="projectSettingsVisible = false">取消</el-button><el-button type="primary" @click="saveProjectSettings">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="sceneVisible" :title="sceneForm.id ? '编辑场景' : '添加场景'" width="760px">
      <el-form label-position="top">
        <el-form-item label="场景标题"><el-input v-model="sceneForm.title" /></el-form-item>
        <el-form-item label="可选章节标签"><el-input v-model="sceneForm.chapterLabel" placeholder="例如：上午、河边、归家；仅作为文字标签" /></el-form-item>
        <el-form-item label="原始剧本"><el-input v-model="sceneForm.sourceText" type="textarea" :rows="7" /></el-form-item>
        <el-form-item label="可选上下文备注"><el-input v-model="sceneForm.contextNote" type="textarea" :rows="3" /></el-form-item>
        <div class="binding-row mode-row">
          <el-form-item label="生成模式">
            <el-radio-group v-model="sceneForm.storyMode">
              <el-radio-button value="single">单片段</el-radio-button>
              <el-radio-button value="multi">多片段</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="sceneForm.storyMode === 'multi'" label="目标片段数">
            <el-input-number v-model="sceneForm.targetShotCount" :min="2" :max="6" />
          </el-form-item>
        </div>
        <el-divider content-position="left">场景造型方案</el-divider>
        <div class="look-grid">
          <el-form-item label="人物服装"><el-input v-model="sceneForm.lookPlan.personWardrobe" /></el-form-item>
          <el-form-item label="人物配件"><el-input v-model="sceneForm.lookPlan.personAccessories" /></el-form-item>
          <el-form-item label="猫咪外观/配件"><el-input v-model="sceneForm.lookPlan.catAppearance" placeholder="默认保持 Canon 外观，不添加服饰" /></el-form-item>
          <el-form-item label="关键道具"><el-input v-model="sceneForm.lookPlan.keyProps" /></el-form-item>
          <el-form-item label="人物姿态"><el-input v-model="sceneForm.lookPlan.personPose" /></el-form-item>
          <el-form-item label="猫咪姿态"><el-input v-model="sceneForm.lookPlan.catPose" /></el-form-item>
          <el-form-item label="环境画风"><el-radio-group v-model="sceneForm.lookPlan.environmentStyle"><el-radio-button value="outdoor">户外</el-radio-button><el-radio-button value="indoor">室内</el-radio-button></el-radio-group></el-form-item>
        </div>
        <el-form-item label="构图与人猫空间关系"><el-input v-model="sceneForm.lookPlan.composition" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="补充生成要求"><el-input v-model="sceneForm.lookPlan.additionalInstructions" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="定妆图建议"><el-switch v-model="sceneForm.lookPlan.imageRecommended" active-text="建议生成（只提醒，不阻断）" /></el-form-item>
        <el-form-item label="建议原因"><el-input v-model="sceneForm.lookPlan.recommendationReason" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="sceneVisible = false">取消</el-button><el-button type="primary" @click="saveScene">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="shotVisible" :title="shotForm.id ? '编辑视频片段' : '手工添加视频片段'" width="760px">
      <el-form label-position="top">
        <el-form-item label="片段标题"><el-input v-model="shotForm.title" /></el-form-item>
        <el-form-item label="完整分镜描述"><el-input v-model="shotForm.direction" type="textarea" :rows="11" placeholder="1. 景别/机位……主体关系……动作……收尾切点与声音……\n2. ……" /></el-form-item>
        <div class="binding-row">
          <el-form-item label="时长"><el-input-number v-model="shotForm.durationSeconds" :min="8" :max="15" /></el-form-item>
          <el-form-item label="锚点方式"><el-select v-model="shotForm.anchorMode"><el-option label="纯文本直出" value="text_only" /><el-option label="使用已有图片（先在右侧绑定最终锚点）" value="existing" :disabled="!shotForm.referenceBindings.some(item => item.usage === 'approved_anchor')" /><el-option label="生成新锚点" value="generate" /></el-select></el-form-item>
        </div>
        <el-form-item label="参考继承">
          <el-checkbox v-model="shotForm.inheritProjectReferences">继承项目默认参考</el-checkbox>
          <el-select v-model="shotForm.sceneLookUsage" style="width: 100%; margin-top: 8px">
            <el-option label="关闭：不引用基础定妆" value="off" />
            <el-option label="只继承造型（默认）：忽略姿态、动作结果和构图" value="appearance_only" />
            <el-option label="完整参考：起始状态与定妆图高度一致" value="full_reference" />
            <el-option label="派生锚点：定妆图只用于生成本片段开场图" value="derive_anchor" />
          </el-select>
          <div class="source-hint">视频输入顺序：锚点 → 片段自定义 → 场景定妆 → 项目默认，再按资产 ID 和内容 SHA 去重。派生锚点获批后，基础定妆不会再次进入视频。</div>
        </el-form-item>
        <div class="shot-local-findings">
          <el-alert title="以下为免费本地检查；只提示，不自动改写。保存后可选择付费 LLM 分析当前及相邻片段。" type="info" :closable="false" />
          <el-alert v-for="finding in shotFormFindings" :key="finding.title" :type="finding.type" :title="finding.title" :closable="false" />
        </div>
      </el-form>
      <template #footer><el-button @click="shotVisible = false">取消</el-button><el-button type="primary" @click="saveShot">保存视频片段</el-button></template>
    </el-dialog>

    <el-dialog v-model="assistConfirmVisible" title="片段已保存：是否进行 LLM 创作分析？" width="760px">
      <template v-if="assistContext">
        <el-alert title="保存已经完成。选择“仅保存”不会调用 Ark；分析失败也不会回滚本次保存。" type="info" :closable="false" />
        <p>模型：{{ assistContext.model || '未配置' }} · 草稿 Revision {{ assistContext.sourceDraftRevision }} · 最多 9 张压缩预览图</p>
        <div class="assist-candidate-grid">
          <label v-for="asset in assistContext.candidates" :key="asset.assetId" :class="{ disabled: !asset.available || asset.duplicate }">
            <el-checkbox v-model="assistCandidateIds" :value="asset.assetId" :disabled="!asset.available || asset.duplicate" />
            <img v-if="asset.contentReady" :src="assetContentUrl(asset.assetId)" />
            <span>{{ asset.displayName }}<small>{{ asset.sourceLayer }} · {{ asset.responsibility }}</small></span>
          </label>
        </div>
        <el-alert v-for="warning in assistContext.warnings" :key="warning" :title="warning" type="warning" :closable="false" />
      </template>
      <template #footer>
        <el-button @click="assistConfirmVisible = false">仅保存</el-button>
        <el-button type="primary" :disabled="!assistContext?.model || assistCandidateIds.length > 9" @click="runShotAssistance">保存并分析（产生 Ark 费用）</el-button>
      </template>
    </el-dialog>

    <el-dialog :model-value="Boolean(suggestion)" title="AI 造型与视频片段建议（编辑后才写入）" width="860px" @update:model-value="closeSuggestion">
      <div v-if="suggestion" class="suggestion-editor">
        <div class="suggestion-summary">
          <b>场景：{{ suggestion.output.sceneTitle }}</b>
          <el-tag>{{ suggestion.output.shots.length }} 个片段</el-tag>
          <el-tag type="info">累计 {{ suggestionDuration }} 秒</el-tag>
        </div>
        <el-divider content-position="left">可编辑场景造型方案</el-divider>
        <div class="look-grid">
          <el-form-item label="人物服装"><el-input v-model="suggestion.output.lookPlan.personWardrobe" /></el-form-item>
          <el-form-item label="人物配件"><el-input v-model="suggestion.output.lookPlan.personAccessories" /></el-form-item>
          <el-form-item label="猫咪外观/配件"><el-input v-model="suggestion.output.lookPlan.catAppearance" /></el-form-item>
          <el-form-item label="关键道具"><el-input v-model="suggestion.output.lookPlan.keyProps" /></el-form-item>
          <el-form-item label="人物姿态"><el-input v-model="suggestion.output.lookPlan.personPose" /></el-form-item>
          <el-form-item label="猫咪姿态"><el-input v-model="suggestion.output.lookPlan.catPose" /></el-form-item>
          <el-form-item label="环境画风"><el-radio-group v-model="suggestion.output.lookPlan.environmentStyle"><el-radio-button value="outdoor">户外</el-radio-button><el-radio-button value="indoor">室内</el-radio-button></el-radio-group></el-form-item>
        </div>
        <el-form-item label="构图与人猫空间关系"><el-input v-model="suggestion.output.lookPlan.composition" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="补充生成要求"><el-input v-model="suggestion.output.lookPlan.additionalInstructions" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="定妆图建议"><el-switch v-model="suggestion.output.lookPlan.imageRecommended" active-text="建议（不阻断视频生成）" /></el-form-item>
        <el-form-item label="建议原因"><el-input v-model="suggestion.output.lookPlan.recommendationReason" type="textarea" :rows="2" /></el-form-item>
        <el-divider content-position="left">可编辑视频片段</el-divider>
        <article v-for="(shot, index) in suggestion.output.shots" :key="index" class="suggestion-shot">
          <div class="suggestion-shot-head"><b>{{ index + 1 }}. 视频片段</b><el-input-number v-model="shot.suggestedDurationSeconds" :min="8" :max="15" /></div>
          <el-form-item label="标题"><el-input v-model="shot.title" /></el-form-item>
          <el-form-item label="完整分镜描述（2–4 个编号子镜头）"><el-input v-model="shot.direction" type="textarea" :rows="8" placeholder="1. 景别/机位、主体关系、动作、人物配合、结果、运镜、切点和声音……" /></el-form-item>
        </article>
      </div>
      <template #footer><el-button @click="suggestion = null">取消</el-button><el-button type="primary" @click="acceptSuggestion">接受编辑稿并建立视频片段</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.studio { min-height: 100%; background: #0d1016; color: #e8eaf0; padding: 22px; }
.studio-header, .queue-heading, .scene-card > header, .shot-head, .shot-card footer { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.studio-header h1, .queue-heading h2 { margin: 0; }.studio-header p { color: #9299a8; margin: 6px 0 0; }
.persistent-alert { margin: 16px 0; }.workspace-grid { display: grid; grid-template-columns: 220px minmax(520px, 1fr) 390px; gap: 14px; margin-top: 18px; align-items: start; }
.panel { background: #151922; border: 1px solid #292f3b; border-radius: 12px; }.project-rail, .inspector { padding: 14px; position: sticky; top: 12px; max-height: calc(100vh - 40px); overflow: auto; }.queue { padding: 18px; min-height: 650px; }
.panel-title { color: #8c95a7; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; }.reference-title { margin-top: 22px; }
.project-item { display: flex; flex-direction: column; width: 100%; color: #d9dde7; background: transparent; border: 0; border-radius: 8px; text-align: left; padding: 10px; cursor: pointer; }.project-item:hover, .project-item.active { background: #232a36; }.project-item span { color: #80899a; font-size: 12px; margin-top: 4px; }
.scene-card { border-top: 1px solid #2b313d; padding: 18px 0; }.scene-card small { color: #7d8798; margin-left: 8px; }.order-chip { color: #68a8ff; margin-right: 10px; }.source-text { color: #aeb5c3; line-height: 1.7; white-space: pre-wrap; }.scene-actions { margin: 12px 0; }.look-plan { display: grid; gap: 5px; border-left: 3px solid #6d8fc7; padding: 10px 12px; background: #101722; color: #aeb8c8; font-size: 13px; }.look-actions { display: flex; gap: 8px; margin-top: 5px; }.look-actions .el-select { min-width: 260px; }.selected-look-preview { width: 160px; max-height: 220px; object-fit: contain; background: #090c11; border-radius: 7px; }.look-candidate { display: flex; gap: 10px; padding: 8px; border: 1px solid #4b3d25; border-radius: 7px; }.look-candidate img { width: 100px; height: 120px; object-fit: contain; background: #090c11; }.look-candidate > div { display: flex; gap: 7px; align-items: center; flex-wrap: wrap; }
.shots { display: grid; gap: 10px; }.shot-card { padding: 14px; background: #10141b; border: 1px solid #292f3b; border-radius: 10px; cursor: pointer; }.shot-card.selected { border-color: #4d96ff; box-shadow: 0 0 0 1px #4d96ff55; }.shot-card p, .direction { color: #b6bdca; font-size: 13px; line-height: 1.65; white-space: pre-wrap; }.shot-card footer { color: #768092; font-size: 12px; }
.inspector h3 { margin: 4px 0 8px; }.inspector-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }.binding-row { display: flex; gap: 8px; margin: 8px 0; }.attempt { padding: 10px 0; border-bottom: 1px solid #292f3b; display: grid; gap: 5px; }.attempt span { color: #8992a3; font-size: 12px; } pre { white-space: pre-wrap; word-break: break-word; max-height: 320px; overflow: auto; background: #0c0f15; padding: 10px; border-radius: 8px; color: #cdd3dd; }
.asset-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; margin-top: 10px; }.asset-strip button { border: 1px solid #2b313d; border-radius: 6px; padding: 3px; background: #0c1017; color: #aab2c1; cursor: pointer; }.asset-strip button.selected { border-color: #409eff; box-shadow: 0 0 0 1px #409eff66; }.asset-strip button.missing { border-color: #8b5e2b; cursor: default; }.asset-strip img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 4px; }.asset-strip button > span { display: grid; place-content: center; aspect-ratio: 1; color: #d6a15e; font-size: 11px; }.asset-strip small { display: block; margin: 2px 0; color: #7f8999; font-size: 9px; }.source-hint { color: #8791a2; font-size: 11px; line-height: 1.5; }.version-card { border: 1px solid #2b313d; border-radius: 8px; padding: 8px; margin: 8px 0; display: grid; gap: 6px; }.version-card img, .version-card video { width: 100%; max-height: 240px; object-fit: contain; background: #090b0f; }.empty-state { text-align: center; color: #8f98a7; padding: 80px 20px; }
.scene-attempts { margin: 10px 0; }.attempt details summary, .master-timeline summary { color: #8fa7c9; cursor: pointer; font-size: 12px; }.sequence-panel, .master-timeline { margin-top: 16px; padding: 16px; }.sequence-heading h3, .master-timeline h3 { margin: 0; }.sequence-heading p, .master-timeline p { color: #9299a8; }.sequence-grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }.sequence-card { border: 1px solid #2b313d; border-radius: 9px; padding: 10px; display: grid; gap: 8px; }.sequence-card.selected { border-color: #4d96ff; }.sequence-open { border: 0; background: transparent; color: #e8eaf0; text-align: left; cursor: pointer; display: grid; gap: 4px; }.sequence-open span { color: #8490a3; font-size: 12px; }.master-timeline video { width: 100%; max-height: 560px; background: #080a0e; }
.mode-row { align-items: end; }.look-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }.suggestion-summary, .suggestion-shot-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }.suggestion-shot { padding: 14px; margin: 10px 0; border: 1px solid #2b313d; border-radius: 9px; background: #10141b; }.suggestion-shot-head { margin-bottom: 10px; }
.assist-context, .assist-analysis, .assist-patch { display: grid; gap: 8px; margin: 8px 0; }.prompt-reference { display: grid; grid-template-columns: 56px 1fr; gap: 8px; align-items: center; margin: 7px 0; padding: 7px; border: 1px solid #293344; border-radius: 7px; }.prompt-reference img { width: 56px; height: 56px; object-fit: cover; }.prompt-reference div { display: grid; gap: 3px; }.prompt-reference span, .prompt-reference small { color: #8f9caf; }.assist-candidate-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 12px 0; }.assist-candidate-grid label { display: grid; grid-template-columns: auto 64px 1fr; gap: 7px; align-items: center; padding: 7px; border: 1px solid #303847; border-radius: 8px; }.assist-candidate-grid label.disabled { opacity: .48; }.assist-candidate-grid img { width: 64px; height: 64px; object-fit: cover; }.assist-candidate-grid span { display: grid; font-size: 12px; }.assist-candidate-grid small { color: #8791a2; }
.shot-local-findings { display: grid; gap: 6px; width: 100%; }
@media (max-width: 1280px) { .workspace-grid { grid-template-columns: 190px 1fr; }.inspector { position: static; grid-column: 1 / -1; max-height: none; } }
</style>
