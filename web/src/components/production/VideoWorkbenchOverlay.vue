<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { creatorApi } from "../../api/client";
import type { CreatorAssetDto, CreatorReferenceDto, CreatorShotDto, CreatorStateDto, CreatorTaskDto, CreatorTimelineClipDto, CreatorTimelineDto, GenerationSnapshotDto } from "../../api/types";

type WorkbenchTab = "preview" | "generate" | "edit";
const props = withDefaults(defineProps<{ projectId: string; tab: WorkbenchTab; trackId?: string; shotId?: string }>(), { trackId: "", shotId: "" });
const emit = defineEmits<{ close: []; "tab-change": [tab: WorkbenchTab]; "track-change": [trackId: string] }>();
const state = ref<"loading" | "ready" | "stale" | "error">("loading");
const error = ref("");
const creatorState = ref<CreatorStateDto>();
const shots = ref<CreatorShotDto[]>([]);
const assets = ref<CreatorAssetDto[]>([]);
const tasks = ref<CreatorTaskDto[]>([]);
const timeline = ref<CreatorTimelineDto>();
const selectedShotId = ref("");
const promptDraft = ref("");
const savingPrompt = ref(false);
const preparing = ref(false);
const pendingSnapshot = ref<GenerationSnapshotDto>();
let controller: AbortController | undefined;

const selectedShot = computed(() => shots.value.find((shot) => shot.id === selectedShotId.value));
const selectedTask = computed(() => tasks.value.find((task) => task.creatorShotId === selectedShotId.value));
const orderedReferences = computed<CreatorReferenceDto[]>(() => {
  const fixed = creatorState.value?.referenceBindings ?? [];
  const overrides = selectedShot.value?.referenceBindings ?? [];
  const required = new Set(["child_identity", "cat_identity", "style_board"]);
  const byRole = new Map<string, CreatorReferenceDto>();
  fixed.filter((item) => item.role !== "style_source").forEach((item) => byRole.set(item.role, item));
  overrides.filter((item) => item.role !== "style_source" && !required.has(item.role)).forEach((item) => byRole.set(item.role, item));
  if (byRole.has("child_appearance")) byRole.delete("child_identity");
  if (byRole.has("cat_appearance")) byRole.delete("cat_identity");
  const order = ["child_identity", "cat_identity", "child_appearance", "cat_appearance", "pair_scale", "environment", "prop", "style_board"];
  return order.flatMap((role) => byRole.has(role) ? [byRole.get(role)!] : []);
});
const assetById = computed(() => new Map(assets.value.map((asset) => [asset.id, asset])));
const versions = computed(() => assets.value.filter((asset) => asset.mediaType === "video" && (asset.creatorShotId === selectedShotId.value || asset.id === selectedShot.value?.selectedVideoAssetId)));
const promptDirty = computed(() => Boolean(selectedShot.value) && promptDraft.value !== (selectedShot.value?.promptDraft || compiledPrompt(selectedShot.value!)));

function compiledPrompt(shot: CreatorShotDto) {
  return `生成一个 ${creatorState.value?.aspectRatio || "9:16"}、${shot.durationSeconds} 秒的原创二维治愈生活短片。\n\n参考职责：\n${orderedReferences.value.map((item, index) => `@图片${index + 1}：${item.title}——${item.role}`).join("\n")}\n\n身份连续性：儿童始终保持项目固定的脸型、五官、8–9 岁年龄感、深色齐下颌短发和儿童身体比例。猫咪始终保持同一只灰白虎斑猫的头脸、眼睛、鼻口、毛色分区、主要虎斑、四足结构和尾巴环纹。画风板只控制线条、材质、色阶和光线，不添加其中不存在的内容。\n\n镜头正文：\n${shot.direction}\n\n连续性：只出现一名儿童和一只猫；不分身、不无理由换装、不改变毛色，不出现额外肢体、融脸、穿模、字幕、Logo 或水印。`;
}

