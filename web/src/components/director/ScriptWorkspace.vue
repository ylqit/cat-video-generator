<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";

import { creatorApi } from "../../api/client";
import type { CreatorStateDto, CreatorStoryDto, GenerationSnapshotDto } from "../../api/types";
import type { DirectorDirtyRegistration } from "./directorDirtyState";

const props = defineProps<{ projectId: string; initialState?: CreatorStateDto; focusedItemId?: string; panel?: string }>();
const emit = defineEmits<{ "dirty-change": [registration?: DirectorDirtyRegistration] }>();
const state = ref<"loading" | "ready" | "stale" | "error">("loading");
const error = ref("");
const data = ref<CreatorStateDto>();
const selectedKey = ref("current");
const saving = ref(false);
const generating = ref(false);
const editingBlank = ref(false);
const briefDraft = ref("");
const draft = reactive<CreatorStoryDto>({ title: "", summary: "", body: "" });
let controller: AbortController | undefined;
let requestSequence = 0;

const candidates = computed(() => data.value?.storyCandidates ?? []);
const sourceStory = computed<CreatorStoryDto | undefined>(() => {
  if (selectedKey.value === "current") {
    const current = data.value?.currentStory;
    return current?.title && current.body ? current as CreatorStoryDto : undefined;
  }
  const index = Number(selectedKey.value.replace("candidate-", ""));
  return candidates.value[index];
});
const dirty = computed(() => {
  const source = sourceStory.value;
  if (editingBlank.value) return Boolean(draft.title.trim() || draft.body.trim() || draft.summary?.trim());
  return Boolean(source) && (draft.title !== source!.title || draft.body !== source!.body || (draft.summary ?? "") !== (source!.summary ?? ""));
});
const briefDirty = computed(() => Boolean(data.value) && briefDraft.value !== data.value!.briefBody);
const hasDirty = computed(() => dirty.value || briefDirty.value);

function syncDraft(story?: CreatorStoryDto) {
  draft.title = story?.title ?? "";
  draft.summary = story?.summary ?? "";
  draft.body = story?.body ?? "";
}

function startBlankStory() {
  editingBlank.value = true;
  syncDraft({ title: "未命名故事", body: "", summary: "" });
}

function apply(value: CreatorStateDto) {
  data.value = value;
  briefDraft.value = value.briefBody;
  if (selectedKey.value === "current" && !value.currentStory?.body && value.storyCandidates.length) selectedKey.value = "candidate-0";
  syncDraft(sourceStory.value);
}

async function load(background = false) {
  controller?.abort();
  const requestController = new AbortController();
  controller = requestController;
  const sequence = ++requestSequence;
  state.value = background && data.value ? "ready" : "loading";
  error.value = "";
  const initialState = props.initialState;
  if (!background && !data.value && initialState?.projectId === props.projectId) {
    apply(initialState);
    state.value = "ready";
    if (sequence === requestSequence) controller = undefined;
    return;
  }
  try {
    const result = await creatorApi.state(props.projectId, requestController.signal);
    if (requestController.signal.aborted || sequence !== requestSequence) return;
    if (hasDirty.value && data.value) {
      state.value = "stale";
      error.value = "服务器数据已更新；本地未保存内容已保留。";
      return;
    }
    apply(result);
    state.value = "ready";
  } catch (reason) {
    if (requestController.signal.aborted || sequence !== requestSequence) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    state.value = data.value ? "stale" : "error";
  } finally {
    if (sequence === requestSequence) controller = undefined;
  }
}

async function choose(key: string) {
  if (key === selectedKey.value) return;
  if (dirty.value) {
    try {
      await ElMessageBox.confirm("当前正文尚未保存。", "切换候选", { confirmButtonText: "保存并切换", cancelButtonText: "放弃并切换", distinguishCancelAndClose: true, type: "warning" });
      if (!await saveStory()) return;
    } catch (reason) {
      if (reason !== "cancel") return;
    }
  }
  selectedKey.value = key;
  syncDraft(sourceStory.value);
}

