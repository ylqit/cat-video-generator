<script setup lang="ts">
import { computed, ref } from "vue";
import { Document, EditPen, User } from "@element-plus/icons-vue";

import type { StoryboardCreationMode } from "../../api/types";
import type { TaskCenterStatus } from "../../tasks/taskCenter";

interface ExecutionItem {
  key: string;
  label: string;
  status: TaskCenterStatus | string;
  error?: Record<string, unknown> | null;
}

const props = defineProps<{
  approvedStoryAvailable: boolean;
  selectedReferenceCount: number;
  busy?: boolean;
  healingRecipe?: boolean;
  executions?: ExecutionItem[];
  storyboardReady?: boolean;
  storyboardApproved?: boolean;
}>();
const emit = defineEmits<{
  run: [payload: { mode: Exclude<StoryboardCreationMode, "manual">; instruction: string }];
  manual: [];
  "select-references": [];
  "open-workflow": [];
  approve: [];
}>();

const activeMode = ref<StoryboardCreationMode | null>(null);
const instruction = ref("");
const characterInstructionMissing = computed(() => (
  activeMode.value === "from_characters" && !instruction.value.trim()
));
const stepStatus = computed(() => {
  const latest = props.executions?.[0];
  if (!latest) return ["pending", "pending", "pending"];
  if (["queued", "pending"].includes(latest.status)) return ["queued", "pending", "pending"];
  if (["running", "submitting"].includes(latest.status)) return ["running", "pending", "pending"];
  if (["failed", "submission_unknown"].includes(latest.status)) return ["failed", "pending", "pending"];
  return ["awaiting_review", "pending", "pending"];
});

function submit() {
  if (activeMode.value !== "from_story" && activeMode.value !== "from_characters") return;
  if (characterInstructionMissing.value) return;
  emit("run", { mode: activeMode.value, instruction: instruction.value.trim() });
}
</script>

