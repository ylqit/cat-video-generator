<script setup lang="ts">
import { Background } from "@vue-flow/background";
import {
  Handle,
  Position,
  VueFlow,
  useVueFlow,
  type Connection,
  type Edge,
  type Node,
} from "@vue-flow/core";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";
import {
  Aim,
  Connection as ConnectionIcon,
  EditPen,
  MagicStick,
  Plus,
  Refresh,
  Timer,
} from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  shallowRef,
  watch,
} from "vue";

import { assetContentUrl, canvasApi } from "../../api/client";
import type {
  CanvasDto,
  CanvasEdgeDto,
  CanvasAssetHistoryDto,
  CanvasNodeDto,
  CanvasNodeType,
  CanvasPortType,
  PromptRunDto,
  ActualReferenceBindingDto,
  GenerationCapabilityDto,
  ProviderCapabilityDto,
  StoryBriefInput,
  SubjectCompletionRunDto,
  SubjectInput,
} from "../../api/types";
import CanvasNodeLibrary from "./CanvasNodeLibrary.vue";
import CanvasNodeCard from "./CanvasNodeCard.vue";
import NodeGenerationComposer from "./NodeGenerationComposer.vue";
import PromptTraceDrawer from "./PromptTraceDrawer.vue";
import SubjectAssistantPanel from "./SubjectAssistantPanel.vue";
import VideoEditWorkspace from "./VideoEditWorkspace.vue";
import { canvasSyncQueue } from "./canvasSync";

const props = defineProps<{ projectId: string }>();
const canvas = ref<CanvasDto | null>(null);
const flowNodes = shallowRef<Node[]>([]);
const flowEdges = shallowRef<Edge[]>([]);
const loading = ref(false);
const busyAction = ref("");
const syncStatus = ref<CanvasDto["syncStatus"]>("saved");
const promptVisible = ref(false);
const promptLoading = ref(false);
const selectedPrompt = ref<PromptRunDto | null>(null);
const briefVisible = ref(false);
const subjectVisible = ref(false);
const beatVisible = ref(false);
const batchVisible = ref(false);
const batchNode = ref<CanvasNodeDto | null>(null);
const nodeLibraryVisible = ref(false);
const assetHistoryVisible = ref(false);
const assetHistoryKind = ref<"image" | "video" | "audio" | undefined>();
const assetHistory = ref<CanvasAssetHistoryDto[]>([]);
const assetHistoryLoading = ref(false);
const assetHistoryError = ref("");
const subjectAssistantVisible = ref(false);
const subjectAssistantNode = ref<CanvasNodeDto | null>(null);
const subjectAssistantRun = ref<SubjectCompletionRunDto | null>(null);
const subjectAssistantLoading = ref(false);
const generationComposerNode = ref<CanvasNodeDto | null>(null);
const generationCapability = ref<GenerationCapabilityDto | null>(null);
const generationReferences = ref<ActualReferenceBindingDto[]>([]);
const generationLoading = ref(false);
const videoEditorNode = ref<CanvasNodeDto | null>(null);
const editingBeat = ref<CanvasNodeDto | null>(null);
const eventSource = ref<EventSource | null>(null);
const { fitView, setViewport } = useVueFlow();

const briefForm = reactive<StoryBriefInput>({
  theme: "",
  audience: "亲子与泛生活观众",
  genre: "治愈生活短剧",
  tone: "温暖、紧凑、可视化",
  aspectRatio: "9:16",
  targetDurationSeconds: 60,
  constraints: [],
});
const subjectForm = reactive<SubjectInput>({
  name: "",
  kind: "person",
  role: "protagonist",
  identityAnchors: [""],
  immutableTraits: [""],
  relationshipNotes: "",
  dramaticFunction: "",
  visualRisks: [],
  references: [],
});
const beatForm = reactive({
  title: "",
  action: "",
  camera: "",
  dialogue: "",
  durationSeconds: 4,
});
const batchForm = reactive({ prompt: "", candidateCount: 4 });

const narrativeSubjects = computed(() => (
  canvas.value?.nodes.filter((node) => node.type === "SubjectNode" && [
    "protagonist",
    "co_protagonist",
    "support",
  ].includes(String(node.data.role))) ?? []
));
const missingSubjectCount = computed(() => Math.max(0, 2 - narrativeSubjects.value.length));
const isShortDrama = computed(() => (canvas.value?.templateKey ?? "short_drama") === "short_drama");
const isProductAd = computed(() => canvas.value?.templateKey === "product_ad");
const productBatchNode = computed(() => canvas.value?.nodes.find(
  (node) => node.type === "GenerationBatchNode",
) ?? null);
const approvedStory = computed(() => canvas.value?.nodes.find(
  (node) => node.type === "StoryCandidateNode" && node.data.status === "approved",
));
const syncLabel = computed(() => ({
  local: "本地待同步",
  syncing: "同步中",
  saved: "已保存",
  conflict: "保存冲突",
  offline: "离线",
  service_error: "服务异常",
})[syncStatus.value]);

