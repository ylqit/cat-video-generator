<script setup lang="ts">
import { computed } from "vue";
import { Document, MagicStick, Picture } from "@element-plus/icons-vue";

import type { CanvasNodeDto, SubjectReferenceDto } from "../../api/types";

const props = withDefaults(defineProps<{
  node: CanvasNodeDto;
  selected?: boolean;
  selectionState?: "none" | "compatible" | "incompatible" | "chosen";
  selectionIndex?: number;
}>(), {
  selected: false,
  selectionState: "none",
  selectionIndex: 0,
});
const emit = defineEmits<{
  "select-node": [node: CanvasNodeDto];
  "activate-node": [node: CanvasNodeDto];
  "inspect-prompt": [promptId: string];
  "approve-story": [revisionId: string];
  "edit-object": [node: CanvasNodeDto];
  "promote-candidate": [node: CanvasNodeDto, candidate: Record<string, unknown>];
  "edit-video": [node: CanvasNodeDto];
  "assist-subject": [node: CanvasNodeDto];
  "open-composer": [node: CanvasNodeDto];
  "upload-reference": [node: CanvasNodeDto];
  "select-history": [node: CanvasNodeDto];
  "create-subject": [node: CanvasNodeDto];
  "open-recipe": [node: CanvasNodeDto];
  "open-context-menu": [node: CanvasNodeDto, event: MouseEvent];
}>();

const data = computed(() => props.node.data);
const subjectReferences = computed<SubjectReferenceDto[]>(() => {
  if (props.node.type !== "SubjectNode" || !Array.isArray(data.value.references)) return [];
  return data.value.references as SubjectReferenceDto[];
});
const title = computed(() => String(data.value.title ?? ({
  RecipeGroupNode: "一人一猫治愈短片",
  BriefNode: "创意简报",
  SubjectNode: "主体",
  StylePresetNode: "画风预设",
  CharacterDesignNode: "角色设计",
  StoryPlannerNode: "三案故事策划",
  StoryCandidateNode: "故事候选",
  ApprovalGateNode: "人工审批",
  StoryboardDirectorNode: "分镜导演",
  SceneNode: "场景",
  ShotBeatNode: "镜头 Beat",
  ReferenceAssetNode: "参考素材",
  GenerationBatchNode: "生成批次",
  ImageAssetNode: "图片资产",
  VideoAssetNode: "视频资产",
  VideoEditNode: "视频重编",
  VideoSegmentNode: "替换片段",
  ReviewNode: "人工审核",
  TimelineNode: "时间线",
  ImageGenerationNode: "图片生成",
  VideoGenerationNode: "视频生成",
  AudioGenerationNode: "音频生成",
  PromptArtifactNode: "Prompt 产物",
} as Record<string, string>)[props.node.type] ?? props.node.type));
const nodeLabel = computed(() => String(data.value.artifactLabel ?? ({
  BriefNode: "BRIEF",
  SubjectNode: "SUBJECT",
  StylePresetNode: "STYLE",
  CharacterDesignNode: "CHARACTER",
  StoryPlannerNode: "PLANNER",
  StoryCandidateNode: "STORY",
  ApprovalGateNode: "GATE",
  StoryboardDirectorNode: "STORYBOARD",
  SceneNode: "SCENE",
  ShotBeatNode: "BEAT",
  ReferenceAssetNode: "REFERENCE",
  GenerationBatchNode: "BATCH",
  ImageAssetNode: "IMAGE",
  VideoAssetNode: "VIDEO",
  VideoEditNode: "EDIT",
  VideoSegmentNode: "SEGMENT",
  ReviewNode: "REVIEW",
  TimelineNode: "TIMELINE",
  ImageGenerationNode: "IMAGE GEN",
  VideoGenerationNode: "VIDEO GEN",
  AudioGenerationNode: "AUDIO GEN",
  PromptArtifactNode: "PROMPT",
  RecipeGroupNode: "RECIPE",
} as Record<string, string>)[props.node.type] ?? "NODE"));
const boundAssets = computed(() => (
  Array.isArray(data.value.assets) ? data.value.assets : []
));
</script>