function syncPrompt() {
  promptDraft.value = selectedShot.value ? selectedShot.value.promptDraft || compiledPrompt(selectedShot.value) : "";
  pendingSnapshot.value = undefined;
}

async function load(background = false) {
  controller?.abort();
  controller = new AbortController();
  state.value = background && creatorState.value ? "ready" : "loading";
  error.value = "";
  try {
    const [nextState, nextShots, nextAssets, diagnostics, nextTimeline] = await Promise.all([
      creatorApi.state(props.projectId, controller.signal),
      creatorApi.shots(props.projectId, controller.signal),
      creatorApi.assets(props.projectId, controller.signal),
      creatorApi.diagnostics(props.projectId, controller.signal),
      creatorApi.timeline(props.projectId, controller.signal),
    ]);
    if (controller.signal.aborted) return;
    creatorState.value = nextState;
    shots.value = nextShots;
    assets.value = nextAssets;
    tasks.value = diagnostics.tasks;
    timeline.value = nextTimeline;
    selectedShotId.value = props.shotId && nextShots.some((shot) => shot.id === props.shotId) ? props.shotId : selectedShotId.value && nextShots.some((shot) => shot.id === selectedShotId.value) ? selectedShotId.value : nextShots[0]?.id || "";
    syncPrompt();
    state.value = "ready";
  } catch (reason) {
    if (controller.signal.aborted) return;
    error.value = reason instanceof Error ? reason.message : String(reason);
    state.value = creatorState.value ? "stale" : "error";
  }
}

function chooseShot(id: string) {
  if (promptDirty.value) {
    ElMessage.warning("请先保存或放弃当前 Prompt 草稿");
    return;
  }
  selectedShotId.value = id;
  syncPrompt();
  emit("track-change", id);
}

async function savePrompt() {
  const shot = selectedShot.value;
  if (!shot || !promptDirty.value) return;
  savingPrompt.value = true;
  try {
    const saved = await creatorApi.updateShot(shot.id, shot.version, { promptDraft: promptDraft.value });
    shots.value = shots.value.map((item) => item.id === saved.id ? saved : item);
    syncPrompt();
    ElMessage.success("Prompt 草稿已保存");
  } catch (reason) {
    ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  } finally {
    savingPrompt.value = false;
  }
}

async function prepareSnapshot() {
  const shot = selectedShot.value;
  if (!shot || preparing.value) return;
  if (promptDirty.value) await savePrompt();
  preparing.value = true;
  try {
    pendingSnapshot.value = await creatorApi.createSnapshot(shot.id, {
      kind: "video",
      promptText: promptDraft.value,
      orderedReferences: orderedReferences.value,
      providerConfig: { provider: "ark", model: "doubao-seedance", mode: "reference_media", durationSeconds: shot.durationSeconds, aspectRatio: creatorState.value?.aspectRatio, qualityTier: creatorState.value?.qualityTier },
    });
  } catch (reason) {
    ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  } finally {
    preparing.value = false;
  }
}

async function submitSnapshot() {
  const snapshot = pendingSnapshot.value;
  if (!snapshot) return;
  const cost = snapshot.estimatedCostMicros ?? 0;
  const costNotice = snapshot.estimatedCostMicros == null
    ? "费用尚未计量，实际费用以 Provider 账单为准"
    : `预计费用 ¥${(cost / 1_000_000).toFixed(4)}`;
  try {
    await ElMessageBox.confirm(`将提交 1 次视频任务。已冻结 ${snapshot.orderedReferences.length} 张有序参考、Prompt、模型参数和输入哈希 ${snapshot.inputHash.slice(0, 12)}…。${costNotice}。取消不会创建任务。`, "确认付费生成输入", { confirmButtonText: "确认提交", cancelButtonText: "取消", type: "warning" });
    const task = await creatorApi.submitSnapshot(snapshot, cost, crypto.randomUUID());
    tasks.value = [task, ...tasks.value.filter((item) => item.taskId !== task.taskId)];
    pendingSnapshot.value = undefined;
    ElMessage.success(`任务已进入${task.status}`);
  } catch (reason) {
    if (reason !== "cancel" && reason !== "close") ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  }
}

