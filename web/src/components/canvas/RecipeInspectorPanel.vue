<script setup lang="ts">
import { computed, reactive, watch } from "vue";

import type {
  ProductionRecipeInstanceDto,
  QualityTier,
  RecipeAssetCandidateDto,
  RecipeSequenceCandidateDto,
  RecipeShotDto,
  RecipeStoryCandidateDto,
  SequenceTransitionDto,
  SequenceTransitionType,
} from "../../api/types";

type RecipeTransitionInput = {
  afterShotId: string;
  transition: SequenceTransitionDto;
};

const props = defineProps<{
  recipe: ProductionRecipeInstanceDto;
  busy?: boolean;
  expanded?: boolean;
  compact?: boolean;
}>();
const emit = defineEmits<{
  close: [];
  "toggle-expanded": [];
  save: [payload: { theme: string; targetDurationSeconds: number; qualityTier: QualityTier }];
  primary: [transitions: RecipeTransitionInput[]];
  "run-anchor": [shot: RecipeShotDto];
  "run-video": [shot: RecipeShotDto];
  "edit-shot": [shot: RecipeShotDto];
  "review-asset": [kind: "anchor_asset" | "video_asset", asset: RecipeAssetCandidateDto];
  "request-asset-changes": [kind: "anchor_asset" | "video_asset", asset: RecipeAssetCandidateDto];
  "edit-video": [asset: RecipeAssetCandidateDto];
  "review-sequence": [sequence: RecipeSequenceCandidateDto];
  "review-story": [story: RecipeStoryCandidateDto];
}>();

const form = reactive({
  theme: props.recipe.theme,
  targetDurationSeconds: props.recipe.targetDurationSeconds,
  qualityTier: props.recipe.qualityTier,
});
const transitionForm = reactive<Record<string, {
  type: SequenceTransitionType;
  durationMs: number;
}>>({});
watch(() => props.recipe, (recipe) => {
  Object.assign(form, {
    theme: recipe.theme,
    targetDurationSeconds: recipe.targetDurationSeconds,
    qualityTier: recipe.qualityTier,
  });
  const followingShotIds = new Set(
    recipe.shots.slice(1).flatMap((shot) => shot.shotId ? [shot.shotId] : []),
  );
  for (const shotId of Object.keys(transitionForm)) {
    if (!followingShotIds.has(shotId)) delete transitionForm[shotId];
  }
  for (const shotId of followingShotIds) {
    transitionForm[shotId] ??= { type: "cut", durationMs: 0 };
  }
}, { immediate: true });

const dirty = computed(() => (
  form.theme !== props.recipe.theme
  || form.targetDurationSeconds !== props.recipe.targetDurationSeconds
  || form.qualityTier !== props.recipe.qualityTier
));
const invalidatingChange = computed(() => (
  form.theme !== props.recipe.theme
  || form.targetDurationSeconds !== props.recipe.targetDurationSeconds
));
const stageLabels = {
  creative: "补全创意输入",
  story: "创意与故事",
  character_design: "角色设计",
  storyboard: "分镜生成",
  render: "视频渲染",
  export: "成品导出",
  anchors: "IP锁定与视觉锚点",
  video: "逐镜视频",
  sequence: "最终音画",
};
const sequenceTransitions = computed<RecipeTransitionInput[]>(() => (
  props.recipe.shots.slice(1).flatMap((shot) => {
    if (!shot.shotId) return [];
    const transition = transitionForm[shot.shotId];
    if (!transition || transition.type === "cut") return [];
    return [{ afterShotId: shot.shotId, transition: { ...transition } }];
  })
));
const currentStage = computed(() => {
  if (["concept", "storyboard"].includes(props.recipe.stage)) return "story";
  if (props.recipe.stage === "anchors") return "anchors";
  if (props.recipe.stage === "video") return "video";
  return "sequence";
});
const stageVisible = (stage: "story" | "anchors" | "video" | "sequence") => (
  !props.compact || currentStage.value === stage
);

function normalizeTransitionDuration(shotId: string) {
  const transition = transitionForm[shotId];
  if (!transition) return;
  transition.durationMs = transition.type === "cut" ? 0 : Math.max(300, transition.durationMs || 500);
}
</script>