const inputPorts: Partial<Record<CanvasNodeType, CanvasPortType[]>> = {
  StoryPlannerNode: ["brief", "subject[]"],
  StoryCandidateNode: ["story_revision"],
  StoryCriticNode: ["story_revision"],
  ApprovalGateNode: ["story_revision"],
  StoryboardDirectorNode: ["story_revision", "subject[]"],
  SceneNode: ["scene_plan"],
  ShotBeatNode: ["shot_beat[]", "subject[]"],
  ReviewNode: ["image_asset", "video_asset"],
  TimelineNode: ["approved_asset"],
  GenerationBatchNode: ["product_subject", "media_reference[]"],
  ImageAssetNode: ["image_asset[]"],
  VideoAssetNode: ["video_asset"],
  VideoEditNode: ["video_asset", "media_reference[]"],
  VideoSegmentNode: ["edit_recipe"],
  ImageGenerationNode: ["prompt", "shot_beat[]", "subject[]", "image_reference[]"],
  VideoGenerationNode: ["prompt", "shot_beat[]", "subject[]", "image_reference[]", "image_asset"],
  AudioGenerationNode: ["prompt"],
};
const outputPorts: Partial<Record<CanvasNodeType, CanvasPortType[]>> = {
  BriefNode: ["brief"],
  SubjectNode: ["subject[]", "product_subject"],
  StoryPlannerNode: ["story_revision"],
  StoryCandidateNode: ["story_revision"],
  StoryCriticNode: ["story_revision"],
  ApprovalGateNode: ["story_revision"],
  StoryboardDirectorNode: ["scene_plan"],
  SceneNode: ["shot_beat[]"],
  ShotBeatNode: ["shot_beat[]"],
  ImageGenerationNode: ["image_asset"],
  VideoGenerationNode: ["video_asset"],
  ReviewNode: ["approved_asset"],
  ReferenceAssetNode: ["media_reference[]"],
  GenerationBatchNode: ["image_asset[]"],
  ImageAssetNode: ["image_asset", "media_reference[]"],
  VideoAssetNode: ["video_asset"],
  VideoEditNode: ["edit_recipe"],
  VideoSegmentNode: ["video_asset"],
  PromptArtifactNode: ["prompt"],
  AudioGenerationNode: ["audio_asset"],
};

function inputPortsFor(type: unknown): CanvasPortType[] {
  return inputPorts[type as CanvasNodeType] ?? [];
}

function outputPortsFor(type: unknown): CanvasPortType[] {
  return outputPorts[type as CanvasNodeType] ?? [];
}

async function loadCanvas(focus = false) {
  loading.value = true;
  try {
    const loaded = await canvasApi.canvas(props.projectId);
    canvas.value = loaded;
    syncStatus.value = loaded.syncStatus;
    flowNodes.value = loaded.nodes.map((node) => ({
      id: node.id,
      type: "canvas",
      position: node.position,
      data: { node },
    }));
    const pendingLayout = canvasSyncQueue.pending(props.projectId).at(-1);
    if (pendingLayout && Array.isArray(pendingLayout.nodes)) {
      const positions = new Map(
        (pendingLayout.nodes as Array<{ nodeId: string; x: number; y: number }>).map(
          (item) => [item.nodeId, { x: item.x, y: item.y }],
        ),
      );
      flowNodes.value = flowNodes.value.map((node) => ({
        ...node,
        position: positions.get(node.id) ?? node.position,
      }));
      syncStatus.value = navigator.onLine ? "local" : "offline";
    }
    flowEdges.value = loaded.edges.map((edge) => ({
      id: edge.id ?? `${edge.sourceNodeId}-${edge.targetNodeId}-${edge.sourcePort}`,
      source: edge.sourceNodeId,
      target: edge.targetNodeId,
      sourceHandle: edge.sourcePort,
      targetHandle: edge.targetPort,
      class: "typed-edge",
    }));
    const brief = loaded.nodes.find((node) => node.type === "BriefNode");
    if (brief) Object.assign(briefForm, brief.data);
    if (focus) focusCanvas(true);
  } catch (error) {
    syncStatus.value = navigator.onLine ? "service_error" : "offline";
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    loading.value = false;
  }
}

async function focusCanvas(initialProductStage = false) {
  await nextTick();
  const xValues = flowNodes.value.map((node) => node.position.x);
  const yValues = flowNodes.value.map((node) => node.position.y);
  const minX = Math.min(...xValues);
  const maxX = Math.max(...xValues) + 380;
  const minY = Math.min(...yValues);
  const maxY = Math.max(...yValues) + 190;
  if (initialProductStage && isProductAd.value) {
    const zoom = 0.7;
    await setViewport(
      { x: 42 - minX * zoom, y: 118 - minY * zoom, zoom },
      { duration: 260 },
    );
    return;
  }
  const zoom = Math.max(0.18, Math.min(
    1.1,
    (window.innerWidth - 320) / (maxX - minX),
    (window.innerHeight - 250) / (maxY - minY),
  ));
  await setViewport(
    { x: 44 - minX * zoom, y: 108 - minY * zoom, zoom },
    { duration: 260 },
  );
}

async function runStories() {
  if (missingSubjectCount.value) return;
  await ElMessageBox.confirm(
    "将调用 3 次故事策划和 3 次独立评审。所有 Prompt 会先落库；不会自动生成图片或视频。",
    "确认生成三套故事",
    { confirmButtonText: "确认生成", cancelButtonText: "取消" },
  );
  busyAction.value = "stories";
  try {
    await canvasApi.runStoryStrategies(props.projectId);
    await loadCanvas(true);
    ElMessage.success("三套故事与评分已生成，请人工选择或继续改写");
  } finally {
    busyAction.value = "";
  }
}

async function approveStory(revisionId: string) {
  await ElMessageBox.confirm(
    "批准后将冻结该 StoryRevision；以后修改会创建新版本，不会自动触发媒体生成。",
    "批准故事定稿",
  );
  await canvasApi.approveStory(revisionId);
  await loadCanvas();
}

async function createStoryboard() {
  if (!approvedStory.value) return;
  await ElMessageBox.confirm(
    "将按简报总时长编译场景与独立 Beat。此操作不会调用图片或视频模型。",
    "生成可编辑分镜",
  );
  busyAction.value = "storyboard";
  try {
    await canvasApi.createStoryboard(props.projectId);
    await loadCanvas(true);
  } finally {
    busyAction.value = "";
  }
}

async function inspectPrompt(promptId: string) {
  promptVisible.value = true;
  promptLoading.value = true;
  selectedPrompt.value = null;
  try {
    selectedPrompt.value = await canvasApi.promptRun(promptId);
  } finally {
    promptLoading.value = false;
  }
}

