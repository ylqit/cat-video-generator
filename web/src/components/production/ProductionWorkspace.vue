<script setup lang="ts">
import { Background } from "@vue-flow/background";
import { VueFlow, useVueFlow } from "@vue-flow/core";
import type { Edge, Node, NodeMouseEvent } from "@vue-flow/core";
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { creatorApi } from "../../api/client";
import type { CreatorAssetDto, CreatorShotDto, CreatorStateDto, CreatorTaskDto } from "../../api/types";
import type { DirectorDirtyRegistration } from "../director/directorDirtyState";
import CreatorShotEditor from "./CreatorShotEditor.vue";
import VideoWorkbenchOverlay from "./VideoWorkbenchOverlay.vue";

type WorkbenchTab = "preview" | "generate" | "edit";
type ArtifactKind = "story" | "canon" | "shot" | "media" | "delivery";
interface CreatorArtifact { id: string; kind: ArtifactKind; title: string; subtitle: string; status: string; shotId?: string; count?: number }

const props = withDefaults(defineProps<{ projectId: string; initialState?: CreatorStateDto; focusedItemId?: string }>(), { focusedItemId: "" });
const emit = defineEmits<{ "dirty-change": [registration?: DirectorDirtyRegistration] }>();
const route = useRoute();
const router = useRouter();
const { fitView } = useVueFlow("creator-production");
const state = ref<"loading" | "ready" | "stale" | "error">("loading");
const error = ref("");
const creatorState = ref<CreatorStateDto>();
const shots = ref<CreatorShotDto[]>([]);
const assets = ref<CreatorAssetDto[]>([]);
const tasks = ref<CreatorTaskDto[]>([]);
const nodes = shallowRef<Node[]>([]);
const edges = shallowRef<Edge[]>([]);
const selectedId = ref("");
const shotEditorOpen = ref(false);
const editorShotId = ref("");
let controller: AbortController | undefined;
let requestSequence = 0;

const workbenchOpen = computed(() => route.query.workspace === "video");
const workbenchTab = computed<WorkbenchTab>(() => ["preview", "generate", "edit"].includes(String(route.query.tab)) ? String(route.query.tab) as WorkbenchTab : "preview");
const trackId = computed(() => typeof route.query.track === "string" ? route.query.track : "");
const shotId = computed(() => typeof route.query.shot === "string" ? route.query.shot : "");
const artifacts = computed<CreatorArtifact[]>(() => {
  const story = creatorState.value?.currentStory;
  const referenceCount = creatorState.value?.referenceBindings.length ?? 0;
  const result: CreatorArtifact[] = [
    { id: "current-story", kind: "story", title: "当前故事", subtitle: story?.title || "尚未保存故事", status: story?.body ? "ready" : "blocked" },
    { id: "canon-board", kind: "canon", title: "Canon 与参考", subtitle: `${referenceCount} 项固定或按需参考`, status: referenceCount >= 3 ? "ready" : "blocked", count: referenceCount },
  ];
  for (const shot of shots.value) {
    result.push({ id: `shot-${shot.id}`, kind: "shot", title: `${shot.sortOrder}. ${shot.title}`, subtitle: `${shot.durationSeconds}s · ${shot.sceneLabel || "未分组场景"}`, status: "ready", shotId: shot.id });
    const versions = assets.value.filter((asset) => asset.mediaType === "video" && asset.creatorShotId === shot.id);
    result.push({ id: `media-${shot.id}`, kind: "media", title: "视频版本", subtitle: shot.selectedVideoAssetId ? `已采用 · ${versions.length || 1} 个版本` : `${versions.length} 个候选`, status: shot.selectedVideoAssetId ? "complete" : tasks.value.some((task) => task.creatorShotId === shot.id && ["local_queued", "submitting", "provider_queued", "provider_running"].includes(task.status)) ? "active" : "ready", shotId: shot.id, count: versions.length });
  }
  result.push({ id: "delivery", kind: "delivery", title: "时间线与成片", subtitle: shots.value.every((shot) => shot.selectedVideoAssetId) && shots.value.length ? "可以编排成片" : "等待采用视频", status: shots.value.every((shot) => shot.selectedVideoAssetId) && shots.value.length ? "ready" : "blocked" });
  return result;
});
const selectedArtifact = computed(() => artifacts.value.find((item) => item.id === selectedId.value));

