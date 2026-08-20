<script setup lang="ts">
import { ArrowLeft } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, canvasApi } from "../api/client";
import type {
  CanvasTemplateDto,
  CanvasTemplateKey,
  ProjectSummary,
  StoryBriefInput,
  SubjectInput,
} from "../api/types";
import AigcCanvasWorkspace from "../components/canvas/AigcCanvasWorkspace.vue";
import CanvasTemplateLibrary from "../components/canvas/CanvasTemplateLibrary.vue";

const route = useRoute();
const router = useRouter();
const projects = ref<ProjectSummary[]>([]);
const templates = ref<CanvasTemplateDto[]>([]);
const selectedTemplate = ref<CanvasTemplateKey | null>(null);
const creating = ref(false);
const productFile = ref<File | null>(null);
const talentFile = ref<File | null>(null);
const projectId = computed(() => String(route.params.projectId ?? ""));

const form = reactive<{
  title: string;
  brief: StoryBriefInput;
  subjects: [SubjectInput, SubjectInput];
  productName: string;
  productDescription: string;
  imagePrompt: string;
  candidateCount: number;
}>({
  title: "",
  brief: {
    theme: "",
    audience: "亲子与泛生活观众",
    genre: "治愈生活短剧",
    tone: "温暖、紧凑、可视化",
    aspectRatio: "9:16",
    targetDurationSeconds: 60,
    constraints: [],
  },
  subjects: [
    {
      name: "",
      kind: "person",
      role: "protagonist",
      identityAnchors: [""],
      immutableTraits: [""],
      dramaticFunction: "推动主要行动",
    },
    {
      name: "",
      kind: "animal",
      role: "co_protagonist",
      identityAnchors: [""],
      immutableTraits: [""],
      dramaticFunction: "发现问题并参与解决",
    },
  ],
  productName: "",
  productDescription: "",
  imagePrompt: "",
  candidateCount: 4,
});

onMounted(async () => {
  [projects.value, templates.value] = await Promise.all([
    api.projects(),
    canvasApi.templates(),
  ]);
});

function chooseFile(event: Event, target: "product" | "talent") {
  const file = (event.target as HTMLInputElement).files?.[0] ?? null;
  if (target === "product") productFile.value = file;
  else talentFile.value = file;
}

function validateCreate(): boolean {
  if (!form.title.trim()) {
    ElMessage.error("请填写项目名称");
    return false;
  }
  if (selectedTemplate.value === "short_drama") {
    if (!form.brief.theme.trim()) {
      ElMessage.error("请填写故事主题或原始剧情");
      return false;
    }
    if (form.subjects.some((subject) => !subject.name.trim() || !subject.identityAnchors[0]?.trim())) {
      ElMessage.error("短剧模板需要两个主体及其身份锚点");
      return false;
    }
  }
  if (selectedTemplate.value === "product_ad" && (
    !form.productName.trim() || !form.productDescription.trim()
  )) {
    ElMessage.error("请填写产品名称和不可变化的包装特征");
    return false;
  }
  return true;
}

