<script setup lang="ts">
import { Close, FullScreen } from "@element-plus/icons-vue";

import type { CanvasConsolePresetKey } from "./canvasPanels";

defineProps<{
  title: string;
  preset: CanvasConsolePresetKey;
  fullscreenAvailable?: boolean;
}>();
const emit = defineEmits<{ fullscreen: []; close: [] }>();
</script>

<template>
  <section class="local-console" :class="`preset-${preset}`" role="region" :aria-label="`${title}局部控制台`">
    <button
      class="close-button"
      type="button"
      aria-label="关闭局部控制台"
      @click="emit('close')"
    ><Close /></button>
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
.local-console { --console-width: 660px; --console-height: 280px; position: fixed; z-index: 1100; contain: layout paint; box-sizing: border-box; width: min(var(--console-width), calc(100vw - 32px)); height: min(var(--console-height), calc(100vh - 96px)); overflow: hidden; color: #f2f2f2; background: #242424; border: 1px solid #424242; border-radius: 16px; box-shadow: 0 18px 48px rgb(0 0 0 / 42%); will-change: transform; animation: console-in 140ms ease-out; }
.preset-text { --console-width: 660px; --console-height: 320px; }.preset-image { --console-width: 760px; --console-height: 440px; }.preset-video { --console-width: 860px; --console-height: 480px; }.preset-storyboard { --console-width: 760px; --console-height: 420px; }
.close-button,.fullscreen-button { position: absolute; top: 12px; z-index: 3; width: 44px; height: 44px; display: grid; place-items: center; padding: 12px; color: #aaa; background: rgb(36 36 36 / 90%); border: 1px solid transparent; border-radius: 10px; cursor: pointer; }.close-button { right: 12px; }.fullscreen-button { right: 60px; }.close-button:hover,.close-button:focus-visible,.fullscreen-button:hover,.fullscreen-button:focus-visible { color: #fff; background: #363636; border-color: #555; outline: 2px solid #7eafff; outline-offset: -2px; }
.console-body { width: 100%; height: 100%; min-height: 0; overflow: hidden; scrollbar-color: #777 #242424; }
@keyframes console-in { from { opacity: 0; } }
@media (max-width: 1280px) { .local-console { width: min(var(--console-width), calc(100vw - 24px)); height: min(var(--console-height), calc(100vh - 96px)); } }
@media (prefers-reduced-motion: reduce) { .local-console { animation: none; } }
</style>
