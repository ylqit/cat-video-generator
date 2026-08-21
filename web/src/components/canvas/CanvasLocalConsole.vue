<script setup lang="ts">
import { FullScreen } from "@element-plus/icons-vue";

import type { CanvasConsolePresetKey } from "./canvasPanels";

defineProps<{
  title: string;
  preset: CanvasConsolePresetKey;
  fullscreenAvailable?: boolean;
}>();
const emit = defineEmits<{ fullscreen: [] }>();
</script>

<template>
  <section class="local-console" :class="`preset-${preset}`" role="region" :aria-label="`${title}局部控制台`">
    <button
      v-if="fullscreenAvailable"
      class="fullscreen-button"
      type="button"
      aria-label="全屏打开"
      @click="emit('fullscreen')"
    ><FullScreen /></button>
    <div class="console-body"><slot /></div>
  </section>
</template>

<style scoped>
.local-console { --console-width: 720px; --console-height: 320px; position: fixed; z-index: 1100; box-sizing: border-box; width: min(var(--console-width), calc(100vw - 32px)); height: min(var(--console-height), calc(100vh - 96px)); overflow: hidden; color: #f2f2f2; background: #252525; border: 1px solid #414141; border-radius: 16px; box-shadow: 0 24px 68px rgb(0 0 0 / 52%); animation: console-in 150ms ease-out; }
.preset-text { --console-width: 760px; --console-height: 360px; }.preset-image { --console-width: 920px; --console-height: 560px; }.preset-video { --console-width: 1040px; --console-height: 560px; }.preset-storyboard { --console-width: 1120px; --console-height: 620px; }
.fullscreen-button { position: absolute; right: 12px; top: 12px; z-index: 3; width: 44px; height: 44px; display: grid; place-items: center; padding: 12px; color: #aaa; background: rgb(37 37 37 / 88%); border: 1px solid transparent; border-radius: 10px; cursor: pointer; }.fullscreen-button:hover,.fullscreen-button:focus-visible { color: #fff; background: #363636; border-color: #555; outline: 2px solid #7eafff; outline-offset: -2px; }
.console-body { width: 100%; height: 100%; overflow: auto; scrollbar-color: #777 #242424; }
@keyframes console-in { from { opacity: 0; transform: translateY(-5px) scale(.99); } }
@media (max-width: 1280px) { .local-console { width: min(var(--console-width), calc(100vw - 24px)); height: min(var(--console-height), calc(100vh - 96px)); } }
@media (prefers-reduced-motion: reduce) { .local-console { animation: none; } }
</style>
