<script setup lang="ts">
import { computed, ref } from "vue";

import type { CanvasNodeType } from "../../api/types";

defineEmits<{
  "create-node": [type: CanvasNodeType];
  "open-asset-history": [];
}>();

type LibraryItem = { type?: CanvasNodeType; label: string; description: string; action?: "history" };
type LibraryCategory = { key: string; label: string; items: LibraryItem[] };

const categories: LibraryCategory[] = [
  { key: "text", label: "文本", items: [
    { type: "BriefNode", label: "创意简报", description: "主题、受众、基调与时长" },
    { type: "StoryPlannerNode", label: "故事策划", description: "三案生成与改写" },
    { type: "StoryCandidateNode", label: "故事候选", description: "版本、评分与人工选择" },
    { type: "StoryCriticNode", label: "故事评审", description: "结构化评分与风险" },
    { type: "ApprovalGateNode", label: "人工审批", description: "冻结可追溯版本" },
    { type: "PromptArtifactNode", label: "Prompt", description: "可连接、可审计的精确指令" },
  ] },
  { key: "image", label: "图片", items: [
    { type: "GenerationBatchNode", label: "图片候选批次", description: "批量生成 1–8 个候选" },
    { type: "ImageGenerationNode", label: "图片生成", description: "文生图与参考图生成" },
    { type: "ImageAssetNode", label: "图片资产", description: "提升候选并创建独立分支" },
  ] },
  { key: "video", label: "视频", items: [
    { type: "VideoGenerationNode", label: "视频生成", description: "文生、图生与首尾帧" },
    { type: "VideoAssetNode", label: "视频资产", description: "版本预览、下载与局部重拍" },
  ] },
  { key: "smart", label: "智能编辑", items: [
    { type: "VideoEditNode", label: "视频局部重编", description: "单区间、标注与主体参考" },
    { type: "ReviewNode", label: "媒体审核", description: "批准或拒绝生成版本" },
  ] },
  { key: "director", label: "导演台", items: [
    { type: "StoryboardDirectorNode", label: "分镜导演", description: "场景与 Beat 编译" },
    { type: "SceneNode", label: "场景", description: "主体绑定与时长预算" },
    { type: "ShotBeatNode", label: "镜头 Beat", description: "动作、机位、对白与状态" },
    { type: "TimelineNode", label: "时间线", description: "只接收已批准资产" },
  ] },
  { key: "filmstrip", label: "逐帧拉片", items: [
    { type: "VideoSegmentNode", label: "视频片段", description: "真实帧带与单区间分析" },
  ] },
  { key: "audio", label: "音频", items: [
    { type: "AudioGenerationNode", label: "音频生成", description: "能力未配置时明确说明原因" },
  ] },
  { key: "script", label: "脚本", items: [
    { type: "SubjectNode", label: "通用主体", description: "人物、动物、产品、道具与风格" },
    { type: "StylePresetNode", label: "画风预设", description: "显式画风证据、职责与版本" },
  ] },
  { key: "assets", label: "素材库", items: [
    { type: "ReferenceAssetNode", label: "上传素材", description: "创建语义明确的参考资产节点" },
    { label: "从素材历史选择", description: "图片、视频和音频历史", action: "history" },
  ] },
];

const selectedKey = ref(categories[0].key);
const selectedCategory = computed(() => categories.find((item) => item.key === selectedKey.value) ?? categories[0]);
</script>

<template>
  <aside class="node-library" aria-label="添加画布节点">
    <header><b>添加节点</b><small>选择分类后，再选择具体节点</small></header>
    <div class="library-body">
      <nav aria-label="节点分类">
        <button
          v-for="category in categories"
          :key="category.key"
          type="button"
          :data-category="category.key"
          :aria-current="selectedKey === category.key ? 'page' : undefined"
          :class="{ active: selectedKey === category.key }"
          @click="selectedKey = category.key"
        >{{ category.label }}</button>
      </nav>
      <section>
        <h3>{{ selectedCategory.label }}</h3>
        <button
          v-for="item in selectedCategory.items"
          :key="item.label"
          class="leaf"
          type="button"
          @click="item.action === 'history' ? $emit('open-asset-history') : $emit('create-node', item.type!)"
        >
          <b>{{ item.label }}</b><small>{{ item.description }}</small>
        </button>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.node-library { width: min(620px, calc(100vw - 36px)); max-height: min(650px, calc(100vh - 120px)); overflow: hidden; padding: 14px; color: #e9edf4; background: #191c22; border: 1px solid #353b46; border-radius: 14px; box-shadow: 0 24px 70px rgb(0 0 0 / 48%); }
header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding: 2px 4px 12px; border-bottom: 1px solid #2c313a; }header small { color: #7f8a9c; }
.library-body { display: grid; grid-template-columns: 150px 1fr; min-height: 370px; }nav { display: grid; align-content: start; gap: 3px; padding: 12px 10px 12px 0; border-right: 1px solid #2c313a; }nav button { padding: 9px 11px; text-align: left; color: #c8d0db; background: transparent; border: 0; border-radius: 8px; cursor: pointer; }nav button:hover, nav button:focus-visible, nav button.active { color: #fff; background: #30353e; outline: none; }
section { max-height: 540px; overflow: auto; padding: 10px 0 10px 12px; }h3 { margin: 0 0 8px; color: #8792a3; font-size: 11px; letter-spacing: .12em; }.leaf { display: grid; width: 100%; gap: 4px; margin-bottom: 6px; padding: 10px; text-align: left; color: #dce3ed; background: #22262e; border: 1px solid #343b47; border-radius: 9px; cursor: pointer; }.leaf:hover, .leaf:focus-visible { border-color: #6f91bc; outline: none; }.leaf small { color: #7e8999; line-height: 1.35; }
@media (max-width: 620px) { .library-body { grid-template-columns: 116px 1fr; }.node-library { padding: 10px; }header small { display: none; } }
</style>
