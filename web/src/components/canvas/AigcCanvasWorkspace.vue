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

import { ApiError, api, assetContentUrl, canvasApi } from "../../api/client";
import type {
  CanvasDto,
  CanvasGroupActionKey,
  CanvasGroupDto,
  CanvasEdgeDto,
  CanvasAssetHistoryDto,
  CanvasNodeDto,
  CanvasNodeActionDto,
  CanvasNodeType,
  CanvasPortType,
  EpisodeVisualProfileDto,
  EpisodeRulesDto,
  PromptRunDto,
  ActualReferenceBindingDto,
  GenerationCapabilityDto,
  GenerationReferenceAnnotationDto,
  JobDto,
  ProviderCapabilityDto,
  ProductionRecipeInstanceDto,
  RecipeAssetCandidateDto,
  RecipeSequenceCandidateDto,
  RecipeShotDto,
  RecipeStoryCandidateDto,
  SequenceTransitionDto,
  StoryBriefInput,
  StoryboardCreationMode,
  SubjectDto,
  SubjectCompletionRunDto,
  SubjectInput,
  VisualPresetKey,
  VisualPresetProfileDto,
  VisualProfileDraft,
} from "../../api/types";
import CanvasNodeLibrary from "./CanvasNodeLibrary.vue";
import CanvasNodeCard from "./CanvasNodeCard.vue";
import BriefNodeConsole from "./BriefNodeConsole.vue";
import CanvasContextToolbar from "./CanvasContextToolbar.vue";
import CanvasNodeContextPanel from "./CanvasNodeContextPanel.vue";
import CanvasLocalConsole from "./CanvasLocalConsole.vue";
import NodeGenerationComposer from "./NodeGenerationComposer.vue";
import PromptTraceDrawer from "./PromptTraceDrawer.vue";
import ReferenceAnnotationEditor from "./ReferenceAnnotationEditor.vue";
import SceneAssetConsole from "./SceneAssetConsole.vue";
import StoryboardNodeConsole from "./StoryboardNodeConsole.vue";
import StoryboardWorkflow, { type StoryboardShotDraft } from "./StoryboardWorkflow.vue";
import SubjectAssistantPanel from "./SubjectAssistantPanel.vue";
import VideoAssetPanel from "./VideoAssetPanel.vue";
import VideoEditWorkspace from "./VideoEditWorkspace.vue";
import VisualEvidenceConsole from "./VisualEvidenceConsole.vue";
import VisualPresetLibrary from "./VisualPresetLibrary.vue";
import {
  CANVAS_OVERLAY_ANCHOR_GAP,
  anchorCanvasOverlay,
  consolePresetForNode,
  projectCanvasNodeRect,
  resolveCanvasConsoleSize,
  type CanvasOverlayGeometry,
  type CanvasOverlaySession,
  type CanvasViewportTransform,
} from "./canvasPanels";
import {
  escapeInteraction,
  isEditableTarget,
  selectedInteraction,
  selectedNodeId as interactionNodeId,
  consoleKind as interactionConsoleKind,
  type CanvasInteractionState,
  type VideoEditConsoleDraft,
} from "./canvasInteraction";
import { planReferenceEdgeChanges, referenceConnection } from "./canvasSelection";
import { canvasSyncQueue, classifyCanvasSaveError } from "./canvasSync";
import { registerTask, useTaskCenter } from "../../tasks/taskCenter";

const props = defineProps<{
  projectId: string;
  focusNodeId?: string;
  focusRequestKey?: string;
}>();
const canvas = ref<CanvasDto | null>(null);
const flowNodes = shallowRef<Node[]>([]);
const flowEdges = shallowRef<Edge[]>([]);
const initialLoading = ref(false);
const backgroundRefreshing = ref(false);
const busyAction = ref("");
const syncStatus = ref<CanvasDto["syncStatus"]>("saved");
const promptVisible = ref(false);
const promptLoading = ref(false);
const selectedPrompt = ref<PromptRunDto | null>(null);
const briefVisible = ref(false);
const subjectVisible = ref(false);
const beatVisible = ref(false);
const nodeLibraryVisible = ref(false);
const assetHistoryVisible = ref(false);
const assetHistoryKind = ref<"image" | "video" | "audio" | undefined>();
const assetHistory = ref<CanvasAssetHistoryDto[]>([]);
const assetHistoryLoading = ref(false);
const assetHistoryError = ref("");
const referenceUploadInput = ref<HTMLInputElement | null>(null);
const canvasSurface = ref<HTMLElement | null>(null);
const referenceBindingNode = ref<CanvasNodeDto | null>(null);
const subjectAssistantVisible = ref(false);
const subjectAssistantNode = ref<CanvasNodeDto | null>(null);
const subjectAssistantRun = ref<SubjectCompletionRunDto | null>(null);
const subjectAssistantLoading = ref(false);
const subjectLibraryVisible = ref(false);
const subjectLibrary = ref<SubjectDto[]>([]);
const subjectLibraryLoading = ref(false);
const subjectLibraryError = ref("");
const visualPresetLibraryVisible = ref(false);
const visualPresetLoading = ref(false);
const visualPresetApplying = ref(false);
const visualPresets = ref<VisualPresetProfileDto[]>([]);
const episodeVisualProfile = ref<EpisodeVisualProfileDto | null>(null);
const episodeVisualProfileLoading = ref(false);
const episodeVisualProfileSaving = ref(false);
const generationComposerNode = ref<CanvasNodeDto | null>(null);
const generationCapability = ref<GenerationCapabilityDto | null>(null);
const generationReferences = ref<ActualReferenceBindingDto[]>([]);
const generationAnnotations = ref<GenerationReferenceAnnotationDto[]>([]);
const annotationAssetId = ref<string | null>(null);
const generationLoading = ref(false);
const interaction = ref<CanvasInteractionState>({ mode: "idle" });
const localConsoleSession = ref<CanvasOverlaySession | null>(null);
const localConsoleGeometry = shallowRef<CanvasOverlayGeometry | null>(null);
const nodeContextMenu = ref<{ nodeId: string; left: number; top: number } | null>(null);
const archiveUndo = ref<{ nodeId: string; title: string; layoutVersion: number } | null>(null);
const archivingNodeId = ref<string | null>(null);
const selectedReferenceNodeIds = ref<Set<string>>(new Set());
const storyboardCharacterNodeIds = ref<Set<string>>(new Set());
const videoEditDrafts = reactive<Record<string, VideoEditConsoleDraft>>({});
const spacePressed = ref(false);
const panningCanvas = ref(false);
const editingBeat = ref<CanvasNodeDto | null>(null);
const recipeCreateVisible = ref(false);
const recipeStoryReviewVisible = ref(false);
const recipeStoryRevisionId = ref<string | null>(null);
const recipeStoryRevision = ref<number | null>(null);
const recipeBusy = ref(false);
const selectedGroupId = ref<string | null>(null);
const loadedRecipe = ref<ProductionRecipeInstanceDto | null>(null);
const groupDragSnapshot = ref<{
  groupId: string;
  x: number;
  y: number;
  members: Record<string, { x: number; y: number }>;
} | null>(null);
const storyboardWorkflowVisible = ref(false);
const storyboardSaving = ref(false);
const storyboardRunBusy = ref(false);
let canvasRefreshPromise: Promise<void> | null = null;
let canvasRefreshQueued = false;
let queuedRefreshFocus = false;
let consumedFocusRequest = "";
const { findNode, fitView, setViewport, viewport } = useVueFlow();
const { items: taskCenterItems, projectSignals } = useTaskCenter();

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
const recipeCreateForm = reactive({
  theme: "孩子和猫咪在雨后发现一片发亮的叶子",
  inspirationKey: "discovery",
  targetDurationSeconds: 15,
  qualityTier: "balanced" as "quick" | "balanced" | "premium",
});
const recipeStoryRulesForm = reactive({
  personWardrobe: "",
  timeWeather: "",
  mainScene: "",
  environment: "indoor" as "indoor" | "outdoor",
  coreProps: "",
  catBehaviorMode: "natural" as "natural" | "light_anthropomorphic",
  ambient: "",
  foley: "",
  musicMood: "",
  stylePositive: "",
  styleExcluded: "",
  canonProfileId: "canon-v2-healing-child-cat",
});
const recipeInspirations = [
  { key: "season", label: "季节", theme: "孩子和猫咪发现季节悄悄变化的小迹象" },
  { key: "weather", label: "天气", theme: "孩子和猫咪在一场小雨后整理窗边" },
  { key: "chores", label: "家务", theme: "孩子做一件简单家务，猫咪在旁边自然参与" },
  { key: "food", label: "食物", theme: "孩子准备一份简单点心，猫咪好奇地观察" },
  { key: "field", label: "田野", theme: "孩子和猫咪在田野边发现一件不起眼的小事" },
  { key: "bedtime", label: "睡前", theme: "睡前，孩子和猫咪一起完成安静的小仪式" },
  { key: "discovery", label: "小发现", theme: "孩子和猫咪发现一个微小而温暖的变化" },
];

function selectRecipeInspiration(item: typeof recipeInspirations[number]) {
  recipeCreateForm.inspirationKey = item.key;
  recipeCreateForm.theme = item.theme;
}

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
const activeGroup = computed(() => canvas.value?.groups?.find(
  (group) => group.type === "recipe" && group.lifecycleStatus === "active",
) ?? null);
const activeRecipe = computed(() => loadedRecipe.value ?? undefined);
const recipeStoryRulesValid = computed(() => (
  Boolean(
    recipeStoryRulesForm.personWardrobe.trim()
    && recipeStoryRulesForm.timeWeather.trim()
    && recipeStoryRulesForm.mainScene.trim()
    && recipeStoryRulesForm.musicMood.trim()
  )
  && recipeRuleLines(recipeStoryRulesForm.ambient).length >= 1
  && recipeRuleLines(recipeStoryRulesForm.foley).length >= 1
  && recipeRuleLines(recipeStoryRulesForm.stylePositive).length >= 3
  && recipeRuleLines(recipeStoryRulesForm.styleExcluded).length >= 2
));
const syncLabel = computed(() => ({
  local: "本地待同步",
  syncing: "同步中",
  saved: "已保存",
  conflict: "保存冲突",
  offline: "离线",
  service_error: "服务异常",
})[syncStatus.value]);
const selectedNodeId = computed<string | null>({
  get: () => interactionNodeId(interaction.value),
  set: (nodeId) => {
    if (!nodeId) {
      interaction.value = { mode: "idle" };
      return;
    }
    const node = canvas.value?.nodes.find((item) => item.id === nodeId);
    interaction.value = node ? selectedInteraction(node) : { mode: "node_selected", nodeId };
  },
});
const selectedNode = computed(() => canvas.value?.nodes.find(
  (node) => node.id === selectedNodeId.value,
) ?? null);
const showContextToolbar = computed(() => Boolean(
  selectedNode.value && ["ImageAssetNode", "VideoAssetNode"].includes(selectedNode.value.type),
));
const contextMenuNode = computed(() => canvas.value?.nodes.find(
  (node) => node.id === nodeContextMenu.value?.nodeId,
) ?? null);
const contextMenuArchiveAction = computed(() => (
  contextMenuNode.value ? archiveAction(contextMenuNode.value) : undefined
));
const referenceSelectionTarget = computed(() => {
  const state = interaction.value;
  if (state.mode !== "reference_picking") return null;
  return canvas.value?.nodes.find((node) => node.id === state.nodeId) ?? null;
});
const videoEditorNode = computed(() => (
  interaction.value.mode === "fullscreen_editing" ? selectedNode.value : null
));
const consoleKind = computed(() => interactionConsoleKind(interaction.value));
const showLocalConsole = computed(() => Boolean(
  selectedNode.value
  && consoleKind.value
  && localConsoleSession.value?.nodeId === selectedNode.value.id,
));
const localConsoleTitle = computed(() => {
  if (!selectedNode.value) return "";
  if (consoleKind.value === "video_segment") return `${String(selectedNode.value.data.title ?? "视频资产")} · 片段重拍`;
  return String(selectedNode.value.data.title ?? selectedNode.value.objectType ?? selectedNode.value.type);
});
const contextPanelStyle = computed(() => ({
  left: "0px",
  top: "0px",
  transform: `translate3d(${localConsoleGeometry.value?.toolbar.left ?? 16}px, ${localConsoleGeometry.value?.toolbar.top ?? 88}px, 0)`,
}));
const localConsoleStyle = computed(() => ({
  left: "0px",
  top: "0px",
  transform: `translate3d(${localConsoleGeometry.value?.console.left ?? 16}px, ${localConsoleGeometry.value?.console.top ?? 160}px, 0)`,
}));
const localConsolePreset = computed(() => localConsoleSession.value?.presetKey ?? "compact");
const selectedExecutions = computed(() => {
  const node = selectedNode.value;
  if (!node) return [];
  const recipeId = activeRecipe.value?.id;
  return taskCenterItems.value.filter((item) => (
    item.canvasNodeId === node.id
    || (recipeId && item.recipeInstanceId === recipeId)
  ));
});
const storyboardReferenceAssetIds = computed(() => {
  const ids = new Set<string>();
  for (const node of canvas.value?.nodes ?? []) {
    if (!storyboardCharacterNodeIds.value.has(node.id)) continue;
    storyboardCharacterAssetIds(node).forEach((id) => ids.add(id));
  }
  return [...ids].slice(0, 6);
});
const storyboardShots = computed<StoryboardShotDraft[]>(() => (
  (canvas.value?.nodes ?? [])
    .filter((node) => node.type === "ShotBeatNode")
    .sort((left, right) => Number(left.data.order ?? left.position.y) - Number(right.data.order ?? right.position.y))
    .map((node, index) => ({
      id: node.objectId ?? node.id,
      revision: Number(node.data.revision ?? node.revision ?? 1),
      order: index + 1,
      durationSeconds: Number(node.data.durationSeconds ?? 8),
      title: String(node.data.title ?? `镜头 ${index + 1}`),
      action: String(node.data.action ?? ""),
      shotSize: String(node.data.shotSize ?? "中景"),
      lighting: String(node.data.lighting ?? "柔和自然光"),
      dialogue: String(node.data.dialogue ?? ""),
      soundEffect: String(node.data.soundEffect ?? "环境声"),
      camera: String(node.data.camera ?? "固定机位"),
      prompt: String(node.data.finalPrompt ?? node.data.prompt ?? ""),
      promptId: String(node.data.promptId ?? "") || undefined,
      promptInputHash: String(node.data.promptInputHash ?? "") || undefined,
      promptWarnings: Array.isArray(node.data.promptWarnings) ? node.data.promptWarnings.map(String) : [],
      promptBlockers: Array.isArray(node.data.promptBlockers) ? node.data.promptBlockers.map(String) : [],
      referenceBindings: Array.isArray(node.data.referenceBindings)
        ? node.data.referenceBindings as StoryboardShotDraft["referenceBindings"]
        : [],
      temporalBeats: Array.isArray(node.data.temporalBeats)
        ? node.data.temporalBeats as Array<Record<string, unknown>>
        : [],
      compositionAssetIds: Array.isArray(node.data.compositionAssetIds)
        ? node.data.compositionAssetIds.map(String)
        : [],
      sceneId: String(node.data.sceneId ?? "") || undefined,
      sceneTitle: String(
        (canvas.value?.nodes ?? []).find((candidate) => (
          candidate.type === "SceneNode"
          && candidate.objectId === String(node.data.sceneId ?? "")
        ))?.data.title ?? "",
      ) || undefined,
    }))
));