async function saveBrief(): Promise<boolean> {
  if (!data.value || !briefDirty.value || saving.value) return !briefDirty.value;
  saving.value = true;
  try {
    apply(await creatorApi.updateState(props.projectId, data.value.version, { briefBody: briefDraft.value }));
    ElMessage.success("创作要求已保存");
    return true;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
    return false;
  } finally {
    saving.value = false;
  }
}

async function saveStory(): Promise<boolean> {
  if (!data.value || saving.value) return false;
  if (!draft.title.trim() || !draft.body.trim()) {
    ElMessage.warning("故事标题和正文不能为空");
    return false;
  }
  saving.value = true;
  try {
    const saved = await creatorApi.saveStory(props.projectId, data.value.version, {
      title: draft.title.trim(),
      body: draft.body,
      summary: draft.summary?.trim() || null,
    });
    selectedKey.value = "current";
    editingBlank.value = false;
    apply(saved);
    ElMessage.success("当前故事已保存");
    return true;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
    return false;
  } finally {
    saving.value = false;
  }
}

async function confirmSnapshot(snapshot: GenerationSnapshotDto) {
  const cost = snapshot.estimatedCostMicros ?? 0;
  const costNotice = snapshot.estimatedCostMicros == null
    ? "费用尚未计量，实际费用以 Provider 账单为准"
    : `预计费用 ¥${(cost / 1_000_000).toFixed(4)}`;
  await ElMessageBox.confirm(
    `将提交 Director 文本任务。输入哈希 ${snapshot.inputHash.slice(0, 10)}…，${costNotice}。取消不会创建任务。`,
    "确认故事候选生成",
    { confirmButtonText: "确认提交", cancelButtonText: "取消", type: "warning" },
  );
  return creatorApi.submitSnapshot(snapshot, cost, crypto.randomUUID());
}

async function generateCandidates() {
  if (!data.value || generating.value) return;
  generating.value = true;
  try {
    if (briefDirty.value && !await saveBrief()) return;
    const snapshot = await creatorApi.storyCandidateSnapshot(props.projectId, {
      briefBody: briefDraft.value,
      requestedCount: 3,
    });
    const task = await confirmSnapshot(snapshot);
    ElMessage.success(`故事任务已进入${task.status}`);
  } catch (reason) {
    if (reason !== "cancel" && reason !== "close") ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  } finally {
    generating.value = false;
  }
}

watch(() => props.projectId, () => { data.value = undefined; selectedKey.value = "current"; void load(); }, { immediate: true });
watch(hasDirty, (value) => emit("dirty-change", value ? { scope: `creator-script:${props.projectId}`, label: "故事与创作要求", save: async () => (await saveBrief()) && (dirty.value ? await saveStory() : true), discard: () => { briefDraft.value = data.value?.briefBody ?? ""; syncDraft(sourceStory.value); } } : undefined), { immediate: true });
onBeforeUnmount(() => { requestSequence += 1; controller?.abort(); emit("dirty-change", undefined); });
</script>

<template>
  <section class="script-workspace" aria-label="剧本工作区">
    <aside class="brief-panel">
      <header><span>CREATIVE BRIEF</span><h1>创作要求</h1></header>
      <textarea v-model="briefDraft" aria-label="创作要求" placeholder="直接输入故事主题、时长节奏和不可改变的要求" />
      <div class="brief-actions"><button type="button" :disabled="!briefDirty || saving" @click="saveBrief">保存要求</button><button class="primary" type="button" :disabled="generating" @click="generateCandidates">{{ generating ? "准备快照…" : "生成故事候选" }}</button></div>
      <section><b>创作原则</b><p>故事由你与模型共同判断；系统只保证内容可编辑、可保存，艺术提示不会阻止继续。</p></section>
    </aside>
    <main class="document-panel">
      <header><div><span>STORY DOCUMENT</span><h1>当前故事</h1></div><button type="button" aria-label="刷新剧本工作区" @click="load(true)">刷新</button></header>
      <nav aria-label="故事候选"><button v-if="data?.currentStory?.body" type="button" :class="{ active: selectedKey === 'current' }" @click="choose('current')">当前故事</button><button v-for="(candidate,index) in candidates" :key="index" type="button" :class="{ active: selectedKey === `candidate-${index}` }" @click="choose(`candidate-${index}`)">候选 {{ index + 1 }}</button></nav>
      <div v-if="state === 'loading' && !data" class="state" aria-busy="true">正在读取故事…</div>
      <div v-else-if="state === 'error' && !data" class="state error" role="alert"><b>故事读取失败</b><p>{{ error }}</p><button type="button" @click="() => load()">重新加载</button></div>
      <div v-else-if="!sourceStory && !editingBlank" class="state"><b>还没有当前故事</b><p>可以直接填写正文，也可以先生成 1–5 个候选。</p><button type="button" @click="startBlankStory">直接创作</button></div>
      <form v-else class="document-editor" @submit.prevent="saveStory"><input v-model="draft.title" aria-label="故事标题" placeholder="故事标题" /><input v-model="draft.summary" aria-label="故事摘要" placeholder="可选摘要" /><textarea v-model="draft.body" aria-label="完整故事正文" placeholder="完整故事正文，可使用 Markdown" /><footer><span>{{ dirty ? "未保存" : "已保存" }}</span><button class="primary" type="submit" :disabled="saving || (!dirty && selectedKey === 'current')">{{ selectedKey === 'current' ? "保存当前故事" : "采用并保存为当前故事" }}</button></footer></form>
      <div v-if="state === 'stale'" class="warning" role="status">{{ error }}</div>
    </main>
  </section>
