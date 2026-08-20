<script setup lang="ts">
import { computed } from "vue";

import type { CanvasNodeDto } from "../../api/types";

const props = defineProps<{ node: CanvasNodeDto }>();
const emit = defineEmits<{
  "inspect-prompt": [promptId: string];
  "approve-story": [revisionId: string];
  "edit-object": [node: CanvasNodeDto];
  "generate-batch": [node: CanvasNodeDto];
  "promote-candidate": [node: CanvasNodeDto, candidate: Record<string, unknown>];
  "edit-video": [node: CanvasNodeDto];
  "assist-subject": [node: CanvasNodeDto];
  "open-composer": [node: CanvasNodeDto];
}>();

const data = computed(() => props.node.data);
const title = computed(() => String(data.value.title ?? ({
  BriefNode: "创意简报",
  SubjectNode: "主体",
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
const nodeLabel = computed(() => ({
  BriefNode: "BRIEF",
  SubjectNode: "SUBJECT",
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
} as Record<string, string>)[props.node.type] ?? "NODE");
</script>

<template>
  <article class="canvas-card" :class="`type-${node.type}`">
    <header>
      <span class="node-type">{{ nodeLabel }}</span>
      <span v-if="data.status" class="node-status" :class="`status-${data.status}`">
        {{ data.status }}
      </span>
    </header>
    <h3>{{ title }}</h3>

    <template v-if="node.type === 'BriefNode'">
      <p>{{ data.theme }}</p>
      <div class="fact-row">
        <span>{{ data.targetDurationSeconds }} 秒</span>
        <span>{{ data.aspectRatio }}</span>
        <span>{{ data.genre }}</span>
      </div>
    </template>

    <template v-else-if="node.type === 'SubjectNode'">
      <p>{{ data.identityAnchors?.join('；') }}</p>
      <div class="fact-row">
        <span>{{ data.kind }}</span>
        <span>{{ data.role }}</span>
        <span>Revision {{ data.revision }}</span>
      </div>
      <small v-if="data.references?.length">{{ data.references.length }} 张语义参考</small>
      <div class="card-actions">
        <button data-action="assist-subject" type="button" @click.stop="emit('assist-subject', node)">AI 分析并补全</button>
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
        <button data-action="open-composer" type="button" @click.stop="emit('open-composer', node)">节点生成器</button>
        <button type="button" class="primary-action" @click.stop="emit('generate-batch', node)">生成图片候选</button>
        <button v-if="data.promptId" type="button" @click.stop="emit('inspect-prompt', data.promptId)">查看 Prompt</button>
      </div>
    </template>

    <template v-else-if="['ImageGenerationNode','VideoGenerationNode','AudioGenerationNode'].includes(node.type)">
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
      <img v-if="data.thumbnailUrl" class="asset-preview" :src="data.thumbnailUrl" :alt="title" />
      <p>{{ data.semanticRole || data.mediaType || '等待绑定素材' }}</p>
      <div v-if="data.promptId" class="card-actions"><button type="button" @click.stop="emit('inspect-prompt', data.promptId)">查看 Prompt</button></div>
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
  width: 268px;
  min-height: 112px;
  padding: 14px;
  color: #e9edf5;
  background: #181b21;
  border: 1px solid #343942;
  border-radius: 12px;
  box-shadow: 0 14px 34px rgb(0 0 0 / 28%);
}
.canvas-card.type-StoryCandidateNode { width: 312px; border-color: #3e5267; }
.canvas-card.type-ApprovalGateNode { border-color: #78663e; }
.canvas-card.type-ShotBeatNode { border-color: #485167; }
.canvas-card.type-GenerationBatchNode { width: 332px; border-color: #4f5d73; }
.canvas-card.type-VideoAssetNode, .canvas-card.type-VideoEditNode { border-color: #5d526d; }
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
.asset-preview { width: 100%; height: 150px; margin: 0 0 10px; object-fit: cover; background: #0b0d11; border-radius: 8px; }
.candidate-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }.candidate-grid button { padding: 0; overflow: hidden; display: grid; background: #111419; }.candidate-grid img { width: 100%; height: 96px; object-fit: cover; }.candidate-grid small { margin: 0; padding: 5px; text-align: center; }
</style>
