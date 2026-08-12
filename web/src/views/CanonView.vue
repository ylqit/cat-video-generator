<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api, assetContentUrl } from "../api/client";
import type { AssetDto } from "../api/types";

const assets = ref<AssetDto[]>([]);
onMounted(async () => { assets.value = await api.canon(); });
</script>

<template>
  <div class="page">
    <h1>Canon 资产</h1>
    <p>这里只展示当前批准的人物、猫咪和定稿画风。项目参考素材在镜头工作台上传。</p>
    <div class="grid">
      <article v-for="asset in assets" :key="asset.id">
        <img v-if="asset.mediaType === 'image'" :src="assetContentUrl(asset.id)" />
        <b>{{ asset.semanticKey || asset.role }}</b><span>{{ asset.sha256.slice(0, 12) }}</span>
      </article>
    </div>
  </div>
</template>

<style scoped>.page { padding: 28px; color: #e8ebf2; }.page p { color: #929aaa; }.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 14px; }.grid article { background: #161b23; border: 1px solid #2b323f; border-radius: 10px; padding: 10px; display: grid; gap: 6px; }.grid img { width: 100%; aspect-ratio: 1; object-fit: contain; background: #0a0d12; }.grid span { color: #818a9a; font-size: 11px; }</style>
