<script setup lang="ts">
import { Film, Goods, Grid } from "@element-plus/icons-vue";
import { computed } from "vue";

import type { CanvasTemplateDto, CanvasTemplateKey } from "../../api/types";

const props = defineProps<{ templates: CanvasTemplateDto[]; loading?: boolean }>();
const emit = defineEmits<{ select: [key: CanvasTemplateKey] }>();

const iconByTemplate = { short_drama: Film, product_ad: Goods, blank: Grid };
const orderedTemplates = computed(() => ["short_drama", "product_ad", "blank"]
  .map((key) => props.templates.find((item) => item.key === key))
  .filter((item): item is CanvasTemplateDto => Boolean(item)));
</script>

<template>
  <section class="template-library" aria-labelledby="template-library-title">
    <header>
      <span>UNIVERSAL MEDIA CANVAS</span>
      <h1 id="template-library-title">AIGC 媒体工作台</h1>
      <p>选择生产结构，再把素材、生成候选、人工选择与视频重编留在同一条可追溯链路里。</p>
    </header>
    <div class="template-grid">
      <button
        v-for="template in orderedTemplates"
        :key="template.key"
        type="button"
        :data-template="template.key"
        :disabled="loading"
        @click="emit('select', template.key)"
      >
        <span class="template-icon"><component :is="iconByTemplate[template.key]" /></span>
        <strong>{{ template.title }}</strong>
        <small>{{ template.description }}</small>
        <b v-if="template.key === 'product_ad'">默认 4 个图片候选</b>
        <b v-else-if="template.key === 'short_drama'">2+ 主体 · 三案定稿</b>
        <b v-else>自由类型化节点</b>
        <i>创建项目 →</i>
      </button>
    </div>
  </section>
</template>

<style scoped>
.template-library { width: min(1160px, calc(100vw - 48px)); margin: 0 auto; padding: 72px 0; color: #edf1f7; }
header { max-width: 690px; margin-bottom: 36px; }
header span { color: #718098; font-size: 11px; font-weight: 800; letter-spacing: .18em; }
h1 { margin: 12px 0; font-size: clamp(34px, 5vw, 62px); line-height: 1.05; letter-spacing: -.04em; }
header p { color: #929dab; font-size: 16px; line-height: 1.75; }
.template-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
button { min-height: 300px; padding: 24px; display: flex; flex-direction: column; align-items: flex-start; color: #dfe5ee; text-align: left; background: #181c22; border: 1px solid #333a45; border-radius: 16px; box-shadow: 0 22px 60px rgb(0 0 0 / 25%); cursor: pointer; transition: .2s ease; }
button:hover { transform: translateY(-4px); background: #1c2129; border-color: #66758a; box-shadow: 0 28px 72px rgb(0 0 0 / 36%); }
button:disabled { cursor: wait; opacity: .55; }
.template-icon { width: 46px; height: 46px; padding: 11px; color: #b8c8da; background: #252b34; border: 1px solid #3a4350; border-radius: 12px; }
.template-icon :deep(svg) { width: 100%; height: 100%; }
strong { margin-top: 26px; font-size: 22px; }
small { min-height: 66px; margin-top: 10px; color: #8f9aaa; font-size: 13px; line-height: 1.7; }
b { margin-top: auto; padding: 6px 9px; color: #aeb9c8; background: #242a32; border-radius: 7px; font-size: 11px; }
i { margin-top: 18px; color: #dce5f2; font-size: 12px; font-style: normal; }
@media (max-width: 880px) { .template-grid { grid-template-columns: 1fr; } button { min-height: 230px; } }
</style>