async function createCanvasProject() {
  if (!selectedTemplate.value || !validateCreate()) return;
  creating.value = true;
  try {
    const sourceText = selectedTemplate.value === "short_drama"
      ? form.brief.theme
      : selectedTemplate.value === "product_ad"
        ? form.imagePrompt || form.productDescription
        : "空白媒体画布";
    const created = await api.createProject({
      project: {
        title: form.title,
        firstSceneTitle: "媒体画布入口",
        firstSceneText: sourceText,
      },
    });
    await canvasApi.instantiateTemplate(created.projectId, selectedTemplate.value);

    if (selectedTemplate.value === "short_drama") {
      await canvasApi.saveBrief(created.projectId, form.brief);
      for (const subject of form.subjects) {
        await canvasApi.createSubject(created.projectId, {
          ...subject,
          identityAnchors: subject.identityAnchors.filter(Boolean),
          immutableTraits: subject.immutableTraits.filter(Boolean),
        });
      }
    } else if (selectedTemplate.value === "product_ad") {
      const productAsset = productFile.value
        ? await api.uploadReference(
            created.projectId,
            "generation_reference",
            "prop",
            `${form.productName}包装正面`,
            productFile.value,
          )
        : null;
      await canvasApi.createSubject(created.projectId, {
        name: form.productName,
        kind: "product",
        role: "hero_product",
        identityAnchors: [form.productDescription],
        immutableTraits: ["商标、标签、包装比例与材质不得变化"],
        dramaticFunction: "广告主视觉与叙事焦点",
        references: productAsset ? [{
          assetId: productAsset.id,
          semanticRole: "packshot_front",
          instruction: "严格保持包装、标签、商标与瓶罐比例",
        }] : [],
      });
      if (talentFile.value) {
        const talentAsset = await api.uploadReference(
          created.projectId,
          "generation_reference",
          "identity",
          "模特参考",
          talentFile.value,
        );
        await canvasApi.createSubject(created.projectId, {
          name: "广告模特",
          kind: "person",
          role: "support",
          identityAnchors: ["严格参考上传模特的面部、发型和体型"],
          immutableTraits: ["面部身份与体型不得变化"],
          dramaticFunction: "展示产品使用动作与尺寸关系",
          references: [{
            assetId: talentAsset.id,
            semanticRole: "front",
            instruction: "保持人物身份一致",
          }],
        });
      }
    }
    await router.push({ name: "aigc-canvas", params: { projectId: created.projectId } });
  } finally {
    creating.value = false;
  }
}
</script>

<template>
  <AigcCanvasWorkspace v-if="projectId" :project-id="projectId" />
  <main v-else class="canvas-entry">
    <CanvasTemplateLibrary
      v-if="!selectedTemplate"
      :templates="templates"
      :loading="creating"
      @select="selectedTemplate = $event"
    />

    <section v-else class="create-shell">
      <button class="back-button" type="button" @click="selectedTemplate = null"><ArrowLeft />返回模板库</button>
      <header>
        <span>{{ selectedTemplate === 'short_drama' ? 'STORY PRODUCTION' : selectedTemplate === 'product_ad' ? 'PRODUCT CAMPAIGN' : 'EMPTY GRAPH' }}</span>
        <h1>{{ templates.find((item) => item.key === selectedTemplate)?.title }}</h1>
        <p>{{ templates.find((item) => item.key === selectedTemplate)?.description }}</p>
      </header>
      <section class="entry-form">
        <el-form label-position="top">
          <el-form-item label="项目名称"><el-input v-model="form.title" placeholder="例如：冰爽一刻 · 夏日产品广告" /></el-form-item>

          <template v-if="selectedTemplate === 'short_drama'">
            <el-form-item label="主题 / 故事剧情输入"><el-input v-model="form.brief.theme" type="textarea" :rows="5" placeholder="写下冲突、目标或原始剧情；后续生成三套可评审方案" /></el-form-item>
            <div class="entry-grid">
              <el-form-item label="目标时长（秒）"><el-input-number v-model="form.brief.targetDurationSeconds" :min="8" :max="600" /></el-form-item>
              <el-form-item label="画幅"><el-select v-model="form.brief.aspectRatio"><el-option label="9:16 竖屏" value="9:16" /><el-option label="16:9 横屏" value="16:9" /><el-option label="1:1 方形" value="1:1" /></el-select></el-form-item>
            </div>
            <div class="subject-entry-grid">
              <article v-for="(subject, index) in form.subjects" :key="index">
                <b>叙事主体 {{ index + 1 }}</b>
                <el-input v-model="subject.name" placeholder="主体名称" />
                <el-select v-model="subject.kind"><el-option v-for="kind in ['person','animal','object','location']" :key="kind" :label="kind" :value="kind" /></el-select>
                <el-input v-model="subject.identityAnchors[0]" type="textarea" :rows="3" placeholder="身份锚点与不可变化特征" />
              </article>
            </div>
          </template>

          <template v-else-if="selectedTemplate === 'product_ad'">
            <div class="entry-grid">
              <el-form-item label="产品主体名称"><el-input v-model="form.productName" placeholder="产品 / 包装名称" /></el-form-item>
              <el-form-item label="图片候选数量"><el-input-number v-model="form.candidateCount" :min="1" :max="8" /></el-form-item>
            </div>
            <el-form-item label="包装身份锚点"><el-input v-model="form.productDescription" type="textarea" :rows="3" placeholder="商标、标签文字、包装比例、颜色、材质和不可变化部分" /></el-form-item>
            <el-form-item label="广告画面目标"><el-input v-model="form.imagePrompt" type="textarea" :rows="4" placeholder="例如：电影级蓝色冰爽广告，产品置于冰块之间，模特从右侧伸手取出产品" /></el-form-item>
            <div class="upload-grid">
              <label><b>产品 / 包装正面</b><small>{{ productFile?.name ?? '支持 JPG / PNG，进入产品主体语义参考' }}</small><input type="file" accept="image/*" @change="chooseFile($event, 'product')" /></label>
              <label><b>模特 / 风格参考</b><small>{{ talentFile?.name ?? '可选，人物与产品参考独立绑定' }}</small><input type="file" accept="image/*" @change="chooseFile($event, 'talent')" /></label>
            </div>
          </template>

          <el-alert v-else title="将只创建业务图与布局，不自动调用任何付费模型。" type="info" :closable="false" />
          <el-button class="create-button" type="primary" :loading="creating" @click="createCanvasProject">创建项目并进入媒体画布</el-button>
        </el-form>
      </section>
    </section>

    <section v-if="!selectedTemplate && projects.length" class="recent-projects">
      <h2>继续已有项目</h2>
      <button v-for="project in projects" :key="project.id" type="button" @click="router.push({ name: 'aigc-canvas', params: { projectId: project.id } })">
        <b>{{ project.title }}</b><span>{{ project.contentDate }}</span>
      </button>
    </section>
  </main>