</template>

<style scoped>
.script-workspace{height:100%;display:grid;grid-template-columns:340px minmax(0,1fr);overflow:hidden;color:#e8eef6;background:#0f151c}.brief-panel{padding:18px;display:grid;grid-template-rows:auto minmax(180px,1fr) auto auto;gap:12px;background:#151c24;border-right:1px solid #29343f}.brief-panel header span,.document-panel>header span{color:#6e91b2;font-size:9px;font-weight:800;letter-spacing:.14em}.brief-panel h1,.document-panel h1{margin:4px 0;font-size:20px}.brief-panel textarea,.document-editor input,.document-editor textarea{width:100%;box-sizing:border-box;color:#dce7f2;background:#0e141a;border:1px solid #303d49;border-radius:10px;outline:none}.brief-panel textarea{padding:13px;resize:none;line-height:1.65}.brief-actions{display:grid;grid-template-columns:1fr 1.2fr;gap:8px}.brief-panel button,.document-panel button{min-height:44px;padding:0 13px;color:#b9c6d3;background:#202a34;border:1px solid #374552;border-radius:9px;cursor:pointer}.brief-panel button.primary,.document-panel button.primary{color:#f4f9ff;background:#28628f;border-color:#4381af}.brief-panel section{padding:12px;color:#8495a7;background:#11171d;border-radius:10px}.brief-panel section p{line-height:1.6}.document-panel{position:relative;min-width:0;display:grid;grid-template-rows:68px 52px minmax(0,1fr);overflow:hidden}.document-panel>header{padding:0 18px;display:flex;align-items:center;border-bottom:1px solid #28323d}.document-panel>header button{margin-left:auto}.document-panel>nav{padding:5px 18px;display:flex;gap:6px;overflow-x:auto;border-bottom:1px solid #26313b}.document-panel>nav button{min-height:40px}.document-panel>nav button.active{color:#e9f4ff;background:#203247;border-color:#3d6387}.state{height:100%;display:grid;place-content:center;justify-items:center;gap:10px;color:#8494a5}.state.error{color:#dfa6a0}.document-editor{min-height:0;padding:18px;display:grid;grid-template-rows:48px 44px minmax(0,1fr) 54px;gap:10px}.document-editor input{padding:0 13px}.document-editor textarea{padding:16px;resize:none;font:14px/1.75 system-ui}.document-editor footer{display:flex;align-items:center;justify-content:space-between;color:#718396}.warning{position:absolute;right:16px;bottom:70px;padding:9px 12px;color:#d8b77e;background:#33291c;border:1px solid #6d5736;border-radius:9px}@media(max-width:900px){.script-workspace{grid-template-columns:280px minmax(0,1fr)}}@media(max-width:700px){.script-workspace{display:block;overflow:auto}.brief-panel{min-height:420px;border-right:0;border-bottom:1px solid #29343f}.document-panel{height:680px}}
</style>
