<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import { ArrowDown, ArrowUp, Close, Delete, Plus } from "@element-plus/icons-vue";

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
}

const props = defineProps<{
  modelValue: boolean;
  shots: StoryboardShotDraft[];
  healingRecipe?: boolean;
  targetDurationSeconds?: number;
  saving?: boolean;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  save: [shots: StoryboardShotDraft[]];
}>();

const state = reactive({ step: 1, rows: [] as StoryboardShotDraft[] });
watch(() => [props.modelValue, props.shots] as const, ([visible, shots]) => {
  if (visible) state.rows = shots.map((shot, index) => ({ ...shot, order: index + 1 }));
}, { immediate: true, deep: true });

const totalDuration = computed(() => state.rows.reduce((sum, row) => sum + Number(row.durationSeconds || 0), 0));
const rowWarnings = computed(() => state.rows.map((row) => {
  const issues: string[] = [];
  if (!row.title.trim() || !row.action.trim()) issues.push("画面描述不完整");
  if (props.healingRecipe && (row.durationSeconds < 8 || row.durationSeconds > 15)) issues.push("治愈组合包每镜必须为 8–15 秒");
  if (props.healingRecipe && row.dialogue.trim()) issues.push("治愈组合包禁止对白");
  if (!row.prompt.trim()) issues.push("最终提示词待合成");
  return issues;
}));
const assetRows = computed(() => state.rows.map((row) => ({
  order: row.order,
  title: row.title || `镜头 ${row.order}`,
  bindings: ["儿童身份 · Canon", "猫咪身份 · Canon", "单一环境水彩风格"],
  missing: row.action.trim() ? [] : ["画面动作"],
})));
const canSave = computed(() => state.rows.length > 0 && rowWarnings.value.every(
  (issues) => !issues.some((issue) => issue.includes("8–15") || issue.includes("禁止对白") || issue.includes("画面描述")),
) && (!props.healingRecipe || !props.targetDurationSeconds || totalDuration.value === props.targetDurationSeconds));

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
function compilePrompts() {
  state.rows.forEach((row) => {
    if (row.prompt.trim()) return;
    row.prompt = [row.action, row.shotSize, row.lighting, row.camera, row.soundEffect]
      .filter(Boolean)
      .join("；");
  });
  state.step = 3;
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
          <thead><tr><th>镜号</th><th>时长</th><th>画面描述</th><th>景别</th><th>光影氛围</th><th>对白/旁白</th><th>音效</th><th>运镜</th><th>最终提示词</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in state.rows" :key="row.id ?? `new-${index}`">
              <td>{{ index + 1 }}</td>
              <td><input v-model.number="row.durationSeconds" type="number" :min="healingRecipe ? 8 : 1" :max="healingRecipe ? 15 : 60" />s</td>
              <td><input v-model="row.title" placeholder="镜头标题" /><textarea v-model="row.action" placeholder="主体动作与画面变化" /></td>
              <td><input v-model="row.shotSize" /></td><td><input v-model="row.lighting" /></td>
              <td><textarea v-model="row.dialogue" :disabled="healingRecipe" :placeholder="healingRecipe ? '固定无对白' : '对白或旁白'" /></td>
              <td><input v-model="row.soundEffect" /></td><td><input v-model="row.camera" /></td>
              <td><textarea v-model="row.prompt" placeholder="可在第 3 步自动合成" /></td>
              <td><div class="row-actions"><button type="button" :disabled="index === 0" aria-label="上移镜头" @click="moveRow(index, -1)"><ArrowUp /></button><button type="button" :disabled="index === state.rows.length - 1" aria-label="下移镜头" @click="moveRow(index, 1)"><ArrowDown /></button><button type="button" aria-label="删除镜头" @click="removeRow(index)"><Delete /></button></div><small v-for="warning in rowWarnings[index]" :key="warning">{{ warning }}</small></td>
            </tr>
          </tbody>
        </table>
      </main>

      <main v-else-if="state.step === 2" class="asset-step">
        <article v-for="row in assetRows" :key="row.order"><span>{{ row.order }}</span><div><b>{{ row.title }}</b><p v-for="binding in row.bindings" :key="binding">✓ {{ binding }}</p><p v-for="missing in row.missing" :key="missing" class="missing">缺失：{{ missing }}</p></div><button type="button">从画布选择资产</button></article>
      </main>

      <main v-else class="prompt-step">
        <article v-for="(row, index) in state.rows" :key="row.id ?? index"><header><b>镜头 {{ index + 1 }} · {{ row.title || '未命名' }}</b><span>{{ row.durationSeconds }} 秒</span></header><textarea v-model="row.prompt" /><footer><span>身份来源：Canon · 环境风格：单一水彩参考</span><b>{{ row.prompt.trim() ? '可生成' : '待合成' }}</b></footer></article>
      </main>

      <footer class="workflow-footer">
        <button type="button" @click="addRow"><Plus /> 添加镜头</button>
        <span v-if="healingRecipe">固定规则：8–15 秒 / 镜 · 无对白 · Canon 身份不可替换 · 总时长 {{ totalDuration }}/{{ targetDurationSeconds }} 秒</span>
        <button v-if="state.step > 1" type="button" @click="state.step -= 1">上一步</button>
        <button v-if="state.step === 1" class="primary" type="button" :disabled="!canSave" @click="state.step = 2">下一步：准备资产</button>
        <button v-else-if="state.step === 2" class="primary" type="button" @click="compilePrompts">下一步：合成提示词</button>
        <button v-else class="primary" type="button" :disabled="saving || !canSave" @click="emit('save', state.rows.map((row) => ({ ...row })))">{{ saving ? '保存中…' : '保存为新分镜版本' }}</button>
      </footer>
    </section>
  </Teleport>
</template>

<style scoped>
.storyboard-workflow { position: fixed; inset: 0; z-index: 3000; display: grid; grid-template-rows: 112px minmax(0,1fr) 86px; color: #ededed; background: #121212; }
.storyboard-workflow > header { display: grid; grid-template-columns: minmax(620px, 1fr) auto; align-items: center; gap: 30px; padding: 18px 42px; border-bottom: 1px solid #333; }.storyboard-workflow > header ol { display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); margin: 0; padding: 0; list-style: none; }.storyboard-workflow > header li { position: relative; }.storyboard-workflow > header li:not(:last-child)::after { content: ""; position: absolute; top: 21px; left: calc(50% + 36px); right: 18px; height: 1px; background: #555; }.storyboard-workflow > header li button { display: flex; align-items: center; gap: 12px; color: #8d8d8d; background: transparent; border: 0; cursor: pointer; }.storyboard-workflow > header li span { width: 40px; height: 40px; display: grid; place-items: center; border: 3px solid #5a5a5a; border-radius: 50%; font-size: 17px; }.storyboard-workflow > header li.active button,.storyboard-workflow > header li.done button { color: #fff; }.storyboard-workflow > header li.active span,.storyboard-workflow > header li.done span { border-color: #fff; }.storyboard-workflow > header > div { display: flex; align-items: center; gap: 18px; }.storyboard-workflow > header > div button { width: 44px; height: 44px; padding: 12px; color: #aaa; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }
main { overflow: auto; }.shot-table-wrap { margin: 28px 48px 0; border: 1px solid #3b3b3b; border-radius: 12px 12px 0 0; background: #181818; }table { width: 100%; min-width: 1640px; border-collapse: collapse; }th,td { border-right: 1px solid #393939; border-bottom: 1px solid #393939; padding: 11px; vertical-align: top; }th { height: 54px; color: #949494; background: #252525; text-align: left; font-size: 12px; }td:first-child { width: 50px; text-align: center; }td:nth-child(2) { width: 80px; white-space: nowrap; }td:nth-child(3),td:nth-child(9) { min-width: 270px; }td:nth-child(10) { width: 126px; }input,textarea { box-sizing: border-box; width: 100%; padding: 9px; color: #eee; background: #202020; border: 1px solid #3d3d3d; border-radius: 7px; font: inherit; }textarea { min-height: 64px; margin-top: 5px; resize: vertical; }input:focus,textarea:focus { border-color: #80aff8; outline: 2px solid #3a67a4; }.row-actions { display: flex; gap: 4px; }.row-actions button { width: 34px; height: 34px; padding: 8px; color: #aaa; background: #2a2a2a; border: 1px solid #444; border-radius: 7px; cursor: pointer; }.row-actions button:disabled { opacity: .3; }.row-actions + small,td > small { display: block; margin-top: 7px; color: #e7a36f; line-height: 1.3; }
.asset-step,.prompt-step { display: grid; align-content: start; gap: 10px; padding: 30px 48px; }.asset-step article { display: grid; grid-template-columns: 46px 1fr auto; align-items: center; gap: 16px; padding: 18px; background: #202020; border: 1px solid #3c3c3c; border-radius: 12px; }.asset-step article > span { width: 38px; height: 38px; display: grid; place-items: center; background: #303030; border-radius: 50%; }.asset-step p { display: inline-block; margin: 8px 14px 0 0; color: #8ed2a6; }.asset-step p.missing { color: #e2ae69; }.asset-step button { min-height: 44px; padding: 9px 13px; color: #eee; background: #333; border: 1px solid #505050; border-radius: 9px; }.prompt-step { grid-template-columns: repeat(2, minmax(360px,1fr)); }.prompt-step article { padding: 16px; background: #202020; border: 1px solid #3c3c3c; border-radius: 12px; }.prompt-step article header,.prompt-step article footer { display: flex; justify-content: space-between; gap: 12px; }.prompt-step article textarea { min-height: 130px; margin: 12px 0; }.prompt-step article footer { color: #969696; font-size: 11px; }.prompt-step article footer b { color: #85d7a1; }
.workflow-footer { display: flex; align-items: center; gap: 10px; padding: 16px 48px; border-top: 1px solid #333; background: #181818; }.workflow-footer span { flex: 1; color: #9d9d9d; font-size: 11px; }.workflow-footer button { min-height: 44px; display: flex; align-items: center; gap: 7px; padding: 10px 16px; color: #eee; background: #303030; border: 1px solid #4a4a4a; border-radius: 9px; cursor: pointer; }.workflow-footer button svg { width: 16px; }.workflow-footer button.primary { color: #111; background: #f4f4f4; border-color: #f4f4f4; font-weight: 800; }.workflow-footer button:disabled { opacity: .36; cursor: not-allowed; }
@media (max-width: 1000px) { .storyboard-workflow > header { grid-template-columns: 1fr; padding-inline: 20px; }.storyboard-workflow > header > div { display: none; }.shot-table-wrap { margin-inline: 16px; }.prompt-step { grid-template-columns: 1fr; padding-inline: 16px; }.workflow-footer { padding-inline: 16px; }.workflow-footer span { display: none; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
</style>
