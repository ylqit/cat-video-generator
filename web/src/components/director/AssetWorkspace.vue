<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { creatorApi } from "../../api/client";
import type { CreatorAssetDto, CreatorReferenceDto, CreatorReferenceRole, CreatorStateDto } from "../../api/types";
import type { DirectorDirtyRegistration } from "./directorDirtyState";

const props = defineProps<{ projectId: string; initialState?: CreatorStateDto; focusedItemId?: string; panel?: string }>();
const emit = defineEmits<{ "dirty-change": [registration?: DirectorDirtyRegistration] }>();
const state = ref<"loading" | "ready" | "stale" | "error">("loading");
const error = ref("");
const creatorState = ref<CreatorStateDto>();
const assets = ref<CreatorAssetDto[]>([]);
const draftBindings = ref<CreatorReferenceDto[]>([]);
const selectedAssetId = ref("");
const addRole = ref<CreatorReferenceRole>("environment");
const saving = ref(false);
let controller: AbortController | undefined;
let requestSequence = 0;

const requiredRoles = new Set<CreatorReferenceRole>(["child_identity", "cat_identity", "style_board"]);
const categories: Array<{ role: CreatorReferenceRole; label: string }> = [
  { role: "child_identity", label: "固定儿童" },
  { role: "cat_identity", label: "固定猫咪" },
  { role: "style_board", label: "净化画风板" },
  { role: "child_appearance", label: "本集儿童造型" },
  { role: "cat_appearance", label: "本集猫咪造型" },
  { role: "pair_scale", label: "人猫同框比例" },
  { role: "environment", label: "环境参考" },
  { role: "prop", label: "道具参考" },
  { role: "style_source", label: "画风来源（不可提交）" },
];
const dirty = computed(() => JSON.stringify(draftBindings.value) !== JSON.stringify(creatorState.value?.referenceBindings ?? []));
const selectedAsset = computed(() => assets.value.find((asset) => asset.id === selectedAssetId.value));
const boundCards = computed(() => draftBindings.value.map((binding) => ({ binding, asset: assets.value.find((item) => item.id === binding.assetId) })));
const availableAssets = computed(() => assets.value.filter((asset) => !draftBindings.value.some((item) => item.assetId === asset.id) && ["approved", "ready"].includes(asset.status)));

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
    const nextAssets = await creatorApi.assets(props.projectId, requestController.signal);
    if (requestController.signal.aborted || sequence !== requestSequence) return;
    if (dirty.value && creatorState.value) {
      state.value = "stale";
      error.value = "服务器参考已更新；本地未保存选择已保留。";
      return;
    }
    creatorState.value = nextState;
    draftBindings.value = nextState.referenceBindings.map((binding) => ({ ...binding }));
    assets.value = nextAssets;
    selectedAssetId.value = props.focusedItemId || draftBindings.value[0]?.assetId || nextAssets[0]?.id || "";
    state.value = "ready";
  } catch (reason) {
    if (requestController.signal.aborted || sequence !== requestSequence) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    state.value = creatorState.value ? "stale" : "error";
  } finally {
    if (sequence === requestSequence) controller = undefined;
  }
}

function contentUrl(asset?: CreatorAssetDto) {
  return asset?.contentUrl || "";
}

function addReference() {
  const asset = selectedAsset.value;
  if (!asset) return;
  if (draftBindings.value.some((item) => item.role === addRole.value)) {
    ElMessage.warning(`${categories.find((item) => item.role === addRole.value)?.label}已经有一个权威参考`);
    return;
  }
  const providerEligible = addRole.value !== "style_source" && asset.metadata.providerEligible !== false;
  draftBindings.value.push({ assetId: asset.id, role: addRole.value, providerEligible, title: asset.semanticKey || asset.role, instruction: "" });
}

function removeReference(binding: CreatorReferenceDto) {
  if (requiredRoles.has(binding.role)) {
    ElMessage.warning("固定儿童、固定猫咪和画风板必须始终保留");
    return;
  }
  draftBindings.value = draftBindings.value.filter((item) => item !== binding);
}