<template>
  <article
    class="canvas-card"
    :class="[`type-${node.type}`, `selection-${selectionState}`, { 'is-selected': selected }]"
    role="button"
    tabindex="0"
    :data-canvas-node-id="node.id"
    :aria-selected="selected"
    @click="emit('select-node', node)"
    @contextmenu.prevent.stop="emit('open-context-menu', node, $event)"
    @dblclick="emit('activate-node', node)"
    @keydown.enter.prevent="emit('select-node', node)"
  >
    <span
      v-if="selectionState === 'chosen' && selectionIndex > 0"
      class="selection-badge"
      :aria-label="`第 ${selectionIndex} 个已选参考`"
    >{{ selectionIndex }}</span>
    <header>
      <span class="node-type">{{ nodeLabel }}</span>
      <span v-if="data.status" class="node-status" :class="`status-${data.status}`">
        {{ data.status }}
      </span>
    </header>
    <h3>{{ title }}</h3>

    <template v-if="node.type === 'RecipeGroupNode'">
      <p>{{ data.theme || '输入一句话，生成固定儿童、固定猫咪与统一水彩画风的治愈短片。' }}</p>
      <div class="fact-row">
        <span>{{ data.targetDurationSeconds ?? 15 }} 秒</span>
        <span>{{ data.shotDurations?.length ?? '—' }} 镜</span>
        <span>{{ ({ quick: '快速', balanced: '平衡', premium: '精品' } as Record<string, string>)[data.qualityTier] ?? '平衡' }}</span>
        <span>720p · 9:16</span>
      </div>
      <ol class="recipe-progress" aria-label="四阶段人工审核进度">
        <li v-for="stage in data.reviewStages ?? []" :key="stage.key">
          {{ ({ story: '故事', anchors: '视觉锚点', video: '逐镜视频', sequence: '最终音画' } as Record<string, string>)[stage.key] }}：{{ stage.complete ? '已完成' : '待处理' }}
        </li>
      </ol>
      <p v-if="data.currentBlocker" class="recipe-blocker">当前阻塞：{{ data.currentBlocker }}</p>
      <small>成本：{{ data.estimatedCostMicros == null ? '提交前确认' : `¥${(data.estimatedCostMicros / 1_000_000).toFixed(3)}` }}</small>
      <div class="card-actions">
        <button class="primary-action" type="button" @click.stop="emit('open-recipe', node)">{{ data.primaryAction || '打开组合包' }}</button>
      </div>
    </template>

    <template v-else-if="node.type === 'BriefNode'">
      <p>{{ data.theme }}</p>
      <div class="fact-row">
        <span>{{ data.targetDurationSeconds }} 秒</span>
        <span>{{ data.aspectRatio }}</span>
        <span>{{ data.genre }}</span>
      </div>
    </template>

    <template v-else-if="node.type === 'SubjectNode'">
      <div v-if="subjectReferences.length" class="evidence-strip" aria-label="Canon 身份证据图">
        <figure v-for="reference in subjectReferences" :key="reference.assetId">
          <img :src="reference.thumbnailUrl || reference.contentUrl" :alt="reference.title || reference.semanticKey" />
          <figcaption>{{ reference.title || reference.semanticKey }}</figcaption>
        </figure>
      </div>
      <p>{{ data.identityAnchors?.join('；') }}</p>
      <div class="fact-row">
        <span>{{ data.kind }}</span>
        <span>{{ data.role }}</span>
        <span>Revision {{ data.revision }}</span>
      </div>
      <small v-if="subjectReferences.length">{{ subjectReferences.length }} 张已批准身份证据</small>
      <small
        v-if="subjectReferences.length && !subjectReferences.some((reference) => !reference.required)"
        class="evidence-missing"
      >表情、多角度或背面证据可补充（不会伪造占位素材）</small>
      <div class="card-actions">
        <button data-action="assist-subject" type="button" @click.stop="emit('assist-subject', node)">AI 分析并补全</button>
      </div>
    </template>

    <template v-else-if="node.type === 'StylePresetNode'">
      <div class="style-preset-body">
        <img
          v-if="data.references?.[0]?.thumbnailUrl || data.references?.[0]?.contentUrl"
          :src="data.references[0].thumbnailUrl || data.references[0].contentUrl"
          :alt="data.references[0].title || '线条材质画风'"
        />
        <div>
          <p>只提取线条、材质与光线，不复制叶片、露珠、绿色配色或微距构图。</p>
          <div class="fact-row"><span>Canon-v3</span><span>style 锁定</span><span>已批准</span></div>
        </div>
      </div>
    </template>

    <template v-else-if="node.type === 'CharacterDesignNode'">
      <div v-if="data.candidates?.length" class="character-node-candidates">
        <img
          v-for="candidate in data.candidates.slice(0, 4)"
          :key="candidate.assetId ?? candidate.id"
          :src="candidate.thumbnailUrl ?? candidate.contentUrl"
          :alt="candidate.title ?? title"
        />
      </div>
      <Picture v-else class="empty-character-icon" />
      <p>{{ ({ child: '儿童本集造型：服装、全身比例与关键姿态', cat: '猫咪本集造型：猫科结构、姿态与允许配件', pair_scale: '一人一猫同框比例与典型构图' } as Record<string, string>)[data.slot] ?? '本集造型与构图参考' }}</p>
      <div class="fact-row">
        <span>Canon identity 固定</span>
        <span>{{ data.candidates?.length ?? 0 }} 个候选</span>
      </div>
    </template>

    <template v-else-if="node.type === 'StoryCandidateNode'">
      <p>{{ data.logline }}</p>
      <div v-if="data.scorecard" class="score-row">
        <b>{{ data.scorecard.average }}</b>
        <span>综合评分</span>
        <span>主体必要性 {{ data.scorecard.subjectNecessity }}</span>
      </div>
      <div class="card-actions">
        <button
          v-if="data.candidatePromptId"
          type="button"
          @click.stop="emit('inspect-prompt', data.candidatePromptId)"
        >查看 Prompt · 策划</button>
        <button
          v-if="data.criticPromptId"
          type="button"
          @click.stop="emit('inspect-prompt', data.criticPromptId)"
        >查看 Prompt · 评审</button>
        <button
          v-if="data.status === 'candidate'"
          class="primary-action"
          type="button"
          @click.stop="node.objectId && emit('approve-story', node.objectId)"
        >批准定稿</button>
      </div>
    </template>

    <template v-else-if="node.type === 'StoryboardDirectorNode'">
      <div class="script-node-body">
        <Document class="script-node-icon" />
        <div class="script-node-steps" aria-label="脚本三步进度">
          <span v-for="(label, index) in ['确认镜头', '准备资产', '合成提示词']" :key="label"><b>{{ index + 1 }}</b>{{ label }}</span>
        </div>
        <button class="script-open" type="button" @click.stop="emit('activate-node', node)">打开脚本节点 →</button>
      </div>
    </template>

    <template v-else-if="node.type === 'ShotBeatNode'">
      <p>{{ data.action }}</p>
      <div class="fact-row">
        <span>{{ data.durationSeconds }} 秒</span>
        <span>Revision {{ data.revision }}</span>
        <span>{{ data.camera }}</span>
      </div>
      <div class="card-actions">
        <button
          v-if="data.promptId"
          type="button"
          @click.stop="emit('inspect-prompt', data.promptId)"
        >查看 Prompt · 分镜</button>
        <button type="button" @click.stop="emit('edit-object', node)">编辑 Beat</button>
      </div>
    </template>

    <template v-else-if="node.type === 'GenerationBatchNode'">
      <p>{{ data.prompt || '添加产品、模特或风格参考后生成图片候选。' }}</p>
      <div class="fact-row">
        <span>{{ data.candidateCount ?? 4 }} 个候选</span>
        <span>可调 1–8</span>
        <span>{{ data.status ?? node.status }}</span>
      </div>
      <div v-if="data.candidates?.length" class="candidate-grid">
        <button
          v-for="candidate in data.candidates"
          :key="candidate.id"
          type="button"
          :title="candidate.title || '提升到画布'"
          @click.stop="emit('promote-candidate', node, candidate)"
        ><img :src="candidate.thumbnailUrl" :alt="candidate.title || '图片候选'" /><small>提升到画布</small></button>
      </div>
      <div class="card-actions">
        <button data-action="open-composer" type="button" class="primary-action" @click.stop="emit('open-composer', node)">生成图片候选</button>
        <button v-if="data.promptId" type="button" @click.stop="emit('inspect-prompt', data.promptId)">查看 Prompt</button>
      </div>
    </template>

    <template v-else-if="node.type === 'ImageGenerationNode'">
      <div class="image-generator-body">
        <Picture class="image-generator-icon" />
        <div class="image-generator-tries">
          <span>尝试：</span>
          <button type="button" @click.stop="emit('open-composer', node)"><MagicStick />图生图</button>
          <button type="button" disabled title="当前版本尚未配置图片高清执行器">图片高清</button>
        </div>
      </div>
    </template>

    <template v-else-if="['VideoGenerationNode','AudioGenerationNode'].includes(node.type)">
      <p>{{ data.prompt || '添加 Prompt、主体或参考素材后，由能力配置决定实际供应商输入。' }}</p>
      <div class="fact-row">
        <span>{{ data.generationConfig?.mode ?? '待配置' }}</span>
        <span>{{ data.generationConfig?.resolution ?? '能力驱动' }}</span>
        <span>{{ data.status ?? node.status }}</span>
      </div>
      <div class="card-actions">
        <button data-action="open-composer" class="primary-action" type="button" @click.stop="emit('open-composer', node)">打开节点生成器</button>
        <button v-if="data.promptId" type="button" @click.stop="emit('inspect-prompt', data.promptId)">查看调用详情</button>
      </div>
    </template>

    <template v-else-if="node.type === 'PromptArtifactNode'">
      <p>{{ data.prompt || data.text || '连接到生成节点，作为可版本化、可审计的 Prompt 输入。' }}</p>
      <div v-if="data.promptId" class="card-actions"><button type="button" @click.stop="emit('inspect-prompt', data.promptId)">查看精确 Prompt</button></div>
    </template>

    <template v-else-if="['ReferenceAssetNode','ImageAssetNode'].includes(node.type)">
      <img v-if="data.thumbnailUrl || boundAssets[0]?.thumbnailUrl" class="asset-preview" :src="data.thumbnailUrl || boundAssets[0]?.thumbnailUrl" :alt="title" />
      <Picture v-else class="empty-asset-icon" />
      <p>{{ data.semanticRole || data.mediaType || '等待绑定素材' }}</p>
      <small v-if="boundAssets.length">已绑定 {{ boundAssets.length }} 个素材</small>
      <div class="card-actions">
        <button v-if="node.type === 'ReferenceAssetNode'" data-action="upload-reference" type="button" @click.stop="emit('upload-reference', node)">上传素材</button>
        <button v-if="node.type === 'ReferenceAssetNode'" data-action="select-history" type="button" @click.stop="emit('select-history', node)">素材历史</button>
        <button v-if="node.type === 'ReferenceAssetNode'" data-action="create-subject" type="button" @click.stop="emit('create-subject', node)">创建主体</button>
        <button v-if="data.promptId" type="button" @click.stop="emit('inspect-prompt', data.promptId)">查看 Prompt</button>
      </div>
    </template>

    <template v-else-if="node.type === 'VideoAssetNode'">
      <video v-if="data.contentUrl" class="asset-preview" :src="data.contentUrl" :poster="data.posterUrl" muted playsinline />
      <p>{{ data.durationMs ? `${(data.durationMs / 1000).toFixed(1)} 秒` : '可打开全屏局部重编器' }}</p>
      <div class="card-actions">
        <button class="primary-action" type="button" @click.stop="emit('edit-video', node)">局部重编</button>
        <button v-if="data.promptId" type="button" @click.stop="emit('inspect-prompt', data.promptId)">查看 Prompt</button>
      </div>
    </template>

    <template v-else-if="node.type === 'VideoEditNode'">
      <p>{{ data.instruction || '单区间、标注、主体参考与能力编译计划' }}</p>
      <div class="fact-row"><span>0.5–13 秒</span><span>Revision {{ data.revision ?? node.revision ?? 1 }}</span></div>
      <div v-if="data.promptId" class="card-actions"><button type="button" @click.stop="emit('inspect-prompt', data.promptId)">查看 Prompt</button></div>
    </template>

    <template v-else>
      <p v-if="data.synopsis">{{ data.synopsis }}</p>
      <p v-else-if="data.title">阶段节点</p>
    </template>
  </article>