async function saveBrief() {
  await canvasApi.saveBrief(props.projectId, {
    ...briefForm,
    constraints: briefForm.constraints.filter(Boolean),
  });
  briefVisible.value = false;
  await loadCanvas(true);
}

async function createSubject() {
  await canvasApi.createSubject(props.projectId, {
    ...subjectForm,
    identityAnchors: subjectForm.identityAnchors.filter(Boolean),
    immutableTraits: subjectForm.immutableTraits.filter(Boolean),
  });
  subjectVisible.value = false;
  Object.assign(subjectForm, {
    name: "",
    identityAnchors: [""],
    immutableTraits: [""],
    relationshipNotes: "",
    dramaticFunction: "",
  });
  await loadCanvas(true);
}

function editObject(node: CanvasNodeDto) {
  if (node.type !== "ShotBeatNode") return;
  editingBeat.value = node;
  Object.assign(beatForm, node.data);
  beatVisible.value = true;
}

async function saveBeat() {
  if (!editingBeat.value?.objectId) return;
  await canvasApi.updateBeat(
    editingBeat.value.objectId,
    Number(editingBeat.value.data.revision),
    { ...beatForm },
  );
  beatVisible.value = false;
  await loadCanvas();
}

function openGenerationBatch(node: CanvasNodeDto) {
  batchNode.value = node;
  batchForm.prompt = String(node.data.prompt ?? "");
  batchForm.candidateCount = Number(node.data.candidateCount ?? 4);
  batchVisible.value = true;
}

async function generateBatch() {
  if (!batchNode.value || !batchForm.prompt.trim()) {
    ElMessage.error("请先描述候选图片的构图、光线与产品动作");
    return;
  }
  await ElMessageBox.confirm(
    `将生成 ${batchForm.candidateCount} 个独立图片候选；未选择候选不会自动生成视频。`,
    "确认创建图片生成批次",
    { confirmButtonText: "确认并排队", cancelButtonText: "取消" },
  );
  await canvasApi.createGenerationBatch({
    projectId: props.projectId,
    canvasNodeId: batchNode.value.id,
    mediaKind: "image",
    candidateCount: batchForm.candidateCount,
    idempotencyKey: crypto.randomUUID(),
    input: { prompt: batchForm.prompt.trim() },
  });
  batchVisible.value = false;
  await loadCanvas();
  ElMessage.success("图片批次已进入持久任务队列");
}

async function promoteCandidate(
  batch: CanvasNodeDto,
  candidate: Record<string, unknown>,
) {
  const assetId = String(candidate.assetId ?? candidate.id ?? "");
  if (!assetId) {
    ElMessage.error("候选尚未生成可追溯资产");
    return;
  }
  const created = await canvasApi.createNode(props.projectId, {
    nodeType: "ImageAssetNode",
    objectType: "asset",
    objectId: assetId,
    data: {
      title: candidate.title ?? "已选择图片候选",
      assetId,
      thumbnailUrl: candidate.thumbnailUrl,
      promptId: candidate.promptId,
      status: "candidate",
    },
  });
  await canvasApi.createEdge(props.projectId, {
    sourceNodeId: batch.id,
    sourceNodeType: "GenerationBatchNode",
    sourcePort: "image_asset[]",
    targetNodeId: String(created.id),
    targetNodeType: "ImageAssetNode",
    targetPort: "image_asset[]",
  });
  await loadCanvas(true);
}

function openVideoEditor(node: CanvasNodeDto) {
  videoEditorNode.value = node;
}

function mediaAssetId(node: CanvasNodeDto): string {
  return String(node.data.assetId ?? node.objectId ?? node.id);
}

function mediaContentUrl(node: CanvasNodeDto): string {
  return String(node.data.contentUrl ?? assetContentUrl(mediaAssetId(node)));
}

const videoEditReferences = computed(() => (
  canvas.value?.nodes
    .filter((node) => ["ReferenceAssetNode", "ImageAssetNode", "SubjectNode"].includes(node.type))
    .filter((node) => node.data.thumbnailUrl && (node.data.assetId || node.objectId))
    .map((node) => ({
      id: String(node.data.assetId ?? node.objectId),
      title: String(node.data.title ?? node.data.name ?? "参考素材"),
      thumbnailUrl: String(node.data.thumbnailUrl),
      semanticRole: String(node.data.semanticRole ?? node.data.kind ?? "reference"),
    })) ?? []
));

async function createCatalogNode(type: CanvasNodeType) {
  if (type === "BriefNode") {
    briefVisible.value = true;
    nodeLibraryVisible.value = false;
    return;
  }
  if (type === "SubjectNode") {
    subjectVisible.value = true;
    nodeLibraryVisible.value = false;
    return;
  }
  const title = ({
    StoryPlannerNode: "故事策划",
    StoryCandidateNode: "故事候选",
    StoryCriticNode: "故事评审",
    ApprovalGateNode: "人工审批",
    StoryboardDirectorNode: "分镜导演",
    SceneNode: "场景",
    ShotBeatNode: "镜头 Beat",
    ReviewNode: "人工审核",
    TimelineNode: "时间线",
    GenerationBatchNode: "图片候选批次",
    ImageGenerationNode: "图片生成",
    ImageAssetNode: "图片资产",
    VideoGenerationNode: "视频生成",
    VideoAssetNode: "视频资产",
    VideoEditNode: "视频重编",
    AudioGenerationNode: "音频生成",
    PromptArtifactNode: "Prompt 产物",
    ReferenceAssetNode: "上传 / 引用素材",
  } as Partial<Record<CanvasNodeType, string>>)[type] ?? type;
  await canvasApi.createNode(props.projectId, {
    nodeType: type,
    objectType: type.replace(/Node$/, "").replace(/[A-Z]/g, (value) => `_${value.toLowerCase()}`).replace(/^_/, ""),
    data: {
      title,
      status: "draft",
      ...(type === "GenerationBatchNode" ? { candidateCount: 4, candidates: [] } : {}),
    },
  });
  nodeLibraryVisible.value = false;
  await loadCanvas(true);
}

