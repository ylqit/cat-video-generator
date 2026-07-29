<script setup lang="ts">
import { computed } from "vue";

import { assetContentUrl } from "../api/client";

const props = withDefaults(
  defineProps<{ assetId: string; label?: string; size?: number }>(),
  { label: "", size: 88 },
);

const url = computed(() => assetContentUrl(props.assetId));
</script>

<template>
  <div style="display: inline-block; text-align: center; margin: 0 6px 6px 0">
    <el-image
      :src="url"
      :preview-src-list="[url]"
      preview-teleported
      fit="cover"
      :style="{ width: `${size}px`, height: `${size}px`, borderRadius: '6px' }"
    />
    <div v-if="label" class="muted" style="margin-top: 2px">{{ label }}</div>
  </div>
</template>
