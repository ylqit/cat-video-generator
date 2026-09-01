<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onMounted, reactive, ref, watch } from "vue";

import { creatorApi } from "../../api/client";
import type { CreatorShotDto, CreatorStateDto } from "../../api/types";

const props = defineProps<{ projectId: string; creatorState: CreatorStateDto; shots: CreatorShotDto[]; initialShotId?: string }>();
const emit = defineEmits<{ close: []; saved: [] }>();
const drafts = ref<Array<Partial<CreatorShotDto>>>([]);
const selectedIndex = ref(0);
const saving = ref(false);
const baseline = ref("");
const titleInput = ref<HTMLInputElement>();

const selected = computed(() => drafts.value[selectedIndex.value]);
const totalDuration = computed(() => drafts.value.reduce((sum, shot) => sum + Number(shot.durationSeconds || 0), 0));
const durationDelta = computed(() => totalDuration.value - props.creatorState.targetDurationSeconds);
const dirty = computed(() => JSON.stringify(drafts.value) !== baseline.value);

function reset() {
  drafts.value = props.shots.length
    ? props.shots.map((shot) => ({
        ...shot,
        referenceBindings: shot.referenceBindings.map((binding) => ({ ...binding })),
      }))
    : [{ title: "镜头 1", direction: "", durationSeconds: props.creatorState.targetDurationSeconds, sceneLabel: "" }];
  const index = props.initialShotId ? drafts.value.findIndex((shot) => shot.id === props.initialShotId) : 0;
  selectedIndex.value = index >= 0 ? index : 0;
  baseline.value = JSON.stringify(drafts.value);
}

function addShot() {
  if (drafts.value.length >= 6) return;
  drafts.value.push({ title: `镜头 ${drafts.value.length + 1}`, direction: "", durationSeconds: Math.max(1, Math.round(props.creatorState.targetDurationSeconds / (drafts.value.length + 1))), sceneLabel: "" });
  selectedIndex.value = drafts.value.length - 1;
}

function duplicateShot() {
  if (!selected.value || drafts.value.length >= 6) return;
  const copy = {
    ...selected.value,
    referenceBindings: selected.value.referenceBindings?.map((binding) => ({ ...binding })),
  };
  delete copy.id;
  delete copy.version;
  copy.title = `${copy.title || "镜头"} · 副本`;
  drafts.value.splice(selectedIndex.value + 1, 0, copy);
  selectedIndex.value += 1;
}

function removeShot() {
  if (drafts.value.length <= 1) return;
  drafts.value.splice(selectedIndex.value, 1);
  selectedIndex.value = Math.min(selectedIndex.value, drafts.value.length - 1);
}

function move(delta: number) {
  const target = selectedIndex.value + delta;
  if (target < 0 || target >= drafts.value.length) return;
  const [shot] = drafts.value.splice(selectedIndex.value, 1);
  drafts.value.splice(target, 0, shot);
  selectedIndex.value = target;
}

function rebalance() {
  const base = Math.floor(props.creatorState.targetDurationSeconds / drafts.value.length);
  let remaining = props.creatorState.targetDurationSeconds;
  drafts.value.forEach((shot, index) => {
    const duration = index === drafts.value.length - 1 ? remaining : Math.max(1, base);
    shot.durationSeconds = duration;
    remaining -= duration;
  });
}

async function save() {
  if (!dirty.value || saving.value) return;
  if (drafts.value.some((shot) => !shot.title?.trim() || !shot.direction?.trim() || !shot.durationSeconds)) {
    ElMessage.warning("每个镜头都需要标题、完整描述和有效时长");
    return;
  }
  saving.value = true;
  try {
    await creatorApi.replaceShots(props.projectId, props.creatorState.version, drafts.value.map((shot) => ({ ...shot, referenceBindings: shot.referenceBindings ?? [] })));
    baseline.value = JSON.stringify(drafts.value);
    ElMessage.success("当前镜头列表已保存");
    emit("saved");
  } catch (reason) {
    ElMessage.error(reason instanceof Error ? reason.message : String(reason));
  } finally {
    saving.value = false;
  }
}

async function close() {
  if (dirty.value) {
    try {
      await ElMessageBox.confirm("当前镜头修改尚未保存。", "关闭镜头编辑", { confirmButtonText: "保存并关闭", cancelButtonText: "放弃修改", distinguishCancelAndClose: true, type: "warning" });
      await save();
      if (dirty.value) return;
    } catch (reason) {
      if (reason !== "cancel") return;
    }
  }
  emit("close");
}

watch(selectedIndex, () => requestAnimationFrame(() => titleInput.value?.focus()));
onMounted(() => { reset(); requestAnimationFrame(() => titleInput.value?.focus()); });
</script>

