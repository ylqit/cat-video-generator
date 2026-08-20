<script setup lang="ts">
import type { CanvasNodeType } from "../../api/types";

defineEmits<{
  "create-node": [type: CanvasNodeType];
  "open-asset-history": [];
}>();

const groups: Array<{
  title: string;
  items: Array<{ type?: CanvasNodeType; label: string; description: string; action?: "history" }>;
}> = [
  {
    title: "创作流程",
    items: [
      { type: "BriefNode", label: "创意简报", description: "主题、受众、基调与时长" },
      { type: "SubjectNode", label: "通用主体", description: "人物、动物、产品、道具与风格" },
      { type: "StoryPlannerNode", label: "故事策划", description: "三案生成与改写" },
      { type: "StoryCandidateNode", label: "故事候选", description: "版本、评分与人工选择" },
      { type: "StoryCriticNode", label: "故事评审", description: "结构化评分与风险" },
      { type: "ApprovalGateNode", label: "人工审批", description: "冻结可追溯版本" },
      { type: "StoryboardDirectorNode", label: "分镜导演", description: "场景与 Beat 编译" },
      { type: "SceneNode", label: "场景", description: "主体绑定与时长预算" },
      { type: "ShotBeatNode", label: "镜头 Beat", description: "动作、机位、对白与状态" },
      { type: "ReviewNode", label: "审核", description: "批准或拒绝媒体版本" },
      { type: "TimelineNode", label: "时间线", description: "只接收已批准资产" },
    ],
  },
  {
    title: "媒体工具",
    items: [
      { type: "GenerationBatchNode", label: "图片候选批次", description: "批量生成 1–8 个候选" },
      { type: "ImageGenerationNode", label: "图片生成", description: "文生图与参考图生成" },
      { type: "ImageAssetNode", label: "图片资产", description: "可提升的独立候选" },
      { type: "VideoGenerationNode", label: "视频生成", description: "文生、图生与首尾帧" },
      { type: "VideoAssetNode", label: "视频资产", description: "版本预览与局部重编" },
      { type: "VideoEditNode", label: "视频重编", description: "单区间标注与参考素材" },
      { type: "AudioGenerationNode", label: "音频", description: "对白、音效与配乐" },
      { type: "PromptArtifactNode", label: "Prompt", description: "可连接、可审计的精确指令" },
    ],
  },
  {
    title: "资源",
    items: [
      { type: "ReferenceAssetNode", label: "上传 / 引用素材", description: "绑定语义明确的参考资产" },
      { label: "素材历史", description: "图片、视频和音频历史", action: "history" },
    ],
  },
];
</script>

<template>
  <aside class="node-library" aria-label="添加画布节点">
    <header><b>添加节点</b><small>业务节点与资源动作分开管理</small></header>
    <section v-for="group in groups" :key="group.title">
      <h3>{{ group.title }}</h3>
      <div class="node-grid">
        <button
          v-for="item in group.items"
          :key="item.label"
          type="button"
          @click="item.action === 'history' ? $emit('open-asset-history') : $emit('create-node', item.type!)"
        >
          <b>{{ item.label }}</b>
          <small>{{ item.description }}</small>
        </button>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.node-library { width: min(620px, calc(100vw - 36px)); max-height: min(720px, calc(100vh - 120px)); overflow: auto; padding: 16px; color: #e9edf4; background: #191c22; border: 1px solid #353b46; border-radius: 14px; box-shadow: 0 24px 70px rgb(0 0 0 / 48%); }
header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding-bottom: 12px; border-bottom: 1px solid #2c313a; } header small { color: #7f8a9c; }
h3 { margin: 16px 0 8px; color: #939eb0; font-size: 11px; letter-spacing: .12em; }
.node-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
button { min-height: 66px; padding: 10px; text-align: left; color: #dce3ed; background: #22262e; border: 1px solid #343b47; border-radius: 9px; cursor: pointer; } button:hover, button:focus-visible { border-color: #6f91bc; outline: none; } button b, button small { display: block; } button small { margin-top: 5px; color: #7e8999; line-height: 1.35; }
@media (max-width: 620px) { .node-grid { grid-template-columns: 1fr; } }
</style>