const inputPorts: Partial<Record<CanvasNodeType, CanvasPortType[]>> = {
  CharacterDesignNode: ["subject[]", "story_revision"],
  StoryPlannerNode: ["brief", "subject[]"],
  StoryCandidateNode: ["story_revision"],
  StoryCriticNode: ["story_revision"],
  ApprovalGateNode: ["brief", "story_revision", "character_design"],
  StoryboardDirectorNode: ["story_revision", "subject[]", "character_design"],
  SceneNode: ["scene_plan"],
  ShotBeatNode: ["shot_beat[]", "subject[]"],
  ReviewNode: ["image_asset", "video_asset"],
  TimelineNode: ["approved_asset"],
  GenerationBatchNode: ["product_subject", "media_reference[]"],
  ImageAssetNode: ["image_asset[]"],
  VideoAssetNode: ["video_asset"],
  StylePresetNode: ["image_reference[]"],
  VideoEditNode: ["video_asset", "media_reference[]"],
  VideoSegmentNode: ["edit_recipe"],
  ImageGenerationNode: ["prompt", "shot_beat[]", "subject[]", "image_reference[]", "media_reference[]"],
  VideoGenerationNode: ["prompt", "shot_beat[]", "subject[]", "image_reference[]", "media_reference[]", "image_asset"],
  AudioGenerationNode: ["prompt"],
};
const outputPorts: Partial<Record<CanvasNodeType, CanvasPortType[]>> = {
  BriefNode: ["brief"],
  SubjectNode: ["subject[]", "product_subject"],
  CharacterDesignNode: ["character_design", "image_asset"],
  StoryPlannerNode: ["story_revision"],
  StoryCandidateNode: ["story_revision"],
  StoryCriticNode: ["story_revision"],
  ApprovalGateNode: ["brief", "story_revision", "character_design"],
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

function selectionStateFor(node: CanvasNodeDto): "none" | "compatible" | "incompatible" | "chosen" {
  const target = referenceSelectionTarget.value;
  if (!target) return "none";
  if (selectedReferenceNodeIds.value.has(node.id)) return "chosen";
  if (interaction.value.mode === "reference_picking" && interaction.value.returnMode === "video_segment_reshoot") {
    return videoReferenceAssetIds(node).length ? "compatible" : "incompatible";
  }
  if (interaction.value.mode === "reference_picking" && interaction.value.returnMode === "storyboard_characters") {
    return storyboardCharacterAssetIds(node).length ? "compatible" : "incompatible";
  }
  return referenceConnection(node, target) ? "compatible" : "incompatible";
}

function referenceSelectionIndex(nodeId: string): number {
  return [...selectedReferenceNodeIds.value].indexOf(nodeId) + 1;
}

const selectedReferenceCount = computed(() => {
  if (interaction.value.mode !== "reference_picking") return 0;
  if (interaction.value.returnMode !== "video_segment_reshoot") return selectedReferenceNodeIds.value.size;
  return new Set(
    (canvas.value?.nodes ?? [])
      .filter((node) => selectedReferenceNodeIds.value.has(node.id))
      .flatMap(videoReferenceAssetIds),
  ).size;
});

function videoReferenceAssetIds(node: CanvasNodeDto): string[] {
  if (!["ReferenceAssetNode", "ImageAssetNode"].includes(node.type)) return [];
  const ids = new Set<string>();
  const assets = Array.isArray(node.data.assets)
    ? node.data.assets as Array<Record<string, unknown>>
    : [];
  for (const asset of assets) {
    const assetId = String(asset.assetId ?? asset.id ?? "");
    const mediaType = String(asset.mediaType ?? "image");
    if (assetId && mediaType === "image") ids.add(assetId);
  }
  const directId = String(node.data.assetId ?? node.objectId ?? "");
  const directMediaType = String(node.data.mediaType ?? "image");
  if (directId && directMediaType === "image") ids.add(directId);
  return [...ids];
}

function storyboardCharacterAssetIds(node: CanvasNodeDto): string[] {
  if (node.type !== "CharacterDesignNode") return [];
  const slot = String(node.data.slot ?? "");
  if (!["child", "cat", "pair_scale"].includes(slot)) return [];
  const candidates = Array.isArray(node.data.candidates)
    ? node.data.candidates as Array<Record<string, unknown>>
    : [];
  return candidates.flatMap((candidate) => {
    if (candidate.selected !== true || String(candidate.status ?? "") !== "approved") return [];
    const id = String(candidate.assetId ?? candidate.id ?? "");
    return id ? [id] : [];
  });
}

function selectCanvasNode(node: CanvasNodeDto) {
  nodeContextMenu.value = null;
  const target = referenceSelectionTarget.value;
  if (target) {
    const videoPick = interaction.value.mode === "reference_picking"
      && interaction.value.returnMode === "video_segment_reshoot";
    const storyboardCharacterPick = interaction.value.mode === "reference_picking"
      && interaction.value.returnMode === "storyboard_characters";
    const compatible = storyboardCharacterPick
      ? storyboardCharacterAssetIds(node).length > 0
      : videoPick
        ? videoReferenceAssetIds(node).length > 0
        : Boolean(referenceConnection(node, target));
    if (!compatible) {
      ElMessage.warning(storyboardCharacterPick
        ? "只能选择已人工批准的儿童、猫咪或同框比例角色设计"
        : "该节点与当前生成器没有兼容的参考端口");
      return;
    }
    const next = new Set(selectedReferenceNodeIds.value);
    if (next.has(node.id)) next.delete(node.id);
    else {
      const nextAssetCount = storyboardCharacterPick
        ? [...next, node.id].flatMap((nodeId) => {
            const source = canvas.value?.nodes.find((item) => item.id === nodeId);
            return source ? storyboardCharacterAssetIds(source) : [];
          }).length
        : videoPick
        ? [...next, node.id].flatMap((nodeId) => {
            const source = canvas.value?.nodes.find((item) => item.id === nodeId);
            return source ? videoReferenceAssetIds(source) : [];
          }).length
        : next.size + 1;
      if (nextAssetCount > 6) {
        ElMessage.warning("当前操作最多选择 6 张参考图");
        return;
      }
      next.add(node.id);
    }
    selectedReferenceNodeIds.value = next;
    return;
  }
  selectedGroupId.value = null;
  interaction.value = selectedInteraction(node);
  generationComposerNode.value = null;
  if (["SubjectNode", "StylePresetNode"].includes(node.type)) {
    void loadEpisodeVisualProfile();
  }
  if (["GenerationBatchNode", "ImageGenerationNode", "VideoGenerationNode", "AudioGenerationNode"].includes(node.type)) {
    void openGenerationComposer(node);
  }
  initializeOverlaySession();
}

function activateCanvasNode(node: CanvasNodeDto) {
  if (node.type === "RecipeGroupNode") {
    selectedNodeId.value = node.id;
    initializeOverlaySession();
    return;
  }
  if (["GenerationBatchNode", "ImageGenerationNode", "VideoGenerationNode", "AudioGenerationNode"].includes(node.type)) {
    void openGenerationComposer(node);
    return;
  }
  if (node.type === "VideoAssetNode") {
    openVideoEditor(node);
    return;
  }
  if (node.type === "ShotBeatNode") {
    editObject(node);
    return;
  }
  if (node.type === "BriefNode") {
    Object.assign(briefForm, node.data);
    briefVisible.value = true;
    return;
  }
  if (node.type === "SubjectNode") {
    if (Array.isArray(node.data.references) && node.data.references.length) {
      selectedNodeId.value = node.id;
      void loadEpisodeVisualProfile();
      initializeOverlaySession();
    } else if (node.objectId) openSubjectAssistant(node);
    else subjectVisible.value = true;
    return;
  }
  if (node.type === "StylePresetNode") {
    selectedNodeId.value = node.id;
    void loadEpisodeVisualProfile();
    initializeOverlaySession();
    return;
  }
  selectedNodeId.value = node.id;
  initializeOverlaySession();
}

let overlayPositionFrame: number | null = null;
let pendingOverlayViewport: CanvasViewportTransform | null = null;
let archiveUndoTimer: number | null = null;

function measureCanvasSurfaceRect() {
  const measured = canvasSurface.value?.getBoundingClientRect();
  return {
    left: measured?.left ?? 0,
    top: measured?.top ?? 0,
    width: measured?.width || window.innerWidth,
    height: measured?.height || window.innerHeight,
  };
}

function selectedNodeGeometry(node: CanvasNodeDto) {
  const graphNode = findNode(node.id);
  const footprint = canvasNodeFootprint(node);
  const width = Number(graphNode?.dimensions.width ?? 0);
  const height = Number(graphNode?.dimensions.height ?? 0);
  return {
    computedPosition: {
      x: Number(graphNode?.computedPosition.x ?? graphNode?.position.x ?? node.position.x),
      y: Number(graphNode?.computedPosition.y ?? graphNode?.position.y ?? node.position.y),
    },
    dimensions: {
      width: width > 0 ? width : footprint.width,
      height: height > 0 ? height : footprint.height,
    },
  };
}

function updateOverlayGeometry() {
  const session = localConsoleSession.value;
  const node = selectedNode.value;
  if (!session || !node || session.nodeId !== node.id) return;
  const transform = pendingOverlayViewport ?? viewport.value;
  pendingOverlayViewport = null;
  const nodeRect = projectCanvasNodeRect(
    selectedNodeGeometry(node),
    transform,
    session.surfaceRect,
  );
  const toolbarWidth = node.type === "VideoAssetNode" ? 980 : 720;
  const toolbarSize = {
    width: Math.min(toolbarWidth, Math.max(0, window.innerWidth - 32)),
    height: 56,
  };
  localConsoleGeometry.value = anchorCanvasOverlay(
    nodeRect,
    { width: session.width, height: session.height },
    toolbarSize,
    session.anchorGap,
  );
}

function initializeOverlaySession() {
  const nodeId = selectedNodeId.value;
  if (!nodeId) return;
  const node = selectedNode.value;
  const kind = consoleKind.value;
  if (!node || node.id !== nodeId || !kind) {
    localConsoleSession.value = null;
    localConsoleGeometry.value = null;
    return;
  }
  const viewportSize = { width: window.innerWidth, height: window.innerHeight };
  const preset = consolePresetForNode(node.type, kind);
  const panelSize = resolveCanvasConsoleSize(preset, viewportSize);
  const existingSession = localConsoleSession.value?.nodeId === nodeId
    ? localConsoleSession.value
    : null;
  localConsoleSession.value = {
    nodeId,
    presetKey: preset.key,
    ...panelSize,
    surfaceRect: existingSession?.surfaceRect ?? measureCanvasSurfaceRect(),
    anchorGap: CANVAS_OVERLAY_ANCHOR_GAP,
  };
  updateOverlayGeometry();
}

function scheduleOverlayAnchorUpdate(
  event?: { flowTransform: CanvasViewportTransform } | { node: Node },
) {
  if (event && "flowTransform" in event) pendingOverlayViewport = event.flowTransform;
  if (overlayPositionFrame !== null) return;
  overlayPositionFrame = requestAnimationFrame(() => {
    overlayPositionFrame = null;
    updateOverlayGeometry();
  });
}

function handleWorkspaceResize() {
  const session = localConsoleSession.value;
  const node = selectedNode.value;
  const kind = consoleKind.value;
  if (!session || !node || !kind || session.nodeId !== node.id) return;
  const viewportSize = { width: window.innerWidth, height: window.innerHeight };
  const preset = consolePresetForNode(node.type, kind);
  const panelSize = resolveCanvasConsoleSize(preset, viewportSize);
  localConsoleSession.value = {
    ...session,
    presetKey: preset.key,
    ...panelSize,
    surfaceRect: measureCanvasSurfaceRect(),
  };
  updateOverlayGeometry();
}

function closeContextPanel() {
  interaction.value = { mode: "idle" };
  localConsoleSession.value = null;
  localConsoleGeometry.value = null;
  nodeContextMenu.value = null;
  generationComposerNode.value = null;
  void nextTick(() => canvasSurface.value?.focus());
}

function selectCanvasGroup(group: CanvasGroupDto) {
  selectedGroupId.value = group.id;
  interaction.value = { mode: "idle" };
  localConsoleSession.value = null;
  localConsoleGeometry.value = null;
  generationComposerNode.value = null;
}

function acceptedRecipeCost(recipe: ProductionRecipeInstanceDto): number {
  return recipe.estimatedCostMicros ?? 0;
}

function recipeCostLabel(recipe: ProductionRecipeInstanceDto): string {
  return recipe.costEstimateLabel
    ?? (recipe.estimatedCostMicros == null
      ? "付费调用·暂未计量"
      : `预计费用 ¥${(recipe.estimatedCostMicros / 1_000_000).toFixed(3)}`);
}

async function runCanvasGroupAction(group: CanvasGroupDto, actionKey: CanvasGroupActionKey) {
  const action = group.availableActions.find((item) => item.key === actionKey);
  if (!action?.enabled) {
    ElMessage.info(action?.disabledReason ?? "当前分组操作不可用");
    return;
  }
  recipeBusy.value = true;
  try {
    if (actionKey === "run_group") {
      const compiled = await canvasApi.compileCanvasGroup(group.id);
      const rawEstimatedCost = compiled.estimatedCostMicros;
      const estimatedCost = typeof rawEstimatedCost === "number" ? rawEstimatedCost : 0;
      const costLabel = String(
        compiled.costEstimateLabel
          ?? (rawEstimatedCost == null
            ? "付费调用·暂未计量，实际费用以供应商账单为准"
            : `预计费用 ¥${(estimatedCost / 1_000_000).toFixed(3)}`),
      );
      await ElMessageBox.confirm(
        `${String(compiled.primaryAction ?? "继续执行")}；本次只运行到下一个人工审核门。${costLabel}。`,
        "确认整组执行",
        { confirmButtonText: "执行到审核门", cancelButtonText: "取消" },
      );
      const job = await canvasApi.runCanvasGroup(group.id, estimatedCost);
      registerCanvasJob(job, {
        label: `${group.title} · ${String(compiled.primaryAction ?? "整组执行")}`,
        nodeId: group.memberNodeIds[0] ?? group.id,
        canvasGroupId: group.id,
        recipeInstanceId: group.recipeInstanceId ?? undefined,
      });
      ElMessage.success("整组任务已排队；完成后会停在下一人工审核门");
    } else if (actionKey === "save_group_template") {
      await canvasApi.saveCanvasGroupTemplate(group.id);
      ElMessage.success("六阶段工作流结构已加入工具箱，未包含本项目候选与审核结果");
    } else if (actionKey === "convert_shot_groups") {
      await canvasApi.convertCanvasGroupToShots(group.id);
      await loadCanvas();
      ElMessage.success("已按批准镜头创建独立子分组");
    } else if (actionKey === "ungroup") {
      await ElMessageBox.confirm(
        "解组会移除分组框并归档配方实例；节点、Canon、素材、血缘和历史版本全部保留。",
        "确认完全解组",
        { confirmButtonText: "保留节点并解组", cancelButtonText: "取消", type: "warning" },
      );
      await canvasApi.ungroupCanvasGroup(group.id, group.revision);
      selectedGroupId.value = null;
      await loadCanvas();
    } else {
      const manifest = await canvasApi.canvasGroupDownloadManifest(group.id) as {
        assets?: Array<Record<string, unknown>>;
      };
      const assets = manifest.assets ?? [];
      if (!assets.length) {
        ElMessage.warning("该分组还没有可批量下载的成功产物");
        return;
      }
      const link = document.createElement("a");
      link.href = canvasApi.canvasGroupDownloadUrl(group.id);
      link.download = `${group.title}-assets.zip`;
      link.click();
      ElMessage.success("正在打包成功产物、媒体文件与人工审核清单");
    }
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error instanceof Error ? error.message : String(error));
    }
  } finally {
    recipeBusy.value = false;
  }
}

async function completeCreativeBrief() {
  const recipe = activeRecipe.value;
  if (!recipe) return;
  await ElMessageBox.confirm(
    `AI 将补全结构化创意简报，完成后仍需人工批准。${recipeCostLabel(recipe)}。`,
    "确认补全创意",
    { confirmButtonText: "提交后台任务", cancelButtonText: "取消" },
  );
  const job = await canvasApi.runRecipeCreativeBrief(recipe.id);
  registerCanvasJob(job, {
    label: "AI 补全创意输入",
    nodeId: selectedNode.value?.id ?? activeGroup.value?.memberNodeIds[0] ?? "creative",
    canvasGroupId: activeGroup.value?.id,
    recipeInstanceId: recipe.id,
  });
  ElMessage.success("创意补全已进入后台执行，完成后必须人工审核")
}

async function reviewCreativeBrief() {
  const recipe = activeRecipe.value;
  const brief = recipe?.creativeBrief;
  if (!recipe || !brief) return;
  await ElMessageBox.confirm(
    "确认主题、受众、情绪、场景、事件、猫咪模式、时长与画幅均可作为本集创意约束？",
    "批准创意简报",
    { confirmButtonText: "批准当前版本", cancelButtonText: "继续编辑" },
  );
  await canvasApi.reviewRecipeTarget({
    recipeInstanceId: recipe.id,
    targetType: "creative_brief",
    targetId: brief.id,
    targetRevision: brief.revision,
    decision: "approve",
  });
  await loadCanvas();
}