async function selectVersion(asset: CreatorAssetDto) {
  const shot = selectedShot.value;
  if (!shot) return;
  try {
    if (asset.status !== "approved") await creatorApi.decideAsset(asset.id, "adopt", "用户在视频工作台采用此版本");
    const saved = await creatorApi.selectVideo(shot.id, shot.version, asset.id);
    shots.value = shots.value.map((item) => item.id === saved.id ? saved : item);
    ElMessage.success("已选择当前视频版本");
  } catch (reason) {
    ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  }
}

async function addSelectedVideosToTimeline() {
  if (!timeline.value) return;
  const clips = shots.value.flatMap<CreatorTimelineClipDto>((shot) => shot.selectedVideoAssetId
    ? [{ creatorShotId: shot.id, assetId: shot.selectedVideoAssetId, transition: "cut", transitionDurationMs: 0 }]
    : []);
  if (!clips.length) {
    ElMessage.warning("请先为至少一个镜头选择采用的视频版本");
    return;
  }
  try {
    timeline.value = await creatorApi.saveTimeline(props.projectId, timeline.value.version, clips);
    ElMessage.success("已按镜头顺序更新成片时间线");
  } catch (reason) {
    ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  }
}

async function updateTransition(index: number, transition: CreatorTimelineClipDto["transition"]) {
  if (!timeline.value) return;
  const clips = timeline.value.clips.map((clip, clipIndex) => clipIndex === index
    ? { ...clip, transition, transitionDurationMs: transition === "cut" ? 0 : 300 }
    : clip);
  try {
    timeline.value = await creatorApi.saveTimeline(props.projectId, timeline.value.version, clips);
    ElMessage.success("转场设置已保存");
  } catch (reason) {
    ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  }
}

watch(() => props.projectId, () => { creatorState.value = undefined; void load(); }, { immediate: true });
watch(() => props.shotId, (id) => { if (id && shots.value.some((shot) => shot.id === id)) { selectedShotId.value = id; syncPrompt(); } });
onBeforeUnmount(() => controller?.abort());
</script>

