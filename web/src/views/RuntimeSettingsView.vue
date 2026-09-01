<script setup lang="ts">
import { onMounted, ref } from "vue";

import { creatorApi } from "../api/client";
import type { HealthDto, RuntimeSettingsDto } from "../api/types";

const health = ref<HealthDto>();
const settings = ref<RuntimeSettingsDto>();
const error = ref("");

async function load() {
  error.value = "";
  try {
    [health.value, settings.value] = await Promise.all([
      creatorApi.health(),
      creatorApi.runtimeSettings(),
    ]);
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
  }
}

onMounted(load);
</script>

<template>
  <main class="settings-page">
    <header><span>SYSTEM</span><h1>运行设置</h1><p>模型和密钥由部署配置管理；页面只展示真实生效值。</p></header>
    <section v-if="error" class="error" role="alert"><b>设置加载失败</b><p>{{ error }}</p><button type="button" @click="load">重试</button></section>
    <section v-else-if="!settings || !health" class="loading" aria-busy="true">正在读取 Creator 运行状态…</section>
    <template v-else>
      <section class="panel"><h2>Creator Core</h2><dl><dt>应用版本</dt><dd>{{ health.applicationVersion }}</dd><dt>数据库基线</dt><dd>{{ health.alembicRevision }}</dd><dt>API 能力</dt><dd>{{ health.apiFeatures.join(' · ') }}</dd></dl></section>
      <section class="panel"><h2>Provider 模型</h2><dl><dt>故事模型</dt><dd>{{ settings.planningModel }}</dd><dt>图片模型</dt><dd>{{ settings.imageModel }}</dd><dt>视频模型</dt><dd>{{ settings.videoModel }}</dd><dt>视频分辨率</dt><dd>{{ settings.videoResolution }}</dd></dl></section>
      <section class="panel"><h2>环境预检</h2><pre>{{ JSON.stringify(settings.preflight, null, 2) }}</pre></section>
    </template>
  </main>
</template>

<style scoped>
.settings-page{height:100%;box-sizing:border-box;padding:28px;overflow:auto;color:#dce6f1;background:#0d1218}.settings-page>header{max-width:1000px;margin:0 auto 20px}.settings-page>header span{color:#6285a3;font-size:9px;font-weight:800;letter-spacing:.16em}.settings-page h1{margin:6px 0}.settings-page header p{margin:0;color:#8291a2}.panel,.error,.loading{max-width:1000px;margin:0 auto 12px;padding:18px;background:#151c24;border:1px solid #2e3945;border-radius:12px}.panel h2{margin:0 0 14px}.panel dl{display:grid;grid-template-columns:180px minmax(0,1fr);gap:8px 14px}.panel dt{color:#7c8ea1}.panel dd{margin:0;overflow-wrap:anywhere}.panel pre{overflow:auto;color:#9fb0c1;white-space:pre-wrap}.error{color:#e0a7a1}.error button{min-height:44px;padding:0 14px;color:#fff;background:#714148;border:1px solid #93555e;border-radius:9px}.loading{display:grid;place-items:center;color:#8493a4}@media(max-width:600px){.settings-page{padding:16px}.panel dl{grid-template-columns:1fr}.panel dd{margin-bottom:8px}}
</style>