function defaultPosition(artifact: CreatorArtifact, index: number) {
  if (artifact.kind === "story") return { x: 40, y: 160 };
  if (artifact.kind === "canon") return { x: 360, y: 20 };
  if (artifact.kind === "delivery") return { x: 360 + Math.max(1, shots.value.length) * 520, y: 160 };
  const shotIndex = shots.value.findIndex((shot) => shot.id === artifact.shotId);
  return artifact.kind === "shot" ? { x: 360 + shotIndex * 520, y: 160 } : { x: 620 + shotIndex * 520, y: 350 };
}

function layoutKey() { return `creator-production-layout:${props.projectId}`; }
function savedPositions() {
  try { return JSON.parse(localStorage.getItem(layoutKey()) || "{}") as Record<string, { x: number; y: number }>; }
  catch { return {}; }
}

function rebuildGraph() {
  const positions = savedPositions();
  nodes.value = artifacts.value.map((artifact, index) => ({
    id: artifact.id,
    type: "creator",
    position: positions[artifact.id] || defaultPosition(artifact, index),
    data: { artifact },
    draggable: true,
    selectable: true,
    zIndex: selectedId.value === artifact.id ? 10 : 1,
  }));
  const result: Edge[] = [];
  let previous = "current-story";
  for (const shot of shots.value) {
    result.push({ id: `edge-${previous}-shot-${shot.id}`, source: previous, target: `shot-${shot.id}`, type: "smoothstep" });
    result.push({ id: `edge-shot-media-${shot.id}`, source: `shot-${shot.id}`, target: `media-${shot.id}`, type: "smoothstep" });
    previous = `media-${shot.id}`;
  }
  if (!shots.value.length) result.push({ id: "edge-story-canon", source: "current-story", target: "canon-board", type: "smoothstep" });
  else result.push({ id: "edge-canon-first-shot", source: "canon-board", target: `shot-${shots.value[0].id}`, type: "smoothstep" });
  result.push({ id: "edge-delivery", source: previous, target: "delivery", type: "smoothstep" });
  edges.value = result.map((edge) => ({ ...edge, style: { stroke: "#465969", strokeWidth: 1.5 } }));
}

async function load(background = false) {
  controller?.abort();
  const requestController = new AbortController();
  controller = requestController;
  const sequence = ++requestSequence;
  state.value = background && creatorState.value ? "ready" : "loading";
  error.value = "";
  try {
    const nextState = !background && !creatorState.value && props.initialState
      ? props.initialState
      : await creatorApi.state(props.projectId, requestController.signal);
    const [nextShots, nextDiagnostics, nextAssets] = await Promise.all([
      creatorApi.shots(props.projectId, requestController.signal),
      creatorApi.diagnostics(props.projectId, requestController.signal),
      creatorApi.assets(props.projectId, requestController.signal),
    ]);
    if (requestController.signal.aborted || sequence !== requestSequence) return;
    creatorState.value = nextState;
    shots.value = nextShots;
    tasks.value = nextDiagnostics.tasks;
    assets.value = nextAssets;
    if (!selectedId.value || !artifacts.value.some((item) => item.id === selectedId.value)) selectedId.value = props.focusedItemId || (nextShots[0] ? `shot-${nextShots[0].id}` : "current-story");
    rebuildGraph();
    state.value = "ready";
    await nextTick();
    if (!background) await fitCurrentGraph();
  } catch (reason) {
    if (requestController.signal.aborted || sequence !== requestSequence) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    state.value = creatorState.value ? "stale" : "error";
  } finally {
    if (sequence === requestSequence) controller = undefined;
  }
}

function onNodeClick(event: NodeMouseEvent) {
  selectedId.value = event.node.id;
  nodes.value = nodes.value.map((node) => ({ ...node, zIndex: node.id === selectedId.value ? 10 : 1 }));
}