async function save(): Promise<boolean> {
  if (!creatorState.value || !dirty.value || saving.value) return !dirty.value;
  saving.value = true;
  try {
    const saved = await creatorApi.updateState(props.projectId, creatorState.value.version, { referenceBindings: draftBindings.value });
    creatorState.value = saved;
    draftBindings.value = saved.referenceBindings.map((binding) => ({ ...binding }));
    ElMessage.success("项目参考已保存；Canon 版本保持固定")
    return true;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
    return false;
  } finally {
    saving.value = false;
  }
}

watch(() => props.projectId, () => { creatorState.value = undefined; assets.value = []; void load(); }, { immediate: true });
watch(dirty, (value) => emit("dirty-change", value ? { scope: `creator-assets:${props.projectId}`, label: "角色资产参考", save, discard: () => { draftBindings.value = (creatorState.value?.referenceBindings ?? []).map((binding) => ({ ...binding })); } } : undefined), { immediate: true });
onBeforeUnmount(() => { requestSequence += 1; controller?.abort(); emit("dirty-change", undefined); });
</script>

<template>
  <section class="asset-workspace" aria-label="角色资产工作区">
    <aside class="categories"><header><span>CANON & REFERENCES</span><h1>角色资产</h1><p>项目固定 Canon，按需添加本集参考。</p></header><nav><button v-for="item in categories" :key="item.role" type="button" @click="addRole = item.role" :class="{ active: addRole === item.role }"><b>{{ item.label }}</b><small>{{ draftBindings.filter(binding => binding.role === item.role).length }}</small></button></nav><section><b>参考职责</b><p><code>style_source</code> 只用于画风提炼，永远不会进入普通 Provider 输入。</p></section></aside>
    <main class="media-board">
      <header><div><span>PROJECT AUTHORITIES</span><h1>当前权威参考</h1></div><button type="button" aria-label="刷新角色资产" @click="load(true)">刷新</button></header>
      <div v-if="state === 'loading' && !creatorState" class="state" aria-busy="true">正在读取 Canon 与媒体…</div>
      <div v-else-if="state === 'error' && !creatorState" class="state error" role="alert"><b>角色资产读取失败</b><p>{{ error }}</p><button type="button" @click="() => load()">重新加载</button></div>
      <div v-else class="grid">
        <article v-for="card in boundCards" :key="`${card.binding.role}:${card.binding.assetId}`" :class="{ selected: selectedAssetId === card.binding.assetId }" @click="selectedAssetId = card.binding.assetId"><img v-if="contentUrl(card.asset) && card.asset?.mediaType === 'image'" :src="contentUrl(card.asset)" :alt="card.binding.title" /><video v-else-if="contentUrl(card.asset)" :src="contentUrl(card.asset)" muted preload="metadata" /><div v-else class="missing">媒体不可预览</div><footer><span>{{ categories.find(item => item.role === card.binding.role)?.label }}</span><b>{{ card.binding.title }}</b><small>{{ card.binding.providerEligible ? "可提交 Provider" : "不可提交 Provider" }}</small><button v-if="!requiredRoles.has(card.binding.role)" type="button" @click.stop="removeReference(card.binding)">移除</button></footer></article>
      </div>
      <footer class="board-actions"><select v-model="selectedAssetId" aria-label="选择项目素材"><option value="">选择已批准素材</option><option v-for="asset in availableAssets" :key="asset.id" :value="asset.id">{{ asset.semanticKey || asset.role }}</option></select><select v-model="addRole" aria-label="参考职责"><option v-for="item in categories" :key="item.role" :value="item.role">{{ item.label }}</option></select><button type="button" :disabled="!selectedAsset || draftBindings.some(item => item.assetId === selectedAssetId)" @click="addReference">添加参考</button><button class="primary" type="button" :disabled="!dirty || saving" @click="save">{{ saving ? "保存中…" : "保存项目参考" }}</button></footer>
      <div v-if="state === 'stale'" class="warning">{{ error }}</div>
    </main>
    <aside class="inspector"><header><span>AUTHORITY</span><h2>职责检查</h2></header><template v-if="draftBindings.find(item => item.assetId === selectedAssetId)"><dl><div><dt>职责</dt><dd>{{ draftBindings.find(item => item.assetId === selectedAssetId)?.role }}</dd></div><div><dt>Provider</dt><dd>{{ draftBindings.find(item => item.assetId === selectedAssetId)?.providerEligible ? "允许" : "禁止" }}</dd></div><div><dt>素材状态</dt><dd>{{ selectedAsset?.status ?? "未知" }}</dd></div><div><dt>SHA-256</dt><dd>{{ selectedAsset?.sha256 ?? "—" }}</dd></div></dl><p>儿童脸型、发型和比例，以及猫咪灰白分区、虎斑、四足结构和尾巴环纹由固定 Canon 保持。</p></template><p v-else>选择一项参考查看其唯一职责和 Provider 资格。</p></aside>
  </section>