</template>

<style scoped>
.canvas-entry { min-height: 100%; overflow: auto; color: #e8edf6; background: #111419; }
.create-shell { width: min(980px, calc(100vw - 56px)); margin: 0 auto; padding: 46px 0 80px; }.create-shell > header { margin: 30px 0 22px; }.create-shell > header span { color: #71819a; font-size: 10px; font-weight: 800; letter-spacing: .17em; }.create-shell h1 { margin: 8px 0; font-size: 42px; }.create-shell header p { color: #8e99aa; }
.back-button { padding: 8px 10px; display: flex; align-items: center; gap: 6px; color: #a8b3c2; background: transparent; border: 0; cursor: pointer; }.back-button :deep(svg) { width: 16px; }
.entry-form { padding: 26px; background: #191d24; border: 1px solid #333a46; border-radius: 16px; box-shadow: 0 24px 70px rgb(0 0 0 / 30%); }.entry-grid, .subject-entry-grid, .upload-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }.subject-entry-grid article { display: grid; gap: 10px; padding: 13px; background: #13171c; border: 1px solid #303642; border-radius: 10px; }.subject-entry-grid { margin-bottom: 18px; }
.upload-grid { margin-bottom: 20px; }.upload-grid label { min-height: 118px; padding: 16px; display: flex; flex-direction: column; justify-content: center; gap: 7px; color: #d5dce7; background: #13171c; border: 1px dashed #475161; border-radius: 11px; cursor: pointer; }.upload-grid small { color: #778397; }.upload-grid input { color: #8590a0; }
.create-button { width: 100%; margin-top: 20px; }
.recent-projects { width: min(1160px, calc(100vw - 48px)); margin: -36px auto 60px; }.recent-projects button { min-width: 220px; margin: 0 8px 8px 0; padding: 13px; color: #d5dce8; text-align: left; background: #1b2028; border: 1px solid #333b48; border-radius: 10px; cursor: pointer; }.recent-projects span { display: block; margin-top: 5px; color: #7e8999; font-size: 11px; }
@media (max-width: 680px) { .entry-grid, .subject-entry-grid, .upload-grid { grid-template-columns: 1fr; }.create-shell { width: calc(100vw - 28px); } }
</style>