<template>
  <section class="video-workbench" aria-label="视频工作台">
    <header><div><span>VIDEO WORKBENCH</span><h1>视频生产与成片</h1></div><nav><button v-for="item in ([['preview','预览'],['generate','视频生成'],['edit','剪辑与交付']] as const)" :key="item[0]" type="button" :class="{ active: tab === item[0] }" @click="emit('tab-change',item[0])">{{ item[1] }}</button></nav><button type="button" aria-label="关闭视频工作台" @click="emit('close')">关闭</button></header>
    <div v-if="state === 'loading' && !creatorState" class="state" aria-busy="true">正在读取镜头、参考、任务和视频版本…</div>
    <div v-else-if="state === 'error' && !creatorState" class="state error" role="alert"><b>视频工作台加载失败</b><p>{{ error }}</p><button type="button" @click="() => load()">重新加载</button></div>
    <template v-else>
      <nav class="shot-strip"><button v-for="shot in shots" :key="shot.id" type="button" :class="{ active: selectedShotId === shot.id }" @click="chooseShot(shot.id)"><b>{{ shot.sortOrder }}. {{ shot.title }}</b><small>{{ shot.durationSeconds }}s · {{ shot.selectedVideoAssetId ? '已采用' : '待选片' }}</small></button></nav>
      <section v-if="tab === 'preview'" class="preview-tab"><main><video v-if="versions.find(item => item.id === selectedShot?.selectedVideoAssetId)" :src="versions.find(item => item.id === selectedShot?.selectedVideoAssetId)?.contentUrl" controls /><div v-else class="empty">当前镜头还没有采用的视频版本</div></main><aside><h2>版本</h2><article v-for="version in versions" :key="version.id"><video :src="version.contentUrl" muted preload="metadata" /><div><b>{{ version.semanticKey || version.role }}</b><small>{{ version.status }}</small><button type="button" @click="selectVersion(version)">{{ selectedShot?.selectedVideoAssetId === version.id ? '当前采用' : '采用此版本' }}</button></div></article><p v-if="!versions.length">暂无视频候选</p></aside></section>
      <section v-else-if="tab === 'generate'" class="generate-tab"><div class="reference-strip"><article v-for="(reference,index) in orderedReferences" :key="reference.assetId"><img v-if="assetById.get(reference.assetId)?.mediaType === 'image'" :src="assetById.get(reference.assetId)?.contentUrl" :alt="reference.title" /><div><span>@图片{{ index + 1 }}</span><b>{{ reference.title }}</b><small>{{ reference.role }} · {{ reference.providerEligible ? 'Provider 可用' : '禁止提交' }}</small></div></article></div><aside class="prompt-panel"><header><h2>Provider 实际 Prompt</h2><button type="button" :disabled="!promptDirty || savingPrompt" @click="savePrompt">保存草稿</button></header><textarea v-model="promptDraft" aria-label="视频生成 Prompt" /><details><summary>审计信息</summary><p>项目 {{ projectId }} · 镜头 {{ selectedShotId }}</p><p v-if="pendingSnapshot">输入哈希 {{ pendingSnapshot.inputHash }}</p></details></aside><main class="result-panel"><div class="generation-summary"><b>{{ selectedShot?.durationSeconds || 0 }}s · {{ creatorState?.aspectRatio }} · reference_media</b><small>{{ orderedReferences.length }} 张实际参考</small></div><video v-if="versions[0]" :src="versions[0].contentUrl" controls /><div v-else class="empty">尚无生成结果</div><div v-if="selectedTask" class="task" :data-status="selectedTask.status"><b>{{ selectedTask.status }}</b><small>{{ selectedTask.providerTaskId || '尚无 Provider task ID' }}</small></div><div v-if="pendingSnapshot" class="snapshot"><b>冻结输入待确认</b><small>{{ pendingSnapshot.inputHash }}</small><button type="button" @click="submitSnapshot">确认并提交</button></div><button v-else class="primary" type="button" :disabled="preparing || !selectedShot" @click="prepareSnapshot">{{ preparing ? '冻结输入中…' : '预览冻结输入' }}</button></main><aside class="history"><h2>历史版本</h2><button v-for="version in versions" :key="version.id" type="button" @click="selectVersion(version)"><b>{{ version.semanticKey || version.id.slice(0,8) }}</b><small>{{ version.status }}</small></button><h2>任务</h2><article v-for="task in tasks.filter(item => item.creatorShotId === selectedShotId)" :key="task.taskId"><b>{{ task.status }}</b><small>{{ task.providerTaskId || task.taskId }}</small></article></aside></section>
      <section v-else class="edit-tab">
        <main>
          <header><div><span>CREATOR TIMELINE</span><h2>剪辑与交付</h2></div><button type="button" @click="addSelectedVideosToTimeline">从已采用版本更新时间线</button></header>
          <div v-if="!timeline?.clips.length" class="empty">时间线为空。先在“预览”中采用视频版本，再按镜头顺序加入。</div>
          <ol v-else class="timeline-clips">
            <li v-for="(clip,index) in timeline.clips" :key="`${clip.creatorShotId}:${clip.assetId}`">
              <video :src="assetById.get(clip.assetId)?.contentUrl" controls preload="metadata" />
              <div><b>{{ shots.find(item => item.id === clip.creatorShotId)?.title || `镜头 ${index + 1}` }}</b><small>{{ shots.find(item => item.id === clip.creatorShotId)?.durationSeconds || 0 }} 秒 · {{ clip.assetId.slice(0,8) }}</small></div>
              <label>进入转场<select :value="clip.transition" @change="updateTransition(index, ($event.target as HTMLSelectElement).value as CreatorTimelineClipDto['transition'])"><option value="cut">直接切换</option><option value="fade_black">黑场淡入</option><option value="cross_dissolve">交叉溶解</option></select></label>
            </li>
          </ol>
        </main>
        <aside><h2>交付状态</h2><dl><dt>时间线版本</dt><dd>V{{ timeline?.version || 0 }}</dd><dt>片段数</dt><dd>{{ timeline?.clips.length || 0 }}</dd><dt>状态</dt><dd>{{ timeline?.status || 'draft' }}</dd><dt>最终成片</dt><dd>{{ timeline?.finalAssetId ? '已有导出' : '尚未合成' }}</dd></dl><p>新基线不恢复任何旧时间线或导出；只有当前采用的视频版本可以进入这里。</p></aside>
      </section>
      <div v-if="state === 'stale'" class="warning">数据可能过期：{{ error }}</div>
    </template>
  </section>