<template>
  <aside class="recipe-inspector" :class="{ compact }" aria-label="一人一猫治愈短片阶段检查器">
    <header>
      <div><small>创作组合包 · Canon-v2</small><h2>一人一猫治愈短片</h2></div>
      <div class="header-actions">
        <button type="button" @click="emit('toggle-expanded')">{{ expanded ? '收起子节点' : '展开子节点' }}</button>
        <button v-if="!compact" type="button" aria-label="关闭阶段检查器" @click="emit('close')">×</button>
      </div>
    </header>

    <section v-if="stageVisible('story')" class="recipe-settings">
      <label>一句话主题<textarea v-model="form.theme" rows="3" /></label>
      <div class="settings-row">
        <label>总时长<input v-model.number="form.targetDurationSeconds" type="number" min="8" max="60" /> 秒</label>
        <label>质量档
          <select v-model="form.qualityTier">
            <option value="quick">快速</option><option value="balanced">平衡</option><option value="premium">精品</option>
          </select>
        </label>
      </div>
      <button v-if="dirty" type="button" :disabled="busy" @click="emit('save', { ...form })">{{ invalidatingChange ? '保存设置（下游将标记过期）' : '保存质量档' }}</button>
    </section>

    <ol class="stage-list" aria-label="审核阶段">
      <li v-for="stage in recipe.reviewStages" :key="stage.key" :class="{ complete: stage.complete }">
        <span>{{ stage.complete ? '已完成' : '待处理' }}</span><b>{{ stageLabels[stage.key] }}</b>
      </li>
    </ol>

    <section v-if="recipe.storyCandidates?.length && stageVisible('story')" class="story-candidates">
      <h3>原创故事候选</h3>
      <article v-for="story in recipe.storyCandidates" :key="story.id">
        <div class="shot-title"><b>{{ story.title }}</b><span v-if="story.scoreAverage !== null">评分 {{ story.scoreAverage }}</span></div>
        <p>{{ story.logline }}</p>
        <details><summary>查看完整梗概与评审理由</summary><p>{{ story.synopsis }}</p><p v-if="story.scoreRationale">{{ story.scoreRationale }}</p></details>
        <button v-if="story.status === 'candidate'" type="button" :disabled="busy || !story.episodeRules" @click="emit('review-story', story)">编辑本集规则并批准</button>
        <span v-else>该故事已批准并锁定规则</span>
      </article>
    </section>

    <section v-if="recipe.episodeRules && stageVisible('anchors')" class="rules-card">
      <h3>本集固定规则</h3>
      <p><b>服装</b>{{ recipe.episodeRules.personWardrobe }}</p>
      <p><b>时间天气</b>{{ recipe.episodeRules.timeWeather }}</p>
      <p><b>场景</b>{{ recipe.episodeRules.mainScene }} · {{ recipe.episodeRules.environment === 'indoor' ? '室内水彩' : '户外水彩' }}</p>
      <p><b>猫咪</b>{{ recipe.episodeRules.catBehaviorMode === 'natural' ? '自然四足猫' : '轻拟人猫' }}</p>
      <p><b>声音</b>原生环境声与动作声；无对白</p>
    </section>

    <section v-if="recipe.shots.length && (stageVisible('anchors') || stageVisible('video'))" class="shot-list">
      <h3>逐镜制作</h3>
      <article v-for="(shot, index) in recipe.shots" :key="shot.beatId">
        <div class="shot-title"><b>镜头 {{ index + 1 }} · {{ shot.title }}</b><span>{{ shot.durationSeconds }} 秒</span></div>
        <ol class="beats"><li v-for="beat in shot.temporalBeats" :key="String(beat.phase)">{{ beat.startSecond }}–{{ beat.endSecond }}秒 · {{ beat.childAction }}</li></ol>
        <button type="button" :disabled="busy" @click="emit('edit-shot', shot)">编辑动作、构图、运镜与时长</button>
        <button v-if="shot.shotId && !shot.selectedAnchorAssetId" type="button" :disabled="busy" @click="emit('run-anchor', shot)">{{ shot.anchorCandidates.length ? '按原因重做视觉锚点' : '生成视觉锚点' }}</button>
        <div v-if="shot.anchorCandidates.length" class="candidates">
          <article v-for="asset in shot.anchorCandidates" :key="asset.id" class="candidate">
            <img :src="asset.contentUrl" alt="视觉锚点候选" />
            <span>{{ asset.status === 'approved' ? '已批准' : asset.diagnosticStatus === 'passed' ? '专项诊断通过 · 待人工审核' : '诊断缺失或有问题 · 需覆盖/退回' }}</span>
            <div v-if="asset.status !== 'approved'" class="candidate-actions">
              <button type="button" :disabled="busy" @click="emit('review-asset', 'anchor_asset', asset)">批准</button>
              <button type="button" :disabled="busy" @click="emit('request-asset-changes', 'anchor_asset', asset)">退回</button>
            </div>
          </article>
        </div>
        <button v-if="shot.shotId && shot.selectedAnchorAssetId && !shot.selectedVideoAssetId" type="button" :disabled="busy" @click="emit('run-video', shot)">{{ shot.videoCandidates.length ? '按原因重做整镜视频' : '生成视频' }}</button>
        <div v-if="shot.videoCandidates.length" class="candidates video-candidates">
          <article v-for="asset in shot.videoCandidates" :key="asset.id" class="candidate">
            <video :src="asset.contentUrl" controls playsinline preload="metadata" />
            <span>{{ asset.status === 'approved' ? '已批准' : asset.diagnosticStatus === 'passed' ? '专项诊断通过' : '需人工覆盖或修改' }}</span>
            <div v-if="asset.status !== 'approved'" class="candidate-actions">
              <button type="button" :disabled="busy" @click="emit('review-asset', 'video_asset', asset)">批准/覆盖</button>
              <button type="button" :disabled="busy" @click="emit('request-asset-changes', 'video_asset', asset)">退回</button>
              <button type="button" :disabled="busy" @click="emit('edit-video', asset)">0.5–13秒局部重编</button>
            </div>
          </article>
        </div>
      </article>
    </section>

    <section v-if="recipe.sequenceCandidate && stageVisible('sequence')" class="final-card">
      <h3>最终音画 · 版本 {{ recipe.sequenceCandidate.revision }}</h3>
      <video
        v-if="recipe.sequenceCandidate.contentUrl"
        class="final-preview"
        :src="recipe.sequenceCandidate.contentUrl"
        controls
        playsinline
        preload="metadata"
      />
      <p v-else role="alert">最终资产尚未完成渲染，暂时不能审核。</p>
      <p>{{ Math.round(recipe.sequenceCandidate.durationMs / 100) / 10 }} 秒 · 保留原生音轨并按转场拼接</p>
      <button
        v-if="recipe.sequenceCandidate.status === 'content_review' && recipe.sequenceCandidate.contentUrl"
        class="final-review"
        type="button"
        :disabled="busy"
        @click="emit('review-sequence', recipe.sequenceCandidate)"
      >批准最终成片</button>
      <a
        v-if="recipe.sequenceCandidate.status === 'approved' && recipe.sequenceCandidate.contentUrl"
        class="final-export"
        :href="recipe.sequenceCandidate.contentUrl"
        download
      >导出最终成片</a>
    </section>

    <section v-else-if="recipe.stage === 'sequence' && recipe.shots.length > 1 && stageVisible('sequence')" class="transition-card">
      <h3>镜头衔接</h3>
      <p>默认直接切换；只在空间与动作连续时使用短叠化或淡黑。</p>
      <label v-for="(shot, index) in recipe.shots.slice(1)" :key="shot.beatId">
        进入镜头 {{ index + 2 }}
        <span v-if="shot.shotId" class="transition-row">
          <select v-model="transitionForm[shot.shotId].type" class="transition-type" @change="normalizeTransitionDuration(shot.shotId)">
            <option value="cut">直接切换</option>
            <option value="cross_dissolve">叠化</option>
            <option value="fade_black">淡黑</option>
          </select>
          <input
            v-if="transitionForm[shot.shotId].type !== 'cut'"
            v-model.number="transitionForm[shot.shotId].durationMs"
            class="transition-duration"
            type="number"
            min="300"
            max="1000"
            step="100"
            aria-label="转场毫秒数"
          />
        </span>
      </label>
    </section>

    <div class="blocker" role="status">{{ recipe.currentBlocker || '全部阶段已批准，可以导出。' }}</div>
    <button class="primary" type="button" :disabled="busy" @click="emit('primary', sequenceTransitions)">{{ busy ? '处理中…' : recipe.primaryAction }}</button>

    <details><summary>高级信息</summary><pre>{{ JSON.stringify({ recipeKey: recipe.recipeKey, recipeVersion: recipe.recipeVersion, canonProfileId: recipe.canonProfileId, revision: recipe.revision }, null, 2) }}</pre></details>
  </aside>