</template>

<style scoped>
.asset-workspace{height:100%;display:grid;grid-template-columns:280px minmax(0,1fr) 320px;overflow:hidden;color:#e7eef6;background:#10161d}.categories,.inspector{min-height:0;overflow:auto;background:#151c24}.categories{border-right:1px solid #29343f}.inspector{border-left:1px solid #29343f}.categories>header,.inspector>header{padding:18px;border-bottom:1px solid #29343f}.categories span,.inspector span,.media-board>header span{color:#6d91b3;font-size:9px;font-weight:800;letter-spacing:.14em}.categories h1,.media-board h1,.inspector h2{margin:5px 0}.categories p,.inspector p{color:#8595a6;line-height:1.6}.categories nav{padding:10px;display:grid;gap:4px}.categories nav button{min-height:44px;padding:0 12px;display:flex;align-items:center;justify-content:space-between;color:#91a1b2;text-align:left;background:transparent;border:1px solid transparent;border-radius:9px;cursor:pointer}.categories nav button.active{color:#e4f1fc;background:#1f2d3a;border-color:#36536d}.categories nav small{min-width:22px;padding:3px;text-align:center;background:#283441;border-radius:999px}.categories>section{margin:10px;padding:12px;background:#11171e;border-radius:10px}.categories code{color:#dcaa75}.media-board{position:relative;min-width:0;display:grid;grid-template-rows:68px minmax(0,1fr) 64px;overflow:hidden}.media-board>header{padding:0 16px;display:flex;align-items:center;border-bottom:1px solid #28323c}.media-board>header button{margin-left:auto}.media-board button,.board-actions select{min-height:44px;padding:0 12px;color:#b9c6d3;background:#202a34;border:1px solid #374552;border-radius:9px}.grid{padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));align-content:start;gap:11px;overflow:auto}.grid article{overflow:hidden;background:#171f27;border:1px solid #2c3742;border-radius:11px;cursor:pointer}.grid article.selected{border-color:#4d7da8;box-shadow:0 0 0 2px rgb(60 113 158 / 24%)}.grid img,.grid video,.missing{width:100%;aspect-ratio:4/3;display:block;object-fit:cover;background:#0c1117}.missing{display:grid;place-items:center;color:#657587}.grid footer{padding:10px;display:grid;gap:4px}.grid footer span{color:#6f92b1;font-size:9px}.grid footer small{color:#788a9c}.grid footer button{margin-top:5px}.board-actions{padding:9px 14px;display:flex;gap:8px;border-top:1px solid #29343f;background:#151c24}.board-actions select{min-width:150px}.board-actions select:first-child{flex:1}.board-actions button.primary{color:#f4f9ff;background:#28628f;border-color:#4381af}.state{height:100%;display:grid;place-content:center;justify-items:center;gap:10px;color:#8393a4}.state.error{color:#dda69f}.inspector dl{margin:14px;display:grid;gap:7px}.inspector dl div{padding:10px 0;display:grid;grid-template-columns:80px minmax(0,1fr);border-bottom:1px solid #29343f}.inspector dt{color:#718396}.inspector dd{margin:0;overflow-wrap:anywhere}.inspector>p{padding:0 14px}.warning{position:absolute;right:16px;bottom:78px;padding:9px 12px;color:#d8b77e;background:#33291c;border:1px solid #6d5736;border-radius:9px}@media(max-width:1100px){.asset-workspace{grid-template-columns:250px minmax(0,1fr)}.inspector{display:none}}@media(max-width:700px){.asset-workspace{display:block;overflow:auto}.categories{height:320px;border-right:0}.media-board{height:780px}.board-actions{overflow-x:auto}}
</style>
