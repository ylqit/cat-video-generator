<script setup lang="ts">
import { useRouter } from "vue-router";

const router = useRouter();
</script>

<template>
  <main class="not-found">
    <span>404</span>
    <h1>页面不存在</h1>
    <p>旧工作台和历史兼容入口已经物理移除。</p>
    <button type="button" @click="router.replace('/projects')">返回项目</button>
  </main>
</template>

<style scoped>
.not-found{height:100%;display:grid;place-content:center;justify-items:center;gap:10px;color:#dce7f1;background:#0d1218}.not-found span{color:#5f86a7;font-size:12px;font-weight:800;letter-spacing:.2em}.not-found h1,.not-found p{margin:0}.not-found p{color:#8090a1}.not-found button{min-height:44px;margin-top:8px;padding:0 16px;color:#eef7ff;background:#285d86;border:1px solid #427ca8;border-radius:9px;cursor:pointer}
</style>