</template>

<style scoped>
.recipe-inspector { width: min(620px, calc(100vw - 28px)); max-height: min(820px, calc(100vh - 32px)); overflow: auto; padding: 18px; color: #e9eee9; background: #171d1b; border: 1px solid #40564a; border-radius: 15px; box-shadow: 0 26px 80px rgb(0 0 0 / 55%); }
.recipe-inspector.compact { box-sizing: border-box; width: 100%; max-height: none; min-height: 100%; padding: 14px 22px 22px; background: #171a20; border: 0; border-radius: 0; box-shadow: none; }.recipe-inspector.compact header { position: static; top: auto; background: transparent; }.recipe-inspector.compact .stage-list { grid-template-columns: repeat(4, minmax(140px, 1fr)); }.recipe-inspector.compact .shot-list > article { grid-template-columns: minmax(220px, .8fr) minmax(320px, 1.2fr); align-items: start; }.recipe-inspector.compact .candidates { grid-template-columns: repeat(4, minmax(150px, 1fr)); }
header, .header-actions, .settings-row, .shot-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }header { position: sticky; top: -18px; z-index: 2; padding: 14px 0; background: #171d1b; }h2, h3, p { margin: 0; }h2 { margin-top: 3px; font-size: 19px; }header small { color: #89a093; }button, input, select, textarea { font: inherit; }button { padding: 8px 10px; color: #dbe7df; background: #25312b; border: 1px solid #496052; border-radius: 8px; cursor: pointer; }button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid #9bc5aa; outline-offset: 2px; }button:disabled { opacity: .48; cursor: not-allowed; }
.recipe-settings, .story-candidates article, .rules-card, .shot-list article, .final-card, .transition-card { margin-top: 12px; padding: 13px; background: #1e2723; border: 1px solid #35463d; border-radius: 11px; }label { display: grid; gap: 6px; color: #aebdb3; font-size: 12px; }.settings-row { margin-top: 10px; justify-content: flex-start; }.settings-row label { display: flex; align-items: center; }textarea, input, select { box-sizing: border-box; padding: 8px; color: #edf3ef; background: #121714; border: 1px solid #425249; border-radius: 7px; }textarea { width: 100%; resize: vertical; }.recipe-settings > button { margin-top: 10px; }
.stage-list { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; padding: 0; list-style: none; }.stage-list li { display: grid; gap: 3px; padding: 9px; color: #c3aa77; background: #2c281e; border-radius: 8px; }.stage-list li.complete { color: #8fc9a2; background: #1d3026; }.stage-list span { font-size: 10px; }.stage-list b { font-size: 12px; }
.story-candidates { margin-top: 14px; }.story-candidates > h3 { margin-bottom: 8px; }.story-candidates article, .rules-card { display: grid; gap: 7px; }.story-candidates p, .rules-card p { color: #afbbb3; font-size: 12px; line-height: 1.5; }.rules-card p b { display: inline-block; width: 72px; color: #dce7df; }.shot-list { margin-top: 14px; }.shot-list > h3 { margin-bottom: 8px; }.shot-list > article, .final-card, .transition-card { display: grid; gap: 9px; }.shot-title span { color: #8ea094; font-size: 11px; }.beats { margin: 0; padding-left: 18px; color: #93a198; font-size: 11px; line-height: 1.55; }.candidates { display: grid; grid-template-columns: repeat(2, 1fr); gap: 7px; }.candidate { overflow: hidden; background: #18201c; border: 1px solid #34463c; border-radius: 8px; }.candidates img, .candidates video { display: block; width: 100%; height: 120px; object-fit: cover; background: #0b0e0c; }.candidates span { display: block; padding: 7px; font-size: 10px; }.candidate-actions { display: flex; flex-wrap: wrap; gap: 5px; padding: 0 7px 7px; }.candidate-actions button { flex: 1 1 auto; padding: 6px; font-size: 10px; }.transition-card p, .final-card p { color: #afbbb3; font-size: 12px; }.transition-row { display: flex; gap: 7px; }.transition-duration { width: 110px; }.final-preview { width: 100%; max-height: 420px; background: #090c0a; border-radius: 8px; }.final-export { padding: 9px; color: #142019; background: #cce4d3; border-radius: 8px; text-align: center; text-decoration: none; font-weight: 800; }.final-export:focus-visible { outline: 2px solid #9bc5aa; outline-offset: 2px; }.blocker { margin: 14px 0 8px; padding: 10px; color: #e2c58a; background: #30291b; border-radius: 8px; }.primary { width: 100%; color: #142019; background: #cce4d3; border-color: #cce4d3; font-weight: 800; }details { margin-top: 12px; color: #7f9185; font-size: 11px; }pre { white-space: pre-wrap; }
@media (max-width: 620px) { .stage-list, .candidates { grid-template-columns: 1fr; }.recipe-inspector { padding: 13px; }header { top: -13px; } }
@media (max-width: 980px) { .recipe-inspector.compact .stage-list,.recipe-inspector.compact .candidates { grid-template-columns: repeat(2, minmax(140px, 1fr)); }.recipe-inspector.compact .shot-list > article { grid-template-columns: 1fr; } }
</style>