function persistLayout() {
  localStorage.setItem(layoutKey(), JSON.stringify(Object.fromEntries(nodes.value.map((node) => [node.id, node.position]))));
}

function activate(artifact?: CreatorArtifact) {
  if (!artifact) return;
  if (artifact.kind === "story") { void router.push({ name: "project-script", params: { projectId: props.projectId } }); return; }
  if (artifact.kind === "canon") { void router.push({ name: "project-assets", params: { projectId: props.projectId } }); return; }
  if (artifact.kind === "shot") { editorShotId.value = artifact.shotId || ""; shotEditorOpen.value = true; return; }
  if (artifact.kind === "media") { openWorkbench("generate", artifact.shotId); return; }
  openWorkbench("edit");
}

function openWorkbench(tab: WorkbenchTab, targetShotId = "") {
  void router.push({ name: "project-production", params: { projectId: props.projectId }, query: { workspace: "video", tab, ...(targetShotId ? { shot: targetShotId } : {}) } });
}
function closeWorkbench() { void router.push({ name: "project-production", params: { projectId: props.projectId } }); }
function updateTab(tab: WorkbenchTab) { void router.replace({ name: "project-production", params: { projectId: props.projectId }, query: { workspace: "video", tab, ...(shotId.value ? { shot: shotId.value } : {}) } }); }

async function fitCurrentGraph() {
  if (!nodes.value.length) return;
  const narrow = typeof window.matchMedia === "function"
    && window.matchMedia("(max-width: 620px)").matches;
  if (narrow) return;
  const focusNodeIds = nodes.value.map((node) => node.id);
  await fitView({
    nodes: focusNodeIds,
    padding: 0.2,
    minZoom: 0.35,
    maxZoom: 0.9,
    duration: typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 220,
  });
}

async function onFlowInit() { await fitCurrentGraph(); }

watch(() => props.projectId, () => { creatorState.value = undefined; shots.value = []; selectedId.value = ""; void load(); }, { immediate: true });
onBeforeUnmount(() => { requestSequence += 1; controller?.abort(); emit("dirty-change", undefined); });
</script>

