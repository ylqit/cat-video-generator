<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";

const route = useRoute();
const active = computed(() => route.path);
</script>

<template>
  <el-container style="height: 100%">
    <el-aside width="200px" style="border-right: 1px solid #26282e">
      <div style="padding: 18px 16px; font-weight: 600">猫咪视频生产台</div>
      <el-menu :default-active="active" router background-color="transparent">
        <el-menu-item index="/runs">生产运行</el-menu-item>
        <el-menu-item index="/canon">Canon 资产</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main style="padding: 0; overflow-y: auto">
      <router-view />
    </el-main>
  </el-container>
</template>
