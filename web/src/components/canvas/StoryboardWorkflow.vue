<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ArrowDown, ArrowUp, Close, Delete, Plus } from "@element-plus/icons-vue";

import { api, canvasApi } from "../../api/client";
import type {
  ProjectGraph,
  SceneAssetReadinessDto,
  SceneDto,
  StoryboardPromptReferenceBindingDto,
} from "../../api/types";

export interface StoryboardShotDraft {
  id?: string;
  revision?: number;
  order: number;
  durationSeconds: number;
  title: string;
  action: string;
  shotSize: string;
  lighting: string;
  dialogue: string;
  soundEffect: string;
  camera: string;
  prompt: string;
  promptId?: string;
  promptInputHash?: string;
  promptWarnings?: string[];
  promptBlockers?: string[];
  referenceBindings?: StoryboardPromptReferenceBindingDto[];
  estimatedCost?: { currency: string; amountMicros: number };
  temporalBeats?: Array<Record<string, unknown>>;
  compositionAssetIds?: string[];
  sceneId?: string;
  sceneTitle?: string;
}

const props = defineProps<{
  modelValue: boolean;
  shots: StoryboardShotDraft[];
  healingRecipe?: boolean;
  targetDurationSeconds?: number;
  saving?: boolean;
  projectId?: string;
  storyRevisionId?: string;
  visualProfileRevisionId?: string;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  save: [shots: StoryboardShotDraft[]];
  openScene: [sceneId: string];
}>();

const state = reactive({ step: 1, rows: [] as StoryboardShotDraft[] });
const graph = ref<ProjectGraph | null>(null);
const readinessBySceneId = ref<Record<string, SceneAssetReadinessDto>>({});
const assetLoading = ref(false);
const assetError = ref("");
const compileLoading = ref(false);
const compileError = ref("");
let assetLoadSequence = 0;
watch(() => [props.modelValue, props.shots] as const, ([visible, shots]) => {
  if (visible) {
    state.rows = shots.map((shot, index) => ({ ...shot, order: index + 1 }));
    state.step = 1;
  }
}, { immediate: true, deep: true });

watch(() => [props.modelValue, state.step, props.projectId] as const, ([visible, _step, projectId]) => {
  if (visible && projectId) void loadSceneAssets(projectId);
});

const totalDuration = computed(() => state.rows.reduce((sum, row) => sum + Number(row.durationSeconds || 0), 0));
const rowWarnings = computed(() => state.rows.map((row) => {
  const issues: string[] = [];
  if (!row.title.trim() || !row.action.trim()) issues.push("画面描述不完整");
  if (props.healingRecipe && (row.durationSeconds < 8 || row.durationSeconds > 15)) issues.push("治愈组合包每镜必须为 8–15 秒");
  if (props.healingRecipe && row.dialogue.trim()) issues.push("治愈组合包禁止对白");
  if (!row.promptId || !row.promptInputHash) issues.push("最终提示词待服务端合成");
  issues.push(...(row.promptBlockers ?? []));
  return issues;
}));
const scenes = computed<SceneDto[]>(() => graph.value?.scenes ?? []);
const sceneById = computed(() => new Map(scenes.value.map((scene) => [scene.id, scene])));
const assetRows = computed(() => state.rows.map((row) => {
  const scene = row.sceneId ? sceneById.value.get(row.sceneId) : undefined;
  const readiness = row.sceneId ? readinessBySceneId.value[row.sceneId] : undefined;
  const bindings = ["儿童身份 · Canon", "猫咪身份 · Canon"];
  if (readiness) {
    bindings.push(...readiness.requiredSlots
      .filter((slot) => slot.status === "ready")
      .map((slot) => `${slot.displayName} · 已批准`));
    if (readiness.sceneLookStatus === "approved") bindings.push("场景视觉基准 · 已批准");
  }
  return {
    order: row.order,
    title: row.title || `镜头 ${row.order}`,
    sceneId: row.sceneId,
    sceneTitle: scene?.title ?? row.sceneTitle ?? "场景待确认",
    bindings,
    missing: [
      ...(!row.sceneId ? ["镜头尚未指定故事场景"] : []),
      ...(readiness?.blockers ?? []),
    ],
    ready: Boolean(readiness?.canCompileShotPrompt),
  };
}));
const structurallyValid = computed(() => state.rows.length > 0 && rowWarnings.value.every(
  (issues) => !issues.some((issue) => issue.includes("8–15") || issue.includes("禁止对白") || issue.includes("画面描述")),
) && (!props.healingRecipe || !props.targetDurationSeconds || totalDuration.value === props.targetDurationSeconds));
const canSave = computed(() => (
  structurallyValid.value
  && state.rows.every((row) => Boolean(row.promptId && row.promptInputHash && !(row.promptBlockers?.length)))
));
const canCompileAssets = computed(() => (
  !assetLoading.value
  && !assetError.value
  && assetRows.value.length > 0
  && assetRows.value.every((row) => row.ready)
));