<template>
  <section class="production-workspace" aria-label="一人一猫生产导航">
    <aside class="inspector"><header><span>CREATOR FLOW</span><h1>生产导航</h1><button type="button" aria-label="刷新生产" @click="load(true)">刷新</button></header><template v-if="selectedArtifact"><article><span>{{ selectedArtifact.kind }}</span><h2>{{ selectedArtifact.title }}</h2><p>{{ selectedArtifact.subtitle }}</p><mark :data-status="selectedArtifact.status">{{ selectedArtifact.status }}</mark></article><button class="primary" type="button" @click="activate(selectedArtifact)">{{ selectedArtifact.kind === 'story' ? '编辑故事' : selectedArtifact.kind === 'canon' ? '管理参考' : selectedArtifact.kind === 'shot' ? '编辑镜头' : selectedArtifact.kind === 'media' ? '打开视频工作台' : '打开剪辑与交付' }}</button></template><p v-else>选择当前故事、Canon、镜头、视频或成片。</p><section><b>当前内容</b><p>画布直接呈现故事、参考、镜头、媒体和成片；节点位置只保存在本机浏览器。</p></section></aside>
    <main class="canvas"><div v-if="state === 'loading' && !creatorState" class="state" aria-busy="true">正在派生当前生产事实…</div><div v-else-if="state === 'error' && !creatorState" class="state error" role="alert"><b>生产数据加载失败</b><p>{{ error }}</p><button type="button" @click="() => load()">重新加载</button></div><template v-else><nav class="mobile-artifacts" aria-label="当前生产产物"><button v-for="artifact in artifacts" :key="artifact.id" type="button" :class="{ selected: artifact.id === selectedId }" @click="selectedId = artifact.id" @dblclick="activate(artifact)"><span>{{ artifact.kind }}</span><b>{{ artifact.title }}</b><small>{{ artifact.subtitle }}</small><mark :data-status="artifact.status">{{ artifact.status }}</mark></button></nav><VueFlow id="creator-production" class="desktop-flow" v-model:nodes="nodes" v-model:edges="edges" :min-zoom="0.35" :max-zoom="1.5" :fit-view-on-init="false" :connect-on-click="false" @init="onFlowInit" @node-click="onNodeClick" @node-double-click="activate($event.node.data.artifact)" @node-drag-stop="persistLayout" @pane-click="selectedId = ''"><Background pattern-color="#26323d" :gap="24" :size="1" /><template #node-creator="{ data, selected }"><article class="flow-node" :class="[{ selected }, `kind-${data.artifact.kind}`]" @dblclick.stop="activate(data.artifact)"><span>{{ data.artifact.kind }}</span><h3>{{ data.artifact.title }}</h3><p>{{ data.artifact.subtitle }}</p><small :data-status="data.artifact.status">{{ data.artifact.status }}</small></article></template></VueFlow></template><div v-if="state === 'stale'" class="warning">数据可能过期：{{ error }}</div></main>
    <footer class="filmstrip"><div><span>SHOTS</span><b>{{ shots.length }}/6 镜</b></div><button v-for="shot in shots" :key="shot.id" type="button" @click="editorShotId = shot.id; shotEditorOpen = true"><span>{{ shot.sortOrder }}</span><b>{{ shot.title }}</b><small>{{ shot.durationSeconds }}s</small></button><button class="add" type="button" :disabled="shots.length >= 6" @click="editorShotId = ''; shotEditorOpen = true">＋ 编辑镜头</button></footer>
    <CreatorShotEditor v-if="shotEditorOpen && creatorState" :project-id="projectId" :creator-state="creatorState" :shots="shots" :initial-shot-id="editorShotId" @close="shotEditorOpen = false" @saved="shotEditorOpen = false; load()" />
    <VideoWorkbenchOverlay v-if="workbenchOpen" :project-id="projectId" :tab="workbenchTab" :track-id="trackId" :shot-id="shotId" @close="closeWorkbench" @tab-change="updateTab" @track-change="() => undefined" />
  </section>
</template>

