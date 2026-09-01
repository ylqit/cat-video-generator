<script setup lang="ts">
import { ElMessageBox } from "element-plus";
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { RouteLocationNormalized } from "vue-router";

import { creatorApi } from "../api/client";
import type { CreatorStateDto } from "../api/types";
import type { DirectorDirtyRegistration, DirectorDirtyResolution } from "../components/director/directorDirtyState";
import { DirectorDirtyCoordinator } from "../components/director/directorDirtyState";

const ScriptWorkspace = defineAsyncComponent(() => import("../components/director/ScriptWorkspace.vue"));
const AssetWorkspace = defineAsyncComponent(() => import("../components/director/AssetWorkspace.vue"));
const ProductionWorkspace = defineAsyncComponent(() => import("../components/production/ProductionWorkspace.vue"));

type ModuleId = "script" | "assets" | "production";
type LoadState = "loading" | "ready" | "error";

const route = useRoute();
const router = useRouter();
const creatorState = ref<CreatorStateDto>();
const loadState = ref<LoadState>("loading");
const loadError = ref("");
const dirtyCoordinator = new DirectorDirtyCoordinator();
let activeController: AbortController | undefined;
let removeGuard: (() => void) | undefined;
let requestSequence = 0;

const projectId = computed(() => String(route.params.projectId ?? ""));
const activeModuleId = computed<ModuleId>(() => {
  const value = route.meta.workspaceModule;
  return value === "script" || value === "assets" || value === "production" ? value : "production";
});
const focusedItemId = computed(() => typeof route.query.item === "string" ? route.query.item : "");
const panel = computed(() => typeof route.query.panel === "string" ? route.query.panel : "main");
const activeWorkspaceComponent = computed(() => activeModuleId.value === "script" ? ScriptWorkspace : activeModuleId.value === "assets" ? AssetWorkspace : ProductionWorkspace);
const navigation: Array<{ id: ModuleId; label: string; caption: string }> = [
  { id: "script", label: "剧本", caption: "故事正文" },
  { id: "assets", label: "角色资产", caption: "Canon 与参考" },
  { id: "production", label: "生产", caption: "镜头、视频与成片" },
];

async function load() {
  activeController?.abort("creator state superseded");
  const controller = new AbortController();
  activeController = controller;
  const sequence = ++requestSequence;
  loadState.value = "loading";
  loadError.value = "";
  try {
    creatorState.value = await creatorApi.state(projectId.value, controller.signal);
    if (controller.signal.aborted || sequence !== requestSequence) return;
    loadState.value = "ready";
  } catch (reason) {
    if (controller.signal.aborted || sequence !== requestSequence) return;
    loadError.value = reason instanceof Error ? reason.message : String(reason);
    loadState.value = "error";
  } finally {
    if (sequence === requestSequence) activeController = undefined;
  }
}

function openModule(moduleId: ModuleId) {
  void router.push({ name: `project-${moduleId}`, params: { projectId: projectId.value } });
}

async function chooseDirtyResolution(registration: DirectorDirtyRegistration): Promise<DirectorDirtyResolution> {
  try {
    await ElMessageBox.confirm(
      `${registration.label}还有未保存修改。`,
      "离开前处理修改",
      { confirmButtonText: "保存修改", cancelButtonText: "放弃修改", distinguishCancelAndClose: true, closeOnClickModal: false, type: "warning" },
    );
    return "save";
  } catch (action) {
    return action === "cancel" ? "discard" : "continue";
  }
}

function registerDirtyState(registration?: DirectorDirtyRegistration) {
  dirtyCoordinator.register(registration);
}

function leavesWorkspace(to: RouteLocationNormalized, from: RouteLocationNormalized) {
  return String(to.params.projectId ?? "") !== String(from.params.projectId ?? "") || to.name !== from.name;
}

watch(projectId, load, { immediate: true });
onMounted(() => {
  removeGuard = router.beforeEach(async (to, from) => {
    if (!dirtyCoordinator.active || !leavesWorkspace(to, from)) return true;
    return dirtyCoordinator.resolve(chooseDirtyResolution);
  });
});
onBeforeUnmount(() => {
  requestSequence += 1;
  activeController?.abort("creator workspace unmounted");
  removeGuard?.();
});
</script>