async function loadSceneAssets(projectId: string) {
  const sequence = ++assetLoadSequence;
  assetLoading.value = true;
  assetError.value = "";
  try {
    const nextGraph = await api.project(projectId);
    if (sequence !== assetLoadSequence) return;
    graph.value = nextGraph;
    const nextReadiness: Record<string, SceneAssetReadinessDto> = {};
    for (const scene of nextGraph.scenes) {
      const response = await api.sceneVisualAssets(scene.id);
      if (sequence !== assetLoadSequence) return;
      nextReadiness[scene.id] = response.readiness;
    }
    readinessBySceneId.value = nextReadiness;
  } catch (error) {
    if (sequence === assetLoadSequence) {
      assetError.value = error instanceof Error ? error.message : String(error);
    }
  } finally {
    if (sequence === assetLoadSequence) assetLoading.value = false;
  }
}

function addRow() {
  state.rows.push({
    order: state.rows.length + 1,
    durationSeconds: props.healingRecipe ? 8 : 5,
    title: "",
    action: "",
    shotSize: "中景",
    lighting: "柔和自然光",
    dialogue: "",
    soundEffect: "环境声",
    camera: "固定机位",
    prompt: "",
    promptWarnings: [],
    promptBlockers: [],
    referenceBindings: [],
    sceneId: scenes.value[Math.min(state.rows.length, Math.max(0, scenes.value.length - 1))]?.id,
  });
}
function removeRow(index: number) {
  state.rows.splice(index, 1);
  state.rows.forEach((row, rowIndex) => { row.order = rowIndex + 1; });
}
function moveRow(index: number, direction: -1 | 1) {
  const target = index + direction;
  if (target < 0 || target >= state.rows.length) return;
  const [row] = state.rows.splice(index, 1);
  state.rows.splice(target, 0, row);
  state.rows.forEach((item, rowIndex) => { item.order = rowIndex + 1; });
}
function invalidateCompiledPrompt(row: StoryboardShotDraft) {
  row.prompt = "";
  row.promptId = undefined;
  row.promptInputHash = undefined;
  row.promptWarnings = [];
  row.promptBlockers = [];
  row.referenceBindings = [];
  row.estimatedCost = undefined;
  compileError.value = "";
}