<style scoped>
.production-workspace{height:100%;display:grid;grid-template-columns:270px minmax(0,1fr);grid-template-rows:minmax(0,1fr) 118px;overflow:hidden;color:#e7eef6;background:#0d1319}.inspector{min-height:0;overflow:auto;background:#151c24;border-right:1px solid #2a3540}.inspector>header{padding:14px;display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-rows:auto auto;align-items:center;border-bottom:1px solid #2a3540}.inspector>header span{grid-column:1;color:#6d91b2;font-size:9px;font-weight:800;letter-spacing:.14em}.inspector h1{grid-column:1;margin:4px 0;font-size:20px}.inspector>header button{grid-column:2;grid-row:1/3}.inspector button,.state button{min-height:44px;padding:0 12px;color:#bac7d4;background:#202a34;border:1px solid #374552;border-radius:9px;cursor:pointer}.inspector>article,.inspector>section{margin:12px;padding:13px;background:#11171e;border:1px solid #2a3540;border-radius:10px}.inspector>article span{color:#6e91b2;font-size:9px;text-transform:uppercase}.inspector h2{margin:6px 0}.inspector p{color:#8595a5;line-height:1.6}.inspector mark{padding:4px 8px;color:#9dccaf;background:#1b3828;border-radius:999px;font-size:9px}.inspector mark[data-status=blocked]{color:#dfa5a0;background:#382124}.inspector>.primary{width:calc(100% - 24px);margin:0 12px;color:#f3f9ff;background:#28628f;border-color:#4381af}.canvas{position:relative;min-width:0;min-height:0;background:#0c1218}.mobile-artifacts{display:none}.state{height:100%;display:grid;place-content:center;justify-items:center;gap:10px;color:#8393a4}.state.error{color:#dfa59e}.flow-node{width:230px;min-height:130px;padding:15px;box-sizing:border-box;color:#cbd7e2;background:#18212a;border:1px solid #34414e;border-radius:12px;box-shadow:0 12px 30px rgb(0 0 0 / 26%)}.flow-node.selected{border-color:#5b8db8;box-shadow:0 0 0 3px rgb(61 116 163 / 20%),0 18px 38px rgb(0 0 0 / 34%)}.flow-node>span{color:#6f92b2;font-size:9px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.flow-node h3{margin:10px 0 6px;font-size:16px}.flow-node p{height:34px;margin:0;color:#8293a5;line-height:1.5}.flow-node small{display:inline-block;margin-top:9px;padding:4px 7px;color:#97c5a8;background:#1b3828;border-radius:999px}.flow-node small[data-status=blocked]{color:#dca29c;background:#382124}.kind-shot{border-top-color:#4e7799}.kind-media{border-top-color:#6d8a64}.kind-delivery{border-top-color:#a27a4c}.warning{position:absolute;right:14px;top:14px;padding:9px 12px;color:#d7b77c;background:#33291c;border:1px solid #6b5636;border-radius:9px}.filmstrip{grid-column:1/-1;padding:9px 12px;display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;background:#141b22;border-top:1px solid #2d3844}.filmstrip::-webkit-scrollbar{display:none}.filmstrip>div{min-width:100px;display:grid;align-content:center}.filmstrip>div span{color:#6d91b2;font-size:9px;font-weight:800}.filmstrip>button{flex:0 0 190px;display:grid;grid-template-columns:30px minmax(0,1fr);grid-template-rows:1fr auto;align-items:center;gap:3px 8px;color:#c7d3df;text-align:left;background:#1b242d;border:1px solid #34414d;border-radius:9px;cursor:pointer}.filmstrip>button span{grid-row:1/3;width:28px;height:28px;display:grid;place-items:center;background:#2b3946;border-radius:7px}.filmstrip>button b,.filmstrip>button small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.filmstrip>button small{color:#7d8fa1}.filmstrip>button.add{display:grid;place-items:center;grid-template:none;min-height:44px}@media(max-width:760px){.production-workspace{grid-template-columns:220px minmax(0,1fr)}}@media(max-width:620px){.production-workspace{grid-template-columns:1fr;grid-template-rows:86px minmax(0,1fr) 92px}.inspector{display:grid;grid-template-columns:minmax(0,1fr) 132px;align-items:center;overflow:hidden;border-right:0;border-bottom:1px solid #2a3540}.inspector>header{padding:10px;border-bottom:0}.inspector>header button,.inspector>article,.inspector>section,.inspector>p{display:none}.inspector h1{font-size:18px}.inspector>.primary{width:auto;min-width:116px;margin:8px}.canvas{grid-row:2}.desktop-flow{display:none}.mobile-artifacts{height:100%;box-sizing:border-box;padding:12px;display:grid;grid-auto-flow:column;grid-auto-columns:minmax(230px,82%);align-content:start;gap:10px;overflow:auto;scrollbar-width:none;background:radial-gradient(circle at 20% 10%,#15212c,transparent 44%),#0c1218}.mobile-artifacts::-webkit-scrollbar{display:none}.mobile-artifacts button{min-height:148px;padding:15px;display:grid;align-content:center;gap:7px;color:#cbd7e2;text-align:left;background:#18212a;border:1px solid #34414e;border-radius:12px}.mobile-artifacts button.selected{border-color:#5b8db8;box-shadow:0 0 0 3px rgb(61 116 163 / 18%)}.mobile-artifacts span{color:#6f92b2;font-size:9px;font-weight:800;text-transform:uppercase}.mobile-artifacts b{font-size:16px}.mobile-artifacts small{color:#8293a5}.mobile-artifacts mark{justify-self:start;padding:4px 7px;color:#97c5a8;background:#1b3828;border-radius:999px;font-size:9px}.mobile-artifacts mark[data-status=blocked]{color:#dca29c;background:#382124}.filmstrip{grid-row:3;padding:7px 8px;gap:6px}.filmstrip>div{min-width:50px}.filmstrip>button{flex:1 1 142px;min-width:142px}.filmstrip>button.add{flex:0 0 104px}.filmstrip>div b{font-size:14px}}
</style>