<template>
  <section class="script-console" aria-label="脚本生成器">
    <div class="step-strip" aria-label="脚本工作流进度">
      <div v-for="(label, index) in ['确认镜头', '准备资产', '合成提示词']" :key="label" :class="`status-${stepStatus[index]}`">
        <span>{{ index + 1 }}</span><b>{{ label }}</b><small>{{ stepStatus[index] === 'pending' ? '待处理' : stepStatus[index] === 'awaiting_review' ? '等待人工核对' : stepStatus[index] }}</small>
      </div>
    </div>

    <div v-if="!activeMode" class="mode-grid">
      <button type="button" :disabled="!approvedStoryAvailable || busy" @click="activeMode = 'from_story'">
        <Document /><span><b>剧本生成分镜脚本</b><small>{{ approvedStoryAvailable ? '读取已批准故事，生成可编辑镜头表' : '请先批准一个故事版本' }}</small></span>
      </button>
      <button type="button" :disabled="!approvedStoryAvailable || busy" @click="activeMode = 'from_characters'">
        <User /><span><b>基于固定角色补充分镜</b><small>{{ approvedStoryAvailable ? '在已批准剧情边界内，用儿童、猫咪和同框图优化动作与构图' : '请先批准剧情脚本，角色素材不能另行创造剧情' }}</small></span>
      </button>
      <button type="button" :disabled="!approvedStoryAvailable || busy" @click="emit('manual')">
        <EditPen /><span><b>自己编写分镜脚本</b><small>{{ approvedStoryAvailable ? '克隆已批准规则与场景，人工编辑且不调用付费模型' : '请先批准剧情脚本与本集规则' }}</small></span>
      </button>
    </div>

    <div v-else class="mode-form">
      <button class="back" type="button" @click="activeMode = null">← 返回三种入口</button>
      <div v-if="activeMode === 'from_characters'" class="reference-row">
        <button type="button" @click="emit('select-references')">＋ 从画布选择角色素材</button>
        <span>已选择 {{ selectedReferenceCount }} / 6</span>
      </div>
      <label>
        {{ activeMode === 'from_story' ? '补充要求（可选）' : '事件或情节要求' }}
        <textarea v-model="instruction" :placeholder="activeMode === 'from_story' ? '例如：雨后清晨、固定机位、收尾更安静' : '例如：孩子整理窗台，猫咪发现一片沾着水珠的叶子'" />
      </label>
      <footer>
        <span v-if="activeMode === 'from_characters' && selectedReferenceCount < (healingRecipe ? 2 : 1)">{{ healingRecipe ? '必须同时选择固定儿童与固定猫咪素材' : '至少选择 1 个角色素材' }}</span>
        <span v-else-if="characterInstructionMissing">请填写本集要发生的低压力事件</span>
        <button class="primary" type="button" :disabled="busy || characterInstructionMissing || (activeMode === 'from_characters' && selectedReferenceCount < (healingRecipe ? 2 : 1))" @click="submit">{{ busy ? '正在生成…' : '生成镜头草稿' }}</button>
      </footer>
    </div>

    <div v-if="executions?.length" class="execution-list" aria-label="节点执行过程">
      <article v-for="item in executions.slice(0, 3)" :key="item.key">
        <span :class="`dot status-${item.status}`" /><b>{{ item.label }}</b><small>{{ item.status }}</small>
        <p v-if="item.error">{{ String(item.error.message ?? '任务失败，请查看任务中心') }}</p>
      </article>
      <button v-if="['awaiting_review', 'succeeded'].includes(executions[0].status)" type="button" @click="emit('open-workflow')">打开脚本三步工作流 →</button>
    </div>

    <div v-if="storyboardReady" class="storyboard-review-state">
      <div>
        <b>{{ storyboardApproved ? '当前分镜已人工批准' : '镜头表已保存，等待人工批准' }}</b>
        <small>{{ storyboardApproved ? '后续可进入视觉锚点与逐镜视频。' : '批准时会固定当前镜头表内容哈希；镜头修改后必须重新审核。' }}</small>
      </div>
      <button type="button" :disabled="busy || storyboardApproved" @click="emit('approve')">
        {{ storyboardApproved ? '已批准' : '批准当前分镜' }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.script-console { min-height: 286px; padding: 18px 20px 20px; color: #efefef; background: #252525; }
.step-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; margin-bottom: 18px; background: #3a3a3a; border: 1px solid #3a3a3a; border-radius: 11px; overflow: hidden; }.step-strip div { min-height: 58px; display: grid; grid-template-columns: 34px 1fr; align-content: center; padding: 10px 14px; background: #202020; }.step-strip span { grid-row: 1 / 3; width: 28px; height: 28px; display: grid; place-items: center; border: 2px solid #666; border-radius: 50%; font-weight: 800; }.step-strip b { font-size: 13px; }.step-strip small { color: #858585; font-size: 10px; }.step-strip .status-running span,.step-strip .status-queued span { color: #dbe9ff; border-color: #75aefb; }.step-strip .status-awaiting_review span { color: #b9f3ce; border-color: #5ed090; }.step-strip .status-failed span { color: #ffd2c7; border-color: #e87968; }
.mode-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }.mode-grid > button { min-height: 112px; display: flex; align-items: flex-start; gap: 14px; padding: 18px; color: #efefef; background: #2d2d2d; border: 1px solid #454545; border-radius: 12px; text-align: left; cursor: pointer; }.mode-grid > button:hover,.mode-grid > button:focus-visible { border-color: #8dbaff; background: #333; outline: 2px solid #508cd7; }.mode-grid > button:disabled { opacity: .42; cursor: not-allowed; }.mode-grid svg { width: 24px; flex: 0 0 auto; }.mode-grid span { display: grid; gap: 8px; }.mode-grid b { font-size: 15px; }.mode-grid small { color: #9a9a9a; line-height: 1.5; }
.mode-form { display: grid; gap: 12px; }.back { justify-self: start; color: #aaa; background: transparent; border: 0; cursor: pointer; }.mode-form label { display: grid; gap: 7px; color: #aaa; font-size: 12px; }.mode-form textarea { box-sizing: border-box; width: 100%; min-height: 92px; padding: 13px; color: #f5f5f5; background: #1a1a1a; border: 1px solid #464646; border-radius: 10px; resize: vertical; }.reference-row,footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.reference-row button,footer button,.execution-list > button { min-height: 44px; padding: 10px 14px; color: #eee; background: #363636; border: 1px solid #515151; border-radius: 9px; cursor: pointer; }.reference-row span,footer span { color: #d0aa64; font-size: 11px; }.primary { margin-left: auto; color: #121212 !important; background: #f2f2f2 !important; border-color: #f2f2f2 !important; font-weight: 800; }.primary:disabled { opacity: .38; cursor: not-allowed; }
.execution-list { display: grid; gap: 6px; margin-top: 14px; padding-top: 12px; border-top: 1px solid #3b3b3b; }.execution-list article { min-height: 34px; display: grid; grid-template-columns: 10px 1fr auto; align-items: center; gap: 9px; }.execution-list small { color: #939393; }.execution-list p { grid-column: 2 / 4; margin: 0; color: #e39b87; font-size: 11px; }.dot { width: 8px; height: 8px; background: #777; border-radius: 50%; }.dot.status-running,.dot.status-queued { background: #70aaff; }.dot.status-succeeded,.dot.status-awaiting_review { background: #62d18f; }.dot.status-failed,.dot.status-submission_unknown { background: #e77969; }
.storyboard-review-state { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-top: 14px; padding: 13px 14px; background: #202923; border: 1px solid #3e5b49; border-radius: 10px; }.storyboard-review-state div { display: grid; gap: 4px; }.storyboard-review-state small { color: #90a898; line-height: 1.45; }.storyboard-review-state button { min-height: 44px; flex: 0 0 auto; padding: 10px 16px; color: #102017; background: #bce7ca; border: 1px solid #bce7ca; border-radius: 9px; font-weight: 800; cursor: pointer; }.storyboard-review-state button:disabled { color: #94a398; background: #34423a; border-color: #43564a; cursor: default; }
@media (max-width: 820px) { .mode-grid { grid-template-columns: 1fr; }.step-strip { grid-template-columns: 1fr; } }
</style>