<template>
  <section class="shot-editor" aria-label="镜头编辑器">
    <header><div><span>SHOT EDITOR</span><h1>当前镜头</h1></div><div class="summary"><b>{{ drafts.length }} 镜 · {{ totalDuration }} 秒</b><small :class="{ mismatch: durationDelta !== 0 }">{{ durationDelta === 0 ? "总时长匹配" : `与目标相差 ${durationDelta > 0 ? '+' : ''}${durationDelta} 秒` }}</small></div><button type="button" aria-label="关闭镜头编辑器" @click="close">关闭</button></header>
    <aside><nav><button v-for="(shot,index) in drafts" :key="shot.id || `new-${index}`" type="button" :class="{ active: selectedIndex === index }" @click="selectedIndex = index"><span>{{ index + 1 }}</span><b>{{ shot.title || `镜头 ${index + 1}` }}</b><small>{{ shot.durationSeconds || 0 }}s</small></button></nav><div class="list-actions"><button type="button" :disabled="drafts.length >= 6" @click="addShot">添加</button><button type="button" :disabled="drafts.length >= 6" @click="duplicateShot">复制</button><button type="button" :disabled="drafts.length <= 1" @click="removeShot">删除</button></div></aside>
    <main v-if="selected"><div class="field title"><label>镜头标题</label><input ref="titleInput" v-model="selected.title" /></div><div class="field duration"><label>时长（秒）</label><input v-model.number="selected.durationSeconds" type="number" min="1" max="60" /></div><div class="field scene"><label>场景（可选）</label><input v-model="selected.sceneLabel" /></div><div class="field direction"><label>完整镜头描述</label><textarea v-model="selected.direction" placeholder="把构图、人物与猫咪动作、时间节拍、运镜、光线和声音写成一段专业自然语言。" /></div><details><summary>更多执行信息</summary><p>摄影、声音和连续性不再是十几个必填 Schema 字段；需要时直接写入完整镜头描述。镜头专用参考在生成输入中按职责绑定。</p></details></main>
    <footer><div><button type="button" :disabled="selectedIndex === 0" @click="move(-1)">上移</button><button type="button" :disabled="selectedIndex === drafts.length - 1" @click="move(1)">下移</button><button type="button" @click="rebalance">重新平衡时长</button></div><span>{{ dirty ? "未保存修改" : "已保存" }}</span><button class="primary" type="button" :disabled="!dirty || saving" @click="save">{{ saving ? "保存中…" : "保存当前镜头" }}</button></footer>
  </section>
</template>

<style scoped>
.shot-editor{position:absolute;z-index:100;inset:12px;display:grid;grid-template-columns:280px minmax(0,1fr);grid-template-rows:68px minmax(0,1fr) 66px;overflow:hidden;color:#e8eef6;background:#121920;border:1px solid #35424f;border-radius:14px;box-shadow:0 24px 70px rgb(0 0 0 / 55%)}.shot-editor>header{grid-column:1/-1;padding:0 16px;display:flex;align-items:center;gap:18px;background:#171f27;border-bottom:1px solid #2d3844}.shot-editor>header span{color:#6d91b3;font-size:9px;font-weight:800;letter-spacing:.14em}.shot-editor h1{margin:4px 0;font-size:19px}.shot-editor>header .summary{margin-left:auto;display:grid;text-align:right}.shot-editor>header small{color:#78a68c}.shot-editor>header small.mismatch{color:#d9ad70}.shot-editor button{min-height:44px;padding:0 12px;color:#b9c6d3;background:#202a34;border:1px solid #374552;border-radius:9px;cursor:pointer}.shot-editor>aside{min-height:0;display:grid;grid-template-rows:minmax(0,1fr) auto;overflow:hidden;background:#151c24;border-right:1px solid #2d3844}.shot-editor nav{padding:10px;display:grid;align-content:start;gap:6px;overflow:auto}.shot-editor nav button{display:grid;grid-template-columns:30px minmax(0,1fr) auto;align-items:center;gap:8px;text-align:left}.shot-editor nav button.active{color:#eff7ff;background:#203247;border-color:#43698c}.shot-editor nav span{width:28px;height:28px;display:grid;place-items:center;background:#2c3a47;border-radius:7px}.shot-editor nav b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.shot-editor nav small{color:#7e91a4}.list-actions{padding:9px;display:grid;grid-template-columns:repeat(3,1fr);gap:6px;border-top:1px solid #2d3844}.shot-editor main{padding:18px;display:grid;grid-template-columns:minmax(0,1fr) 140px 220px;grid-template-rows:auto minmax(0,1fr) auto;gap:13px;overflow:auto}.field{display:grid;gap:6px}.field label{color:#8193a5;font-size:10px}.field input,.field textarea{width:100%;box-sizing:border-box;color:#e2ebf4;background:#0d1319;border:1px solid #303d49;border-radius:9px;outline:none}.field input{height:44px;padding:0 12px}.field.direction{grid-column:1/-1;min-height:260px}.field textarea{height:100%;min-height:260px;padding:14px;resize:none;font:14px/1.7 system-ui}.shot-editor details{grid-column:1/-1;padding:12px;color:#8798a8;background:#151c24;border-radius:9px}.shot-editor details p{line-height:1.6}.shot-editor>footer{grid-column:1/-1;padding:10px 14px;display:flex;align-items:center;gap:8px;background:#171f27;border-top:1px solid #2d3844}.shot-editor>footer div{display:flex;gap:6px}.shot-editor>footer span{margin-left:auto;color:#7f91a3}.shot-editor>footer button.primary{color:#f4f9ff;background:#28628f;border-color:#4381af}@media(max-width:850px){.shot-editor{grid-template-columns:220px minmax(0,1fr)}.shot-editor main{grid-template-columns:1fr 120px}.field.scene{grid-column:1/-1}}@media(max-width:650px){.shot-editor{inset:0;border-radius:0;grid-template-columns:1fr;grid-template-rows:68px 170px minmax(0,1fr) 110px}.shot-editor>aside{border-right:0;border-bottom:1px solid #2d3844}.shot-editor nav{display:flex;overflow-x:auto}.shot-editor nav button{min-width:200px}.shot-editor>footer{align-items:stretch;flex-wrap:wrap}.shot-editor>footer span{order:-1;width:100%;margin:0}}
</style>
