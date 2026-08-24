<script setup lang="ts">
import { computed, ref } from "vue";

import type { VisualPresetKey, VisualPresetProfileDto } from "../../api/types";

const props = defineProps<{
  modelValue: boolean;
  presets: VisualPresetProfileDto[];
  loading?: boolean;
  applying?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  apply: [presetKey: VisualPresetKey];
}>();

type LibraryTab = "roles" | "styles" | "recent";
const activeTab = ref<LibraryTab>("roles");
const query = ref("");

const filteredPresets = computed(() => {
  const normalized = query.value.trim().toLocaleLowerCase();
  if (!normalized) return props.presets;
  return props.presets.filter((preset) => (
    `${preset.title} ${preset.description} ${preset.slots.map((slot) => `${slot.title} ${slot.semanticKey}`).join(" ")}`
      .toLocaleLowerCase()
      .includes(normalized)
  ));
});

function visibleSlots(preset: VisualPresetProfileDto) {
  if (activeTab.value === "roles") return preset.slots.filter((slot) => slot.role !== "style");
  if (activeTab.value === "styles") return preset.slots.filter((slot) => slot.role === "style");
  return preset.slots;
}

function slotGroups(preset: VisualPresetProfileDto) {
  if (activeTab.value === "roles") {
    return [
      { key: "person", title: "固定儿童证据包", supplement: "可补充：表情、多角度、背面", slots: preset.slots.filter((slot) => slot.role === "person") },
      { key: "cat", title: "固定猫咪证据包", supplement: "可补充：表情、多角度、背面", slots: preset.slots.filter((slot) => slot.role === "cat") },
    ].filter((group) => group.slots.length);
  }
  if (activeTab.value === "styles") {
    return [{ key: "style", title: "统一画风证据", supplement: "只提取线条、材质与光线", slots: preset.slots.filter((slot) => slot.role === "style") }];
  }
  return [{ key: "recent", title: "最近应用的完整证据包", supplement: "复用原始资产与审核状态", slots: preset.slots }];
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    width="min(1040px, calc(100vw - 32px))"
    class="visual-preset-library"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="library-heading">
        <div><b>素材与预设库</b><small>复用真实 Canon 资产；应用只创建版本化引用和画布血缘。</small></div>
        <label><span class="sr-only">搜索素材与预设</span><input v-model="query" type="search" placeholder="搜索角色、风格或语义键" /></label>
      </div>
    </template>

    <nav class="library-tabs" aria-label="素材库分类">
      <button type="button" :class="{ active: activeTab === 'roles' }" @click="activeTab = 'roles'">角色库</button>
      <button type="button" :class="{ active: activeTab === 'styles' }" @click="activeTab = 'styles'">风格库</button>
      <button type="button" :class="{ active: activeTab === 'recent' }" @click="activeTab = 'recent'">最近使用</button>
    </nav>

    <div v-if="loading" class="library-state">正在读取真实素材索引…</div>
    <div v-else-if="!filteredPresets.length" class="library-state">没有匹配的预设。</div>
    <section v-else class="preset-grid">
      <article v-for="preset in filteredPresets" :key="preset.key" class="preset-card">
        <header>
          <div><b>{{ preset.title }}</b><small>Canon v{{ preset.version }} · {{ preset.canonProfileId }}</small></div>
          <span :class="preset.ready ? 'ready' : 'blocked'">{{ preset.ready ? '资产齐全' : '缺少必需素材' }}</span>
        </header>
        <p>{{ preset.description }}</p>
        <div class="evidence-packages">
          <section v-for="group in slotGroups(preset)" :key="group.key" class="evidence-package">
            <div class="package-heading"><b>{{ group.title }}</b><small>{{ group.supplement }}</small></div>
            <div class="evidence-strip">
              <figure v-for="(slot, index) in group.slots" :key="slot.semanticKey">
                <img :src="slot.thumbnailUrl || slot.contentUrl" :alt="slot.title" />
                <figcaption><i>{{ index + 1 }}</i><span>{{ slot.title }}</span><small>{{ slot.semanticKey }}</small></figcaption>
              </figure>
            </div>
          </section>
        </div>
        <footer>
          <small>{{ visibleSlots(preset).length }} 个真实证据槽位 · 不复制二进制资产</small>
          <button
            type="button"
            :disabled="!preset.ready || applying"
            :title="preset.ready ? '应用后创建显式节点、引用和血缘边' : '必需 Canon 资产尚未齐全'"
            @click="emit('apply', preset.key)"
          >应用至画布</button>
        </footer>
      </article>
    </section>
  </el-dialog>
</template>

<style scoped>
.library-heading { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding-right: 36px; }.library-heading > div { display: grid; gap: 4px; }.library-heading b { color: #f2f4f8; font-size: 18px; }.library-heading small { color: #8993a1; }.library-heading input { width: 300px; min-height: 40px; padding: 0 13px; color: #e7ebf1; background: #17191d; border: 1px solid #3a3d44; border-radius: 9px; outline: none; }.library-heading input:focus { border-color: #7ca6d8; box-shadow: 0 0 0 3px rgb(89 142 203 / 18%); }
.library-tabs { display: flex; gap: 4px; margin-bottom: 14px; padding: 4px; background: #1b1d21; border: 1px solid #33363c; border-radius: 10px; }.library-tabs button { min-height: 40px; padding: 0 18px; color: #8f98a5; background: transparent; border: 0; border-radius: 7px; cursor: pointer; }.library-tabs button.active { color: #f2f4f7; background: #303238; }.library-tabs button:focus-visible { outline: 2px solid #78aef0; outline-offset: -2px; }
.preset-grid { display: grid; gap: 14px; max-height: min(650px, calc(100vh - 250px)); overflow: auto; padding-right: 4px; }.preset-card { display: grid; gap: 12px; padding: 16px; color: #e4e8ee; background: #202226; border: 1px solid #393c42; border-radius: 13px; }.preset-card header,.preset-card footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; }.preset-card header div { display: grid; gap: 3px; }.preset-card header small,.preset-card footer small { color: #818b99; }.preset-card header > span { padding: 5px 8px; border-radius: 999px; font-size: 11px; }.preset-card header > span.ready { color: #a9dbc1; background: #1d3a2c; }.preset-card header > span.blocked { color: #e2bd7b; background: #42351e; }.preset-card p { margin: 0; color: #aeb6c1; line-height: 1.5; }
.evidence-packages { display: grid; gap: 12px; }.evidence-package { display: grid; gap: 8px; padding: 11px; background: #191b1f; border: 1px solid #30343a; border-radius: 10px; }.package-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.package-heading b { font-size: 12px; }.package-heading small { color: #b09261; }.evidence-strip { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; }.evidence-strip figure { min-width: 0; margin: 0; overflow: hidden; background: #15171a; border: 1px solid #343840; border-radius: 10px; }.evidence-strip img { display: block; width: 100%; height: 150px; object-fit: cover; background: #101216; }.evidence-strip figcaption { position: relative; display: grid; gap: 2px; padding: 9px; }.evidence-strip figcaption i { position: absolute; top: -28px; left: 7px; display: grid; width: 22px; height: 22px; place-items: center; color: #fff; background: rgb(18 20 24 / 88%); border-radius: 50%; font-size: 11px; font-style: normal; }.evidence-strip figcaption span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.evidence-strip figcaption small { overflow: hidden; color: #7d8795; text-overflow: ellipsis; white-space: nowrap; }
.preset-card footer button { min-height: 44px; padding: 0 18px; color: #15202b; font-weight: 700; background: #dceaf9; border: 0; border-radius: 9px; cursor: pointer; }.preset-card footer button:disabled { color: #777d84; background: #33363a; cursor: not-allowed; }.library-state { display: grid; min-height: 300px; place-items: center; color: #8d96a3; }.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media (max-width: 900px) { .library-heading { align-items: stretch; flex-direction: column; gap: 12px; }.library-heading input { width: 100%; }.evidence-strip { grid-template-columns: repeat(2, minmax(120px, 1fr)); } }
</style>