async function openAssetHistory(kind?: "image" | "video" | "audio") {
  assetHistoryVisible.value = true;
  assetHistoryKind.value = kind;
  assetHistoryLoading.value = true;
  assetHistoryError.value = "";
  try {
    assetHistory.value = await canvasApi.assets(props.projectId, kind);
  } catch (error) {
    assetHistoryError.value = error instanceof Error ? error.message : String(error);
  } finally {
    assetHistoryLoading.value = false;
  }
}

function subjectDraft(node: CanvasNodeDto): SubjectInput {
  return {
    name: String(node.data.name ?? node.data.title ?? "未命名主体"),
    kind: (node.data.kind ?? "object") as SubjectInput["kind"],
    role: (node.data.role ?? "support") as SubjectInput["role"],
    identityAnchors: Array.isArray(node.data.identityAnchors) ? [...node.data.identityAnchors] as string[] : [],
    immutableTraits: Array.isArray(node.data.immutableTraits) ? [...node.data.immutableTraits] as string[] : [],
    relationshipNotes: String(node.data.relationshipNotes ?? ""),
    dramaticFunction: String(node.data.dramaticFunction ?? ""),
    visualRisks: Array.isArray(node.data.visualRisks) ? [...node.data.visualRisks] as string[] : [],
    references: Array.isArray(node.data.references)
      ? (node.data.references as NonNullable<SubjectInput["references"]>).map((item) => ({ ...item }))
      : [],
  };
}

function openSubjectAssistant(node: CanvasNodeDto) {
  if (!node.objectId) {
    ElMessage.error("主体节点尚未绑定可版本化的领域主体");
    return;
  }
  subjectAssistantNode.value = node;
  subjectAssistantRun.value = null;
  subjectAssistantVisible.value = true;
}

async function runSubjectAssistant(instruction: string) {
  const node = subjectAssistantNode.value;
  if (!node?.objectId) return;
  subjectAssistantLoading.value = true;
  try {
    subjectAssistantRun.value = await canvasApi.createSubjectCompletionRun(
      props.projectId,
      node.objectId,
      instruction,
    );
    ElMessage.success("主体分析已进入持久任务队列，建议不会自动覆盖当前版本");
  } finally {
    subjectAssistantLoading.value = false;
  }
}

async function refreshSubjectAssistantRun() {
  if (!subjectAssistantRun.value) return;
  subjectAssistantRun.value = await canvasApi.subjectCompletionRun(subjectAssistantRun.value.id);
}

async function applySubjectAssistant(payload: { acceptedFields: string[]; finalDraft: SubjectInput }) {
  if (!subjectAssistantRun.value) return;
  subjectAssistantLoading.value = true;
  try {
    await canvasApi.applySubjectCompletion(
      subjectAssistantRun.value.id,
      payload.acceptedFields,
      payload.finalDraft,
    );
    subjectAssistantVisible.value = false;
    await loadCanvas();
    ElMessage.success("已按人工选择创建新的不可变 SubjectRevision");
  } finally {
    subjectAssistantLoading.value = false;
  }
}

async function openGenerationComposer(node: CanvasNodeDto) {
  generationComposerNode.value = node;
  generationCapability.value = null;
  generationReferences.value = [];
  const mediaKind = node.type === "VideoGenerationNode" ? "video"
    : node.type === "AudioGenerationNode" ? "audio" : "image";
  generationLoading.value = true;
  try {
    const rows = await canvasApi.providerCapabilities(mediaKind);
    const row = rows[0];
    if (!row) {
      ElMessage.error(`没有启用的 ${mediaKind} ProviderCapability`);
      return;
    }
    const raw = row.capabilities as Partial<GenerationCapabilityDto> & { maxReferenceImages?: number };
    generationCapability.value = {
      provider: row.provider,
      model: row.model,
      modes: raw.modes?.length ? raw.modes : [mediaKind === "video" ? "text_to_video" : mediaKind === "audio" ? "audio" : "text_to_image"],
      aspectRatios: raw.aspectRatios?.length ? raw.aspectRatios : ["9:16", "16:9"],
      resolutions: raw.resolutions?.length ? raw.resolutions : ["720p"],
      durations: raw.durations?.length ? raw.durations : [mediaKind === "video" ? 8 : 1],
      candidateCounts: raw.candidateCounts?.length ? raw.candidateCounts : [1],
      audio: Boolean(raw.audio),
    };
    const incomingIds = new Set(
      canvas.value?.edges.filter((edge) => edge.targetNodeId === node.id).map((edge) => edge.sourceNodeId),
    );
    const sources = canvas.value?.nodes.filter((item) => incomingIds.has(item.id)) ?? [];
    const candidates: Array<{ assetId: string; subjectRevisionId?: string; semanticRole: string }> = [];
    for (const source of sources) {
      if (["ReferenceAssetNode", "ImageAssetNode"].includes(source.type)) {
        const assetId = String(source.data.assetId ?? source.objectId ?? "");
        if (assetId) candidates.push({ assetId, semanticRole: String(source.data.semanticRole ?? "reference") });
      }
      if (source.type === "SubjectNode" && Array.isArray(source.data.references)) {
        for (const reference of source.data.references as Array<Record<string, unknown>>) {
          if (reference.assetId) candidates.push({
            assetId: String(reference.assetId),
            subjectRevisionId: String(source.data.revisionId ?? source.objectId ?? "") || undefined,
            semanticRole: String(reference.semanticRole ?? source.data.kind ?? "subject"),
          });
        }
      }
    }
    const maxReferences = Math.max(0, Number(raw.maxReferenceImages ?? 9));
    generationReferences.value = candidates.map((item, index) => ({
      ...item,
      providerIncluded: index < maxReferences,
      omissionReason: index < maxReferences ? null : `当前 ${row.model} 最多接受 ${maxReferences} 张参考图`,
    }));
  } finally {
    generationLoading.value = false;
  }
}