</template>

<style scoped>
.video-workbench{position:absolute;z-index:120;inset:0;display:grid;grid-template-rows:62px 64px minmax(0,1fr);overflow:hidden;color:#e8eef6;background:#0d1319}.video-workbench>header{padding:0 14px;display:flex;align-items:center;gap:18px;background:#151c24;border-bottom:1px solid #2b3641}.video-workbench>header span{color:#6e91b2;font-size:9px;font-weight:800;letter-spacing:.14em}.video-workbench h1{margin:3px 0;font-size:19px}.video-workbench>header nav{margin-left:auto;display:flex;gap:5px}.video-workbench button{min-height:44px;padding:0 13px;color:#b9c6d3;background:#202a34;border:1px solid #374552;border-radius:9px;cursor:pointer}.video-workbench>header nav button.active{color:#eef8ff;background:#244968;border-color:#47789f}.shot-strip{padding:8px 12px;display:flex;gap:7px;overflow-x:auto;background:#111820;border-bottom:1px solid #2a3540}.shot-strip button{min-width:190px;display:grid;text-align:left}.shot-strip button.active{border-color:#4b7fa9;background:#1c2b39}.shot-strip small{color:#788a9c}.state{grid-row:2/-1;height:100%;display:grid;place-content:center;justify-items:center;gap:10px;color:#8494a5}.state.error{color:#dea59f}.preview-tab{min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 340px}.preview-tab>main{padding:18px;display:grid;place-items:center;background:#080c10}.preview-tab>main video{max-width:100%;max-height:100%}.preview-tab>aside{padding:14px;overflow:auto;background:#151c24;border-left:1px solid #2b3641}.preview-tab>aside article{margin-bottom:10px;display:grid;grid-template-columns:110px 1fr;overflow:hidden;background:#111820;border:1px solid #2d3944;border-radius:9px}.preview-tab>aside video{width:110px;height:90px;object-fit:cover}.preview-tab>aside article div{padding:8px;display:grid;gap:5px}.preview-tab>aside small,.history small{color:#7d8fa1}.empty{min-height:180px;display:grid;place-items:center;color:#718294;background:#10171e;border:1px dashed #34414e;border-radius:11px}.generate-tab{min-height:0;display:grid;grid-template-columns:380px minmax(360px,1fr) 300px;grid-template-rows:112px minmax(0,1fr);overflow:hidden}.reference-strip{grid-column:1/-1;padding:9px 12px;display:flex;gap:8px;overflow-x:auto;background:#111820;border-bottom:1px solid #2b3641}.reference-strip article{flex:0 0 220px;display:grid;grid-template-columns:76px 1fr;overflow:hidden;background:#1a222b;border:1px solid #34414e;border-radius:9px}.reference-strip img{width:76px;height:92px;object-fit:cover}.reference-strip article>div{padding:8px;display:grid;align-content:center;gap:4px}.reference-strip span{color:#6e91b2;font-size:9px}.reference-strip small{color:#788a9c}.prompt-panel,.history{min-height:0;padding:12px;overflow:auto;background:#151c24}.prompt-panel{border-right:1px solid #2b3641}.prompt-panel>header{display:flex;align-items:center;justify-content:space-between}.prompt-panel textarea{width:100%;height:calc(100% - 110px);min-height:260px;padding:13px;box-sizing:border-box;resize:none;color:#dde7f1;background:#0c1218;border:1px solid #303d49;border-radius:9px;font:12px/1.7 system-ui}.prompt-panel details{margin-top:8px;color:#7f91a3}.result-panel{min-height:0;padding:16px;display:grid;grid-template-rows:auto minmax(0,1fr) auto auto;gap:10px;overflow:auto;background:#0b1015}.result-panel video{width:100%;height:100%;min-height:240px;object-fit:contain;background:#050709;border-radius:10px}.generation-summary{display:flex;justify-content:space-between}.generation-summary small{color:#7f91a3}.task,.snapshot{padding:10px;display:flex;align-items:center;gap:10px;background:#16202a;border:1px solid #2d3d4c;border-radius:9px}.task small,.snapshot small{overflow:hidden;text-overflow:ellipsis;color:#8092a4}.task[data-status=provider_running]{border-color:#76603d}.snapshot button{margin-left:auto}.result-panel>.primary{color:#f4f9ff;background:#28628f;border-color:#4381af}.history{border-left:1px solid #2b3641}.history>button,.history>article{width:100%;margin-bottom:6px;padding:9px;display:grid;text-align:left}.warning{position:absolute;right:14px;bottom:14px;padding:9px 12px;color:#d7b77c;background:#33291c;border:1px solid #6b5636;border-radius:9px}@media(max-width:1100px){.generate-tab{grid-template-columns:330px minmax(0,1fr)}.history{display:none}}@media(max-width:760px){.generate-tab{display:block;overflow:auto}.reference-strip{height:96px}.prompt-panel,.result-panel{height:520px}.preview-tab{grid-template-columns:1fr}.preview-tab>aside{display:none}}
@media(max-width:760px){.video-workbench{grid-template-rows:108px 58px minmax(0,1fr)}.video-workbench>header{padding:7px 8px;display:grid;grid-template-columns:minmax(0,1fr) 58px;grid-template-rows:43px 44px;gap:5px 7px}.video-workbench>header>div{min-width:0}.video-workbench h1{overflow:hidden;margin:1px 0;font-size:15px;text-overflow:ellipsis;white-space:nowrap}.video-workbench>header nav{grid-column:1/-1;margin:0;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px}.video-workbench>header nav button{min-width:0;padding:0 4px}.video-workbench>header>button{grid-column:2;grid-row:1;padding:0 7px}.shot-strip{padding:7px 8px}.shot-strip button{min-width:158px;padding:0 9px}.reference-strip article{flex-basis:190px}.preview-tab>main{padding:10px}}
.edit-tab{min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 320px;overflow:hidden}.edit-tab>main{padding:18px;overflow:auto;background:#0b1015}.edit-tab>main>header{display:flex;align-items:center;justify-content:space-between}.edit-tab>main>header span{color:#6e91b2;font-size:9px;font-weight:800;letter-spacing:.14em}.edit-tab h2{margin:4px 0 14px}.edit-tab>aside{padding:18px;overflow:auto;background:#151c24;border-left:1px solid #2b3641}.edit-tab dl{display:grid;grid-template-columns:1fr auto;gap:10px}.edit-tab dt{color:#8395a7}.edit-tab dd{margin:0;color:#dce7f2}.edit-tab>aside p{color:#8395a7;line-height:1.7}.timeline-clips{margin:18px 0 0;padding:0;display:grid;gap:10px;list-style:none}.timeline-clips li{min-height:120px;padding:10px;display:grid;grid-template-columns:160px minmax(0,1fr) 180px;align-items:center;gap:14px;background:#151d25;border:1px solid #303d49;border-radius:11px}.timeline-clips video{width:160px;height:96px;object-fit:cover;background:#050709;border-radius:8px}.timeline-clips div{display:grid;gap:6px}.timeline-clips small{color:#8192a4}.timeline-clips label{display:grid;gap:6px;color:#8fa0b1}.timeline-clips select{min-height:44px;padding:0 9px;color:#dce7f2;background:#0e151c;border:1px solid #374552;border-radius:8px}
</style>