async function compilePrompts() {
  if (!props.projectId || !props.storyRevisionId || !props.visualProfileRevisionId) {
    compileError.value = "剧情脚本定稿或本集视觉档案尚未加载，无法进行可审计的 Prompt 编译";
    return;
  }
  compileLoading.value = true;
  compileError.value = "";
  try {
    const response = await canvasApi.compileStoryboardPrompts(props.projectId, {
      storyRevisionId: props.storyRevisionId,
      visualProfileRevisionId: props.visualProfileRevisionId,
      healingRecipe: Boolean(props.healingRecipe),
      shots: state.rows.map((row, index) => ({
        beatId: row.id,
        expectedRevision: row.id ? Number(row.revision ?? 1) : 0,
        order: index + 1,
        sceneId: row.sceneId,
        durationSeconds: Number(row.durationSeconds),
        title: row.title.trim(),
        action: row.action.trim(),
        shotSize: row.shotSize.trim(),
        lighting: row.lighting.trim(),
        dialogue: row.dialogue.trim(),
        soundEffect: row.soundEffect.trim(),
        camera: row.camera.trim(),
        temporalBeats: row.temporalBeats ?? [],
        compositionAssetIds: row.compositionAssetIds ?? [],
      })),
    });
    const resultByOrder = new Map(response.shots.map((shot) => [shot.order, shot]));
    state.rows.forEach((row, index) => {
      const result = resultByOrder.get(index + 1);
      if (!result) return;
      row.prompt = result.finalPrompt;
      row.promptId = result.promptId ?? undefined;
      row.promptInputHash = result.inputHash;
      row.promptWarnings = [...result.warnings];
      row.promptBlockers = [...result.blockers];
      row.referenceBindings = [...result.referenceBindings];
      row.estimatedCost = result.estimatedCost;
    });
    if (response.status === "blocked") {
      compileError.value = "部分镜头缺少已批准的 Canon、本集造型、Scene Look、道具或构图资产，请按镜头修复阻塞项";
    }
    state.step = 3;
  } catch (error) {
    compileError.value = error instanceof Error ? error.message : String(error);
  } finally {
    compileLoading.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <section v-if="modelValue" class="storyboard-workflow" role="dialog" aria-modal="true" aria-label="分镜脚本三步工作流">
      <header>
        <ol>
          <li v-for="(label, index) in ['确认镜头', '准备资产', '合成提示词']" :key="label" :class="{ active: state.step === index + 1, done: state.step > index + 1 }">
            <button type="button" @click="state.step = index + 1"><span>{{ index + 1 }}</span><b>{{ label }}</b></button>
          </li>
        </ol>
        <div><b>{{ state.rows.length }} 个镜头 · {{ totalDuration }} 秒</b><button type="button" aria-label="关闭脚本工作流" @click="emit('update:modelValue', false)"><Close /></button></div>
      </header>

      <main v-if="state.step === 1" class="shot-table-wrap">
        <table>
          <thead><tr><th>镜号</th><th>时长</th><th>所属场景</th><th>画面描述</th><th>景别</th><th>光影氛围</th><th>对白/旁白</th><th>音效</th><th>运镜</th><th>最终提示词</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in state.rows" :key="row.id ?? `new-${index}`">
              <td>{{ index + 1 }}</td>
              <td><input v-model.number="row.durationSeconds" type="number" :min="healingRecipe ? 8 : 1" :max="healingRecipe ? 15 : 60" @input="invalidateCompiledPrompt(row)" />s</td>
              <td><select v-model="row.sceneId" aria-label="镜头所属场景" @change="invalidateCompiledPrompt(row)"><option value="">请选择场景</option><option v-for="scene in scenes" :key="scene.id" :value="scene.id">{{ scene.title }}</option></select></td>
              <td><input v-model="row.title" placeholder="镜头标题" @input="invalidateCompiledPrompt(row)" /><textarea v-model="row.action" placeholder="主体动作与画面变化" @input="invalidateCompiledPrompt(row)" /></td>
              <td><input v-model="row.shotSize" @input="invalidateCompiledPrompt(row)" /></td><td><input v-model="row.lighting" @input="invalidateCompiledPrompt(row)" /></td>
              <td><textarea v-model="row.dialogue" :disabled="healingRecipe" :placeholder="healingRecipe ? '固定无对白' : '对白或旁白'" @input="invalidateCompiledPrompt(row)" /></td>
              <td><input v-model="row.soundEffect" @input="invalidateCompiledPrompt(row)" /></td><td><input v-model="row.camera" @input="invalidateCompiledPrompt(row)" /></td>
              <td><textarea :value="row.prompt" readonly placeholder="将在第 3 步由服务端按引用职责编译" /></td>
              <td><div class="row-actions"><button type="button" :disabled="index === 0" aria-label="上移镜头" @click="moveRow(index, -1)"><ArrowUp /></button><button type="button" :disabled="index === state.rows.length - 1" aria-label="下移镜头" @click="moveRow(index, 1)"><ArrowDown /></button><button type="button" aria-label="删除镜头" @click="removeRow(index)"><Delete /></button></div><small v-for="warning in rowWarnings[index]" :key="warning">{{ warning }}</small></td>
            </tr>
          </tbody>
        </table>
      </main>

      <main v-else-if="state.step === 2" class="asset-step">
        <p v-if="assetLoading" class="asset-message">正在逐场景核对已批准资产与 Scene Look…</p>
        <p v-else-if="assetError" class="asset-message error">{{ assetError }} <button type="button" @click="projectId && loadSceneAssets(projectId)">重试</button></p>
        <article v-for="row in assetRows" :key="row.order" :class="{ ready: row.ready }"><span>{{ row.order }}</span><div><b>{{ row.title }}</b><small>所属场景：{{ row.sceneTitle }}</small><p v-for="binding in row.bindings" :key="binding">✓ {{ binding }}</p><p v-for="missing in row.missing" :key="missing" class="missing">缺失：{{ missing }}</p></div><button type="button" :disabled="!row.sceneId" @click="row.sceneId && emit('openScene', row.sceneId)">准备该场景资产</button></article>
      </main>

      <main v-else class="prompt-step">
        <p v-if="compileError" class="compile-message error">{{ compileError }}</p>
        <article v-for="(row, index) in state.rows" :key="row.id ?? index" :class="{ blocked: row.promptBlockers?.length }">
          <header><b>镜头 {{ index + 1 }} · {{ row.title || '未命名' }}</b><span>{{ row.durationSeconds }} 秒</span></header>
          <textarea :value="row.prompt" readonly placeholder="该镜头尚未通过服务端分层编译" />
          <section v-if="row.referenceBindings?.length" class="reference-audit" aria-label="实际引用职责">
            <b>实际引用</b>
            <span v-for="binding in row.referenceBindings" :key="`${binding.role}-${binding.assetId}`">
              {{ binding.role }} · {{ binding.title || binding.semanticKey || binding.purpose }}
            </span>
          </section>
          <p v-for="warning in row.promptWarnings" :key="warning" class="compile-warning">提示：{{ warning }}</p>
          <p v-for="blocker in row.promptBlockers" :key="blocker" class="compile-blocker">阻塞：{{ blocker }}</p>
          <footer>
            <span>输入哈希：{{ row.promptInputHash?.slice(0, 12) || '未生成' }} · 预计费用：{{ row.estimatedCost ? `${row.estimatedCost.currency} ${(row.estimatedCost.amountMicros / 1_000_000).toFixed(3)}` : '待计算' }}</span>
            <b>{{ row.promptId && !row.promptBlockers?.length ? '可进入分镜审核' : '不可生成锚点' }}</b>
          </footer>
        </article>
      </main>

      <footer class="workflow-footer">
        <button type="button" @click="addRow"><Plus /> 添加镜头</button>
        <span v-if="healingRecipe">固定规则：8–15 秒 / 镜 · 无对白 · Canon 身份不可替换 · 总时长 {{ totalDuration }}/{{ targetDurationSeconds }} 秒</span>
        <button v-if="state.step > 1" type="button" @click="state.step -= 1">上一步</button>
        <button v-if="state.step === 1" class="primary" type="button" :disabled="!structurallyValid" @click="state.step = 2">下一步：准备资产</button>
        <button v-else-if="state.step === 2" class="primary" type="button" :disabled="!canCompileAssets || compileLoading" @click="compilePrompts">{{ compileLoading ? '服务端编译中…' : '下一步：合成提示词' }}</button>
        <button v-else class="primary" type="button" :disabled="saving || !canSave" @click="emit('save', state.rows.map((row) => ({ ...row })))">{{ saving ? '保存中…' : '保存为新分镜版本' }}</button>
      </footer>
    </section>
  </Teleport>
</template>

<style scoped>
.storyboard-workflow { position: fixed; inset: 0; z-index: 3000; display: grid; grid-template-rows: 112px minmax(0,1fr) 86px; color: #ededed; background: #121212; }
.storyboard-workflow > header { display: grid; grid-template-columns: minmax(620px, 1fr) auto; align-items: center; gap: 30px; padding: 18px 42px; border-bottom: 1px solid #333; }.storyboard-workflow > header ol { display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); margin: 0; padding: 0; list-style: none; }.storyboard-workflow > header li { position: relative; }.storyboard-workflow > header li:not(:last-child)::after { content: ""; position: absolute; top: 21px; left: calc(50% + 36px); right: 18px; height: 1px; background: #555; }.storyboard-workflow > header li button { display: flex; align-items: center; gap: 12px; color: #8d8d8d; background: transparent; border: 0; cursor: pointer; }.storyboard-workflow > header li span { width: 40px; height: 40px; display: grid; place-items: center; border: 3px solid #5a5a5a; border-radius: 50%; font-size: 17px; }.storyboard-workflow > header li.active button,.storyboard-workflow > header li.done button { color: #fff; }.storyboard-workflow > header li.active span,.storyboard-workflow > header li.done span { border-color: #fff; }.storyboard-workflow > header > div { display: flex; align-items: center; gap: 18px; }.storyboard-workflow > header > div button { width: 44px; height: 44px; padding: 12px; color: #aaa; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }
main { overflow: auto; }.shot-table-wrap { margin: 28px 48px 0; border: 1px solid #3b3b3b; border-radius: 12px 12px 0 0; background: #181818; }table { width: 100%; min-width: 1780px; border-collapse: collapse; }th,td { border-right: 1px solid #393939; border-bottom: 1px solid #393939; padding: 11px; vertical-align: top; }th { height: 54px; color: #949494; background: #252525; text-align: left; font-size: 12px; }td:first-child { width: 50px; text-align: center; }td:nth-child(2) { width: 80px; white-space: nowrap; }td:nth-child(3) { min-width: 160px; }td:nth-child(4),td:nth-child(10) { min-width: 270px; }td:nth-child(11) { width: 126px; }input,textarea,select { box-sizing: border-box; width: 100%; padding: 9px; color: #eee; background: #202020; border: 1px solid #3d3d3d; border-radius: 7px; font: inherit; }textarea { min-height: 64px; margin-top: 5px; resize: vertical; }input:focus,textarea:focus,select:focus { border-color: #80aff8; outline: 2px solid #3a67a4; }.row-actions { display: flex; gap: 4px; }.row-actions button { width: 34px; height: 34px; padding: 8px; color: #aaa; background: #2a2a2a; border: 1px solid #444; border-radius: 7px; cursor: pointer; }.row-actions button:disabled { opacity: .3; }.row-actions + small,td > small { display: block; margin-top: 7px; color: #e7a36f; line-height: 1.3; }
.asset-step,.prompt-step { display: grid; align-content: start; gap: 10px; padding: 30px 48px; }.asset-step article { display: grid; grid-template-columns: 46px 1fr auto; align-items: center; gap: 16px; padding: 18px; background: #202020; border: 1px solid #3c3c3c; border-radius: 12px; }.asset-step article.ready { border-color: #37654f; }.asset-step article > span { width: 38px; height: 38px; display: grid; place-items: center; background: #303030; border-radius: 50%; }.asset-step article small { display: block; margin-top: 5px; color: #9ba5b3; }.asset-step p { display: inline-block; margin: 8px 14px 0 0; color: #8ed2a6; }.asset-step p.missing { color: #e2ae69; }.asset-step button { min-height: 44px; padding: 9px 13px; color: #eee; background: #333; border: 1px solid #505050; border-radius: 9px; }.asset-step button:disabled { opacity: .35; }.asset-message { margin: 0; padding: 14px; border: 1px solid #3c3c3c; border-radius: 10px; color: #aeb7c4; background: #202020; }.asset-message.error { color: #efaaaf; }.asset-message button { margin-left: 10px; }.prompt-step { grid-template-columns: repeat(2, minmax(360px,1fr)); }.prompt-step > .compile-message { grid-column: 1 / -1; }.prompt-step article { padding: 16px; background: #202020; border: 1px solid #3c3c3c; border-radius: 12px; }.prompt-step article.blocked { border-color: #8a6332; }.prompt-step article header,.prompt-step article footer { display: flex; justify-content: space-between; gap: 12px; }.prompt-step article textarea { min-height: 130px; margin: 12px 0; }.prompt-step article footer { color: #969696; font-size: 11px; }.prompt-step article footer b { color: #85d7a1; }.prompt-step article.blocked footer b { color: #e3a861; }.reference-audit { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }.reference-audit > b { width: 100%; color: #aaa; font-size: 11px; }.reference-audit > span { padding: 4px 7px; color: #a8c7f4; background: #252c35; border: 1px solid #39495c; border-radius: 999px; font-size: 10px; }.compile-warning,.compile-blocker,.compile-message { margin: 7px 0; padding: 8px 10px; border-radius: 7px; font-size: 11px; }.compile-warning { color: #d8b279; background: #30291f; }.compile-blocker,.compile-message.error { color: #efaaaf; background: #342126; }
.workflow-footer { display: flex; align-items: center; gap: 10px; padding: 16px 48px; border-top: 1px solid #333; background: #181818; }.workflow-footer span { flex: 1; color: #9d9d9d; font-size: 11px; }.workflow-footer button { min-height: 44px; display: flex; align-items: center; gap: 7px; padding: 10px 16px; color: #eee; background: #303030; border: 1px solid #4a4a4a; border-radius: 9px; cursor: pointer; }.workflow-footer button svg { width: 16px; }.workflow-footer button.primary { color: #111; background: #f4f4f4; border-color: #f4f4f4; font-weight: 800; }.workflow-footer button:disabled { opacity: .36; cursor: not-allowed; }
@media (max-width: 1000px) { .storyboard-workflow > header { grid-template-columns: 1fr; padding-inline: 20px; }.storyboard-workflow > header > div { display: none; }.shot-table-wrap { margin-inline: 16px; }.prompt-step { grid-template-columns: 1fr; padding-inline: 16px; }.workflow-footer { padding-inline: 16px; }.workflow-footer span { display: none; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
</style>