async function generateCharacterDesign() {
  const recipe = activeRecipe.value;
  if (!recipe) return;
  await ElMessageBox.confirm(
    `将按质量档位生成儿童、猫咪和同框比例三个槽位。${recipeCostLabel(recipe)}。`,
    "确认生成角色设计",
    { confirmButtonText: "提交后台任务", cancelButtonText: "取消" },
  );
  const job = await canvasApi.runRecipeCharacterDesign(
    recipe.id,
    acceptedRecipeCost(recipe),
  );
  registerCanvasJob(job, {
    label: "一人一猫三槽位角色设计",
    nodeId: selectedNode.value?.id ?? activeGroup.value?.memberNodeIds[0] ?? "character-design",
    canvasGroupId: activeGroup.value?.id,
    recipeInstanceId: recipe.id,
  });
  ElMessage.success("三个角色设计槽位已按顺序加入图片生成队列")
}

async function reviewCharacterCandidate(node: CanvasNodeDto, assetId: string) {
  const recipe = activeRecipe.value;
  const slot = String(node.data.slot ?? "") as "child" | "cat" | "pair_scale";
  const candidate = recipe?.characterDesign?.slots?.[slot]?.find((item) => item.assetId === assetId);
  if (!recipe || !candidate) {
    ElMessage.error("角色候选尚未同步到配方版本，请刷新后重试");
    return;
  }
  await ElMessageBox.confirm(
    "确认该图片保持固定 Canon 身份，且只承担造型、姿态、比例或构图参考职责？",
    "批准角色设计候选",
    { confirmButtonText: "批准该槽位版本", cancelButtonText: "取消" },
  );
  await canvasApi.reviewRecipeTarget({
    recipeInstanceId: recipe.id,
    targetType: "character_design",
    targetId: candidate.assetId,
    targetHash: candidate.sha256,
    decision: "approve",
  });
  await loadCanvas();
}

function registerCanvasJob(
  job: JobDto,
  options: {
    label: string;
    nodeId: string;
    canvasGroupId?: string;
    recipeInstanceId?: string;
    creationMode?: StoryboardCreationMode;
  },
) {
  const context = job.context ?? job;
  registerTask(job.jobId, {
    kind: job.kind,
    label: options.label,
    projectId: props.projectId,
    canvasNodeId: context.canvasNodeId ?? options.nodeId,
    canvasGroupId: context.canvasGroupId ?? options.canvasGroupId,
    recipeInstanceId: context.recipeInstanceId ?? options.recipeInstanceId,
    creationMode: context.creationMode ?? options.creationMode,
    workflowStage: context.workflowStage,
    phase: context.phase,
    operationKey: context.operationKey,
    shotId: context.shotId,
  });
}

async function runStoryboardCreation(payload: {
  mode: "from_story" | "from_characters";
  instruction: string;
}) {
  const node = selectedNode.value;
  if (!node || node.type !== "StoryboardDirectorNode") return;
  storyboardRunBusy.value = true;
  try {
    if (activeRecipe.value) {
      await ElMessageBox.confirm(
        `${payload.mode === "from_story" ? "将从已批准故事生成分镜" : "将从已批准儿童与猫咪角色图生成分镜"}，完成后仍需逐镜人工确认。${recipeCostLabel(activeRecipe.value)}。`,
        "确认生成分镜",
        { confirmButtonText: "提交后台任务", cancelButtonText: "取消" },
      );
    }
    const options = {
      creationMode: payload.mode,
      referenceAssetIds: payload.mode === "from_characters" ? storyboardReferenceAssetIds.value : [],
      instruction: payload.instruction || undefined,
    };
    const job = activeRecipe.value
      ? await canvasApi.runRecipeStoryboard(activeRecipe.value.id, acceptedRecipeCost(activeRecipe.value), options)
      : await canvasApi.createStoryboard(props.projectId, options);
    registerCanvasJob(job, {
      label: payload.mode === "from_story" ? "剧本生成分镜脚本" : "基于固定角色补充分镜",
      nodeId: node.id,
      recipeInstanceId: activeRecipe.value?.id,
      creationMode: payload.mode,
    });
    ElMessage.success("分镜任务已进入后台执行；当前节点会持续显示执行步骤");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    storyboardRunBusy.value = false;
  }
}

async function openStoryboardWorkflow() {
  if (!episodeVisualProfile.value) await loadEpisodeVisualProfile();
  if (!episodeVisualProfile.value) {
    ElMessage.error("请先应用并确认一人一猫 Canon 预设，建立本集视觉档案");
    return;
  }
  storyboardWorkflowVisible.value = true;
}

function openManualStoryboard() {
  void openStoryboardWorkflow();
}

function openStoryboardScene(sceneId: string) {
  const sceneNode = canvas.value?.nodes.find((node) => (
    node.type === "SceneNode" && node.objectId === sceneId
  ));
  if (!sceneNode) {
    ElMessage.warning("对应场景节点尚未投影到画布，请刷新后重试");
    return;
  }
  storyboardWorkflowVisible.value = false;
  selectCanvasNode(sceneNode);
}

async function saveStoryboardWorkflow(rows: StoryboardShotDraft[]) {
  storyboardSaving.value = true;
  try {
    const revision = Math.max(0, ...storyboardShots.value.map((row) => row.revision ?? 0));
    await canvasApi.saveManualStoryboard(
      props.projectId,
      revision,
      rows.map((row, index) => ({ ...row, order: index + 1 })),
      Boolean(activeRecipe.value),
    );
    storyboardWorkflowVisible.value = false;
    await loadCanvas();
    ElMessage.success("人工分镜已保存为新版本；新增、删除、排序与旧版本血缘均已保留");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    storyboardSaving.value = false;
  }
}

function startStoryboardReferenceSelection() {
  const target = selectedNode.value;
  if (!target || target.type !== "StoryboardDirectorNode") return;
  selectedReferenceNodeIds.value = new Set(storyboardCharacterNodeIds.value);
  interaction.value = {
    mode: "reference_picking",
    nodeId: target.id,
    returnMode: "storyboard_characters",
    snapshotNodeIds: [...storyboardCharacterNodeIds.value],
  };
}

function startReferenceSelection() {
  const target = generationComposerNode.value;
  if (!target || !canvas.value) return;
  const selected = new Set(
    canvas.value.edges
      .filter((edge) => edge.targetNodeId === target.id)
      .map((edge) => edge.sourceNodeId)
      .filter((sourceId) => {
        const source = canvas.value?.nodes.find((node) => node.id === sourceId);
        return Boolean(source && referenceConnection(source, target));
      }),
  );
  selectedReferenceNodeIds.value = selected;
  interaction.value = {
    mode: "reference_picking",
    nodeId: target.id,
    returnMode: "node_selected",
    snapshotNodeIds: [...selected],
  };
}

function cancelReferenceSelection() {
  if (interaction.value.mode !== "reference_picking") return;
  const snapshotNodeIds = interaction.value.snapshotNodeIds;
  interaction.value = escapeInteraction(interaction.value);
  selectedReferenceNodeIds.value = new Set(snapshotNodeIds);
}

function startVideoReferenceSelection(assetIds: string[]) {
  const node = selectedNode.value;
  if (!node || node.type !== "VideoAssetNode" || !canvas.value) return;
  const selected = new Set(
    canvas.value.nodes
      .filter((item) => videoReferenceAssetIds(item).some((assetId) => assetIds.includes(assetId)))
      .map((item) => item.id),
  );
  selectedReferenceNodeIds.value = selected;
  interaction.value = {
    mode: "reference_picking",
    nodeId: node.id,
    returnMode: "video_segment_reshoot",
    snapshotNodeIds: [...selected],
  };
}