async function submitNodeGeneration(payload: Record<string, unknown>) {
  const node = generationComposerNode.value;
  const config = payload.config as Record<string, unknown>;
  if (!node || !config) return;
  if (node.type === "AudioGenerationNode") {
    ElMessage.error("首期 Ark 尚未启用音频生成能力，配置可见但不会静默提交");
    return;
  }
  await ElMessageBox.confirm(
    "将先保存精确 Prompt、实际引用、参数和幂等键，再由 PostgreSQL Worker 提交供应商。",
    "确认节点生成",
  );
  generationLoading.value = true;
  try {
    await canvasApi.saveNodeGenerationConfig(node.id, node.revision ?? 1, config);
    const mediaKind = node.type === "VideoGenerationNode" ? "video" : "image";
    await canvasApi.createGenerationBatch({
      projectId: props.projectId,
      canvasNodeId: node.id,
      mediaKind,
      candidateCount: Number(config.candidateCount ?? 1),
      provider: String(config.provider),
      model: String(config.model),
      idempotencyKey: crypto.randomUUID(),
      input: { prompt: String(payload.prompt), generationConfig: config },
    });
    generationComposerNode.value = null;
    await loadCanvas();
    ElMessage.success(`${mediaKind === "video" ? "视频" : "图片"}生成已进入持久任务队列`);
  } finally {
    generationLoading.value = false;
  }
}

function autoLayout() {
  const columns: Partial<Record<CanvasNodeType, number>> = {
    BriefNode: 0,
    SubjectNode: 0,
    StoryPlannerNode: 1,
    StoryCandidateNode: 2,
    ApprovalGateNode: 3,
    StoryboardDirectorNode: 4,
    SceneNode: 5,
    ShotBeatNode: 6,
    ReferenceAssetNode: 0,
    GenerationBatchNode: 1,
    ImageAssetNode: 2,
    VideoGenerationNode: 3,
    VideoAssetNode: 4,
    VideoEditNode: 5,
    VideoSegmentNode: 6,
    ReviewNode: 7,
    TimelineNode: 8,
  };
  const rows = new Map<number, number>();
  flowNodes.value = flowNodes.value.map((flowNode) => {
    const node = flowNode.data.node as CanvasNodeDto;
    const column = columns[node.type] ?? 7;
    const row = rows.get(column) ?? 0;
    rows.set(column, row + 1);
    return { ...flowNode, position: { x: 90 + column * 360, y: 100 + row * 230 } };
  });
  syncStatus.value = "local";
  void persistLayout("auto_layout");
  requestAnimationFrame(() => void fitView({ padding: 0.15, duration: 360 }));
}

