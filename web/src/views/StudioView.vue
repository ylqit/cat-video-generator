<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, assetContentUrl } from "../api/client";
import type {
  AnchorMode,
  AssetDto,
  LookReferencePurpose,
  ProductionBoardDto,
  ProjectGraph,
  ProjectSummary,
  ReferenceBinding,
  ReferenceRole,
  ReferenceUsage,
  SceneDto,
  SceneLookPlan,
  SceneLookUsage,
  SequenceDto,
  SequenceTransitionDto,
  ShotAssistContext,
  ShotAssistPatch,
  ShotAssistRecord,
  ShotDto,
  ShotGenerationWorkspaceDto,
  ShotPromptPreview,
  ShotProductionSummaryDto,
  StoryMode,
  VisualProfileDraft,
  VisualProfileRevisionDto,
} from "../api/types";
import VideoTimeline from "../components/VideoTimeline.vue";
import CreativeWorkflowPanel from "../components/CreativeWorkflowPanel.vue";
import SceneLookWorkbench from "../components/SceneLookWorkbench.vue";
import ShotGenerationWorkspace from "../components/ShotGenerationWorkspace.vue";
import { registerTask, rememberProject, useTaskCenter } from "../tasks/taskCenter";

const route = useRoute();
const router = useRouter();
const projects = ref<ProjectSummary[]>([]);
const graph = ref<ProjectGraph | null>(null);
const productionBoard = ref<ProductionBoardDto | null>(null);
const generationWorkspace = ref<ShotGenerationWorkspaceDto | null>(null);
const selectedShotId = ref<string | null>(null);
const selectedSequenceId = ref<string | null>(null);
const busy = ref(false);
const persistentError = ref("");
const createVisible = ref(false);
const projectSettingsVisible = ref(false);
const sequenceBuilderVisible = ref(false);
const sceneVisible = ref(false);
const shotVisible = ref(false);
const assistConfirmVisible = ref(false);
const assetDrawerVisible = ref(false);
const shotWorkspaceVisible = ref(false);
const anchorPromptPreview = ref<ShotPromptPreview | null>(null);
const videoPromptPreview = ref<ShotPromptPreview | null>(null);
const assistContext = ref<ShotAssistContext | null>(null);
const assistAnalyses = ref<ShotAssistRecord[]>([]);
const assistCandidateIds = ref<string[]>([]);
const shotOriginalDuration = ref(8);
const createReferenceFile = ref<File | null>(null);
const sequenceTransitions = ref<Record<string, SequenceTransitionDto>>({});
const projectProfile = ref<VisualProfileRevisionDto | null>(null);
const projectProfileForm = ref<VisualProfileDraft | null>(null);
const projectProfileReferenceIds = ref<string[]>([]);
const taskCenter = useTaskCenter();

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
const uploadForm = reactive({
  usage: "generation_reference" as ReferenceUsage,
  role: "prop" as ReferenceRole,
  displayName: "",
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
const canonAssets = computed(() => graph.value?.assets.filter(
  (item) => item.mediaType === "image" && item.scope === "canon",
) ?? []);
const projectReferenceAssets = computed(() => graph.value?.assets.filter(
  (item) => item.mediaType === "image"
    && item.scope === "project"
    && item.projectId === graph.value?.project.id,
) ?? []);
const projectGeneratedAssets = computed(() => graph.value?.assets.filter(
  (item) => item.mediaType === "image"
    && item.scope !== "canon"
    && item.scope !== "project"
    && item.projectId === graph.value?.project.id,
) ?? []);
const selectableAssets = computed(() => graph.value?.assets.filter(
  (item) => item.mediaType === "image"
    && item.contentReady
    && ["canon", "project"].includes(item.scope),
) ?? []);
const selectedScene = computed<SceneDto | null>(() => {
  if (!graph.value || !selectedShot.value) return null;
  return graph.value.scenes.find((item) => item.id === selectedShot.value?.sceneId) ?? null;
});
const productionStage = computed(() => {
  const summaries = productionBoard.value?.scenes.flatMap((scene) => scene.shots) ?? [];
  if (!summaries.length || productionBoard.value?.scenes.some((scene) => !scene.selectedLookAssetId)) return 0;
  if (summaries.some((item) => ["needs_opening", "generating_anchor", "blocked"].includes(item.state))) return 1;
  if (summaries.some((item) => ["ready_video", "generating_video"].includes(item.state))) return 2;
  if (summaries.some((item) => ["awaiting_review", "stale"].includes(item.state))) return 3;
  return 4;
});
const productionStageLabels = ["场景视觉基准", "片段开场", "视频生成", "审核与衔接", "成片编排"];
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
const sequenceShots = computed(() => graph.value?.scenes.flatMap(
  (scene) => scene.shots.filter((shot) => Boolean(shot.selectedVideoAssetId)),
) ?? []);
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
    productionBoard.value = null;
    return;
  }
  rememberProject(id);
  [graph.value, productionBoard.value] = await Promise.all([
    api.project(id),
    api.productionBoard(id),
  ]);
  const requestedSequence = String(route.query.sequence ?? "");
  if (graph.value.sequences.some((item) => item.id === requestedSequence)) {
    selectedSequenceId.value = requestedSequence;
    selectedShotId.value = null;
    generationWorkspace.value = null;
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
  const requestedWorkspace = Boolean(requestedShot && selectedShotId.value === requestedShot);
  if (requestedWorkspace || shotWorkspaceVisible.value) {
    await Promise.all([loadShotAssistance(selectedShotId.value), loadGenerationWorkspace(selectedShotId.value)]);
    if (requestedWorkspace) shotWorkspaceVisible.value = true;
    return;
  }
  await Promise.all([loadShotAssistance(null), loadGenerationWorkspace(null)]);
}

async function reloadGenerationInputs() {
  anchorPromptPreview.value = null;
  videoPromptPreview.value = null;
  await loadGraph();
}

async function selectProject(id: string) {
  shotWorkspaceVisible.value = false;
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
  anchorPromptPreview.value = null;
  videoPromptPreview.value = null;
  shotWorkspaceVisible.value = true;
  await Promise.all([loadShotAssistance(id), loadGenerationWorkspace(id)]);
}

async function loadGenerationWorkspace(shotId?: string | null) {
  if (!shotId) {
    generationWorkspace.value = null;
    anchorPromptPreview.value = null;
    videoPromptPreview.value = null;
    return;
  }
  try {
    generationWorkspace.value = await api.shotGenerationWorkspace(shotId);
    anchorPromptPreview.value = generationWorkspace.value.anchorPreview;
    videoPromptPreview.value = generationWorkspace.value.videoPreview;
  } catch (error) {
    generationWorkspace.value = null;
    persistentError.value = error instanceof Error ? error.message : String(error);
  }
}

async function closeShotWorkspace() {
  shotWorkspaceVisible.value = false;
  await router.replace({ path: "/studio", query: { project: graph.value?.project.id } });
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
  shotWorkspaceVisible.value = false;
  selectedSequenceId.value = id;
  selectedShotId.value = null;
  anchorPromptPreview.value = null;
  videoPromptPreview.value = null;
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
        createReferenceFile.value.name.replace(/\.[^.]+$/, ""),
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

function cloneVisualProfile(value: VisualProfileDraft): VisualProfileDraft {
  return structuredClone({
    personIdentity: value.personIdentity,
    personHair: value.personHair,
    personBody: value.personBody,
    catIdentity: value.catIdentity,
    stylePositive: value.stylePositive,
    styleNegative: value.styleNegative,
    referenceBindings: value.referenceBindings,
  });
}

function boardScene(sceneId: string) {
  return productionBoard.value?.scenes.find((item) => item.sceneId === sceneId) ?? null;
}

function boardShot(shotId: string): ShotProductionSummaryDto | null {
  return productionBoard.value?.scenes.flatMap((item) => item.shots).find(
    (item) => item.shotId === shotId,
  ) ?? null;
}

function productionPreviewAsset(summary: ShotProductionSummaryDto | null): AssetDto | null {
  if (!summary?.previewAssetId || !graph.value) return null;
  return graph.value.assets.find((item) => item.id === summary.previewAssetId)
    ?? graph.value.scenes.flatMap((scene) => scene.shots.flatMap((shot) => shot.assets)).find(
      (item) => item.id === summary.previewAssetId,
    )
    ?? null;
}

function productionPreviewUrl(summary: ShotProductionSummaryDto | null): string | null {
  const asset = productionPreviewAsset(summary);
  return asset?.contentReady ? assetContentUrl(asset.id) : null;
}

function canonPurpose(asset: AssetDto): LookReferencePurpose {
  if (asset.referencePurpose) return asset.referencePurpose;
  if (asset.semanticKey?.startsWith("style:")) return "style";
  if (asset.semanticKey?.startsWith("cat:")) return "cat_identity";
  return "person_identity";
}

function updateProjectStyleList(kind: "positive" | "negative", value: unknown) {
  if (!projectProfileForm.value) return;
  const items = String(value).split("\n").map((item) => item.trim()).filter(Boolean);
  if (kind === "positive") projectProfileForm.value.stylePositive = items;
  else projectProfileForm.value.styleNegative = items;
}

function restoreProjectProfileDraft() {
  if (!projectProfile.value?.canonDefaults) return;
  projectProfileForm.value = cloneVisualProfile(projectProfile.value.canonDefaults);
  projectProfileReferenceIds.value = projectProfileForm.value.referenceBindings.map(
    (item) => item.assetId,
  );
  ElMessage.info("已恢复 Canon 默认草稿；保存项目设置后才会切换 Revision");
}

async function editProjectSettings() {
  if (!graph.value) return;
  Object.assign(projectSettingsForm, {
    title: graph.value.project.title,
    contentDate: graph.value.project.contentDate,
  });
  projectSettingsVisible.value = true;
  await act(async () => {
    projectProfile.value = await api.visualProfile(graph.value!.project.id);
    projectProfileForm.value = cloneVisualProfile(projectProfile.value);
    projectProfileReferenceIds.value = projectProfileForm.value.referenceBindings.map(
      (item) => item.assetId,
    );
  });
}

async function saveProjectSettings() {
  if (!graph.value || !projectSettingsForm.title.trim() || !projectSettingsForm.contentDate) {
    ElMessage.warning("请填写项目标题和日期");
    return;
  }
  await act(async () => {
    if (projectProfileForm.value) {
      const previous = new Map(
        projectProfileForm.value.referenceBindings.map((item) => [item.assetId, item]),
      );
      projectProfileForm.value.referenceBindings = canonAssets.value
        .filter((asset) => projectProfileReferenceIds.value.includes(asset.id))
        .map((asset) => previous.get(asset.id) ?? {
          assetId: asset.id,
          purpose: canonPurpose(asset),
          instruction: "",
        });
      projectProfile.value = await api.updateVisualProfile(
        graph.value!.project.id,
        projectProfileForm.value,
      );
    }
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
    "这会恢复项目视觉档案中的 Canon 人物、猫咪和画风参考，并从片段自定义集合移除误绑的场景视觉基准；不会删除任何历史生成记录。",
    "恢复 Canon 默认引用",
  );
  await act(async () => {
    const result = await api.restoreProjectCanonReferences(graph.value!.project.id);
    await reloadGenerationInputs();
    ElMessage.success(`已恢复 ${result.referenceCount} 张 Canon 引用，清理 ${result.cleanedShotCount} 个片段`);
  });
}

async function submitStudioTask(
  submit: () => Promise<{ jobId: string }>,
  options: {
    kind: string;
    label: string;
    operationKey: string;
    projectId: string;
    sceneId?: string;
    shotId?: string;
  },
) {
  persistentError.value = "";
  try {
    const submitted = await submit();
    registerTask(submitted.jobId, options);
    ElMessage.success(`${options.label}已提交到全局任务中心，当前页面可以继续操作`);
  } catch (error) {
    persistentError.value = error instanceof Error ? error.message : String(error);
  }
}

async function runShotAssistance() {
  if (!selectedShot.value || !assistContext.value) return;
  const shot = selectedShot.value;
  const context = assistContext.value;
  assistConfirmVisible.value = false;
  await submitStudioTask(
    () => api.assistShot(
      shot.id,
      context.sourceDraftRevision,
      assistCandidateIds.value,
    ),
    {
      kind: "shot_assistance",
      label: "片段视觉与 Prompt 审稿",
      operationKey: "director:shot-assistance",
      projectId: graph.value!.project.id,
      sceneId: shot.sceneId,
      shotId: shot.id,
    },
  );
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

async function generate(kind: "anchor" | "video") {
  if (!selectedShot.value || !graph.value) return;
  const shot = selectedShot.value;
  const preview = await api.promptPreview(shot.id, kind);
  if (!preview.ready) {
    persistentError.value = preview.blockers.join("；") || "当前生成输入尚未就绪";
    ElMessage.warning(persistentError.value);
    return;
  }
  if (kind === "anchor") anchorPromptPreview.value = preview;
  else videoPromptPreview.value = preview;
  const operationKey = kind === "anchor" ? "image:anchor" : "video:shot";
  const priorAttempts = shot.attempts.filter(
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
    const retryPreview = await api.promptPreview(shot.id, kind, reason);
    if (!retryPreview.ready) {
      persistentError.value = retryPreview.blockers.join("；") || "当前生成输入尚未就绪";
      ElMessage.warning(persistentError.value);
      return;
    }
    if (kind === "anchor") anchorPromptPreview.value = retryPreview;
    else videoPromptPreview.value = retryPreview;
  }
  await ElMessageBox.confirm(
    kind === "anchor"
      ? `${action}锚点会产生一次 Seedream 费用，是否继续？`
      : `${action}当前视频片段会产生一次 Seedance 费用，是否继续？`,
    "付费确认",
  );
  await submitStudioTask(
    () => kind === "anchor"
      ? api.generateAnchor(shot.id, regenerate, reason)
      : api.generateVideo(shot.id, regenerate, reason),
    {
      kind: kind === "anchor" ? "generate_anchor" : "generate_video",
      label: kind === "anchor" ? "片段开场锚点" : "视频片段",
      operationKey,
      projectId: graph.value.project.id,
      sceneId: shot.sceneId,
      shotId: shot.id,
    },
  );
}

async function resumeAttempt(stepId: string) {
  if (!graph.value || !selectedShot.value) return;
  const shot = selectedShot.value;
  await submitStudioTask(
    () => api.resumeStep(stepId),
    {
      kind: "resume_step",
      label: "恢复 Provider 任务查询",
      operationKey: "resume",
      projectId: graph.value.project.id,
      sceneId: shot.sceneId,
      shotId: shot.id,
    },
  );
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

async function review(
  asset: AssetDto,
  decision: "approved" | "rejected",
  stale = false,
) {
  if (decision === "approved" && stale) {
    await ElMessageBox.confirm(
      "该媒体基于旧的片段文字、时长、锚点或参考图。批准后仍会设为当前版本，是否继续？",
      "确认采用旧输入版本",
      { type: "warning" },
    );
  }
  const reason = decision === "approved" ? "人工观看通过" : "人工观看未通过";
  await act(async () => {
    await api.reviewAsset(asset.id, decision, reason);
    await loadGraph();
  });
}

async function uploadReference() {
  if (!graph.value || !uploadForm.file) return;
  await act(async () => {
    await api.uploadReference(
      graph.value!.project.id,
      uploadForm.usage,
      uploadForm.role,
      uploadForm.displayName,
      uploadForm.file!,
    );
    uploadForm.displayName = "";
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
    ElMessage.warning("场景视觉基准只能通过片段场景策略使用，不能保存为项目身份参考");
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

async function bindShotReference(binding: ReferenceBinding) {
  if (!selectedShot.value) return;
  const selectedAsset = selectableAssets.value.find((item) => item.id === binding.assetId);
  const fixedRole = fixedReferenceRole(selectedAsset);
  if (fixedRole) binding.role = fixedRole;
  const bindings = selectedShot.value.referenceBindings.filter(
    (item) => item.assetId !== binding.assetId,
  );
  bindings.push(binding);
  const approvedAnchor = binding.usage === "approved_anchor";
  const draft = {
    title: selectedShot.value.title,
    direction: selectedShot.value.direction,
    durationSeconds: selectedShot.value.durationSeconds,
    anchorMode: approvedAnchor ? "existing" : selectedShot.value.anchorMode,
    referenceBindings: bindings,
    inheritProjectReferences: selectedShot.value.inheritProjectReferences,
    sceneLookUsage: approvedAnchor && selectedShot.value.sceneLookUsage === "derive_anchor"
      ? "appearance_only"
      : selectedShot.value.sceneLookUsage,
  };
  await act(async () => {
    await api.updateShot(selectedShot.value!.id, draft);
    await reloadGenerationInputs();
  });
}

async function saveShotWorkspaceSettings(settings: {
  anchorMode: AnchorMode;
  sceneLookUsage: SceneLookUsage;
  inheritProjectReferences: boolean;
}) {
  if (!selectedShot.value) return;
  await act(async () => {
    await api.updateShot(selectedShot.value!.id, {
      title: selectedShot.value!.title,
      direction: selectedShot.value!.direction,
      durationSeconds: selectedShot.value!.durationSeconds,
      anchorMode: settings.anchorMode,
      referenceBindings: selectedShot.value!.referenceBindings,
      inheritProjectReferences: settings.inheritProjectReferences,
      sceneLookUsage: settings.sceneLookUsage,
    });
    await reloadGenerationInputs();
    ElMessage.success("片段开场与参考策略已保存");
  });
}

async function openSelectedShotEditor() {
  if (!selectedShot.value) return;
  editShot(selectedShot.value.sceneId, selectedShot.value);
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


function openSequenceBuilder() {
  if (!sequenceShots.value.length) {
    ElMessage.warning("请先为至少一个视频片段选择批准的视频版本");
    return;
  }
  sequenceTransitions.value = Object.fromEntries(
    sequenceShots.value.slice(1).map((shot) => [
      shot.id,
      { type: "cut", durationMs: 0 },
    ]),
  );
  sequenceBuilderVisible.value = true;
}

function updateSequenceTransitionType(shotId: string, value: string) {
  const type = value as SequenceTransitionDto["type"];
  sequenceTransitions.value[shotId] = {
    type,
    durationMs: type === "cut" ? 0 : 300,
  };
}

async function buildSequence() {
  if (!graph.value) return;
  const projectId = graph.value.project.id;
  const transitions = sequenceShots.value.slice(1).map((shot) => ({
    afterShotId: shot.id,
    transition: sequenceTransitions.value[shot.id] ?? { type: "cut", durationMs: 0 },
  }));
  sequenceBuilderVisible.value = false;
  await submitStudioTask(
    () => api.buildSequence(projectId, transitions),
    {
      kind: "build_sequence",
      label: "本地成片合成",
      operationKey: "sequence:build",
      projectId,
    },
  );
}

async function decideSequence(sequence: SequenceDto, approve: boolean) {
  if (!graph.value) return;
  await act(async () => {
    await api.selectSequence(graph.value!.project.id, sequence.id, approve);
    await loadGraph();
    if (approve) await showSequence(sequence.id);
  });
}

async function selectVideoVersion(asset: AssetDto, stale = false) {
  if (!selectedShot.value) return;
  if (stale) {
    await ElMessageBox.confirm(
      "该版本基于旧输入。它仍可使用，但可能与当前 Prompt 或参考图不一致，是否继续选择？",
      "选择历史版本",
      { type: "warning" },
    );
  }
  await act(async () => {
    await api.selectVersion(selectedShot.value!.id, asset.id);
    await loadGraph();
  });
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
watch(() => route.query.shot, (shotId) => {
  if (shotId && String(shotId) !== selectedShotId.value) void loadGraph();
});
watch(() => taskCenter.revision.value, () => {
  const event = taskCenter.lastEvent.value;
  if (event?.item.projectId === graph.value?.project.id) void loadGraph();
});
watch(() => shotForm.sceneLookUsage, (value) => {
  if (value === "derive_anchor") shotForm.anchorMode = "generate";
});
watch(() => shotForm.anchorMode, (value) => {
  if (value !== "generate" && shotForm.sceneLookUsage === "derive_anchor") {
    shotForm.sceneLookUsage = "appearance_only";
  }
});
onMounted(async () => {
  await act(async () => {
    await loadProjects();
    if (route.query.project) await loadGraph();
  });
});
</script>

<template>
  <div class="studio" v-loading="busy">
    <header class="studio-header">
      <div>
        <h1>视觉制作看板</h1>
        <p>分镜确认后，按场景视觉基准、片段开场、视频生成、审核衔接和成片顺序制作。</p>
      </div>
      <div class="header-actions">
        <el-select
          :model-value="graph?.project.id"
          filterable
          placeholder="选择项目"
          style="width: 220px"
          @update:model-value="selectProject(String($event))"
        >
          <el-option v-for="project in projects" :key="project.id" :label="project.title" :value="project.id" />
        </el-select>
        <el-button v-if="graph" @click="assetDrawerVisible = true">项目素材</el-button>
        <el-button type="primary" @click="createVisible = true">新建项目</el-button>
      </div>
    </header>

    <el-alert v-if="persistentError" type="error" :closable="false" show-icon class="persistent-alert">
      <template #title>操作未完成</template>
      {{ persistentError }}
    </el-alert>

    <section v-if="graph" class="production-shell">
      <div class="production-heading panel">
        <div>
          <span class="panel-title">当前项目</span>
          <h2>{{ graph.project.title }}</h2>
          <p>V5 · {{ graph.project.contentDate }} · {{ graph.scenes.length }} 个场景</p>
        </div>
        <div class="production-heading-actions">
          <el-button @click="editProjectSettings">项目设置</el-button>
          <el-button @click="restoreCanonReferences">恢复 Canon 引用</el-button>
          <el-button @click="editScene()">添加场景</el-button>
          <el-button type="success" @click="openSequenceBuilder">编排并合成已批准片段</el-button>
        </div>
      </div>

      <div class="production-steps panel" aria-label="视觉制作阶段">
        <el-steps :active="productionStage" align-center finish-status="success">
          <el-step v-for="label in productionStageLabels" :key="label" :title="label" />
        </el-steps>
      </div>

      <section v-for="(scene, sceneIndex) in graph.scenes" :key="scene.id" class="production-scene panel">
        <header class="scene-production-header">
          <div>
            <span class="order-chip">场景 {{ scene.order }}</span>
            <h2>{{ scene.title }}</h2>
            <p>{{ scene.storyMode === 'single' ? '单视频片段' : `${scene.targetShotCount} 个独立视频片段` }} · 每个片段独立生成与审核</p>
          </div>
          <div>
            <el-button text @click="moveScene(sceneIndex, -1)">上移</el-button>
            <el-button text @click="moveScene(sceneIndex, 1)">下移</el-button>
            <el-button text @click="editScene(scene)">编辑场景</el-button>
            <el-button text type="danger" @click="removeScene(scene)">删除</el-button>
          </div>
        </header>

        <details class="creative-summary" :open="!scene.shots.length">
          <summary>
            <span><b>剧情与分镜创作</b><small>{{ scene.shots.length ? '分镜已同步，可继续查看历史或重新设计' : '请先完成剧情与分镜' }}</small></span>
            <el-tag :type="scene.shots.length ? 'success' : 'warning'">{{ scene.shots.length ? '已建立片段' : '待完成' }}</el-tag>
          </summary>
          <p class="source-text">{{ scene.sourceText }}</p>
          <CreativeWorkflowPanel :scene="scene" :project-id="graph.project.id" @changed="loadGraph()" />
        </details>

        <div class="scene-production-grid">
          <SceneLookWorkbench
            :project-id="graph.project.id"
            :scene="scene"
            :assets="graph.assets"
            @refreshed="reloadGenerationInputs()"
          />
          <div class="scene-overview">
            <div class="overview-stat"><span>场景基准版本</span><b>{{ boardScene(scene.id)?.lookVersionCount ?? 0 }}</b></div>
            <div class="overview-stat"><span>视频片段</span><b>{{ scene.shots.length }}</b></div>
            <div class="overview-stat"><span>已批准视频</span><b>{{ scene.shots.filter(item => item.selectedVideoAssetId).length }}</b></div>
          </div>
        </div>

        <div class="shot-board-heading">
          <div><h3>视频片段制作</h3><p>每张卡只显示当前最重要的下一步；点击进入完整片段生成台。</p></div>
          <el-button @click="editShot(scene.id)">手工添加片段</el-button>
        </div>

        <div class="production-shot-grid">
          <div v-for="(shot, shotIndex) in scene.shots" :key="shot.id" class="shot-flow-item">
            <div v-if="shotIndex > 0" class="continuity-link">
              <span>上一片段尾帧</span><i>→</i><span>{{ boardShot(shot.id)?.state === 'needs_opening' ? '待确认开场' : '片段开场' }}</span>
            </div>
            <article class="production-shot-card" @click="selectShot(shot.id)">
              <div class="shot-preview">
                <template v-if="productionPreviewUrl(boardShot(shot.id))">
                  <img
                    v-if="productionPreviewAsset(boardShot(shot.id))?.mediaType === 'image'"
                    :src="productionPreviewUrl(boardShot(shot.id)) || undefined"
                  />
                  <video
                    v-else
                    muted
                    preload="metadata"
                    :src="productionPreviewUrl(boardShot(shot.id)) || undefined"
                  />
                </template>
                <div v-else><span>{{ shot.order }}</span><small>尚无开场图或视频</small></div>
                <el-tag class="shot-state" :type="boardShot(shot.id)?.state === 'approved' ? 'success' : boardShot(shot.id)?.state === 'stale' ? 'warning' : 'info'">
                  {{ boardShot(shot.id)?.stateLabel ?? '读取制作状态' }}
                </el-tag>
              </div>
              <div class="production-shot-content">
                <div class="shot-head"><b>{{ shot.order }}. {{ shot.title }}</b><el-tag size="small">{{ shot.durationSeconds }}s</el-tag></div>
                <p>{{ shot.direction }}</p>
                <div class="visual-slots" aria-label="实际视觉来源">
                  <span :class="{ ready: (boardShot(shot.id)?.referenceCounts.person ?? 0) > 0 }">人物</span>
                  <span :class="{ ready: (boardShot(shot.id)?.referenceCounts.cat ?? 0) > 0 }">猫咪</span>
                  <span :class="{ ready: (boardShot(shot.id)?.referenceCounts.style ?? 0) > 0 }">画风</span>
                  <span :class="{ ready: (boardShot(shot.id)?.referenceCounts.scene ?? 0) > 0 }">场景</span>
                  <span :class="{ ready: (boardShot(shot.id)?.referenceCounts.prop ?? 0) > 0 }">道具</span>
                  <span :class="{ ready: (boardShot(shot.id)?.referenceCounts.opening ?? 0) > 0 }">开场</span>
                </div>
                <div class="reference-counts">
                  <span>片段专用 {{ boardShot(shot.id)?.referenceCounts.custom ?? 0 }}</span>
                  <span>场景 {{ boardShot(shot.id)?.referenceCounts.scene ?? 0 }}</span>
                  <span>项目 {{ boardShot(shot.id)?.referenceCounts.project ?? 0 }}</span>
                  <b>实际提交 {{ boardShot(shot.id)?.referenceCounts.total ?? 0 }} 张</b>
                </div>
                <div class="version-counts">
                  <span>开场图 {{ boardShot(shot.id)?.anchorVersionCount ?? 0 }} 个版本</span>
                  <span>视频 {{ boardShot(shot.id)?.videoVersionCount ?? 0 }} 个版本</span>
                </div>
                <el-alert
                  v-if="boardShot(shot.id)?.blockers[0]"
                  type="warning"
                  :closable="false"
                  :title="boardShot(shot.id)!.blockers[0]"
                />
                <footer>
                  <div>
                    <el-button text size="small" @click.stop="moveShot(scene, shotIndex, -1)">↑</el-button>
                    <el-button text size="small" @click.stop="moveShot(scene, shotIndex, 1)">↓</el-button>
                    <el-button text size="small" @click.stop="editShot(scene.id, shot)">编辑</el-button>
                    <el-button text size="small" type="danger" @click.stop="removeShot(shot)">删除</el-button>
                  </div>
                  <el-button type="primary" @click.stop="selectShot(shot.id)">{{ boardShot(shot.id)?.primaryActionLabel ?? '打开片段生成台' }}</el-button>
                </footer>
              </div>
            </article>
          </div>
        </div>
      </section>
    </section>

    <section v-else class="empty-state panel project-empty">
      <div>
        <h2>从一个主题或一段剧情开始</h2>
        <p>创建项目后，先完成剧情与分镜，再进入场景视觉基准、片段开场和视频生成。</p>
        <el-button type="primary" @click="createVisible = true">创建第一个项目</el-button>
      </div>
    </section>

    <el-dialog
      v-model="shotWorkspaceVisible"
      fullscreen
      :show-close="false"
      destroy-on-close
      class="shot-workspace-dialog"
    >
      <ShotGenerationWorkspace
        v-if="selectedShot && selectedScene"
        :shot="selectedShot"
        :scene="selectedScene"
        :all-assets="graph?.assets ?? []"
        :selectable-assets="selectableAssets"
        :anchor-preview="anchorPromptPreview"
        :video-preview="videoPromptPreview"
        :reference-slots="generationWorkspace?.referenceSlots ?? null"
        :previous-tail="generationWorkspace?.previousTail ?? assistContext?.previousTail ?? null"
        :active-tasks="generationWorkspace?.activeTasks ?? []"
        :assist-context="assistContext"
        :assist-records="assistAnalyses"
        @close="closeShotWorkspace"
        @edit="openSelectedShotEditor"
        @generate="generate"
        @review="review"
        @select-version="selectVideoVersion"
        @resume="resumeAttempt"
        @reconcile="reconcileAttempt"
        @adopt-tail="adoptPreviousTail"
        @bind-reference="bindShotReference"
        @remove-binding="removeBinding"
        @save-settings="saveShotWorkspaceSettings"
        @apply-assistance="applyShotAssistance"
      />
    </el-dialog>

    <el-drawer v-model="assetDrawerVisible" title="当前项目素材" size="520px">
      <template v-if="graph">
        <el-alert type="info" :closable="false" title="全局 Canon 只提供长期人物、猫咪和画风；场景图、开场图和视频帧只属于当前项目。" />
        <div class="asset-upload-panel">
          <h3>上传可复用项目素材</h3>
          <el-input v-model="uploadForm.displayName" placeholder="素材名称，例如：伸缩鱼竿" />
          <div class="binding-row">
            <el-select v-model="uploadForm.role"><el-option label="画风" value="style" /><el-option label="道具" value="prop" /><el-option label="构图" value="composition" /></el-select>
            <input type="file" accept="image/*" @change="chooseUploadFile" />
          </div>
          <el-button type="primary" :disabled="!uploadForm.file" @click="uploadReference">上传项目素材</el-button>
        </div>
        <section class="drawer-assets">
          <h3>全局 Canon 引用</h3>
          <div class="drawer-asset-grid">
            <button
              v-for="asset in canonAssets"
              :key="asset.id"
              type="button"
              :class="{ selected: isProjectDefault(asset.id), missing: !asset.contentReady }"
              @click="toggleProjectDefault(asset)"
            >
              <img v-if="asset.contentReady" :src="assetContentUrl(asset.id)" />
              <span v-else>内容缺失</span>
              <b>{{ asset.displayName }}</b><small>{{ isProjectDefault(asset.id) ? '项目已引用' : '全局 Canon' }}</small>
            </button>
          </div>
        </section>
        <section v-if="projectReferenceAssets.length" class="drawer-assets">
          <h3>项目上传素材</h3>
          <div class="drawer-asset-grid">
            <button
              v-for="asset in projectReferenceAssets"
              :key="asset.id"
              type="button"
              :class="{ selected: isProjectDefault(asset.id) }"
              @click="toggleProjectDefault(asset)"
            >
              <img v-if="asset.contentReady" :src="assetContentUrl(asset.id)" />
              <span v-else>内容缺失</span>
              <b>{{ asset.displayName }}</b><small>{{ isProjectDefault(asset.id) ? '项目默认' : '项目素材' }}</small>
            </button>
          </div>
        </section>
        <section v-if="projectGeneratedAssets.length" class="drawer-assets">
          <h3>项目生成媒体</h3>
          <div class="drawer-asset-grid generated">
            <article v-for="asset in projectGeneratedAssets" :key="asset.id">
              <img v-if="asset.contentReady" :src="assetContentUrl(asset.id)" />
              <span v-else>内容缺失</span>
              <b>{{ asset.displayName }}</b><small>{{ asset.scope === 'scene' ? '场景专属' : '片段专属' }}</small>
            </article>
          </div>
        </section>
      </template>
    </el-drawer>

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
      :project-id="graph!.project.id"
      :scene-id="selectedShot.sceneId"
      :asset-id="selectedVideo.id"
      :src="assetContentUrl(selectedVideo.id)"
      :duration-ms="selectedVideoDurationMs"
      :direction="selectedShot.direction"
      :frames="selectedVideoFrames"
      :markers-ms="selectedVideoMarkersMs"
      @submitted="loadGraph()"
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

    <el-dialog v-model="sequenceBuilderVisible" title="成片编排与转场" width="760px">
      <el-alert type="info" :closable="false" title="各视频片段仍是独立生成结果；这里仅执行本地 FFmpeg 转场与组装，不调用 Ark。" />
      <div class="sequence-editor">
        <article v-for="(shot, index) in sequenceShots" :key="shot.id" class="sequence-editor-row">
          <div><b>{{ index + 1 }}. {{ shot.title }}</b><small>{{ shot.durationSeconds }} 秒 · {{ shot.selectedVideoAssetId }}</small></div>
          <template v-if="index > 0">
            <el-select
              :model-value="sequenceTransitions[shot.id]?.type ?? 'cut'"
              @update:model-value="updateSequenceTransitionType(shot.id, String($event))"
            >
              <el-option label="硬切（默认）" value="cut" />
              <el-option label="淡出黑场后进入" value="fade_black" />
              <el-option label="短叠化" value="cross_dissolve" />
            </el-select>
            <el-input-number
              v-if="sequenceTransitions[shot.id]?.type !== 'cut'"
              v-model="sequenceTransitions[shot.id]!.durationMs"
              :min="150"
              :max="1000"
              :step="50"
            />
          </template>
          <el-tag v-else>成片起点</el-tag>
        </article>
      </div>
      <template #footer><el-button @click="sequenceBuilderVisible = false">取消</el-button><el-button type="primary" @click="buildSequence">开始本地合成</el-button></template>
    </el-dialog>

    <el-dialog v-model="projectSettingsVisible" title="项目设置" width="860px">
      <el-form label-position="top">
        <el-form-item label="项目标题"><el-input v-model="projectSettingsForm.title" /></el-form-item>
        <el-form-item label="内容日期"><el-date-picker v-model="projectSettingsForm.contentDate" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-divider content-position="left">项目视觉档案</el-divider>
        <el-alert
          type="info"
          :closable="false"
          title="这里选择全局 Canon 作为本项目长期人物、猫咪和画风基准。保存会创建或复用不可变 Revision；场景视觉基准、锚点和视频帧不会写入该档案。"
        />
        <template v-if="projectProfileForm && projectProfile">
          <p class="source-hint">当前 Revision {{ projectProfile.revision }} · {{ projectProfile.profileHash.slice(0, 16) }}</p>
          <div class="look-grid project-profile-grid">
            <el-form-item label="人物身份"><el-input v-model="projectProfileForm.personIdentity" type="textarea" :rows="3" /></el-form-item>
            <el-form-item label="人物发型"><el-input v-model="projectProfileForm.personHair" type="textarea" :rows="3" /></el-form-item>
            <el-form-item label="年龄、体型与比例"><el-input v-model="projectProfileForm.personBody" type="textarea" :rows="3" /></el-form-item>
            <el-form-item label="猫咪身份"><el-input v-model="projectProfileForm.catIdentity" type="textarea" :rows="3" /></el-form-item>
            <el-form-item label="画风正向（每行一项）">
              <el-input :model-value="projectProfileForm.stylePositive.join('\n')" type="textarea" :rows="4" @update:model-value="updateProjectStyleList('positive', $event)" />
            </el-form-item>
            <el-form-item label="画风排除（每行一项）">
              <el-input :model-value="projectProfileForm.styleNegative.join('\n')" type="textarea" :rows="4" @update:model-value="updateProjectStyleList('negative', $event)" />
            </el-form-item>
          </div>
          <div class="profile-reference-heading">
            <b>全局 Canon 引用</b>
            <el-button size="small" @click="restoreProjectProfileDraft">恢复 Canon 默认草稿</el-button>
          </div>
          <el-checkbox-group v-model="projectProfileReferenceIds" class="profile-reference-grid">
            <el-checkbox v-for="asset in canonAssets" :key="asset.id" :value="asset.id" :disabled="!asset.contentReady">
              {{ asset.displayName }} · {{ asset.referencePurpose || canonPurpose(asset) }}
            </el-checkbox>
          </el-checkbox-group>
        </template>
      </el-form>
      <template #footer><el-button @click="projectSettingsVisible = false">取消</el-button><el-button type="primary" @click="saveProjectSettings">保存项目与新 Revision</el-button></template>
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
        <el-form-item label="场景视觉基准建议"><el-switch v-model="sceneForm.lookPlan.imageRecommended" active-text="建议生成（只提醒，不阻断）" /></el-form-item>
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
            <el-option label="关闭：不引用场景视觉基准" value="off" />
            <el-option label="只继承造型（默认）：忽略姿态、动作结果和构图" value="appearance_only" />
            <el-option label="完整参考：起始状态与视觉基准高度一致" value="full_reference" />
            <el-option label="派生锚点：视觉基准只用于生成本片段开场图" value="derive_anchor" />
          </el-select>
          <div class="source-hint">视频输入顺序：锚点 → 片段自定义 → 场景视觉基准 → 项目默认，再按资产 ID 和内容 SHA 去重。派生锚点获批后，场景视觉基准不会再次进入视频。</div>
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

  </div>
</template>

<style scoped>
.studio { min-height: 100%; background: #0d1016; color: #e8eaf0; padding: 22px; }
.studio-header, .shot-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.studio-header h1 { margin: 0; }.studio-header p { color: #9299a8; margin: 6px 0 0; }
.header-actions,.production-heading-actions,.scene-production-header,.shot-board-heading,.production-shot-card footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.production-shell { display: grid; gap: 14px; margin-top: 18px; }.production-heading { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 18px 20px; }.production-heading h2 { margin: 3px 0; }.production-heading p,.scene-production-header p,.shot-board-heading p { margin: 4px 0 0; color: #8c97a9; }.production-steps { padding: 18px 12px; }.production-scene { padding: 20px; }.scene-production-header h2 { display: inline; margin: 0 0 0 6px; }.scene-production-header { align-items: flex-start; }
.creative-summary { margin: 16px 0; border: 1px solid #2b3441; border-radius: 10px; background: #10151d; }.creative-summary > summary { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 13px 15px; cursor: pointer; list-style: none; }.creative-summary > summary span { display: grid; gap: 3px; }.creative-summary > summary small { color: #8793a6; }.creative-summary > .source-text,.creative-summary > :deep(.creative-workflow) { margin-left: 14px; margin-right: 14px; }.creative-summary[open] { padding-bottom: 14px; }
.scene-production-grid { display: grid; grid-template-columns: minmax(360px, 1fr) minmax(360px, .8fr); gap: 12px; align-items: stretch; }.scene-overview { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; }.overview-stat { display: grid; place-content: center; gap: 5px; min-height: 116px; text-align: center; border: 1px solid #2c3645; border-radius: 10px; background: #111821; }.overview-stat span { color: #8995a8; font-size: 12px; }.overview-stat b { font-size: 24px; }
.shot-board-heading { margin: 20px 0 10px; }.shot-board-heading h3 { margin: 0; }.production-shot-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }.shot-flow-item { min-width: 0; display: grid; gap: 7px; }.continuity-link { min-height: 28px; display: flex; align-items: center; justify-content: center; gap: 7px; color: #8492a7; font-size: 11px; }.continuity-link i { color: #5fa3f5; font-style: normal; }.production-shot-card { min-width: 0; height: 100%; display: grid; grid-template-rows: 190px 1fr; overflow: hidden; border: 1px solid #2c3747; border-radius: 12px; background: #101720; cursor: pointer; transition: border-color .15s ease, transform .15s ease; }.production-shot-card:hover { border-color: #4d96ff; transform: translateY(-1px); }.shot-preview { position: relative; display: grid; place-items: center; overflow: hidden; background: #080b10; }.shot-preview img,.shot-preview video { width: 100%; height: 100%; object-fit: cover; }.shot-preview > div { display: grid; gap: 6px; place-items: center; color: #8792a4; }.shot-preview > div > span { font-size: 36px; color: #41516a; }.shot-state { position: absolute; top: 10px; right: 10px; }.production-shot-content { display: grid; gap: 10px; padding: 13px; }.production-shot-content > p { margin: 0; max-height: 86px; overflow: hidden; color: #b7c0ce; font-size: 13px; line-height: 1.65; white-space: pre-wrap; }.visual-slots,.reference-counts,.version-counts { display: flex; gap: 6px; flex-wrap: wrap; }.visual-slots span { padding: 4px 7px; border: 1px solid #303a49; border-radius: 6px; color: #6f7b8e; font-size: 11px; }.visual-slots span.ready { color: #bfe3cb; border-color: #2f6547; background: #12251d; }.reference-counts span,.version-counts span { color: #8794a7; font-size: 11px; }.reference-counts b { margin-left: auto; color: #dce4ef; font-size: 11px; }
.asset-upload-panel,.drawer-assets { display: grid; gap: 10px; margin-top: 16px; }.drawer-asset-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }.drawer-asset-grid button,.drawer-asset-grid article { min-width: 0; display: grid; gap: 5px; padding: 7px; text-align: left; color: #dce4ef; background: #101721; border: 1px solid #2d3746; border-radius: 8px; cursor: pointer; }.drawer-asset-grid button.selected { border-color: #409eff; }.drawer-asset-grid button.missing { border-color: #9a6732; }.drawer-asset-grid img,.drawer-asset-grid button > span,.drawer-asset-grid article > span { width: 100%; height: 112px; object-fit: cover; border-radius: 5px; background: #080b10; }.drawer-asset-grid button > span,.drawer-asset-grid article > span { display: grid; place-items: center; color: #d09a61; }.drawer-asset-grid small { color: #7f8da0; font-size: 10px; }.drawer-asset-grid.generated { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.shot-workspace-dialog :deep(.el-dialog__header) { display: none; }.shot-workspace-dialog :deep(.el-dialog__body) { padding: 0; }
.persistent-alert { margin: 16px 0; }
.panel { background: #151922; border: 1px solid #292f3b; border-radius: 12px; }
.panel-title { color: #8c95a7; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; }.reference-title { margin-top: 22px; }
.order-chip { color: #68a8ff; margin-right: 10px; }.source-text { color: #aeb5c3; line-height: 1.7; white-space: pre-wrap; }
.binding-row { display: flex; gap: 8px; margin: 8px 0; } pre { white-space: pre-wrap; word-break: break-word; max-height: 320px; overflow: auto; background: #0c0f15; padding: 10px; border-radius: 8px; color: #cdd3dd; }
.source-hint { color: #8791a2; font-size: 11px; line-height: 1.5; }.empty-state { text-align: center; color: #8f98a7; padding: 80px 20px; }.project-empty { display: grid; min-height: 520px; margin-top: 18px; place-items: center; }
.master-timeline summary { color: #8fa7c9; cursor: pointer; font-size: 12px; }.sequence-panel, .master-timeline { margin-top: 16px; padding: 16px; }.sequence-heading h3, .master-timeline h3 { margin: 0; }.sequence-heading p, .master-timeline p { color: #9299a8; }.sequence-grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }.sequence-card { border: 1px solid #2b313d; border-radius: 9px; padding: 10px; display: grid; gap: 8px; }.sequence-card.selected { border-color: #4d96ff; }.sequence-open { border: 0; background: transparent; color: #e8eaf0; text-align: left; cursor: pointer; display: grid; gap: 4px; }.sequence-open span { color: #8490a3; font-size: 12px; }.master-timeline video { width: 100%; max-height: 560px; background: #080a0e; }
.mode-row { align-items: end; }.look-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }.suggestion-summary, .suggestion-shot-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }.suggestion-shot { padding: 14px; margin: 10px 0; border: 1px solid #2b313d; border-radius: 9px; background: #10141b; }.suggestion-shot-head { margin-bottom: 10px; }
.assist-candidate-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 12px 0; }.assist-candidate-grid label { display: grid; grid-template-columns: auto 64px 1fr; gap: 7px; align-items: center; padding: 7px; border: 1px solid #303847; border-radius: 8px; }.assist-candidate-grid label.disabled { opacity: .48; }.assist-candidate-grid img { width: 64px; height: 64px; object-fit: cover; }.assist-candidate-grid span { display: grid; font-size: 12px; }.assist-candidate-grid small { color: #8791a2; }
.shot-local-findings { display: grid; gap: 6px; width: 100%; }
.profile-reference-heading { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin: 12px 0 8px; }.profile-reference-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px 16px; }
.sequence-editor { display: grid; gap: 9px; margin-top: 14px; }.sequence-editor-row { display: grid; grid-template-columns: minmax(0, 1fr) 180px 150px; align-items: center; gap: 10px; padding: 10px; border: 1px solid #2c3645; border-radius: 8px; }.sequence-editor-row div { display: grid; gap: 4px; }.sequence-editor-row small { color: #8791a2; }
@media (max-width: 1280px) { .scene-production-grid { grid-template-columns: 1fr; }.production-shot-grid { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); } }
</style>