<template>
  <div class="creator-shell">
    <header class="project-header">
      <button type="button" aria-label="返回项目" @click="router.push({ name: 'projects' })">←</button>
      <div><span>ONE CHILD · ONE CAT</span><b>{{ creatorState?.currentStory?.title || "一人一猫创作项目" }}</b></div>
      <p>{{ creatorState ? `${creatorState.targetDurationSeconds}s · ${creatorState.aspectRatio} · ${creatorState.qualityTier}` : "正在读取项目…" }}</p>
      <a href="/settings">设置</a>
    </header>
    <nav class="module-navigation" aria-label="项目创作入口">
      <button v-for="item in navigation" :key="item.id" type="button" :class="{ active: activeModuleId === item.id }" @click="openModule(item.id)"><b>{{ item.label }}</b><span>{{ item.caption }}</span></button>
    </nav>
    <main class="workspace-content">
      <div v-if="loadState === 'loading'" class="load-state" aria-busy="true"><i /><b>正在打开简洁创作主线…</b></div>
      <div v-else-if="loadState === 'error'" class="load-state error" role="alert"><b>项目加载失败</b><p>{{ loadError }}</p><button type="button" @click="load">重新加载</button></div>
      <Suspense v-else :timeout="0">
        <component :is="activeWorkspaceComponent" :project-id="projectId" :initial-state="creatorState" :focused-item-id="focusedItemId" :panel="activeModuleId === 'production' ? undefined : panel" @dirty-change="registerDirtyState" />
        <template #fallback><div class="load-state" aria-busy="true">正在载入工作区…</div></template>
      </Suspense>
    </main>
  </div>
</template>

<style scoped>
.creator-shell{height:100%;display:grid;grid-template-rows:54px 54px minmax(0,1fr);overflow:hidden;color:#e8eef6;background:#090d12}.project-header{padding:0 14px;display:flex;align-items:center;gap:12px;background:#11171e;border-bottom:1px solid #242d37}.project-header>button{width:44px;height:44px;color:#aab8c7;background:#1a222b;border:1px solid #303b47;border-radius:10px;cursor:pointer}.project-header>div{display:grid;gap:2px}.project-header span{color:#6387a9;font-size:9px;font-weight:800;letter-spacing:.14em}.project-header b{font-size:14px}.project-header p{margin-left:auto;color:#77889a;font-size:11px}.project-header a{min-height:44px;padding:0 12px;display:grid;place-items:center;color:#aab8c7;text-decoration:none;background:#1a222b;border:1px solid #303b47;border-radius:10px}.module-navigation{padding:5px 14px;display:flex;gap:6px;background:#0e141a;border-bottom:1px solid #242d37}.module-navigation button{min-width:150px;min-height:44px;padding:5px 13px;display:grid;grid-template-columns:auto auto;align-items:center;gap:8px;color:#8090a1;text-align:left;background:transparent;border:1px solid transparent;border-radius:9px;cursor:pointer}.module-navigation button span{font-size:9px}.module-navigation button.active{color:#eaf4ff;background:#1c2a37;border-color:#36536d}.workspace-content{position:relative;min-height:0;margin:12px;overflow:hidden;background:#10161d;border:1px solid #27313c;border-radius:15px}.load-state{height:100%;display:grid;place-content:center;justify-items:center;gap:10px;color:#8292a3}.load-state i{width:38px;height:38px;border:3px solid #263443;border-top-color:#5c90bd;border-radius:50%;animation:spin .9s linear infinite}.load-state.error{color:#dfa49e}.load-state.error button{min-height:44px;padding:0 14px;color:#fff;background:#6e4148;border:1px solid #94565f;border-radius:9px}@keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.load-state i{animation:none}}@media(max-width:720px){.creator-shell{grid-template-rows:54px 58px minmax(0,1fr)}.project-header p{display:none}.module-navigation{overflow-x:auto}.module-navigation button{min-width:130px}.workspace-content{margin:8px}}
@media(max-width:720px){.project-header{padding:0 8px;gap:8px}.project-header>div{min-width:0}.project-header b{overflow:hidden;display:block;max-width:48vw;text-overflow:ellipsis;white-space:nowrap}.project-header a{padding:0 9px}.module-navigation{padding:6px 8px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;overflow:visible}.module-navigation button{min-width:0;padding:4px 5px;grid-template-columns:1fr;justify-items:center;gap:1px;text-align:center}.module-navigation button span{overflow:hidden;max-width:100%;text-overflow:ellipsis;white-space:nowrap}.workspace-content{margin:7px}}
</style>