async function finishReferenceSelection() {
  const target = referenceSelectionTarget.value;
  if (!target || !canvas.value) return;
  if (interaction.value.mode === "reference_picking" && interaction.value.returnMode === "video_segment_reshoot") {
    const assetIds = [...new Set(
      canvas.value.nodes
        .filter((node) => selectedReferenceNodeIds.value.has(node.id))
        .flatMap(videoReferenceAssetIds),
    )].slice(0, 6);
    const current = videoEditDrafts[target.id] ?? defaultVideoEditDraft(target);
    videoEditDrafts[target.id] = { ...current, referenceAssetIds: assetIds };
    interaction.value = { mode: "video_segment_reshoot", nodeId: target.id };
    return;
  }
  if (interaction.value.mode === "reference_picking" && interaction.value.returnMode === "storyboard_characters") {
    storyboardCharacterNodeIds.value = new Set(selectedReferenceNodeIds.value);
    interaction.value = { mode: "node_selected", nodeId: target.id };
    selectedReferenceNodeIds.value = new Set();
    initializeOverlaySession();
    return;
  }
  const selectedSources = canvas.value.nodes.filter((node) => selectedReferenceNodeIds.value.has(node.id));
  const changes = planReferenceEdgeChanges(canvas.value.edges, target, selectedSources);
  try {
    for (const edgeId of changes.deleteEdgeIds) await canvasApi.deleteEdge(edgeId);
    for (const edge of changes.createEdges) await canvasApi.createEdge(props.projectId, edge);
    interaction.value = { mode: "node_selected", nodeId: target.id };
    selectedReferenceNodeIds.value = new Set();
    await loadCanvas();
    ElMessage.success("参考关系已保存；普通布局保存不会修改这些业务边");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

function canvasNodeFootprint(node: CanvasNodeDto): { width: number; height: number } {
  if (node.type === "ImageGenerationNode") return { width: 790, height: 430 };
  if (["VideoAssetNode", "ImageAssetNode", "ReferenceAssetNode"].includes(node.type)) {
    return { width: 360, height: 460 };
  }
  if (node.type === "StoryCandidateNode") return { width: 430, height: 250 };
  return { width: 330, height: 210 };
}

function groupFlowNodes(
  groups: CanvasGroupDto[],
  businessNodes: Node[],
): Node[] {
  const byId = new Map(businessNodes.map((node) => [node.id, node]));
  return groups.flatMap((group) => {
    const members = group.memberNodeIds.map((id) => byId.get(id)).filter(Boolean) as Node[];
    if (!members.length) return [];
    const minX = Math.min(...members.map((node) => node.position.x)) - 54;
    const minY = Math.min(...members.map((node) => node.position.y)) - 104;
    const maxX = Math.max(...members.map((node) => {
      const source = node.data.node as CanvasNodeDto;
      return node.position.x + canvasNodeFootprint(source).width;
    })) + 54;
    const maxY = Math.max(...members.map((node) => {
      const source = node.data.node as CanvasNodeDto;
      return node.position.y + canvasNodeFootprint(source).height;
    })) + 64;
    return [{
      id: `canvas-group:${group.id}`,
      type: "canvasGroup",
      position: { x: minX, y: minY },
      data: { group },
      style: { width: `${maxX - minX}px`, height: `${maxY - minY}px` },
      zIndex: -10,
      selectable: false,
      draggable: true,
      connectable: false,
    }];
  });
}

function withGroupFrames(businessNodes: Node[]): Node[] {
  return [
    ...groupFlowNodes(canvas.value?.groups ?? [], businessNodes),
    ...businessNodes,
  ];
}

function loadCanvas(focus = false): Promise<void> {
  queuedRefreshFocus = queuedRefreshFocus || focus;
  if (canvasRefreshPromise) {
    canvasRefreshQueued = true;
    return canvasRefreshPromise;
  }
  canvasRefreshPromise = refreshCanvasProjection();
  return canvasRefreshPromise;
}

async function refreshCanvasProjection() {
  const showInitialState = canvas.value === null && flowNodes.value.length === 0;
  initialLoading.value = showInitialState;
  backgroundRefreshing.value = !showInitialState;
  try {
    do {
      canvasRefreshQueued = false;
      const focus = queuedRefreshFocus;
      queuedRefreshFocus = false;
      const projectId = props.projectId;
      const loaded = await canvasApi.canvas(projectId);
      if (projectId !== props.projectId) continue;
      const recipeGroup = loaded.groups?.find((group) => (
        group.type === "recipe" && group.lifecycleStatus === "active" && group.recipeInstanceId
      ));
      const recipe = recipeGroup?.recipeInstanceId
        ? await canvasApi.recipeInstance(recipeGroup.recipeInstanceId)
        : null;
      if (projectId !== props.projectId) continue;
      const currentPositions = new Map(flowNodes.value
        .filter((node) => node.type === "canvas")
        .map((node) => [node.id, node.position]));
      canvas.value = loaded;
      loadedRecipe.value = recipe;
      syncStatus.value = loaded.syncStatus;
      const visibleNodeIds = new Set(loaded.nodes.map((node) => node.id));
      let businessNodes = loaded.nodes.map((node) => ({
        id: node.id,
        type: "canvas",
        position: currentPositions.get(node.id) ?? node.position,
        data: { node },
      }));
      const pendingLayout = canvasSyncQueue.pending(projectId).at(-1);
      if (pendingLayout && Array.isArray(pendingLayout.nodes)) {
        const positions = new Map(
          (pendingLayout.nodes as Array<{ nodeId: string; x: number; y: number }>).map(
            (item) => [item.nodeId, { x: item.x, y: item.y }],
          ),
        );
        businessNodes = businessNodes.map((node) => ({
          ...node,
          position: positions.get(node.id) ?? node.position,
        }));
        syncStatus.value = navigator.onLine ? "local" : "offline";
      }
      flowNodes.value = withGroupFrames(businessNodes);
      flowEdges.value = loaded.edges.filter((edge) => (
        visibleNodeIds.has(edge.sourceNodeId) && visibleNodeIds.has(edge.targetNodeId)
      )).map((edge) => ({
        id: edge.id ?? `${edge.sourceNodeId}-${edge.targetNodeId}-${edge.sourcePort}`,
        source: edge.sourceNodeId,
        target: edge.targetNodeId,
        sourceHandle: edge.sourcePort,
        targetHandle: edge.targetPort,
        class: "typed-edge",
      }));
      const brief = loaded.nodes.find((node) => node.type === "BriefNode");
      if (brief) Object.assign(briefForm, brief.data);
      if (focus) await focusCanvas(true);
      await focusRequestedNode();
    } while (canvasRefreshQueued);
  } catch (error) {
    syncStatus.value = navigator.onLine ? "service_error" : "offline";
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    initialLoading.value = false;
    backgroundRefreshing.value = false;
    canvasRefreshPromise = null;
  }
}

async function focusRequestedNode() {
  const nodeId = props.focusNodeId?.trim();
  if (!nodeId) return;
  const requestKey = `${props.projectId}:${nodeId}:${props.focusRequestKey ?? ""}`;
  if (requestKey === consumedFocusRequest) return;
  const node = canvas.value?.nodes.find((item) => item.id === nodeId);
  if (!node) return;
  consumedFocusRequest = requestKey;
  selectCanvasNode(node);
  await nextTick();
  await fitView({ nodes: [nodeId], padding: 1.2, duration: 260 });
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
    const job = await canvasApi.runStoryStrategies(props.projectId);
    const planner = canvas.value?.nodes.find((node) => node.type === "StoryPlannerNode");
    if (planner) registerCanvasJob(job, { label: "三案故事策划", nodeId: planner.id });
    ElMessage.success("故事策划已进入后台执行；节点会显示排队、生成与待审核状态");
  } finally {
    busyAction.value = "";
  }
}

async function approveStory(revisionId: string) {
  if (activeRecipe.value) {
    const candidate = canvas.value?.nodes.find((node) => node.objectId === revisionId);
    const episodeRules = candidate?.data.episodeRules as EpisodeRulesDto | undefined;
    if (!candidate || !episodeRules) {
      ElMessage.error("故事候选缺少可编辑的 EpisodeRules，不能静默批准");
      return;
    }
    openRecipeStoryReview(revisionId, Number(candidate.data.revision), episodeRules);
    return;
  }
  await ElMessageBox.confirm(
    "批准后将冻结该 StoryRevision；以后修改会创建新版本，不会自动触发媒体生成。",
    "批准故事定稿",
  );
  await canvasApi.approveStory(revisionId);
  await loadCanvas();
}

function openRecipeStoryReview(
  revisionId: string,
  revision: number,
  episodeRules: EpisodeRulesDto,
) {
  recipeStoryRevisionId.value = revisionId;
  recipeStoryRevision.value = revision;
  Object.assign(recipeStoryRulesForm, {
    personWardrobe: episodeRules.personWardrobe,
    timeWeather: episodeRules.timeWeather,
    mainScene: episodeRules.mainScene,
    environment: episodeRules.environment,
    coreProps: episodeRules.coreProps.join("\n"),
    catBehaviorMode: episodeRules.catBehaviorMode,
    ambient: episodeRules.soundPlan.ambient.join("\n"),
    foley: episodeRules.soundPlan.foley.join("\n"),
    musicMood: episodeRules.soundPlan.musicMood,
    stylePositive: episodeRules.stylePositive.join("\n"),
    styleExcluded: episodeRules.styleExcluded.join("\n"),
    canonProfileId: episodeRules.canonProfileId,
  });
  recipeStoryReviewVisible.value = true;
}

function reviewRecipeStory(story: RecipeStoryCandidateDto) {
  if (!story.episodeRules) {
    ElMessage.error("故事候选缺少 EpisodeRules，不能静默批准");
    return;
  }
  openRecipeStoryReview(story.id, story.revision, story.episodeRules);
}

function recipeRuleLines(source: string): string[] {
  return source.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

async function confirmRecipeStory() {
  const recipe = activeRecipe.value;
  if (!recipe || !recipeStoryRevisionId.value || !recipeStoryRevision.value) return;
  const episodeRules: EpisodeRulesDto = {
    personWardrobe: recipeStoryRulesForm.personWardrobe,
    timeWeather: recipeStoryRulesForm.timeWeather,
    mainScene: recipeStoryRulesForm.mainScene,
    environment: recipeStoryRulesForm.environment,
    coreProps: recipeRuleLines(recipeStoryRulesForm.coreProps),
    catBehaviorMode: recipeStoryRulesForm.catBehaviorMode,
    soundPlan: {
      ambient: recipeRuleLines(recipeStoryRulesForm.ambient),
      foley: recipeRuleLines(recipeStoryRulesForm.foley),
      musicMood: recipeStoryRulesForm.musicMood,
      dialoguePolicy: "none",
    },
    stylePositive: recipeRuleLines(recipeStoryRulesForm.stylePositive),
    styleExcluded: recipeRuleLines(recipeStoryRulesForm.styleExcluded),
    canonProfileId: recipeStoryRulesForm.canonProfileId,
  };
  recipeBusy.value = true;
  try {
    await canvasApi.reviewRecipeTarget({
      recipeInstanceId: recipe.id,
      targetType: "story_revision",
      targetId: recipeStoryRevisionId.value,
      targetRevision: recipeStoryRevision.value,
      decision: "approve",
      episodeRules,
    });
    recipeStoryReviewVisible.value = false;
    await loadCanvas();
  } finally {
    recipeBusy.value = false;
  }
}

async function createStoryboard() {
  if (!approvedStory.value) return;
  await ElMessageBox.confirm(
    "将按简报总时长编译场景与独立 Beat。此操作不会调用图片或视频模型。",
    "生成可编辑分镜",
  );
  busyAction.value = "storyboard";
  try {
    const job = await canvasApi.createStoryboard(props.projectId, { creationMode: "from_story" });
    const director = canvas.value?.nodes.find((node) => node.type === "StoryboardDirectorNode");
    if (director) registerCanvasJob(job, { label: "剧本生成分镜脚本", nodeId: director.id, creationMode: "from_story" });
    ElMessage.success("分镜任务已进入后台执行");
  } finally {
    busyAction.value = "";
  }
}

async function reviewStoryboardRevision() {
  const recipe = activeRecipe.value;
  const story = recipe?.storyCandidates?.find((item) => item.status === "approved");
  if (!recipe || !story || !recipe.storyboardHash || !recipe.progress.shotCount) {
    ElMessage.error("分镜、已批准故事或内容哈希尚未同步，请刷新后重试");
    return;
  }
  await ElMessageBox.confirm(
    `确认批准当前 ${recipe.progress.shotCount} 个镜头？后续修改任一镜头都需要重新审核。`,
    "批准当前分镜版本",
    { confirmButtonText: "批准分镜", cancelButtonText: "继续检查" },
  );
  recipeBusy.value = true;
  try {
    await canvasApi.reviewRecipeTarget({
      recipeInstanceId: recipe.id,
      targetType: "storyboard_revision",
      targetId: story.id,
      targetHash: recipe.storyboardHash,
      decision: "approve",
    });
    await loadCanvas();
    ElMessage.success("分镜已人工批准，可以进入视觉锚点生成");
  } finally {
    recipeBusy.value = false;
  }
}

async function createHealingRecipe() {
  recipeBusy.value = true;
  try {
    const created = await canvasApi.createRecipeInstance(props.projectId, {
      recipeKey: "healing_child_cat_v1",
      theme: recipeCreateForm.theme,
      inspirationKey: recipeCreateForm.inspirationKey,
      targetDurationSeconds: recipeCreateForm.targetDurationSeconds,
      qualityTier: recipeCreateForm.qualityTier,
    });
    recipeCreateVisible.value = false;
    await loadCanvas(true);
    selectedGroupId.value = activeGroup.value?.id ?? null;
    ElMessage.success(`组合包已创建，将均衡拆成 ${created.shotDurations.length} 个镜头`);
  } finally {
    recipeBusy.value = false;
  }
}

async function saveRecipeSettings(payload: {
  theme: string;
  targetDurationSeconds: number;
  qualityTier: "quick" | "balanced" | "premium";
}) {
  const recipe = activeRecipe.value;
  if (!recipe) return;
  const invalidatesMedia = (
    payload.theme !== recipe.theme
    || payload.targetDurationSeconds !== recipe.targetDurationSeconds
  );
  await ElMessageBox.confirm(
    invalidatesMedia
      ? "保存会创建新的配方版本，并将已有故事、锚点、视频与时间线标记为过期。历史版本仍会保留。"
      : "仅修改质量档会创建配方新版本，并影响后续候选数量；已有审核结果保持有效。",
    "确认修改组合包",
  );
  recipeBusy.value = true;
  try {
    await canvasApi.updateRecipeInstance(recipe.id, recipe.revision, payload);
    await loadCanvas();
  } finally {
    recipeBusy.value = false;
  }
}

async function runRecipePrimary(
  transitions: Array<{ afterShotId: string; transition: SequenceTransitionDto }> = [],
) {
  const recipe = activeRecipe.value;
  if (!recipe) return;
  recipeBusy.value = true;
  try {
    if (recipe.phase === "creative") {
      if ((recipe.creativeBrief?.revision ?? 0) >= 2) await reviewCreativeBrief();
      else await completeCreativeBrief();
    } else if (recipe.phase === "story") {
      if (recipe.storyCandidates?.some((item) => item.status === "candidate")) {
        ElMessage.info("请在上方三个候选中选择一个，编辑 EpisodeRules 后人工批准");
      } else {
        await ElMessageBox.confirm(
          `将生成 3 个原创低压力故事候选，不会自动批准。${recipeCostLabel(recipe)}。`,
          "生成故事候选",
        );
        const job = await canvasApi.runRecipeStory(recipe.id, acceptedRecipeCost(recipe));
        registerCanvasJob(job, {
          label: "治愈短片故事候选",
          nodeId: selectedNode.value?.id ?? activeGroup.value?.memberNodeIds[0] ?? "recipe",
          recipeInstanceId: recipe.id,
        });
      }
    } else if (recipe.phase === "character_design") {
      if (recipe.characterDesign?.status === "awaiting_review") {
        const characterNode = canvas.value?.nodes.find((node) => (
          node.type === "CharacterDesignNode" && Array.isArray(node.data.candidates) && node.data.candidates.length
        ));
        if (characterNode) selectCanvasNode(characterNode);
      } else await generateCharacterDesign();
    } else if (recipe.phase === "storyboard") {
      const director = canvas.value?.nodes.find((node) => node.type === "StoryboardDirectorNode");
      if (director) {
        selectedNodeId.value = director.id;
        initializeOverlaySession();
      } else {
        const job = await canvasApi.runRecipeStoryboard(
          recipe.id,
          acceptedRecipeCost(recipe),
        );
        registerCanvasJob(job, {
          label: "治愈短片分镜脚本",
          nodeId: activeGroup.value?.memberNodeIds[0] ?? "recipe",
          recipeInstanceId: recipe.id,
          creationMode: "from_story",
        });
      }
    } else if (recipe.phase === "render" && recipe.stage === "anchors") {
      const shot = recipe.shots.find((item) => item.shotId && !item.selectedAnchorAssetId);
      if (shot) await runRecipeAnchor(shot);
    } else if (recipe.phase === "render" && recipe.stage === "video") {
      const shot = recipe.shots.find((item) => item.shotId && !item.selectedVideoAssetId);
      if (shot) await runRecipeVideo(shot);
    } else if (recipe.phase === "export") {
      if (recipe.sequenceCandidate?.status === "content_review") {
        await reviewRecipeSequence(recipe.sequenceCandidate);
      } else {
        const job = await canvasApi.runRecipeSequence(recipe.id, 0, transitions);
        registerCanvasJob(job, {
          label: "治愈短片最终音画",
          nodeId: selectedNode.value?.id ?? activeGroup.value?.memberNodeIds[0] ?? "recipe",
          recipeInstanceId: recipe.id,
        });
      }
    } else {
      if (recipe.sequenceCandidate?.contentUrl) {
        window.open(recipe.sequenceCandidate.contentUrl, "_blank", "noopener,noreferrer");
      } else {
        ElMessage.warning("最终资产暂不可下载，请刷新后重试");
      }
    }
  } finally {
    recipeBusy.value = false;
  }
}

async function runRecipeAnchor(shot: RecipeShotDto) {
  const recipe = activeRecipe.value;
  if (!recipe || !shot.shotId) return;
  let reason: string | undefined;
  const estimatedCost = recipeCostLabel(recipe);
  if (shot.anchorCandidates.length) {
    const answer = await ElMessageBox.prompt(
      `请描述开场身份、画风、构图或身体结构问题。新锚点会保留旧版本，预计费用 ${estimatedCost}。`,
      "重做视觉锚点",
      { inputPattern: /\S{4,}/, inputErrorMessage: "重做原因至少需要 4 个有效字符" },
    );
    reason = answer.value;
  } else {
    await ElMessageBox.confirm(
      `将按${recipe.qualityTier === "premium" ? "精品" : recipe.qualityTier === "balanced" ? "平衡" : "快速"}档生成视觉锚点候选，预计费用 ${estimatedCost}。`,
      "确认生成视觉锚点",
    );
  }
  recipeBusy.value = true;
  try {
    const job = await canvasApi.runRecipeAnchor(
      recipe.id,
      shot.shotId,
      acceptedRecipeCost(recipe),
      reason,
    );
    registerCanvasJob(job, {
      label: `镜头 ${shot.title} · 视觉锚点`,
      nodeId: selectedNode.value?.id ?? activeGroup.value?.memberNodeIds[0] ?? "recipe",
      recipeInstanceId: recipe.id,
    });
    ElMessage.success("视觉锚点任务已进入后台执行");
  } finally {
    recipeBusy.value = false;
  }
}

async function runRecipeVideo(shot: RecipeShotDto) {
  const recipe = activeRecipe.value;
  if (!recipe || !shot.shotId) return;
  let reason: string | undefined;
  const estimatedCost = recipeCostLabel(recipe);
  if (shot.videoCandidates.length) {
    const answer = await ElMessageBox.prompt(
      `请描述整镜动作、运镜或声音问题。若只在 0.5–13 秒区间畸变，请使用局部重编。预计费用 ${estimatedCost}。`,
      "重做整镜视频",
      { inputPattern: /\S{4,}/, inputErrorMessage: "重做原因至少需要 4 个有效字符" },
    );
    reason = answer.value;
  } else {
    await ElMessageBox.confirm(
      `将从已批准锚点生成带原生音轨的视频候选，预计费用 ${estimatedCost}。`,
      "确认生成视频",
    );
  }
  recipeBusy.value = true;
  try {
    const job = await canvasApi.runRecipeVideo(
      recipe.id,
      shot.shotId,
      acceptedRecipeCost(recipe),
      reason,
    );
    registerCanvasJob(job, {
      label: `镜头 ${shot.title} · 视频生成`,
      nodeId: selectedNode.value?.id ?? activeGroup.value?.memberNodeIds[0] ?? "recipe",
      recipeInstanceId: recipe.id,
    });
    ElMessage.success("视频任务已进入后台执行");
  } finally {
    recipeBusy.value = false;
  }
}

async function reviewRecipeAsset(
  kind: "anchor_asset" | "video_asset",
  asset: RecipeAssetCandidateDto,
) {
  const recipe = activeRecipe.value;
  if (!recipe) return;
  const blocking = asset.diagnosticStatus !== "passed";
  let decision: "approve" | "override" = "approve";
  let reason: string | undefined;
  if (blocking) {
    const answer = await ElMessageBox.prompt(
      kind === "video_asset"
        ? "该视频的专项语义诊断缺失或失败。若逐帧人工确认可接受，请填写覆盖理由；否则关闭并重做或局部重编。"
        : "该锚点的人物、猫咪、画风或身体结构诊断缺失或失败。若人工确认可接受，请填写覆盖理由；否则关闭并重做锚点。",
      "人工覆盖阻断诊断",
      { inputPattern: /\S{4,}/, inputErrorMessage: "覆盖理由至少需要 4 个有效字符" },
    );
    decision = "override";
    reason = answer.value;
  } else {
    await ElMessageBox.confirm("确认已人工检查身份、画风、身体结构与动作顺序？", "批准当前版本");
  }
  recipeBusy.value = true;
  try {
    await canvasApi.reviewRecipeTarget({
      recipeInstanceId: recipe.id,
      targetType: kind,
      targetId: asset.id,
      targetHash: asset.sha256 ?? undefined,
      decision,
      blockingDiagnosticPresent: blocking,
      reason,
    });
    await loadCanvas();
  } finally {
    recipeBusy.value = false;
  }
}

async function reviewRecipeSequence(sequence: RecipeSequenceCandidateDto) {
  const recipe = activeRecipe.value;
  if (!recipe) return;
  await ElMessageBox.confirm(
    "请完整播放并确认每镜原生音轨、转场拼接、人物与猫咪身份后再批准。",
    "批准最终音画",
    { confirmButtonText: "已完整检查并批准", cancelButtonText: "继续检查" },
  );
  recipeBusy.value = true;
  try {
    await canvasApi.reviewRecipeTarget({
      recipeInstanceId: recipe.id,
      targetType: "final_sequence",
      targetId: sequence.id,
      targetRevision: sequence.revision,
      decision: "approve",
    });
    await loadCanvas();
  } finally {
    recipeBusy.value = false;
  }
}

async function requestRecipeAssetChanges(
  kind: "anchor_asset" | "video_asset",
  asset: RecipeAssetCandidateDto,
) {
  const recipe = activeRecipe.value;
  if (!recipe) return;
  const answer = await ElMessageBox.prompt(
    kind === "anchor_asset"
      ? "请填写身份、画风、身体结构或构图问题；该问题会固定到当前锚点版本。"
      : "请填写动作、运镜、声音、身份或身体结构问题；该问题会固定到当前视频版本。",
    "退回当前候选",
    { inputPattern: /\S{4,}/, inputErrorMessage: "退回原因至少需要 4 个有效字符" },
  );
  recipeBusy.value = true;
  try {
    await canvasApi.reviewRecipeTarget({
      recipeInstanceId: recipe.id,
      targetType: kind,
      targetId: asset.id,
      targetHash: asset.sha256 ?? undefined,
      decision: "request_changes",
      issues: [answer.value],
      reason: answer.value,
    });
    await loadCanvas();
  } finally {
    recipeBusy.value = false;
  }
}

function openRecipeVideoEdit(asset: RecipeAssetCandidateDto) {
  const node = canvas.value?.nodes.find((item) => (
    item.type === "VideoAssetNode"
    && (item.objectId === asset.id || String(item.data.assetId ?? "") === asset.id)
  ));
  if (!node) {
    ElMessage.error("画布尚未投影该视频资产，请刷新后再进入局部重编");
    return;
  }
  openVideoEditor(node, "compact");
}

function editRecipeShot(shot: RecipeShotDto) {
  const node = canvas.value?.nodes.find((item) => (
    item.type === "ShotBeatNode" && (item.objectId === shot.beatId || item.id === shot.beatId)
  ));
  if (!node) {
    ElMessage.error("画布尚未投影该镜头，请刷新后重试");
    return;
  }
  editObject(node);
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

async function saveBriefFromConsole(payload: {
  theme: string;
  targetDurationSeconds: number;
  aspectRatio: string;
}) {
  busyAction.value = "brief";
  Object.assign(briefForm, payload);
  try {
    await saveBrief();
    ElMessage.success("创意简报已保存为新版本");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    busyAction.value = "";
  }
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

function defaultVideoEditDraft(node: CanvasNodeDto): VideoEditConsoleDraft {
  const durationMs = Number(node.data.durationMs ?? 13_000);
  return {
    startMs: Number(node.data.defaultEditStartMs ?? 0),
    endMs: Number(node.data.defaultEditEndMs ?? Math.min(durationMs, 7_000)),
    instruction: "",
    referenceAssetIds: videoEditReferences.value.map((item) => item.id).slice(0, 6),
    annotations: [],
  };
}

function updateVideoEditDraft(nodeId: string, draft: VideoEditConsoleDraft) {
  videoEditDrafts[nodeId] = draft;
}

function openVideoEditor(node: CanvasNodeDto, mode: "compact" | "full" = "compact") {
  if (!videoEditDrafts[node.id]) videoEditDrafts[node.id] = defaultVideoEditDraft(node);
  interaction.value = mode === "full"
    ? { mode: "fullscreen_editing", nodeId: node.id, returnMode: "video_segment_reshoot" }
    : { mode: "video_segment_reshoot", nodeId: node.id };
  initializeOverlaySession();
}

function closeVideoEditor() {
  if (interaction.value.mode === "fullscreen_editing") {
    interaction.value = interaction.value.returnMode === "video_segment_reshoot"
      ? { mode: "video_segment_reshoot", nodeId: interaction.value.nodeId }
      : { mode: "video_selected", nodeId: interaction.value.nodeId };
  } else if (selectedNode.value?.type === "VideoAssetNode") {
    interaction.value = { mode: "video_selected", nodeId: selectedNode.value.id };
  }
  initializeOverlaySession();
}

async function composeCanvasSequence(node: CanvasNodeDto) {
  try {
    const submitted = await api.buildSequence(props.projectId, []);
    registerTask(submitted.jobId, {
      kind: "build_sequence",
      label: "合成最终音画",
      projectId: props.projectId,
      canvasNodeId: node.id,
      operationKey: "sequence:build",
      workflowStage: "sequence",
    });
    ElMessage.success("音画合成已进入后台执行；时间线节点会持续显示进度");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

async function openVisualPresetLibrary() {
  visualPresetLibraryVisible.value = true;
  if (visualPresets.value.length || visualPresetLoading.value) return;
  visualPresetLoading.value = true;
  try {
    visualPresets.value = await canvasApi.visualPresets();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    visualPresetLoading.value = false;
  }
}

async function loadEpisodeVisualProfile() {
  if (episodeVisualProfileLoading.value) return;
  episodeVisualProfileLoading.value = true;
  try {
    episodeVisualProfile.value = await canvasApi.episodeVisualProfile(props.projectId);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) {
      ElMessage.error(error instanceof Error ? error.message : String(error));
    }
  } finally {
    episodeVisualProfileLoading.value = false;
  }
}

async function applyVisualPreset(presetKey: VisualPresetKey) {
  visualPresetApplying.value = true;
  try {
    const applied = await canvasApi.applyVisualPreset(props.projectId, presetKey);
    episodeVisualProfile.value = applied.visualProfile;
    visualPresetLibraryVisible.value = false;
    await loadCanvas(false);
    const appliedNode = canvas.value?.nodes.find((node) => node.id === applied.canvasNodeId);
    if (appliedNode) selectCanvasNode(appliedNode);
    ElMessage.success(`已复用 ${applied.reusedAssetIds.length} 个 Canon 资产并创建显式节点与血缘`);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    visualPresetApplying.value = false;
  }
}

async function saveEpisodeVisualProfile(draft: VisualProfileDraft) {
  const profile = episodeVisualProfile.value;
  if (!profile) {
    ElMessage.warning("请先从素材与预设库应用 Canon-v3 预设");
    return;
  }
  episodeVisualProfileSaving.value = true;
  try {
    episodeVisualProfile.value = await canvasApi.updateEpisodeVisualProfile(
      props.projectId,
      profile.revision,
      draft,
    );
    await loadCanvas(false);
    ElMessage.success("本集视觉档案已创建新版本；相关下游资产已标记为过期");
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      await loadEpisodeVisualProfile();
      ElMessage.warning("视觉档案已被其他操作更新，已加载最新版本，请核对后再保存");
    } else {
      ElMessage.error(error instanceof Error ? error.message : String(error));
    }
  } finally {
    episodeVisualProfileSaving.value = false;
  }
}

function runContextToolbarAction(action: CanvasNodeActionDto) {
  const node = selectedNode.value;
  if (!node || !action.enabled) return;
  if (node.type === "VideoAssetNode") {
    if (action.key === "edit") openVideoEditor(node, "full");
    else if (action.key === "segment_reshoot") openVideoEditor(node, "compact");
    else if (action.key === "download") {
      const link = document.createElement("a");
      link.href = mediaContentUrl(node);
      link.download = String(node.data.title ?? "video.mp4");
      link.click();
    } else if (action.key === "fullscreen") {
      const video = document.querySelector<HTMLVideoElement>(`[data-canvas-node-id="${node.id}"] video`);
      void video?.requestFullscreen?.();
    } else ElMessage.error(`视频动作 ${action.key} 缺少前端处理器，已阻止执行`);
    return;
  }
  switch (action.key) {
    case "recipe_primary": void runRecipePrimary([]); break;
    case "toggle_children": ElMessage.info("六阶段真实节点已全部展示，无需展开聚合节点"); break;
    case "complete_creative": void completeCreativeBrief(); break;
    case "review_creative": void reviewCreativeBrief(); break;
    case "generate_character_design": void generateCharacterDesign(); break;
    case "review_character_design": {
      if (node.type === "CharacterDesignNode") {
        ElMessage.info("请在角色设计候选区选择一个版本进行审核");
        break;
      }
      const characterNode = canvas.value?.nodes.find((item) => (
        item.type === "CharacterDesignNode" && Array.isArray(item.data.candidates) && item.data.candidates.length
      ));
      if (characterNode) selectCanvasNode(characterNode);
      else ElMessage.info("角色设计候选尚未生成完成");
      break;
    }
    case "edit_brief": Object.assign(briefForm, node.data); briefVisible.value = true; break;
    case "edit_subject": activateCanvasNode(node); break;
    case "open_asset_library": void openVisualPresetLibrary(); break;
    case "apply_visual_preset": void openVisualPresetLibrary(); break;
    case "edit_episode_visual_profile": {
      void loadEpisodeVisualProfile();
      selectCanvasNode(node);
      break;
    }
    case "assist_subject": openSubjectAssistant(node); break;
    case "generate_stories": {
      if (activeGroup.value) void runCanvasGroupAction(activeGroup.value, "run_group");
      else void runStories();
      break;
    }
    case "approve_story": if (node.objectId) void approveStory(node.objectId); break;
    case "inspect_prompt": {
      const promptId = String(node.data.promptId ?? node.data.candidatePromptId ?? node.data.criticPromptId ?? "");
      if (promptId) void inspectPrompt(promptId);
      else ElMessage.info("该节点还没有可审计的 Prompt 记录");
      break;
    }
    case "review_story": {
      const candidate = (canvas.value?.nodes ?? [])
        .filter((item) => item.type === "StoryCandidateNode" && item.data.status === "candidate")
        .sort((left, right) => Number((right.data.scorecard as Record<string, unknown> | undefined)?.average ?? 0) - Number((left.data.scorecard as Record<string, unknown> | undefined)?.average ?? 0))[0];
      if (candidate) selectCanvasNode(candidate);
      else ElMessage.info("当前没有等待人工审核的故事候选");
      break;
    }
    case "storyboard_from_story": void runStoryboardCreation({ mode: "from_story", instruction: "" }); break;
    case "storyboard_from_characters": startStoryboardReferenceSelection(); break;
    case "storyboard_manual": openManualStoryboard(); break;
    case "review_storyboard": void reviewStoryboardRevision(); break;
    case "open_scene": {
      selectCanvasNode(node);
      break;
    }
    case "edit_shot": editObject(node); break;
    case "generate_anchor": {
      const recipeShot = activeRecipe.value?.shots.find((shot) => shot.beatId === node.objectId || shot.beatId === node.id);
      if (recipeShot) void runRecipeAnchor(recipeShot);
      else {
        const generationNode = canvas.value?.nodes.find((candidate) => candidate.type === "ImageGenerationNode" && canvas.value?.edges.some((edge) => edge.sourceNodeId === node.id && edge.targetNodeId === candidate.id));
        if (generationNode) void openGenerationComposer(generationNode);
        else ElMessage.warning("请先连接图片生成节点，再生成视觉锚点");
      }
      break;
    }
    case "open_generator": void openGenerationComposer(node); break;
    case "select_references": startReferenceSelection(); break;
    case "review_asset": {
      const asset = (canvas.value?.edges ?? []).filter((edge) => edge.targetNodeId === node.id)
        .map((edge) => canvas.value?.nodes.find((item) => item.id === edge.sourceNodeId))
        .find((item) => item && ["ImageAssetNode", "VideoAssetNode"].includes(item.type));
      if (asset) selectCanvasNode(asset);
      else ElMessage.info("当前审核节点还没有候选资产");
      break;
    }
    case "compose_sequence": activeRecipe.value ? void runRecipePrimary([]) : void composeCanvasSequence(node); break;
    case "export_sequence": {
      const url = String(node.data.contentUrl ?? "");
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      else ElMessage.info("最终成片尚未批准，暂不能导出");
      break;
    }
    case "upload_reference": requestReferenceUpload(node); break;
    case "select_history": openReferenceHistory(node); break;
    case "create_subject": selectedNodeId.value = node.id; subjectVisible.value = true; break;
    case "inspect_asset": window.open(mediaContentUrl(node), "_blank", "noopener,noreferrer"); break;
    case "download": {
      const link = document.createElement("a");
      link.href = mediaContentUrl(node);
      link.download = String(node.data.title ?? "asset");
      link.click();
      break;
    }
    case "edit": activateCanvasNode(node); break;
    case "archive_node": void archiveCanvasNode(node); break;
    case "restore_node": ElMessage.info("请通过移除后的撤销通知恢复节点"); break;
    case "unavailable": ElMessage.info(action.disabledReason ?? "该节点尚未配置可执行处理器"); break;
    default:
      ElMessage.error(`动作 ${action.key} 缺少前端处理器，已阻止执行`);
  }
}

function handlePaneClick() {
  if (spacePressed.value || panningCanvas.value || interaction.value.mode === "reference_picking") return;
  selectedGroupId.value = null;
  closeContextPanel();
}

function openNodeContextMenu(node: CanvasNodeDto, event: MouseEvent) {
  selectedGroupId.value = null;
  interaction.value = selectedInteraction(node);
  generationComposerNode.value = null;
  localConsoleSession.value = null;
  localConsoleGeometry.value = null;
  nodeContextMenu.value = {
    nodeId: node.id,
    left: Math.max(12, Math.min(window.innerWidth - 236, event.clientX)),
    top: Math.max(12, Math.min(window.innerHeight - 92, event.clientY)),
  };
  void nextTick(() => initializeOverlaySession());
}

function archiveAction(node: CanvasNodeDto): CanvasNodeActionDto | undefined {
  return node.availableActions?.find((action) => action.key === "archive_node");
}

async function archiveCanvasNode(node: CanvasNodeDto) {
  const action = archiveAction(node);
  nodeContextMenu.value = null;
  if (!action?.enabled) {
    ElMessage.info(action?.disabledReason ?? "该节点受当前工作流保护，不能从画布移除");
    return;
  }
  if (!canvas.value || archivingNodeId.value) return;
  archivingNodeId.value = node.id;
  try {
    const result = await canvasApi.archiveNode(
      props.projectId,
      node.id,
      canvas.value.layoutVersion,
    );
    closeContextPanel();
    await loadCanvas(false);
    if (archiveUndoTimer !== null) window.clearTimeout(archiveUndoTimer);
    archiveUndo.value = {
      nodeId: node.id,
      title: String(node.data.title ?? node.objectType ?? "节点"),
      layoutVersion: result.layoutVersion,
    };
    archiveUndoTimer = window.setTimeout(() => {
      archiveUndo.value = null;
      archiveUndoTimer = null;
    }, 8_000);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    archivingNodeId.value = null;
  }
}

async function restoreArchivedNode() {
  const pending = archiveUndo.value;
  if (!pending || !canvas.value) return;
  try {
    await canvasApi.restoreNode(
      props.projectId,
      pending.nodeId,
      canvas.value.layoutVersion,
    );
    if (archiveUndoTimer !== null) window.clearTimeout(archiveUndoTimer);
    archiveUndoTimer = null;
    archiveUndo.value = null;
    await loadCanvas(false);
    ElMessage.success("节点已恢复到画布");
  } catch (error) {
    await loadCanvas(false);
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

function groupExecutionFor(groupId: string) {
  return taskCenterItems.value.find((item) => (
    item.canvasGroupId === groupId
    && ["queued", "pending", "running", "restart_pending", "awaiting_review", "failed"].includes(item.status)
  ));
}

function handleMoveStart() {
  if (spacePressed.value) panningCanvas.value = true;
}

function handleMoveEnd() {
  window.setTimeout(() => { panningCanvas.value = false; }, 0);
}

function handleWorkspaceKeyDown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    if (nodeContextMenu.value) {
      event.preventDefault();
      nodeContextMenu.value = null;
      return;
    }
    if (interaction.value.mode === "idle") return;
    event.preventDefault();
    if (interaction.value.mode === "reference_picking") cancelReferenceSelection();
    else {
      interaction.value = escapeInteraction(interaction.value);
      if (interaction.value.mode === "idle") generationComposerNode.value = null;
      if (interaction.value.mode === "idle") {
        localConsoleSession.value = null;
        localConsoleGeometry.value = null;
      }
    }
    return;
  }
  if (
    (event.key === "Delete" || event.key === "Backspace")
    && !isEditableTarget(event.target)
    && selectedNode.value
    && ["node_selected", "video_selected"].includes(interaction.value.mode)
  ) {
    event.preventDefault();
    void archiveCanvasNode(selectedNode.value);
    return;
  }
  if (event.code !== "Space" || isEditableTarget(event.target)) return;
  spacePressed.value = true;
  event.preventDefault();
}

function handleWorkspaceKeyUp(event: KeyboardEvent) {
  if (event.code === "Space") spacePressed.value = false;
}

function handleWindowBlur() {
  spacePressed.value = false;
  panningCanvas.value = false;
}

function mediaAssetId(node: CanvasNodeDto): string {
  return String(node.data.assetId ?? node.objectId ?? node.id);
}

function mediaContentUrl(node: CanvasNodeDto): string {
  return String(node.data.contentUrl ?? assetContentUrl(mediaAssetId(node)));
}

const videoEditReferences = computed(() => {
  const references = new Map<string, {
    id: string;
    title: string;
    thumbnailUrl: string;
    semanticRole: string;
  }>();
  for (const node of canvas.value?.nodes ?? []) {
    if (!["ReferenceAssetNode", "ImageAssetNode", "SubjectNode"].includes(node.type)) continue;
    const title = String(node.data.title ?? node.data.name ?? "参考素材");
    const nodeAssets = Array.isArray(node.data.assets)
      ? node.data.assets as Array<Record<string, unknown>>
      : [];
    const subjectReferences = Array.isArray(node.data.references)
      ? node.data.references as Array<Record<string, unknown>>
      : [];
    for (const item of [...nodeAssets, ...subjectReferences]) {
      const assetId = String(item.assetId ?? item.id ?? "");
      if (!assetId) continue;
      references.set(assetId, {
        id: assetId,
        title,
        thumbnailUrl: String(item.thumbnailUrl ?? item.contentUrl ?? assetContentUrl(assetId)),
        semanticRole: String(item.semanticRole ?? node.data.semanticRole ?? node.data.kind ?? "reference"),
      });
    }
    const assetId = node.type === "SubjectNode"
      ? String(node.data.assetId ?? "")
      : String(node.data.assetId ?? node.objectId ?? "");
    if (assetId && node.data.thumbnailUrl) {
      references.set(assetId, {
        id: assetId,
        title,
        thumbnailUrl: String(node.data.thumbnailUrl),
        semanticRole: String(node.data.semanticRole ?? node.data.kind ?? "reference"),
      });
    }
  }
  return [...references.values()];
});

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

function openReferenceHistory(node: CanvasNodeDto) {
  selectedNodeId.value = node.id;
  referenceBindingNode.value = node;
  void openAssetHistory("image");
}

function currentAssetBindings(node: CanvasNodeDto) {
  if (!Array.isArray(node.data.assets)) return [];
  return (node.data.assets as Array<Record<string, unknown>>)
    .map((item) => ({
      assetId: String(item.assetId ?? item.id ?? ""),
      semanticRole: String(item.semanticRole ?? node.data.semanticRole ?? "other"),
    }))
    .filter((item) => item.assetId);
}

async function bindHistoricalAsset(asset: CanvasAssetHistoryDto, allowMove = false) {
  const node = referenceBindingNode.value;
  if (!node) return;
  const bindings = currentAssetBindings(node);
  if (!bindings.some((item) => item.assetId === asset.id)) {
    bindings.push({
      assetId: asset.id,
      semanticRole: String(node.data.semanticRole ?? "other"),
    });
  }
  try {
    await canvasApi.bindNodeAssets(node.id, node.revision ?? 1, bindings, allowMove);
    assetHistoryVisible.value = false;
    referenceBindingNode.value = null;
    await loadCanvas();
    ElMessage.success("历史素材已绑定到参考节点");
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && !allowMove) {
      await ElMessageBox.confirm(
        "该素材已属于其他画布节点。是否明确将其移至当前节点？原节点会保留可追溯记录并将下游标记为 stale。",
        "素材所有权冲突",
        { confirmButtonText: "移至此节点", cancelButtonText: "保留原绑定" },
      );
      await bindHistoricalAsset(asset, true);
      return;
    }
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
}

function requestReferenceUpload(node: CanvasNodeDto) {
  selectedNodeId.value = node.id;
  referenceBindingNode.value = node;
  void nextTick(() => referenceUploadInput.value?.click());
}

async function uploadReferenceAsset(event: Event) {
  const input = event.currentTarget as HTMLInputElement;
  const file = input.files?.[0];
  const node = referenceBindingNode.value;
  if (!file || !node) return;
  try {
    const asset = await api.uploadReference(
      props.projectId,
      "generation_reference",
      "identity",
      String(node.data.title ?? file.name),
      file,
    );
    await canvasApi.bindNodeAssets(
      node.id,
      node.revision ?? 1,
      [{
        assetId: asset.id,
        semanticRole: String(node.data.semanticRole ?? (isProductAd.value ? "packshot_front" : "other")),
      }],
      false,
    );
    await loadCanvas();
    ElMessage.success("素材已上传并绑定到参考节点");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    input.value = "";
    referenceBindingNode.value = null;
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

async function openSubjectLibrary() {
  if (!generationComposerNode.value) return;
  subjectLibraryVisible.value = true;
  subjectLibraryLoading.value = true;
  subjectLibraryError.value = "";
  try {
    subjectLibrary.value = await canvasApi.subjects(props.projectId);
  } catch (error) {
    subjectLibraryError.value = error instanceof Error ? error.message : String(error);
  } finally {
    subjectLibraryLoading.value = false;
  }
}

function chooseSubjectForGenerator(subject: SubjectDto) {
  const node = canvas.value?.nodes.find(
    (item) => item.type === "SubjectNode" && item.objectId === subject.id,
  );
  if (!node || !generationComposerNode.value) {
    ElMessage.warning("该主体尚未投影为可连接的画布节点，请刷新画布后重试");
    return;
  }
  startReferenceSelection();
  if (!referenceConnection(node, generationComposerNode.value)) {
    ElMessage.warning("当前生成节点不接受该主体端口");
    return;
  }
  selectedReferenceNodeIds.value = new Set([...selectedReferenceNodeIds.value, node.id]);
  subjectLibraryVisible.value = false;
}

function missingSubjectReferences(subject: SubjectDto): string[] {
  const expected = subject.kind === "product"
    ? ["packshot_front", "label_detail", "material", "size_scale"]
    : subject.kind === "person" || subject.kind === "animal"
      ? ["front", "side", "full_body"]
      : [];
  const actual = new Set<string>((subject.references ?? []).map((item) => item.semanticRole));
  return expected.filter((item) => !actual.has(item));
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
  selectedNodeId.value = node.id;
  generationComposerNode.value = node;
  initializeOverlaySession();
  generationCapability.value = null;
  generationReferences.value = [];
  const persistedConfig = node.data.generationConfig;
  const persistedAnnotations = persistedConfig && typeof persistedConfig === "object"
    ? (persistedConfig as Record<string, unknown>).referenceAnnotations
    : null;
  generationAnnotations.value = Array.isArray(persistedAnnotations)
    ? [...persistedAnnotations] as GenerationReferenceAnnotationDto[]
    : [];
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
      cameraMotions: raw.cameraMotions ?? [],
      estimatedCostMicros: raw.estimatedCostMicros,
    };
    const incomingIds = new Set(
      canvas.value?.edges.filter((edge) => edge.targetNodeId === node.id).map((edge) => edge.sourceNodeId),
    );
    const sources = canvas.value?.nodes.filter((item) => incomingIds.has(item.id)) ?? [];
    const candidates: Array<{ assetId: string; subjectRevisionId?: string; semanticRole: string }> = [];
    for (const source of sources) {
      if (["ReferenceAssetNode", "ImageAssetNode"].includes(source.type)) {
        if (Array.isArray(source.data.assets)) {
          for (const asset of source.data.assets as Array<Record<string, unknown>>) {
            const assetId = String(asset.assetId ?? asset.id ?? "");
            if (assetId) candidates.push({
              assetId,
              semanticRole: String(asset.semanticRole ?? source.data.semanticRole ?? "reference"),
            });
          }
        }
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
    const uniqueCandidates = [...new Map(candidates.map((item) => [item.assetId, item])).values()];
    generationReferences.value = uniqueCandidates.map((item, index) => ({
      ...item,
      providerIncluded: index < maxReferences,
      providerSlot: index < maxReferences ? `reference_image_${index + 1}` : null,
      omissionReason: index < maxReferences ? null : `当前 ${row.model} 最多接受 ${maxReferences} 张参考图`,
    }));
  } finally {
    generationLoading.value = false;
    initializeOverlaySession();
  }
}

function initialGenerationConfig(node: CanvasNodeDto): Record<string, unknown> {
  const persisted = node.data.generationConfig;
  return {
    ...(persisted && typeof persisted === "object" && !Array.isArray(persisted) ? persisted : {}),
    candidateCount: (
      persisted && typeof persisted === "object" && !Array.isArray(persisted)
        ? (persisted as Record<string, unknown>).candidateCount
        : undefined
    ) ?? node.data.candidateCount,
  };
}

const generationDraftSaveChains = new Map<string, Promise<void>>();
function saveNodeGenerationDraft(node: CanvasNodeDto, payload: Record<string, unknown>) {
  const previous = generationDraftSaveChains.get(node.id) ?? Promise.resolve();
  const current = previous.then(async () => {
    const latest = canvas.value?.nodes.find((item) => item.id === node.id) ?? node;
    const saved = await canvasApi.saveNodeGenerationConfig(node.id, latest.revision ?? 1, payload);
    const revision = Number(saved.revision ?? latest.revision ?? 1);
    const updated = {
      ...latest,
      revision,
      data: { ...latest.data, generationConfig: payload },
    };
    if (canvas.value) {
      canvas.value = {
        ...canvas.value,
        nodes: canvas.value.nodes.map((item) => item.id === node.id ? updated : item),
      };
    }
    flowNodes.value = flowNodes.value.map((item) => item.id === node.id
      ? { ...item, data: { ...item.data, node: updated } }
      : item);
    if (generationComposerNode.value?.id === node.id) generationComposerNode.value = updated;
  }).catch((error) => {
    ElMessage.error(`生成器草稿保存失败：${error instanceof Error ? error.message : String(error)}`);
  }).finally(() => {
    if (generationDraftSaveChains.get(node.id) === current) generationDraftSaveChains.delete(node.id);
  });
  generationDraftSaveChains.set(node.id, current);
}

function saveActiveNodeGenerationDraft(payload: Record<string, unknown>) {
  const node = generationComposerNode.value;
  if (node) saveNodeGenerationDraft(node, payload);
}

function openReferenceAnnotation(assetIds: string[]) {
  const assetId = assetIds[0];
  if (!assetId) {
    ElMessage.warning("请先选择一张实际进入供应商请求的图片参考");
    return;
  }
  annotationAssetId.value = assetId;
}

function saveReferenceAnnotation(annotation: GenerationReferenceAnnotationDto) {
  generationAnnotations.value = [
    ...generationAnnotations.value.filter((item) => item.assetId !== annotation.assetId),
    annotation,
  ];
  annotationAssetId.value = null;
  ElMessage.success("归一化标注已加入本次生成配置");
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
    RecipeGroupNode: 0,
    BriefNode: 0,
    SubjectNode: 0,
    StoryPlannerNode: 1,
    StoryCandidateNode: 2,
    ApprovalGateNode: 3,
    CharacterDesignNode: 4,
    StoryboardDirectorNode: 5,
    SceneNode: 6,
    ShotBeatNode: 7,
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
  const businessNodes = flowNodes.value.filter((flowNode) => flowNode.type === "canvas").map((flowNode) => {
    const node = flowNode.data.node as CanvasNodeDto;
    const column = columns[node.type] ?? 7;
    const row = rows.get(column) ?? 0;
    rows.set(column, row + 1);
    return { ...flowNode, position: { x: 90 + column * 360, y: 100 + row * 230 } };
  });
  flowNodes.value = withGroupFrames(businessNodes);
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
  const businessNodes = flowNodes.value.filter((node) => node.type === "canvas");
  const operation = {
    operationId: crypto.randomUUID(),
    type: operationType,
    createdAt: new Date().toISOString(),
    nodes: businessNodes.map((node) => ({
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
        nodes: businessNodes.map((node) => ({
          nodeId: node.id,
          x: node.position.x,
          y: node.position.y,
        })),
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
    syncStatus.value = classifyCanvasSaveError(error, navigator.onLine);
    if (error instanceof ApiError && error.status === 422) {
      const pendingIds = canvasSyncQueue.pending(props.projectId).map((item) => item.operationId);
      canvasSyncQueue.quarantine(props.projectId, pendingIds, error.detail);
      ElMessage.error(`布局操作字段无效，已隔离且不会重复重放：${error.message}`);
      return;
    }
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

function nodeDragStart(event: { node: Node }) {
  const group = event.node.data?.group as CanvasGroupDto | undefined;
  if (!group) return;
  const members: Record<string, { x: number; y: number }> = {};
  for (const node of flowNodes.value) {
    if (group.memberNodeIds.includes(node.id)) members[node.id] = { ...node.position };
  }
  groupDragSnapshot.value = {
    groupId: group.id,
    x: event.node.position.x,
    y: event.node.position.y,
    members,
  };
}

function nodeDragStop(event?: { node?: Node }) {
  const snapshot = groupDragSnapshot.value;
  const draggedGroup = event?.node?.data?.group as CanvasGroupDto | undefined;
  if (snapshot && draggedGroup?.id === snapshot.groupId && event?.node) {
    const deltaX = event.node.position.x - snapshot.x;
    const deltaY = event.node.position.y - snapshot.y;
    flowNodes.value = flowNodes.value.map((node) => {
      const initial = snapshot.members[node.id];
      return initial
        ? { ...node, position: { x: initial.x + deltaX, y: initial.y + deltaY } }
        : node;
    });
  }
  groupDragSnapshot.value = null;
  scheduleOverlayAnchorUpdate();
  syncStatus.value = "local";
  void persistLayout(draggedGroup ? "move_group" : "move_node");
}

watch([selectedNodeId, consoleKind], async ([nodeId, kind], [previousNodeId, previousKind]) => {
  if (!nodeId) {
    localConsoleSession.value = null;
    localConsoleGeometry.value = null;
    return;
  }
  if (nodeId !== previousNodeId || kind !== previousKind) {
    if (localConsoleSession.value?.nodeId === nodeId) {
      initializeOverlaySession();
      return;
    }
    localConsoleSession.value = null;
    localConsoleGeometry.value = null;
    await nextTick();
    initializeOverlaySession();
  }
});
watch(() => props.projectId, async () => {
  consumedFocusRequest = "";
  interaction.value = { mode: "idle" };
  generationComposerNode.value = null;
  localConsoleSession.value = null;
  localConsoleGeometry.value = null;
  canvas.value = null;
  flowNodes.value = [];
  flowEdges.value = [];
  await loadCanvas(true);
});
watch(
  () => [props.focusNodeId, props.focusRequestKey],
  () => void focusRequestedNode(),
);
watch(
  () => projectSignals.value[props.projectId]?.revision,
  (revision, previous) => {
    if (revision == null || revision === previous) return;
    void loadCanvas(false);
  },
);
onMounted(async () => {
  await loadCanvas(true);
  window.addEventListener("online", replayPendingLayout);
  window.addEventListener("resize", handleWorkspaceResize);
  window.addEventListener("keydown", handleWorkspaceKeyDown);
  window.addEventListener("keyup", handleWorkspaceKeyUp);
  window.addEventListener("blur", handleWindowBlur);
});
onBeforeUnmount(() => {
  if (overlayPositionFrame !== null) cancelAnimationFrame(overlayPositionFrame);
  if (archiveUndoTimer !== null) window.clearTimeout(archiveUndoTimer);
  window.removeEventListener("online", replayPendingLayout);
  window.removeEventListener("resize", handleWorkspaceResize);
  window.removeEventListener("keydown", handleWorkspaceKeyDown);
  window.removeEventListener("keyup", handleWorkspaceKeyUp);
  window.removeEventListener("blur", handleWindowBlur);
});
</script>

<template>
  <section class="canvas-workspace">
    <input
      ref="referenceUploadInput"
      data-role="reference-upload"
      type="file"
      accept="image/*,video/*,audio/*"
      hidden
      @change="uploadReferenceAsset"
    />
    <header class="canvas-topbar">
      <div>
        <span class="eyebrow">AIGC MEDIA CANVAS · V2</span>
        <h1>{{ isProductAd ? '产品广告媒体画布' : isShortDrama ? '类型化短剧创作画布' : '通用媒体画布' }}</h1>
      </div>
      <div class="topbar-state">
        <span class="sync-indicator" :class="`sync-${syncStatus}`" />
        {{ backgroundRefreshing ? '正在同步…' : syncLabel }}
      </div>
    </header>

    <div class="canvas-stage">
      <section v-if="initialLoading" class="canvas-initial-skeleton" aria-busy="true" aria-label="正在加载画布">
        <div class="skeleton-node skeleton-node-wide" />
        <div class="skeleton-link" />
        <div class="skeleton-node" />
        <div class="skeleton-link" />
        <div class="skeleton-node" />
      </section>
      <div
        ref="canvasSurface"
        class="canvas-surface"
        :class="{ 'space-ready': spacePressed, panning: panningCanvas, 'low-detail': viewport.zoom < 0.48 }"
        tabindex="-1"
        aria-label="无限媒体画布"
      >
      <aside v-if="referenceSelectionTarget" class="reference-selection-banner" role="status">
        <div><b>从画布选择参考</b><span>兼容素材已高亮；按住 Space 可平移寻找素材，最多选择 6 张图片</span></div>
        <button type="button" @click="cancelReferenceSelection">取消</button>
        <button class="primary" type="button" @click="finishReferenceSelection">完成选择（{{ selectedReferenceCount }}）</button>
      </aside>
      <div class="canvas-shortcut-hint" aria-hidden="true"><kbd>Space</kbd> + 拖动平移画布 · 点击空白取消选择 · <kbd>Esc</kbd> 退出当前操作</div>
      <VueFlow
        v-model:nodes="flowNodes"
        v-model:edges="flowEdges"
        :min-zoom="0.18"
        :max-zoom="1.8"
        :only-render-visible-elements="false"
        :default-viewport="canvas?.viewport"
        :pan-on-drag="false"
        pan-activation-key-code="Space"
        @connect="connect"
        @pane-click="handlePaneClick"
        @move="scheduleOverlayAnchorUpdate"
        @move-start="handleMoveStart"
        @move-end="handleMoveEnd"
        @node-drag="scheduleOverlayAnchorUpdate"
        @node-drag-start="nodeDragStart"
        @node-drag-stop="nodeDragStop"
      >
        <Background pattern-color="#29303a" :gap="24" :size="1" />
        <template #node-canvasGroup="{ data }">
          <section
            class="canvas-group-frame"
            :class="{ selected: selectedGroupId === data.group.id }"
            :style="{ '--group-color': data.group.color }"
            :aria-label="`${data.group.title}分组`"
            @click.stop="selectCanvasGroup(data.group)"
          >
            <div class="canvas-group-heading">
              <span>分组 {{ data.group.memberNodeIds.length }} 个节点</span>
              <b>{{ data.group.title }}</b>
              <small v-if="data.group.blocker">{{ data.group.blocker }}</small>
            </div>
            <div class="group-phase-strip" aria-label="六阶段进度">
              <span
                v-for="phase in data.group.phaseProgress"
                :key="phase.key"
                :class="phase.status"
              >{{ phase.label }}</span>
            </div>
            <nav v-if="selectedGroupId === data.group.id" class="canvas-group-toolbar" aria-label="分组操作">
              <button
                v-for="action in data.group.availableActions"
                :key="action.key"
                type="button"
                :disabled="!action.enabled || recipeBusy"
                :title="action.disabledReason ?? action.label"
                @click.stop="runCanvasGroupAction(data.group, action.key)"
              >{{ action.label }}</button>
              <span v-if="groupExecutionFor(data.group.id)" class="group-execution-state">
                {{ groupExecutionFor(data.group.id)?.label }} · {{ groupExecutionFor(data.group.id)?.status }}
              </span>
            </nav>
          </section>
        </template>
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
            :selected="selectedNodeId === data.node.id"
            :selection-state="selectionStateFor(data.node)"
            :selection-index="referenceSelectionIndex(data.node.id)"
            @select-node="selectCanvasNode"
            @activate-node="activateCanvasNode"
            @inspect-prompt="inspectPrompt"
            @approve-story="approveStory"
            @edit-object="editObject"
            @promote-candidate="promoteCandidate"
            @edit-video="openVideoEditor"
            @assist-subject="openSubjectAssistant"
            @open-composer="openGenerationComposer"
            @upload-reference="requestReferenceUpload"
            @select-history="openReferenceHistory"
            @create-subject="(node) => { selectedNodeId = node.id; subjectVisible = true; }"
            @open-recipe="activateCanvasNode"
            @open-context-menu="openNodeContextMenu"
          />
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

      <Teleport to="body">
        <CanvasContextToolbar
          v-if="selectedNode && showContextToolbar && localConsoleSession?.nodeId === selectedNode.id && !referenceSelectionTarget && !videoEditorNode"
          :node="selectedNode"
          :style="contextPanelStyle"
          @action="runContextToolbarAction"
          @close="closeContextPanel"
        />
        <CanvasLocalConsole
          v-if="selectedNode && showLocalConsole && !referenceSelectionTarget && !videoEditorNode"
          :style="localConsoleStyle"
          :title="localConsoleTitle"
          :preset="localConsolePreset"
          :fullscreen-available="selectedNode.type === 'StoryboardDirectorNode' || selectedNode.type === 'VideoAssetNode'"
          @fullscreen="selectedNode.type === 'StoryboardDirectorNode' ? openManualStoryboard() : openVideoEditor(selectedNode, 'full')"
          @close="closeContextPanel"
        >
          <VideoEditWorkspace
            v-if="consoleKind === 'video_segment' && selectedNode.type === 'VideoAssetNode'"
            :key="`embedded-${selectedNode.id}`"
            :project-id="projectId"
            :source-asset-id="mediaAssetId(selectedNode)"
            :video-url="mediaContentUrl(selectedNode)"
            :poster-url="String(selectedNode.data.posterUrl ?? '')"
            :duration-ms="Number(selectedNode.data.durationMs ?? 13000)"
            :initial-start-ms="Number(selectedNode.data.defaultEditStartMs ?? 0)"
            :initial-end-ms="Number(selectedNode.data.defaultEditEndMs ?? Math.min(Number(selectedNode.data.durationMs ?? 13000), 7000))"
            :references="videoEditReferences"
            :initial-draft="videoEditDrafts[selectedNode.id]"
            :reference-asset-ids="videoEditDrafts[selectedNode.id]?.referenceAssetIds"
            embedded
            @expand="openVideoEditor(selectedNode, 'full')"
            @close="closeVideoEditor"
            @draft-change="updateVideoEditDraft(selectedNode.id, $event)"
            @select-references="startVideoReferenceSelection"
            @submitted="closeVideoEditor(); loadCanvas()"
          />
          <NodeGenerationComposer
            v-else-if="generationComposerNode?.id === selectedNode.id && generationCapability"
            :key="selectedNode.id"
            :node-revision="selectedNode.revision ?? 1"
            :capabilities="generationCapability"
            :actual-references="generationReferences"
            :reference-annotations="generationAnnotations"
            :initial-prompt="String((selectedNode.data.generationConfig as Record<string, unknown> | undefined)?.draftPrompt ?? selectedNode.data.prompt ?? '')"
            :initial-config="initialGenerationConfig(selectedNode)"
            embedded
            @save-config="saveActiveNodeGenerationDraft"
            @generate="submitNodeGeneration"
            @select-references="startReferenceSelection"
            @open-subject-library="openSubjectLibrary"
            @open-annotation="openReferenceAnnotation"
          />
          <StoryboardNodeConsole
            v-else-if="selectedNode.type === 'StoryboardDirectorNode'"
            :approved-story-available="Boolean(approvedStory)"
            :selected-reference-count="storyboardReferenceAssetIds.length"
            :busy="storyboardRunBusy"
            :healing-recipe="Boolean(activeRecipe)"
            :executions="selectedExecutions"
            :storyboard-ready="Boolean(activeRecipe?.progress.shotCount)"
            :storyboard-approved="Boolean(activeRecipe?.progress.storyboardApproved)"
            @run="runStoryboardCreation"
            @manual="openManualStoryboard"
            @select-references="startStoryboardReferenceSelection"
            @open-workflow="openStoryboardWorkflow"
            @approve="reviewStoryboardRevision"
          />
          <VideoAssetPanel
            v-else-if="selectedNode.type === 'VideoAssetNode'"
            :node="selectedNode"
            embedded
            @close="closeContextPanel"
            @edit="(node) => openVideoEditor(node, 'full')"
            @segment-reshoot="(node) => openVideoEditor(node, 'compact')"
          />
          <BriefNodeConsole
            v-else-if="selectedNode.type === 'BriefNode'"
            :node="selectedNode"
            :executions="selectedExecutions"
            :busy="busyAction === 'brief' || recipeBusy"
            @save="saveBriefFromConsole"
            @action="runContextToolbarAction"
          />
          <SceneAssetConsole
            v-else-if="selectedNode.type === 'SceneNode' && selectedNode.objectId"
            :key="selectedNode.id"
            :project-id="projectId"
            :scene-id="selectedNode.objectId"
          />
          <VisualEvidenceConsole
            v-else-if="selectedNode.type === 'SubjectNode' || selectedNode.type === 'StylePresetNode'"
            :key="selectedNode.id"
            :node="selectedNode"
            :profile="episodeVisualProfile"
            :loading="episodeVisualProfileLoading"
            :saving="episodeVisualProfileSaving"
            @open-library="openVisualPresetLibrary"
            @save="saveEpisodeVisualProfile"
          />
          <section v-else-if="selectedNode.type === 'CharacterDesignNode'" class="character-design-console">
            <header>
              <div><b>{{ selectedNode.data.title }}</b><small>Canon 继续承担 identity；候选图仅用于造型、姿态、比例或构图。</small></div>
              <button type="button" :disabled="recipeBusy" @click="generateCharacterDesign">重新生成本轮三槽位</button>
            </header>
            <div v-if="Array.isArray(selectedNode.data.candidates) && selectedNode.data.candidates.length" class="character-candidate-grid">
              <article v-for="candidate in selectedNode.data.candidates" :key="candidate.assetId ?? candidate.id">
                <img :src="candidate.thumbnailUrl" :alt="String(candidate.title ?? '角色设计候选')" />
                <div><span>{{ candidate.title }}</span><small>{{ selectedNode.data.slot }}</small></div>
                <button type="button" @click="reviewCharacterCandidate(selectedNode, String(candidate.assetId ?? candidate.id))">选择并批准</button>
              </article>
            </div>
            <p v-else class="empty-console-state">角色图片仍在队列中，完成后将在这里显示候选与真实资产版本。</p>
          </section>
          <CanvasNodeContextPanel
            v-else
            :node="selectedNode"
            :executions="selectedExecutions"
            embedded
            @action="runContextToolbarAction"
          />
        </CanvasLocalConsole>
        <aside
          v-if="nodeContextMenu && contextMenuNode"
          class="canvas-node-context-menu"
          :style="{ left: `${nodeContextMenu.left}px`, top: `${nodeContextMenu.top}px` }"
          role="menu"
          aria-label="节点菜单"
          @click.stop
        >
          <button
            type="button"
            role="menuitem"
            :aria-disabled="contextMenuArchiveAction?.enabled !== true"
            :title="contextMenuArchiveAction?.disabledReason ?? '从画布移除'"
            @click="archiveCanvasNode(contextMenuNode)"
          >从画布移除</button>
        </aside>
        <aside v-if="archiveUndo" class="canvas-archive-undo" role="status" aria-live="polite">
          <span>“{{ archiveUndo.title }}”已从画布移除</span>
          <button type="button" @click="restoreArchivedNode">撤销</button>
        </aside>
      </Teleport>

      <aside class="stage-guide">
        <b>{{ activeGroup ? '一人一猫六阶段链路' : isProductAd ? '产品生产链路' : '创作链路' }}</b>
        <template v-if="activeGroup"><span>补全创意输入</span><span>AI剧情生成</span><span>角色设计</span><span>分镜生成</span><span>视频渲染</span><span>成品导出</span></template>
        <template v-if="isProductAd"><span>参考素材</span><span>候选批次</span><span>选中分支</span><span>视频重编</span><span>审核与时间线</span></template>
        <template v-else-if="!activeGroup"><span>简报与主体</span><span>三案与评分</span><span>人工定稿</span><span>场景与 Beat</span><span>媒体与时间线</span></template>
      </aside>

      <nav class="canvas-toolbar" aria-label="画布工具">
        <button type="button" title="添加节点" @click="nodeLibraryVisible = !nodeLibraryVisible"><Plus /></button>
        <button type="button" title="编辑创意简报" @click="briefVisible = true"><EditPen /></button>
        <button type="button" title="添加通用主体" @click="subjectVisible = true"><Plus /></button>
        <button type="button" title="自动布局" @click="autoLayout"><MagicStick /></button>
        <button type="button" title="聚焦全部节点" @click="fitView({ padding: .16, duration: 320 })"><Aim /></button>
        <button type="button" title="刷新画布" @click="loadCanvas()"><Refresh /></button>
        <button class="asset-library-entry" type="button" title="打开角色库、风格库和最近使用" @click="openVisualPresetLibrary">素材库</button>
        <button v-if="!activeGroup" class="recipe-entry" type="button" title="创建一人一猫治愈短片组合包" @click="recipeCreateVisible = true">一人一猫</button>
      </nav>

      <CanvasNodeLibrary
        v-if="nodeLibraryVisible"
        class="node-library-popover"
        @create-node="createCatalogNode"
        @open-asset-history="openAssetHistory()"
      />

      <aside v-if="isShortDrama && !activeGroup" class="workflow-actions">
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
        <button v-if="productBatchNode" type="button" @click="openGenerationComposer(productBatchNode)"><ConnectionIcon />生成 {{ productBatchNode.data.candidateCount ?? 4 }} 个候选</button>
      </aside>
      </div>
    </div>

    <StoryboardWorkflow
      v-model="storyboardWorkflowVisible"
      :shots="storyboardShots"
      :project-id="projectId"
      :story-revision-id="approvedStory?.objectId ?? undefined"
      :visual-profile-revision-id="episodeVisualProfile?.id"
      :healing-recipe="Boolean(activeRecipe)"
      :target-duration-seconds="activeRecipe?.targetDurationSeconds ?? Number(canvas?.nodes.find((node) => node.type === 'BriefNode')?.data.targetDurationSeconds ?? 0)"
      :saving="storyboardSaving"
      @save="saveStoryboardWorkflow"
      @open-scene="openStoryboardScene"
    />

    <PromptTraceDrawer
      v-model="promptVisible"
      :prompt="selectedPrompt"
      :loading="promptLoading"
    />

    <VisualPresetLibrary
      v-model="visualPresetLibraryVisible"
      :presets="visualPresets"
      :loading="visualPresetLoading"
      :applying="visualPresetApplying"
      @apply="applyVisualPreset"
    />

    <el-dialog v-model="recipeCreateVisible" title="创建一人一猫治愈短片" width="min(560px, calc(100vw - 28px))">
      <el-alert title="仅可用于没有画布业务节点的项目；固定 9:16、720p、无对白与原生音轨。" type="info" :closable="false" show-icon />
      <el-form label-position="top" class="recipe-create-form">
        <div class="inspiration-cards" aria-label="治愈日常灵感卡">
          <button v-for="item in recipeInspirations" :key="item.key" type="button" :class="{ active: recipeCreateForm.inspirationKey === item.key }" @click="selectRecipeInspiration(item)">{{ item.label }}</button>
        </div>
        <el-form-item label="一句话主题"><el-input v-model="recipeCreateForm.theme" type="textarea" :rows="3" /></el-form-item>
        <div class="recipe-create-grid">
          <el-form-item label="总时长（8–60 秒）"><el-input-number v-model="recipeCreateForm.targetDurationSeconds" :min="8" :max="60" /></el-form-item>
          <el-form-item label="质量档"><el-select v-model="recipeCreateForm.qualityTier"><el-option label="快速" value="quick" /><el-option label="平衡（推荐）" value="balanced" /><el-option label="精品" value="premium" /></el-select></el-form-item>
        </div>
      </el-form>
      <template #footer><el-button @click="recipeCreateVisible = false">取消</el-button><el-button type="primary" :loading="recipeBusy" :disabled="!recipeCreateForm.theme.trim()" @click="createHealingRecipe">创建组合包</el-button></template>
    </el-dialog>

    <el-dialog v-model="recipeStoryReviewVisible" title="批准故事并锁定本集规则" width="min(720px, calc(100vw - 28px))">
      <el-alert title="这些规则会锁定整条视频；修改后批准会让旧的分镜与媒体版本过期，但不会删除历史。" type="warning" :closable="false" show-icon />
      <el-form label-position="top" class="recipe-rules-form">
        <div class="recipe-rules-grid">
          <el-form-item label="儿童本集固定服装"><el-input v-model="recipeStoryRulesForm.personWardrobe" /></el-form-item>
          <el-form-item label="时间与天气"><el-input v-model="recipeStoryRulesForm.timeWeather" /></el-form-item>
          <el-form-item label="主要场景"><el-input v-model="recipeStoryRulesForm.mainScene" /></el-form-item>
          <el-form-item label="环境画风参考">
            <el-select v-model="recipeStoryRulesForm.environment"><el-option label="室内水彩（仅室内参考）" value="indoor" /><el-option label="户外水彩（仅户外参考）" value="outdoor" /></el-select>
          </el-form-item>
          <el-form-item label="猫咪行为模式">
            <el-select v-model="recipeStoryRulesForm.catBehaviorMode"><el-option label="自然四足猫" value="natural" /><el-option label="轻拟人但保持猫科结构" value="light_anthropomorphic" /></el-select>
          </el-form-item>
          <el-form-item label="轻音乐情绪"><el-input v-model="recipeStoryRulesForm.musicMood" /></el-form-item>
        </div>
        <div class="recipe-rules-grid">
          <el-form-item label="核心道具（每行一个）"><el-input v-model="recipeStoryRulesForm.coreProps" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="环境声（每行一个）"><el-input v-model="recipeStoryRulesForm.ambient" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="关键动作声（每行一个）"><el-input v-model="recipeStoryRulesForm.foley" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="风格正向词（至少三行）"><el-input v-model="recipeStoryRulesForm.stylePositive" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="排除词（至少两行）"><el-input v-model="recipeStoryRulesForm.styleExcluded" type="textarea" :rows="3" /></el-form-item>
        </div>
        <el-alert title="声音固定为原生环境声、动作声和轻音乐；对白始终禁用。" type="info" :closable="false" />
      </el-form>
      <template #footer><el-button @click="recipeStoryReviewVisible = false">继续编辑故事</el-button><el-button type="primary" :loading="recipeBusy" :disabled="!recipeStoryRulesValid" @click="confirmRecipeStory">批准并锁定规则</el-button></template>
    </el-dialog>

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

    <el-drawer v-model="subjectLibraryVisible" title="主体库" size="620px">
      <el-alert
        v-if="subjectLibraryError"
        :title="`主体库加载失败：${subjectLibraryError}`"
        description="不会无限加载。可重试，或稍后恢复。"
        type="error"
        :closable="false"
        show-icon
      >
        <template #default><el-button @click="openSubjectLibrary">重试</el-button></template>
      </el-alert>
      <div v-if="subjectLibraryLoading" class="asset-history-state">正在读取冻结的主体版本…</div>
      <div v-else-if="!subjectLibrary.length && !subjectLibraryError" class="asset-history-state">
        当前项目还没有主体。<button type="button" @click="subjectLibraryVisible = false; subjectVisible = true">创建主体</button>
      </div>
      <div v-else class="subject-library-grid">
        <article v-for="subject in subjectLibrary" :key="subject.id">
          <div><b>{{ subject.name }}</b><small>{{ subject.kind }} · {{ subject.role }} · Revision {{ subject.revision }} · {{ subject.status }}</small></div>
          <p>{{ subject.identityAnchors.join('；') || '尚未填写身份锚点' }}</p>
          <small v-if="missingSubjectReferences(subject).length" class="subject-warning">
            缺少参考：{{ missingSubjectReferences(subject).join('、') }}
          </small>
          <button type="button" @click="chooseSubjectForGenerator(subject)">选择到当前生成节点</button>
        </article>
      </div>
    </el-drawer>

    <el-drawer
      :model-value="Boolean(annotationAssetId)"
      title="参考图标记"
      size="760px"
      @close="annotationAssetId = null"
    >
      <ReferenceAnnotationEditor
        v-if="annotationAssetId"
        :asset-id="annotationAssetId"
        :image-url="assetContentUrl(annotationAssetId)"
        @save="saveReferenceAnnotation"
        @close="annotationAssetId = null"
      />
    </el-drawer>

    <el-drawer
      v-model="assetHistoryVisible"
      title="项目素材历史"
      size="720px"
      @closed="referenceBindingNode = null"
    >
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
          <button
            v-if="referenceBindingNode && asset.mediaType === 'image'"
            type="button"
            @click="bindHistoricalAsset(asset)"
          >绑定到此节点</button>
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
        <el-form-item v-if="!activeRecipe" label="对白 / 声音"><el-input v-model="beatForm.dialogue" type="textarea" /></el-form-item>
        <el-alert v-else title="组合包固定无对白；声音按 EpisodeRules 与镜头动作自动编译。" type="info" :closable="false" />
        <el-form-item label="时长（秒）"><el-input-number v-model="beatForm.durationSeconds" :min="activeRecipe ? 8 : 1" :max="activeRecipe ? 15 : 60" /></el-form-item>
        <el-button type="primary" @click="saveBeat">保存为新 Revision</el-button>
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
      :initial-draft="videoEditDrafts[videoEditorNode.id]"
      :reference-asset-ids="videoEditDrafts[videoEditorNode.id]?.referenceAssetIds"
      @draft-change="updateVideoEditDraft(videoEditorNode.id, $event)"
      @select-references="startVideoReferenceSelection"
      @close="closeVideoEditor"
      @submitted="closeVideoEditor(); loadCanvas()"
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
.canvas-stage { position: relative; height: calc(100% - 72px); min-height: 648px; background: #111317; }
.canvas-initial-skeleton { position: absolute; inset: 0; z-index: 40; display: flex; align-items: center; justify-content: center; gap: 28px; background: #111317; }
.skeleton-node { width: 220px; height: 150px; border: 1px solid #303744; border-radius: 14px; background: linear-gradient(110deg, #171b22 8%, #222833 18%, #171b22 33%); background-size: 240% 100%; animation: canvas-skeleton 1.4s linear infinite; }
.skeleton-node-wide { width: 300px; height: 190px; }
.skeleton-link { width: 72px; height: 2px; background: #303744; }
@keyframes canvas-skeleton { to { background-position-x: -240%; } }
@media (prefers-reduced-motion: reduce) { .skeleton-node { animation: none; } }
.canvas-surface { position: relative; width: 100%; height: 100%; min-width: 0; min-height: 0; overflow: hidden; }.vue-flow { background: #111317; }.canvas-surface.space-ready :deep(.vue-flow__pane) { cursor: grab; }.canvas-surface.panning :deep(.vue-flow__pane) { cursor: grabbing; }
.vue-flow :deep(.vue-flow__edge-path) { stroke: #5c6675; stroke-width: 1.2; }
.vue-flow :deep(.vue-flow__handle) { width: 9px; height: 9px; background: #9aa8ba; border: 2px solid #1b1e24; }
.canvas-group-frame { position: relative; box-sizing: border-box; width: 100%; height: 100%; color: #aeb6c2; background: rgb(53 54 57 / 34%); border: 1px solid color-mix(in srgb, var(--group-color) 54%, #5a5e66); border-radius: 12px; cursor: default; transition: border-color 150ms ease, background 150ms ease, box-shadow 150ms ease; }
.canvas-group-frame:hover { background: rgb(58 59 63 / 42%); }.canvas-group-frame.selected { background: rgb(58 61 68 / 48%); border-color: var(--group-color); box-shadow: 0 0 0 2px color-mix(in srgb, var(--group-color) 36%, transparent); }
.canvas-group-heading { position: absolute; top: 17px; left: 22px; display: flex; align-items: baseline; gap: 10px; max-width: calc(100% - 44px); pointer-events: none; }.canvas-group-heading span { color: #878e99; font-size: 12px; }.canvas-group-heading b { color: #dce1e9; font-size: 14px; }.canvas-group-heading small { overflow: hidden; color: #c79b5d; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.group-phase-strip { position: absolute; top: 48px; left: 22px; right: 22px; display: flex; gap: 6px; pointer-events: none; }.group-phase-strip span { padding: 4px 8px; color: #6f7784; background: #292c31; border: 1px solid #3a3e45; border-radius: 999px; font-size: 9px; }.group-phase-strip .complete { color: #a8d7bc; border-color: #446956; }.group-phase-strip .current { color: #d8e9ff; background: #263b54; border-color: #4b79aa; }
.canvas-group-toolbar { position: absolute; top: -68px; left: 50%; z-index: 12; display: flex; align-items: center; gap: 4px; min-height: 48px; padding: 6px 8px; transform: translateX(-50%); background: #2a2a2b; border: 1px solid #414247; border-radius: 13px; box-shadow: 0 16px 44px rgb(0 0 0 / 46%); white-space: nowrap; }.canvas-group-toolbar button { min-height: 44px; padding: 0 12px; color: #e2e3e6; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }.canvas-group-toolbar button:hover:not(:disabled), .canvas-group-toolbar button:focus-visible { background: #3a3b3f; outline: 2px solid #79aef0; outline-offset: -2px; }.canvas-group-toolbar button:disabled { color: #70737a; cursor: not-allowed; }.group-execution-state { padding: 0 10px; color: #9dc0e8; border-left: 1px solid #484a50; font-size: 11px; }
.canvas-surface.low-detail :deep(.canvas-card > p),
.canvas-surface.low-detail :deep(.canvas-card .fact-row),
.canvas-surface.low-detail :deep(.canvas-card .card-actions),
.canvas-surface.low-detail :deep(.canvas-card .score-row),
.canvas-surface.low-detail :deep(.canvas-card .character-node-candidates) { opacity: 0; pointer-events: none; }
.character-design-console { display: grid; gap: 14px; padding: 4px; }.character-design-console > header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }.character-design-console header div { display: grid; gap: 3px; }.character-design-console header small { color: #8c96a4; }.character-design-console button { min-height: 44px; padding: 0 14px; color: #e8edf5; background: #313740; border: 1px solid #4a5361; border-radius: 9px; cursor: pointer; }.character-design-console button:hover { background: #3a4553; }.character-candidate-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }.character-candidate-grid article { display: grid; gap: 8px; padding: 9px; background: #1b1e23; border: 1px solid #343a44; border-radius: 10px; }.character-candidate-grid img { width: 100%; height: 190px; object-fit: contain; background: #111318; border-radius: 7px; }.character-candidate-grid article div { display: flex; justify-content: space-between; }.character-candidate-grid small { color: #7e8998; }.empty-console-state { padding: 46px 18px; color: #8792a1; text-align: center; border: 1px dashed #3b424c; border-radius: 10px; }
.stage-guide { position: absolute; top: 18px; left: 18px; z-index: 5; display: flex; gap: 8px; align-items: center; padding: 9px 11px; color: #778396; background: rgb(20 23 28 / 92%); border: 1px solid #30353e; border-radius: 10px; font-size: 10px; }
.stage-guide b { color: #d9dee8; margin-right: 4px; }.stage-guide span { padding-left: 8px; border-left: 1px solid #333942; }
.canvas-toolbar { position: absolute; left: 50%; bottom: 22px; z-index: 5; display: flex; gap: 5px; padding: 7px; transform: translateX(-50%); background: #24272c; border: 1px solid #3b3f47; border-radius: 14px; box-shadow: 0 14px 35px rgb(0 0 0 / 34%); }
.canvas-toolbar button { width: 36px; height: 36px; padding: 8px; color: #c3cad5; background: transparent; border: 0; border-radius: 9px; cursor: pointer; }
.canvas-toolbar button:hover { color: #fff; background: #383d45; }
.canvas-toolbar button.recipe-entry { width: auto; padding-inline: 12px; color: #b9d8c2; font-weight: 700; }
.canvas-toolbar button.asset-library-entry { width: auto; padding-inline: 12px; color: #d9e4f2; font-weight: 700; }
.node-library-popover { position: absolute; left: 50%; bottom: 78px; z-index: 12; transform: translateX(-50%); }
.reference-selection-banner { position: absolute; top: 14px; left: 50%; z-index: 30; display: flex; align-items: center; gap: 10px; min-width: 560px; padding: 10px 12px; transform: translateX(-50%); color: #dfeafa; background: #1d3f61; border: 1px solid #4f88bc; border-radius: 12px; box-shadow: 0 14px 40px rgb(0 0 0 / 38%); }
.reference-selection-banner div { display: grid; flex: 1; }.reference-selection-banner span { color: #a9c4dd; font-size: 11px; }.reference-selection-banner button { padding: 7px 10px; color: #dbe9f8; background: #254d71; border: 1px solid #5586b1; border-radius: 7px; cursor: pointer; }.reference-selection-banner button.primary { color: #15202b; background: #d7e8f7; }
.canvas-shortcut-hint { position: absolute; left: 16px; bottom: 16px; z-index: 6; padding: 7px 9px; color: #758297; background: rgb(20 23 28 / 88%); border: 1px solid #303741; border-radius: 8px; font-size: 10px; pointer-events: none; }.canvas-shortcut-hint kbd { padding: 2px 5px; color: #a8cdf8; background: #252b34; border: 1px solid #3d4653; border-radius: 5px; font: inherit; }
.workflow-actions { position: absolute; right: 18px; bottom: 22px; z-index: 5; display: flex; align-items: center; gap: 8px; max-width: calc(100% - 180px); padding: 9px; color: #c8d0db; background: rgb(28 31 37 / 95%); border: 1px solid #3a4049; border-radius: 12px; box-shadow: 0 14px 35px rgb(0 0 0 / 32%); }
.workflow-actions div { display: grid; padding: 0 8px; }.workflow-actions small { color: #808b9c; }
.workflow-actions button { display: flex; align-items: center; gap: 6px; padding: 9px 11px; color: #dce5f2; background: #303640; border: 1px solid #48515f; border-radius: 8px; cursor: pointer; }
.workflow-actions button :deep(svg) { width: 14px; }.workflow-actions button:disabled { opacity: .38; cursor: not-allowed; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 12px; }
.recipe-create-form, .recipe-rules-form { margin-top: 16px; }.recipe-create-grid, .recipe-rules-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.inspiration-cards { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 14px; }.inspiration-cards button { padding: 7px 10px; color: #aeb8c7; background: #272b32; border: 1px solid #3c424d; border-radius: 999px; cursor: pointer; }.inspiration-cards button.active { color: #172019; background: #bdd9c5; border-color: #bdd9c5; }
.asset-history-filters { display: flex; gap: 7px; margin-bottom: 12px; }.asset-history-filters button { padding: 7px 10px; color: #cad3df; background: #292e36; border: 1px solid #3c4450; border-radius: 7px; cursor: pointer; }
.asset-history-state { padding: 30px; color: #8b96a7; text-align: center; }.asset-history-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }.asset-history-grid article { display: grid; gap: 5px; padding: 8px; color: #dce3ed; background: #1c2026; border: 1px solid #303741; border-radius: 9px; }.asset-history-grid img, .asset-history-grid video, .audio-placeholder { width: 100%; height: 132px; object-fit: cover; background: #101217; border-radius: 6px; }.asset-history-grid small { color: #7f8a9a; }.audio-placeholder { display: grid; place-items: center; color: #758196; font-size: 11px; letter-spacing: .16em; }
.subject-library-grid { display: grid; gap: 10px; }.subject-library-grid article { display: grid; gap: 8px; padding: 13px; color: #dce3ed; background: #1c2026; border: 1px solid #303741; border-radius: 10px; }.subject-library-grid article div { display: grid; }.subject-library-grid small { color: #8792a3; }.subject-library-grid p { margin: 0; color: #b9c2cf; }.subject-library-grid .subject-warning { color: #e3ad6d; }.subject-library-grid button { justify-self: start; padding: 7px 10px; color: #15202b; background: #d7e8f7; border: 0; border-radius: 7px; cursor: pointer; }
.canvas-node-context-menu { position: fixed; z-index: 1400; width: 224px; padding: 6px; color: #eceff4; background: #252525; border: 1px solid #454545; border-radius: 12px; box-shadow: 0 14px 38px rgb(0 0 0 / 42%); }.canvas-node-context-menu button { width: 100%; min-height: 44px; padding: 0 12px; color: inherit; text-align: left; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }.canvas-node-context-menu button:hover,.canvas-node-context-menu button:focus-visible { background: #383838; outline: 2px solid #78aef0; outline-offset: -2px; }.canvas-node-context-menu button[aria-disabled="true"] { color: #777; cursor: not-allowed; }
.canvas-archive-undo { position: fixed; right: 24px; bottom: 24px; z-index: 1700; display: flex; align-items: center; gap: 18px; min-height: 52px; padding: 8px 10px 8px 16px; color: #eceff4; background: #292929; border: 1px solid #494949; border-radius: 12px; box-shadow: 0 16px 40px rgb(0 0 0 / 40%); animation: archive-undo-in 140ms ease-out; }.canvas-archive-undo button { min-width: 64px; min-height: 40px; color: #9fc8ff; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }.canvas-archive-undo button:hover,.canvas-archive-undo button:focus-visible { color: #fff; background: #3a4654; outline: 2px solid #78aef0; outline-offset: -2px; }
@keyframes archive-undo-in { from { opacity: 0; transform: translateY(6px); } }
@media (max-width: 1280px) {
  .stage-guide { display: none; }
  .workflow-actions { left: 12px; right: 12px; bottom: 70px; max-width: none; flex-wrap: wrap; }
  .canvas-toolbar { bottom: 14px; }
  .canvas-shortcut-hint { display: none; }
  .asset-history-grid { grid-template-columns: repeat(2, 1fr); }
  .recipe-rules-grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .canvas-group-frame { transition: none; }
  .canvas-archive-undo { animation: none; }
}
</style>