async function connect(connection: Connection) {
  if (!canvas.value || !connection.source || !connection.target) return;
  const source = canvas.value.nodes.find((node) => node.id === connection.source);
  const target = canvas.value.nodes.find((node) => node.id === connection.target);
  const sourcePort = connection.sourceHandle as CanvasPortType | null;
  const targetPort = connection.targetHandle as CanvasPortType | null;
  if (!source || !target || !sourcePort || !targetPort || !portsCompatible(sourcePort, targetPort)) {
    ElMessage.error("端口类型不兼容，连接已拒绝");
    return;
  }
  const edge: Omit<CanvasEdgeDto, "id"> = {
    sourceNodeId: source.id,
    sourceNodeType: source.type,
    sourcePort,
    targetNodeId: target.id,
    targetNodeType: target.type,
    targetPort,
  };
  try {
    const stored = await canvasApi.createEdge(props.projectId, edge);
    canvas.value.edges.push(stored);
    flowEdges.value.push({
      id: stored.id ?? crypto.randomUUID(),
      source: source.id,
      target: target.id,
      sourceHandle: sourcePort,
      targetHandle: targetPort,
    });
    syncStatus.value = "saved";
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

function portsCompatible(source: CanvasPortType, target: CanvasPortType): boolean {
  return source === target || (
    source === "image_asset" && ["image_reference[]", "image_asset"].includes(target)
  );
}

async function persistLayout(operationType: string) {
  if (!canvas.value) return;
  const operation = {
    operationId: crypto.randomUUID(),
    type: operationType,
    createdAt: new Date().toISOString(),
    nodes: flowNodes.value.map((node) => ({
      nodeId: node.id,
      x: node.position.x,
      y: node.position.y,
    })),
  };
  canvasSyncQueue.enqueue(props.projectId, operation);
  if (!navigator.onLine) {
    syncStatus.value = "offline";
    return;
  }
  syncStatus.value = "syncing";
  try {
    const viewport = { x: 0, y: 0, zoom: 1 };
    const saved = await canvasApi.saveLayout(
      props.projectId,
      canvas.value.layoutVersion,
      {
        nodes: flowNodes.value.map((node) => ({
          nodeId: node.id,
          x: node.position.x,
          y: node.position.y,
        })),
        edges: canvas.value.edges,
        viewport,
        operations: canvasSyncQueue.pending(props.projectId),
      },
    );
    canvas.value.layoutVersion = saved.layoutVersion;
    canvasSyncQueue.confirm(
      props.projectId,
      canvasSyncQueue.pending(props.projectId).map((item) => item.operationId),
    );
    syncStatus.value = "saved";
  } catch (error) {
    syncStatus.value = navigator.onLine ? "conflict" : "offline";
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

async function replayPendingLayout() {
  if (!canvasSyncQueue.pending(props.projectId).length) {
    await loadCanvas();
    return;
  }
  await persistLayout("replay_offline_operations");
}

function nodeDragStop() {
  syncStatus.value = "local";
  void persistLayout("move_node");
}

function connectEvents() {
  if (typeof EventSource === "undefined") return;
  eventSource.value?.close();
  const source = new EventSource(canvasApi.eventsUrl(props.projectId));
  for (const eventName of [
    "workflow_changed",
    "template_instantiated",
    "canvas_node_created",
    "canvas_edge_created",
    "canvas_edge_deleted",
    "generation_batch_queued",
    "video_edit_recipe_created",
    "video_edit_recipe_revised",
    "video_edit_recipe_compiled",
    "video_edit_recipe_queued",
    "subject_completion_ready",
    "subject_completion_applied",
    "node_generation_config_saved",
    "generation_candidate_ready",
  ]) source.addEventListener(eventName, () => void loadCanvas());
  source.addEventListener("subject_completion_ready", () => void refreshSubjectAssistantRun());
  source.onerror = () => { syncStatus.value = navigator.onLine ? "service_error" : "offline"; };
  eventSource.value = source;
}

watch(() => props.projectId, async () => {
  await loadCanvas(true);
  connectEvents();
});
onMounted(async () => {
  await loadCanvas(true);
  connectEvents();
  window.addEventListener("online", replayPendingLayout);
});
onBeforeUnmount(() => {
  eventSource.value?.close();
  window.removeEventListener("online", replayPendingLayout);
});
</script>

<template>
  <section class="canvas-workspace" v-loading="loading">
    <header class="canvas-topbar">
      <div>
        <span class="eyebrow">AIGC MEDIA CANVAS · V2</span>
        <h1>{{ isProductAd ? '产品广告媒体画布' : isShortDrama ? '类型化短剧创作画布' : '通用媒体画布' }}</h1>
      </div>
      <div class="topbar-state">
        <span class="sync-indicator" :class="`sync-${syncStatus}`" />
        {{ syncLabel }}
      </div>
    </header>

    <div class="canvas-stage">
      <VueFlow
        v-model:nodes="flowNodes"
        v-model:edges="flowEdges"
        :min-zoom="0.18"
        :max-zoom="1.8"
        :only-render-visible-elements="false"
        :default-viewport="canvas?.viewport"
        @connect="connect"
        @node-drag-stop="nodeDragStop"
      >
        <Background pattern-color="#29303a" :gap="24" :size="1" />
        <template #node-canvas="{ data }">
          <Handle
            v-for="(port, index) in inputPortsFor(data.node.type)"
            :id="port"
            :key="`in-${port}`"
            type="target"
            :position="Position.Left"
            :style="{ top: `${36 + index * 20}px` }"
          />
          <CanvasNodeCard
            :node="data.node"
            @inspect-prompt="inspectPrompt"
            @approve-story="approveStory"
            @edit-object="editObject"
            @generate-batch="openGenerationBatch"
            @promote-candidate="promoteCandidate"
            @edit-video="openVideoEditor"
            @assist-subject="openSubjectAssistant"
            @open-composer="openGenerationComposer"
          />
          <div
            v-if="generationComposerNode?.id === data.node.id && generationCapability"
            class="node-composer-anchor"
          >
            <NodeGenerationComposer
              :node-revision="data.node.revision ?? 1"
              :capabilities="generationCapability"
              :actual-references="generationReferences"
              @save-config="() => undefined"
              @generate="submitNodeGeneration"
            />
          </div>
          <Handle
            v-for="(port, index) in outputPortsFor(data.node.type)"
            :id="port"
            :key="`out-${port}`"
            type="source"
            :position="Position.Right"
            :style="{ top: `${36 + index * 20}px` }"
          />
        </template>
      </VueFlow>

      <aside class="stage-guide">
        <b>{{ isProductAd ? '产品生产链路' : '创作链路' }}</b>
        <template v-if="isProductAd"><span>参考素材</span><span>候选批次</span><span>选中分支</span><span>视频重编</span><span>审核与时间线</span></template>
        <template v-else><span>简报与主体</span><span>三案与评分</span><span>人工定稿</span><span>场景与 Beat</span><span>媒体与时间线</span></template>
      </aside>

      <nav class="canvas-toolbar" aria-label="画布工具">
        <button type="button" title="添加节点" @click="nodeLibraryVisible = !nodeLibraryVisible"><Plus /></button>
        <button type="button" title="编辑创意简报" @click="briefVisible = true"><EditPen /></button>
        <button type="button" title="添加通用主体" @click="subjectVisible = true"><Plus /></button>
        <button type="button" title="自动布局" @click="autoLayout"><MagicStick /></button>
        <button type="button" title="聚焦全部节点" @click="fitView({ padding: .16, duration: 320 })"><Aim /></button>
        <button type="button" title="刷新画布" @click="loadCanvas()"><Refresh /></button>
      </nav>

      <CanvasNodeLibrary
        v-if="nodeLibraryVisible"
        class="node-library-popover"
        @create-node="createCatalogNode"
        @open-asset-history="openAssetHistory()"
      />

      <aside v-if="isShortDrama" class="workflow-actions">
        <div>
          <b>人工审批流程</b>
          <small v-if="missingSubjectCount">还需 {{ missingSubjectCount }} 个叙事主体</small>
          <small v-else-if="!approvedStory">主体已就绪，先生成并批准故事</small>
          <small v-else>故事已冻结，可显式生成分镜</small>
        </div>
        <button
          type="button"
          :disabled="Boolean(missingSubjectCount) || Boolean(busyAction)"
          @click="runStories"
        ><ConnectionIcon />生成三案</button>
        <button
          type="button"
          :disabled="!approvedStory || Boolean(busyAction)"
          @click="createStoryboard"
        ><Timer />生成分镜</button>
      </aside>
      <aside v-else-if="isProductAd" class="workflow-actions">
        <div><b>产品广告生产</b><small>候选保持在批次内；提升后才创建独立资产分支</small></div>
        <button v-if="productBatchNode" type="button" @click="openGenerationBatch(productBatchNode)"><ConnectionIcon />生成 {{ productBatchNode.data.candidateCount ?? 4 }} 个候选</button>
      </aside>
    </div>

    <PromptTraceDrawer
      v-model="promptVisible"
      :prompt="selectedPrompt"
      :loading="promptLoading"
    />

    <el-drawer v-model="subjectAssistantVisible" title="主体分析与人工补全" size="620px">
      <SubjectAssistantPanel
        v-if="subjectAssistantNode"
        :subject="subjectDraft(subjectAssistantNode)"
        :run="subjectAssistantRun"
        :loading="subjectAssistantLoading"
        @run="runSubjectAssistant"
        @apply="applySubjectAssistant"
        @inspect-prompt="inspectPrompt"
      />
    </el-drawer>

    <el-drawer v-model="assetHistoryVisible" title="项目素材历史" size="720px">
      <div class="asset-history-filters">
        <button type="button" @click="openAssetHistory()">全部</button>
        <button type="button" @click="openAssetHistory('image')">图片</button>
        <button type="button" @click="openAssetHistory('video')">视频</button>
        <button type="button" @click="openAssetHistory('audio')">音频</button>
      </div>
      <el-alert
        v-if="assetHistoryError"
        :title="`素材加载失败：${assetHistoryError}`"
        description="可重试；当前已加载缓存不会被清空。"
        type="error"
        :closable="false"
        show-icon
      >
        <template #default><el-button @click="openAssetHistory(assetHistoryKind)">重试</el-button></template>
      </el-alert>
      <div v-if="assetHistoryLoading" class="asset-history-state">正在读取素材索引…</div>
      <div v-else-if="!assetHistory.length && !assetHistoryError" class="asset-history-state">当前项目还没有可用素材。</div>
      <div class="asset-history-grid">
        <article v-for="asset in assetHistory" :key="asset.id">
          <img v-if="asset.mediaType === 'image'" :src="asset.contentUrl" :alt="asset.semanticKey || asset.role" />
          <video v-else-if="asset.mediaType === 'video'" :src="asset.contentUrl" muted preload="metadata" />
          <div v-else class="audio-placeholder">AUDIO</div>
          <b>{{ asset.semanticKey || asset.role }}</b><small>{{ asset.status }} · {{ asset.mediaType }}</small>
        </article>
      </div>
    </el-drawer>

    <el-drawer v-model="briefVisible" title="创意简报" size="520px">
      <el-form label-position="top">
        <el-form-item label="主题 / 故事种子"><el-input v-model="briefForm.theme" type="textarea" :rows="4" /></el-form-item>
        <div class="form-grid">
          <el-form-item label="受众"><el-input v-model="briefForm.audience" /></el-form-item>
          <el-form-item label="类型"><el-input v-model="briefForm.genre" /></el-form-item>
          <el-form-item label="基调"><el-input v-model="briefForm.tone" /></el-form-item>
          <el-form-item label="目标时长"><el-input-number v-model="briefForm.targetDurationSeconds" :min="8" :max="600" /></el-form-item>
        </div>
        <el-button type="primary" @click="saveBrief">保存新 Revision</el-button>
      </el-form>
    </el-drawer>

    <el-drawer v-model="subjectVisible" title="添加通用主体" size="560px">
      <el-form label-position="top">
        <el-form-item label="主体名称"><el-input v-model="subjectForm.name" /></el-form-item>
        <div class="form-grid">
          <el-form-item label="类型"><el-select v-model="subjectForm.kind"><el-option v-for="kind in ['person','animal','object','location','style','product']" :key="kind" :label="kind" :value="kind" /></el-select></el-form-item>
          <el-form-item label="角色"><el-select v-model="subjectForm.role"><el-option v-for="role in ['protagonist','co_protagonist','support','prop','environment','hero_product']" :key="role" :label="role" :value="role" /></el-select></el-form-item>
        </div>
        <el-form-item label="身份锚点"><el-input v-model="subjectForm.identityAnchors[0]" type="textarea" /></el-form-item>
        <el-form-item label="不可变化特征"><el-input v-model="subjectForm.immutableTraits[0]" type="textarea" /></el-form-item>
        <el-form-item label="戏剧功能"><el-input v-model="subjectForm.dramaticFunction" type="textarea" /></el-form-item>
        <el-button type="primary" @click="createSubject">创建主体 Revision 1</el-button>
      </el-form>
    </el-drawer>

    <el-drawer v-model="beatVisible" title="编辑独立 Shot Beat" size="560px">
      <el-form label-position="top">
        <el-form-item label="标题"><el-input v-model="beatForm.title" /></el-form-item>
        <el-form-item label="动作"><el-input v-model="beatForm.action" type="textarea" :rows="4" /></el-form-item>
        <el-form-item label="机位 / 景别"><el-input v-model="beatForm.camera" /></el-form-item>
        <el-form-item label="对白 / 声音"><el-input v-model="beatForm.dialogue" type="textarea" /></el-form-item>
        <el-form-item label="时长（秒）"><el-input-number v-model="beatForm.durationSeconds" :min="1" :max="60" /></el-form-item>
        <el-button type="primary" @click="saveBeat">保存为新 Revision</el-button>
      </el-form>
    </el-drawer>

    <el-drawer v-model="batchVisible" title="产品图片候选批次" size="560px">
      <el-alert title="一次生成 1–8 个独立候选；只有人工提升到画布的候选可以继续生成视频。" type="info" :closable="false" />
      <el-form label-position="top" class="batch-form">
        <el-form-item label="候选数量"><el-input-number v-model="batchForm.candidateCount" :min="1" :max="8" /></el-form-item>
        <el-form-item label="图片生成 Prompt"><el-input v-model="batchForm.prompt" type="textarea" :rows="8" placeholder="描述广告构图、产品位置、模特动作、光线、镜头与必须保持的包装细节" /></el-form-item>
        <el-button type="primary" @click="generateBatch">确认 Prompt 并进入队列</el-button>
      </el-form>
    </el-drawer>

    <VideoEditWorkspace
      v-if="videoEditorNode"
      :project-id="projectId"
      :source-asset-id="mediaAssetId(videoEditorNode)"
      :video-url="mediaContentUrl(videoEditorNode)"
      :poster-url="videoEditorNode.data.posterUrl"
      :duration-ms="Number(videoEditorNode.data.durationMs ?? 13000)"
      :initial-start-ms="Number(videoEditorNode.data.defaultEditStartMs ?? 0)"
      :initial-end-ms="Number(videoEditorNode.data.defaultEditEndMs ?? Math.min(Number(videoEditorNode.data.durationMs ?? 13000), 7000))"
      :references="videoEditReferences"
      @close="videoEditorNode = null"
      @submitted="videoEditorNode = null; loadCanvas()"
    />
  </section>
</template>

<style scoped>
.canvas-workspace { height: 100%; min-height: 720px; overflow: hidden; color: #e9edf4; background: #101216; }
.canvas-topbar { height: 72px; padding: 0 24px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #272b32; background: #15171b; }
.canvas-topbar h1 { margin: 3px 0 0; font-size: 16px; }
.eyebrow { color: #687487; font-size: 9px; font-weight: 800; letter-spacing: .15em; }
.topbar-state { display: flex; align-items: center; gap: 7px; color: #929dad; font-size: 12px; }
.sync-indicator { width: 7px; height: 7px; border-radius: 50%; background: #798393; }
.sync-saved { background: #5bd09a; }.sync-syncing, .sync-local { background: #e1bd62; }.sync-conflict, .sync-offline, .sync-service_error { background: #e07373; }
.canvas-stage { position: relative; height: calc(100% - 72px); min-height: 648px; }
.vue-flow { background: #111317; }
.vue-flow :deep(.vue-flow__edge-path) { stroke: #5c6675; stroke-width: 1.2; }
.vue-flow :deep(.vue-flow__handle) { width: 9px; height: 9px; background: #9aa8ba; border: 2px solid #1b1e24; }
.stage-guide { position: absolute; top: 18px; left: 18px; z-index: 5; display: flex; gap: 8px; align-items: center; padding: 9px 11px; color: #778396; background: rgb(20 23 28 / 92%); border: 1px solid #30353e; border-radius: 10px; font-size: 10px; }
.stage-guide b { color: #d9dee8; margin-right: 4px; }.stage-guide span { padding-left: 8px; border-left: 1px solid #333942; }
.canvas-toolbar { position: absolute; left: 50%; bottom: 22px; z-index: 5; display: flex; gap: 5px; padding: 7px; transform: translateX(-50%); background: #24272c; border: 1px solid #3b3f47; border-radius: 14px; box-shadow: 0 14px 35px rgb(0 0 0 / 34%); }
.canvas-toolbar button { width: 36px; height: 36px; padding: 8px; color: #c3cad5; background: transparent; border: 0; border-radius: 9px; cursor: pointer; }
.canvas-toolbar button:hover { color: #fff; background: #383d45; }
.node-library-popover { position: absolute; left: 50%; bottom: 78px; z-index: 12; transform: translateX(-50%); }
.node-composer-anchor { position: absolute; top: 0; left: 292px; z-index: 20; }
.workflow-actions { position: absolute; right: 18px; bottom: 22px; z-index: 5; display: flex; align-items: center; gap: 8px; max-width: calc(100% - 180px); padding: 9px; color: #c8d0db; background: rgb(28 31 37 / 95%); border: 1px solid #3a4049; border-radius: 12px; box-shadow: 0 14px 35px rgb(0 0 0 / 32%); }
.workflow-actions div { display: grid; padding: 0 8px; }.workflow-actions small { color: #808b9c; }
.workflow-actions button { display: flex; align-items: center; gap: 6px; padding: 9px 11px; color: #dce5f2; background: #303640; border: 1px solid #48515f; border-radius: 8px; cursor: pointer; }
.workflow-actions button :deep(svg) { width: 14px; }.workflow-actions button:disabled { opacity: .38; cursor: not-allowed; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }
.batch-form { margin-top: 18px; }
.asset-history-filters { display: flex; gap: 7px; margin-bottom: 12px; }.asset-history-filters button { padding: 7px 10px; color: #cad3df; background: #292e36; border: 1px solid #3c4450; border-radius: 7px; cursor: pointer; }
.asset-history-state { padding: 30px; color: #8b96a7; text-align: center; }.asset-history-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }.asset-history-grid article { display: grid; gap: 5px; padding: 8px; color: #dce3ed; background: #1c2026; border: 1px solid #303741; border-radius: 9px; }.asset-history-grid img, .asset-history-grid video, .audio-placeholder { width: 100%; height: 132px; object-fit: cover; background: #101217; border-radius: 6px; }.asset-history-grid small { color: #7f8a9a; }.audio-placeholder { display: grid; place-items: center; color: #758196; font-size: 11px; letter-spacing: .16em; }
@media (max-width: 980px) {
  .stage-guide { display: none; }
  .workflow-actions { left: 12px; right: 12px; bottom: 70px; max-width: none; flex-wrap: wrap; }
  .canvas-toolbar { bottom: 14px; }
  .node-composer-anchor { position: fixed; top: auto; right: 12px; bottom: 70px; left: 12px; }
  .asset-history-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