</template>

<style scoped>
.canvas-card {
  position: relative;
  width: 268px;
  min-height: 112px;
  padding: 14px;
  color: #e9edf5;
  background: #181b21;
  border: 1px solid #343942;
  border-radius: 12px;
  box-shadow: 0 14px 34px rgb(0 0 0 / 28%);
}
.canvas-card:focus-visible { outline: 2px solid #8db9ee; outline-offset: 3px; }
.canvas-card.selection-incompatible { opacity: .42; }
.canvas-card.type-StoryCandidateNode { width: 312px; border-color: #3e5267; }
.canvas-card.type-CharacterDesignNode { width: 312px; min-height: 270px; border-color: #596b7d; background: #1d2025; }
.canvas-card.type-SubjectNode, .canvas-card.type-StylePresetNode { width: 332px; min-height: 246px; }
.canvas-card.type-StoryboardDirectorNode { width: 460px; min-height: 430px; border-color: #8a8a8a; background: #202020; }
.canvas-card.type-RecipeGroupNode { width: 360px; border-color: #6f8f7b; background: linear-gradient(145deg, #1b2421, #181b21 58%); }
.canvas-card.type-ApprovalGateNode { border-color: #78663e; }
.canvas-card.type-ShotBeatNode { border-color: #485167; }
.canvas-card.type-GenerationBatchNode { width: 332px; border-color: #4f5d73; }
.canvas-card.type-ImageGenerationNode { width: 790px; min-height: 430px; border-color: #8a8a8a; background: #242424; }
.canvas-card.type-ReferenceAssetNode, .canvas-card.type-ImageAssetNode { width: 500px; min-height: 330px; }
.canvas-card.type-VideoAssetNode { width: 410px; min-height: 300px; border-color: #5d526d; }
.canvas-card.type-VideoEditNode { border-color: #5d526d; }
.canvas-card.selection-compatible { border-color: #63c994; }
.canvas-card.selection-chosen { border-color: #7caef2; box-shadow: 0 0 0 2px rgb(92 150 218 / 32%); }
.canvas-card.is-selected { border-color: #8db9ee; box-shadow: 0 0 0 2px rgb(92 150 218 / 24%), 0 14px 34px rgb(0 0 0 / 28%); }
.selection-badge { position: absolute; top: -10px; right: -10px; z-index: 2; min-width: 24px; height: 24px; box-sizing: border-box; display: grid; place-items: center; padding: 0 6px; color: #0e1a27; background: #8fc2ff; border: 2px solid #17202b; border-radius: 999px; font-size: 11px; font-weight: 800; }
header, .fact-row, .score-row, .card-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
header { justify-content: space-between; }
.node-type { color: #79859a; font-size: 10px; font-weight: 800; letter-spacing: .14em; }
.node-status { padding: 2px 7px; color: #9aa6b8; background: #242932; border-radius: 999px; font-size: 10px; }
.status-approved, .status-ready { color: #7ee2ae; background: #153228; }
.status-candidate { color: #e7c775; background: #332c19; }
h3 { margin: 9px 0 7px; font-size: 15px; line-height: 1.4; }
p { margin: 0 0 10px; color: #aeb6c4; font-size: 12px; line-height: 1.55; }
small { display: block; margin-top: 8px; color: #737f92; }
.fact-row span { padding: 3px 7px; color: #9faabb; background: #23272e; border-radius: 6px; font-size: 10px; }
.score-row { margin: 10px 0; color: #9eabbc; font-size: 11px; }
.score-row b { color: #f2ce78; font-size: 22px; }
.evidence-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 8px 0 10px; }
.evidence-strip figure { min-width: 0; margin: 0; overflow: hidden; background: #11151a; border: 1px solid #343b46; border-radius: 8px; }
.evidence-strip img { display: block; width: 100%; height: 94px; object-fit: cover; }
.evidence-strip figcaption { padding: 5px 7px; overflow: hidden; color: #aeb8c7; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.evidence-missing { color: #d1a85c; }
.style-preset-body { display: grid; grid-template-columns: 116px 1fr; gap: 12px; align-items: stretch; margin-top: 8px; }
.style-preset-body > img { width: 116px; height: 148px; object-fit: cover; border: 1px solid #414a56; border-radius: 8px; }
.card-actions { margin-top: 10px; }
button {
  padding: 6px 9px;
  color: #b8c5d8;
  background: #242a33;
  border: 1px solid #3b4655;
  border-radius: 7px;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
button:hover { color: #fff; border-color: #6887ad; }
.primary-action { color: #16191e; background: #d7e4f5; border-color: #d7e4f5; font-weight: 700; }
.asset-preview { width: 100%; height: 245px; margin: 0 0 10px; object-fit: cover; background: #0b0d11; border-radius: 8px; }
.type-VideoAssetNode .asset-preview { height: 220px; }.empty-asset-icon { width: 82px; height: 82px; display: block; margin: 58px auto; color: #555; }.script-node-body { min-height: 350px; display: grid; grid-template-rows: 1fr auto auto; align-items: center; }.script-node-icon { width: 72px; height: 72px; justify-self: center; color: #5e5e5e; }.script-node-steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }.script-node-steps span { display: grid; justify-items: center; gap: 7px; color: #aaa; font-size: 11px; text-align: center; }.script-node-steps span:not(:last-child) { border-right: 1px solid #444; }.script-node-steps b { width: 30px; height: 30px; display: grid; place-items: center; color: #ddd; border: 2px solid #777; border-radius: 50%; }.script-open { min-height: 44px; margin-top: 28px; color: #f3f3f3; background: #3b3b3b; border-color: #4a4a4a; font-weight: 800; }.image-generator-body { min-height: 350px; display: grid; grid-template-rows: 1fr auto; align-items: center; padding: 10px 18px 6px; }.image-generator-icon { width: 86px; height: 86px; justify-self: center; color: #5c5c5c; }.image-generator-tries { display: grid; grid-template-columns: 100px 1fr; align-items: center; gap: 8px; color: #aaa; }.image-generator-tries span { grid-row: 1 / 3; align-self: start; padding-top: 8px; }.image-generator-tries button { min-height: 44px; justify-self: start; display: flex; align-items: center; gap: 8px; padding: 8px 12px; color: #eee; background: transparent; border-color: transparent; font-weight: 700; }.image-generator-tries button svg { width: 18px; }.image-generator-tries button:disabled { opacity: .42; cursor: not-allowed; }
.candidate-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }.candidate-grid button { padding: 0; overflow: hidden; display: grid; background: #111419; }.candidate-grid img { width: 100%; height: 96px; object-fit: cover; }.candidate-grid small { margin: 0; padding: 5px; text-align: center; }
.character-node-candidates { display: grid; grid-template-columns: repeat(2, 1fr); gap: 5px; margin: 4px 0 10px; }.character-node-candidates img { width: 100%; height: 88px; object-fit: cover; background: #111; border-radius: 7px; }.empty-character-icon { width: 64px; height: 64px; display: block; margin: 26px auto; color: #505760; }
.recipe-progress { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; margin: 10px 0; padding: 0; list-style: none; color: #aeb9ae; font-size: 10px; }.recipe-progress li { padding: 5px 7px; background: #202b27; border-radius: 6px; }.recipe-blocker { margin-top: 9px; color: #e0bd7d; }
</style>
