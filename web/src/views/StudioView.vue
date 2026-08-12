<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, assetContentUrl } from "../api/client";
import type {
  AnchorMode,
  AssetDto,
  JobDto,
  ProjectGraph,
  ProjectSummary,
  ReferenceBinding,
  ReferenceRole,
  ReferenceTarget,
  ReferenceUsage,
  SceneDto,
  SequenceDto,
  ShotDto,
} from "../api/types";
import VideoTimeline from "../components/VideoTimeline.vue";

const route = useRoute();
const router = useRouter();
const projects = ref<ProjectSummary[]>([]);
const graph = ref<ProjectGraph | null>(null);
const selectedShotId = ref<string | null>(null);
const selectedSequenceId = ref<string | null>(null);
const busy = ref(false);
const persistentError = ref("");
const createVisible = ref(false);
const projectSettingsVisible = ref(false);
const sceneVisible = ref(false);
const shotVisible = ref(false);
const suggestion = ref<{ stepId: string; output: { sceneTitle: string; shots: unknown[] } } | null>(null);
const promptPreview = ref<{ prompt: string; charCount: number; utf8Bytes: number } | null>(null);
const createReferenceFile = ref<File | null>(null);
let polling: number | undefined;

const createForm = reactive({ title: "", sceneTitle: "第一场景", sourceText: "" });
const projectSettingsForm = reactive({ title: "", contentDate: "" });
const sceneForm = reactive({
  id: "",
  title: "",
  sourceText: "",
  chapterLabel: "",
  contextNote: "",
});
const shotForm = reactive({
  id: "",
  sceneId: "",
  title: "",
  direction: "",
  durationSeconds: 8,
  anchorMode: "text_only" as AnchorMode,
  referenceBindings: [] as ReferenceBinding[],
});
const referenceForm = reactive({
  assetId: "",
  usage: "generation_reference" as ReferenceUsage,
  role: "identity" as ReferenceRole,
  applyTo: "both" as ReferenceTarget,
});
const uploadForm = reactive({
  usage: "generation_reference" as ReferenceUsage,
  role: "identity" as ReferenceRole,
  file: null as File | null,
});

const selectedShot = computed<ShotDto | null>(() => {
  if (!graph.value || !selectedShotId.value) return null;
  for (const scene of graph.value.scenes) {
    const shot = scene.shots.find((item) => item.id === selectedShotId.value);
    if (shot) return shot;
  }
  return null;
});
const selectedVideo = computed(() => {
  const shot = selectedShot.value;
  if (!shot) return null;
  return shot.assets.find((item) => item.id === shot.selectedVideoAssetId)
    ?? [...shot.assets].reverse().find((item) => item.mediaType === "video")
    ?? null;
});
const selectableAssets = computed(() => graph.value?.assets.filter((item) => item.mediaType === "image") ?? []);
const selectedVideoDurationMs = computed(() => {
  if (!selectedVideo.value || !selectedShot.value) return 0;
  const qc = selectedVideo.value.metadata.qc as Record<string, unknown> | undefined;
  return Number(qc?.durationMs ?? selectedShot.value.durationSeconds * 1000);
});
const selectedVideoFrames = computed(() => {
  if (!selectedShot.value || !selectedVideo.value) return [];
  return selectedShot.value.assets
    .filter(
      (item) => item.role === "review_frame"
        && item.metadata.sourceVideoAssetId === selectedVideo.value?.id,
    )
    .sort((left, right) => Number(left.metadata.ordinal) - Number(right.metadata.ordinal))
    .map((item) => ({
      src: assetContentUrl(item.id),
      label: `${String(item.metadata.ordinal)}/${String(item.metadata.frameCount)}`,
      timestampMs: Math.round(
        ((Number(item.metadata.ordinal) - 1)
          / Math.max(1, Number(item.metadata.frameCount) - 1))
          * selectedVideoDurationMs.value,
      ),
    }));
});
const selectedVideoMarkersMs = computed(() => {
  if (!selectedShot.value || !selectedVideo.value?.producingStepId) return [];
  const attempt = selectedShot.value.attempts.find(
    (item) => item.id === selectedVideo.value?.producingStepId,
  );
  const review = attempt?.reviews.find(
    (item) => Array.isArray(item.evidence.shotBoundariesSeconds),
  );
  if (!review) return [];
  const boundaries = Array.isArray(review.evidence.shotBoundariesSeconds)
    ? review.evidence.shotBoundariesSeconds.map((item) => Number(item) * 1000)
    : [];
  const findings = Array.isArray(review.evidence.evidence)
    ? review.evidence.evidence.flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const raw = String((item as Record<string, unknown>).timestamp ?? "").trim();
        const clock = raw.match(/^(?:(\d+):)?(\d+(?:\.\d+)?)s?$/);
        if (!clock) return [];
        return [((Number(clock[1] ?? 0) * 60) + Number(clock[2])) * 1000];
      })
    : [];
  return [...new Set([...boundaries, ...findings]
    .map((item) => Math.round(item))
    .filter((item) => Number.isFinite(item) && item >= 0 && item <= selectedVideoDurationMs.value))]
    .sort((left, right) => left - right);
});
const selectedSequence = computed<SequenceDto | null>(() => {
  if (!graph.value || !selectedSequenceId.value) return null;
  return graph.value.sequences.find((item) => item.id === selectedSequenceId.value) ?? null;
});
const selectedSequenceAsset = computed(() => {
  if (!graph.value || !selectedSequence.value?.renderedAssetId) return null;
  return graph.value.assets.find((item) => item.id === selectedSequence.value?.renderedAssetId) ?? null;
});

function localDateText(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

async function loadProjects() {
  projects.value = await api.projects();
}

async function loadGraph(projectId?: string) {
  const id = projectId ?? String(route.query.project ?? "");
  if (!id) {
    graph.value = null;
    return;
  }
  graph.value = await api.project(id);
  const requestedSequence = String(route.query.sequence ?? "");
  if (graph.value.sequences.some((item) => item.id === requestedSequence)) {
    selectedSequenceId.value = requestedSequence;
    selectedShotId.value = null;
    return;
  }
  selectedSequenceId.value = null;
  const requestedShot = String(route.query.shot ?? "");
  const allShots = graph.value.scenes.flatMap((scene) => scene.shots);
  selectedShotId.value = allShots.some((item) => item.id === requestedShot)
    ? requestedShot
    : selectedShotId.value && allShots.some((item) => item.id === selectedShotId.value)
      ? selectedShotId.value
      : allShots[0]?.id ?? null;
}

async function selectProject(id: string) {
  await router.replace({ path: "/studio", query: { project: id } });
  await loadGraph(id);
}

async function selectShot(id: string) {
  selectedShotId.value = id;
  selectedSequenceId.value = null;
  await router.replace({
    path: "/studio",
    query: { project: graph.value?.project.id, shot: id },
  });
  promptPreview.value = null;
}

async function showSequence(id: string) {
  selectedSequenceId.value = id;
  selectedShotId.value = null;
  await router.replace({
    path: "/studio",
    query: { project: graph.value?.project.id, sequence: id },
  });
}

async function createProject() {
  if (!createForm.title.trim() || !createForm.sourceText.trim()) {
    ElMessage.warning("请填写项目标题和第一场景原始剧本");
    return;
  }
  await act(async () => {
    const result = await api.createProject({
      project: {
        title: createForm.title,
        firstSceneTitle: createForm.sceneTitle,
        firstSceneText: createForm.sourceText,
      },
      contentDate: localDateText(),
    });
    if (createReferenceFile.value) {
      await api.uploadReference(result.projectId, "generation_reference", "identity", createReferenceFile.value);
    }
    createVisible.value = false;
    Object.assign(createForm, { title: "", sceneTitle: "第一场景", sourceText: "" });
    createReferenceFile.value = null;
    await loadProjects();
    await selectProject(result.projectId);
  });
}

function editProjectSettings() {
  if (!graph.value) return;
  Object.assign(projectSettingsForm, {
    title: graph.value.project.title,
    contentDate: graph.value.project.contentDate,
  });
  projectSettingsVisible.value = true;
}

async function saveProjectSettings() {
  if (!graph.value || !projectSettingsForm.title.trim() || !projectSettingsForm.contentDate) {
    ElMessage.warning("请填写项目标题和日期");
    return;
  }
  await act(async () => {
    await api.updateProject(graph.value!.project.id, {
      title: projectSettingsForm.title,
      contentDate: projectSettingsForm.contentDate,
    });
    projectSettingsVisible.value = false;
    await loadProjects();
    await loadGraph();
  });
}

function editScene(scene?: SceneDto) {
  Object.assign(sceneForm, scene
    ? {
        id: scene.id,
        title: scene.title,
        sourceText: scene.sourceText,
        chapterLabel: scene.chapterLabel ?? "",
        contextNote: scene.contextNote ?? "",
      }
    : { id: "", title: "新场景", sourceText: "", chapterLabel: "", contextNote: "" });
  sceneVisible.value = true;
}

async function saveScene() {
  const payload = {
    title: sceneForm.title,
    sourceText: sceneForm.sourceText,
    chapterLabel: sceneForm.chapterLabel || null,
    contextNote: sceneForm.contextNote || null,
  };
  await act(async () => {
    if (sceneForm.id) await api.updateScene(sceneForm.id, payload);
    else if (graph.value) await api.addScene(graph.value.project.id, payload);
    sceneVisible.value = false;
    await loadGraph();
  });
}

async function removeScene(scene: SceneDto) {
  await ElMessageBox.confirm(`删除场景“${scene.title}”？已有 Provider 历史的场景不会被允许删除。`, "确认");
  await act(async () => {
    await api.deleteScene(scene.id);
    await loadGraph();
  });
}

function editShot(sceneId: string, shot?: ShotDto) {
  Object.assign(shotForm, shot
    ? {
        id: shot.id,
        sceneId,
        title: shot.title,
        direction: shot.direction,
        durationSeconds: shot.durationSeconds,
        anchorMode: shot.anchorMode,
        referenceBindings: shot.referenceBindings.map((item) => ({ ...item })),
      }
    : {
        id: "",
        sceneId,
        title: "新镜头",
        direction: "中景固定机位，人物与灰白猫处于清晰相对位置；主体完成一个连续动作并在稳定状态结束。",
        durationSeconds: 8,
        anchorMode: "text_only",
        referenceBindings: [],
      });
  shotVisible.value = true;
}

async function saveShot() {
  const payload = {
    title: shotForm.title,
    direction: shotForm.direction,
    durationSeconds: shotForm.durationSeconds,
    anchorMode: shotForm.anchorMode,
    referenceBindings: shotForm.referenceBindings,
  };
  await act(async () => {
    const saved = shotForm.id
      ? await api.updateShot(shotForm.id, payload)
      : await api.addShot(shotForm.sceneId, payload);
    shotVisible.value = false;
    await loadGraph();
    await selectShot(saved.id);
  });
}

async function removeShot(shot: ShotDto) {
  await ElMessageBox.confirm(`删除镜头“${shot.title}”？已有生成历史的镜头不会被允许删除。`, "确认");
  await act(async () => {
    await api.deleteShot(shot.id);
    await loadGraph();
  });
}

async function moveScene(index: number, delta: number) {
  if (!graph.value) return;
  const ids = graph.value.scenes.map((item) => item.id);
  const target = index + delta;
  if (target < 0 || target >= ids.length) return;
  [ids[index], ids[target]] = [ids[target], ids[index]];
  await act(async () => {
    await api.reorderScenes(graph.value!.project.id, ids);
    await loadGraph();
  });
}

async function moveShot(scene: SceneDto, index: number, delta: number) {
  const ids = scene.shots.map((item) => item.id);
  const target = index + delta;
  if (target < 0 || target >= ids.length) return;
  [ids[index], ids[target]] = [ids[target], ids[index]];
  await act(async () => {
    await api.reorderShots(scene.id, ids);
    await loadGraph();
  });
}

async function suggest(scene: SceneDto) {
  await ElMessageBox.confirm("AI 镜头建议会产生一次规划模型费用，是否继续？", "付费确认");
  await act(async () => {
    const accepted = await api.suggestShots(scene.id);
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "镜头建议失败"));
    suggestion.value = job.result as typeof suggestion.value;
  });
}

async function acceptSuggestion() {
  if (!suggestion.value) return;
  await act(async () => {
    await api.acceptSuggestions(suggestion.value!.stepId);
    suggestion.value = null;
    await loadGraph();
  });
}

async function showPrompt() {
  if (!selectedShot.value) return;
  await act(async () => { promptPreview.value = await api.promptPreview(selectedShot.value!.id); });
}

async function generate(kind: "anchor" | "video") {
  if (!selectedShot.value) return;
  const operationKey = kind === "anchor" ? "image:anchor" : "video:shot";
  const priorAttempts = selectedShot.value.attempts.filter(
    (item) => item.operationKey === operationKey,
  );
  const regenerate = priorAttempts.length > 0;
  const action = regenerate ? "重新生成并保留旧版本" : "生成";
  let reason = kind === "anchor" ? "生成镜头开场锚点" : "生成单镜头视频";
  if (regenerate) {
    const answer = await ElMessageBox.prompt(
      "请只写本次需要修正的一项问题。该说明会进入本次实际调用Prompt，旧Prompt不会被覆盖。",
      "填写重做目标",
      { inputPlaceholder: "例如：保持猫咪四足着地，钓线只连接人物手中的鱼竿与浮标" },
    );
    reason = answer.value.trim();
    if (!reason) {
      ElMessage.warning("重新生成必须填写修正目标");
      return;
    }
  }
  await ElMessageBox.confirm(
    kind === "anchor"
      ? `${action}锚点会产生一次 Seedream 费用，是否继续？`
      : `${action}本镜头片段会产生一次 Seedance 费用，是否继续？`,
    "付费确认",
  );
  await act(async () => {
    const accepted = kind === "anchor"
      ? await api.generateAnchor(selectedShot.value!.id, regenerate, reason)
      : await api.generateVideo(selectedShot.value!.id, regenerate, reason);
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "生成失败"));
    await loadGraph();
  });
}

async function resumeAttempt(stepId: string) {
  await act(async () => {
    const accepted = await api.resumeStep(stepId);
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "继续查询失败"));
    await loadGraph();
  });
}

async function reconcileAttempt(stepId: string) {
  await act(async () => {
    const candidates = await api.reconciliationCandidates(stepId);
    if (!candidates.length) throw new Error("Ark任务列表中没有匹配候选，请稍后再次查询");
    const lines = candidates.map((item) => String(item.taskId)).join("\n");
    const answer = await ElMessageBox.prompt(
      `候选Task ID：\n${lines}\n请输入确认绑定的Task ID`,
      "对账供应商任务",
      { inputValue: candidates.length === 1 ? String(candidates[0].taskId) : "" },
    );
    await api.reconcileStep(stepId, answer.value);
    await loadGraph();
  });
}

async function review(asset: AssetDto, decision: "approved" | "rejected") {
  const reason = decision === "approved" ? "人工观看通过" : "人工观看未通过";
  await act(async () => {
    await api.reviewAsset(asset.id, decision, reason);
    await loadGraph();
  });
}

async function uploadReference() {
  if (!graph.value || !uploadForm.file) return;
  await act(async () => {
    await api.uploadReference(graph.value!.project.id, uploadForm.usage, uploadForm.role, uploadForm.file!);
    uploadForm.file = null;
    await loadGraph();
  });
}

async function bindReference() {
  if (!selectedShot.value || !referenceForm.assetId) return;
  const bindings = selectedShot.value.referenceBindings.filter((item) => item.assetId !== referenceForm.assetId);
  bindings.push({ ...referenceForm });
  const draft = {
    title: selectedShot.value.title,
    direction: selectedShot.value.direction,
    durationSeconds: selectedShot.value.durationSeconds,
    anchorMode: referenceForm.usage === "approved_anchor"
      ? "existing"
      : selectedShot.value.anchorMode,
    referenceBindings: bindings,
  };
  await act(async () => {
    await api.updateShot(selectedShot.value!.id, draft);
    await loadGraph();
  });
}

async function removeBinding(assetId: string) {
  if (!selectedShot.value) return;
  const removed = selectedShot.value.referenceBindings.find((item) => item.assetId === assetId);
  const references = selectedShot.value.referenceBindings.filter(
    (item) => item.assetId !== assetId,
  );
  await act(async () => {
    if (removed?.usage === "approved_anchor") {
      await api.updateShot(selectedShot.value!.id, {
        title: selectedShot.value!.title,
        direction: selectedShot.value!.direction,
        durationSeconds: selectedShot.value!.durationSeconds,
        anchorMode: "text_only",
        referenceBindings: references,
      });
    } else {
      await api.updateReferences(selectedShot.value!.id, references);
    }
    await loadGraph();
  });
}

function chooseUploadFile(event: Event) {
  uploadForm.file = (event.target as HTMLInputElement).files?.[0] ?? null;
}

function chooseCreateReference(event: Event) {
  createReferenceFile.value = (event.target as HTMLInputElement).files?.[0] ?? null;
}

function closeSuggestion(value: boolean) {
  if (!value) suggestion.value = null;
}

async function buildSequence() {
  if (!graph.value) return;
  await act(async () => {
    const accepted = await api.buildSequence(graph.value!.project.id);
    const job = await waitJob(accepted.jobId);
    if (job.status === "failed") throw new Error(String(job.error?.message ?? "总片合成失败"));
    await loadGraph();
  });
}

async function decideSequence(sequence: SequenceDto, approve: boolean) {
  if (!graph.value) return;
  await act(async () => {
    await api.selectSequence(graph.value!.project.id, sequence.id, approve);
    await loadGraph();
    if (approve) await showSequence(sequence.id);
  });
}

async function selectVideoVersion(asset: AssetDto) {
  if (!selectedShot.value) return;
  await act(async () => {
    await api.selectVersion(selectedShot.value!.id, asset.id);
    await loadGraph();
  });
}

async function waitJob(id: string): Promise<JobDto> {
  for (;;) {
    const job = await api.job(id);
    if (job.status === "succeeded" || job.status === "failed") return job;
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
  }
}

async function act(fn: () => Promise<void>) {
  busy.value = true;
  persistentError.value = "";
  try {
    await fn();
  } catch (error) {
    persistentError.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

watch(() => route.query.project, () => void loadGraph());
onMounted(async () => {
  await act(async () => {
    await loadProjects();
    if (route.query.project) await loadGraph();
  });
  polling = window.setInterval(() => {
    if (route.query.project && !busy.value) void loadGraph();
  }, 10000);
});
onBeforeUnmount(() => window.clearInterval(polling));
</script>

<template>
  <div class="studio" v-loading="busy">
    <header class="studio-header">
      <div>
        <h1>镜头片段工作台</h1>
        <p>任意场景、逐镜确认、独立版本；生成前的文字和素材均可修改。</p>
      </div>
      <el-button type="primary" @click="createVisible = true">新建项目</el-button>
    </header>

    <el-alert v-if="persistentError" type="error" :closable="false" show-icon class="persistent-alert">
      <template #title>操作未完成</template>
      {{ persistentError }}
    </el-alert>

    <div class="workspace-grid">
      <aside class="project-rail panel">
        <div class="panel-title">项目</div>
        <button
          v-for="project in projects"
          :key="project.id"
          class="project-item"
          :class="{ active: graph?.project.id === project.id }"
          @click="selectProject(project.id)"
        >
          <strong>{{ project.title }}</strong>
          <span>{{ project.contentDate }}</span>
        </button>
        <template v-if="graph">
          <div class="panel-title reference-title">参考素材</div>
          <el-select v-model="uploadForm.usage" size="small">
            <el-option label="生成参考" value="generation_reference" />
            <el-option label="最终锚点" value="approved_anchor" />
          </el-select>
          <el-select v-model="uploadForm.role" size="small">
            <el-option v-for="item in ['identity','style','scene','prop','composition']" :key="item" :label="item" :value="item" />
          </el-select>
          <input type="file" accept="image/*" @change="chooseUploadFile" />
          <el-button size="small" :disabled="!uploadForm.file" @click="uploadReference">上传</el-button>
          <div class="asset-strip">
            <img v-for="asset in graph.assets.filter(item => item.mediaType === 'image')" :key="asset.id" :src="assetContentUrl(asset.id)" :title="`${String(asset.metadata.referenceRole ?? asset.role)} / ${String(asset.metadata.usage ?? asset.status)}`" />
          </div>
        </template>
      </aside>

      <main class="queue panel">
        <div v-if="!graph" class="empty-state">
          <h2>从一段场景剧本开始</h2>
          <p>不需要先设计全天结构，也不会预建上午、中午、傍晚节点。</p>
          <el-button type="primary" @click="createVisible = true">创建第一个项目</el-button>
        </div>
        <template v-else>
          <div class="queue-heading">
            <div><h2>{{ graph.project.title }}</h2><span>V4 · {{ graph.project.contentDate }} · {{ graph.scenes.length }} 个场景</span></div>
            <div><el-button @click="editProjectSettings">项目设置</el-button><el-button @click="editScene()">添加场景</el-button><el-button type="success" @click="buildSequence">合成已批准片段</el-button></div>
          </div>
          <section v-for="(scene, sceneIndex) in graph.scenes" :key="scene.id" class="scene-card">
            <header>
              <div>
                <span class="order-chip">场景 {{ scene.order }}</span>
                <strong>{{ scene.title }}</strong>
                <small v-if="scene.chapterLabel">{{ scene.chapterLabel }}</small>
              </div>
              <div>
                <el-button text @click="moveScene(sceneIndex, -1)">上移</el-button>
                <el-button text @click="moveScene(sceneIndex, 1)">下移</el-button>
                <el-button text @click="editScene(scene)">编辑</el-button>
                <el-button text type="danger" @click="removeScene(scene)">删除</el-button>
              </div>
            </header>
            <p class="source-text">{{ scene.sourceText }}</p>
            <div class="scene-actions">
              <el-button type="primary" plain @click="suggest(scene)">AI 建议镜头卡</el-button>
              <el-button @click="editShot(scene.id)">手工添加镜头</el-button>
            </div>
            <el-collapse v-if="scene.attempts.length" class="scene-attempts">
              <el-collapse-item title="AI 镜头建议历史" name="suggestions">
                <div v-for="attempt in scene.attempts" :key="attempt.id" class="attempt">
                  <b>#{{ attempt.attempt }} {{ attempt.status }}</b>
                  <span>{{ attempt.model || "未记录模型" }}</span>
                  <pre v-if="attempt.prompt">{{ attempt.prompt.text }}</pre>
                  <details>
                    <summary>Provider 输入与原始输出</summary>
                    <pre>{{ JSON.stringify(attempt.inputSnapshot, null, 2) }}</pre>
                  </details>
                  <el-alert
                    v-if="attempt.error"
                    type="error"
                    :title="String(attempt.error.message ?? attempt.error.code)"
                    :closable="false"
                  />
                </div>
              </el-collapse-item>
            </el-collapse>
            <div class="shots">
              <article
                v-for="(shot, shotIndex) in scene.shots"
                :key="shot.id"
                class="shot-card"
                :class="{ selected: selectedShotId === shot.id }"
                @click="selectShot(shot.id)"
              >
                <div class="shot-head"><b>{{ shot.order }}. {{ shot.title }}</b><el-tag size="small">{{ shot.durationSeconds }}s</el-tag></div>
                <p>{{ shot.direction }}</p>
                <footer>
                  <span>{{ shot.anchorMode }} · {{ shot.status }}</span>
                  <span>
                    <el-button text size="small" @click.stop="moveShot(scene, shotIndex, -1)">↑</el-button>
                    <el-button text size="small" @click.stop="moveShot(scene, shotIndex, 1)">↓</el-button>
                    <el-button text size="small" @click.stop="editShot(scene.id, shot)">编辑</el-button>
                    <el-button text size="small" type="danger" @click.stop="removeShot(shot)">删除</el-button>
                  </span>
                </footer>
              </article>
            </div>
          </section>
        </template>
      </main>

      <aside class="inspector panel">
        <template v-if="selectedShot">
          <div class="panel-title">镜头详情</div>
          <h3>{{ selectedShot.title }}</h3>
          <p class="direction">{{ selectedShot.direction }}</p>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="时长">{{ selectedShot.durationSeconds }} 秒</el-descriptions-item>
            <el-descriptions-item label="锚点">{{ selectedShot.anchorMode }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ selectedShot.status }}</el-descriptions-item>
          </el-descriptions>
          <div class="inspector-actions">
            <el-button @click="showPrompt">查看最终 Prompt</el-button>
            <el-button v-if="selectedShot.anchorMode === 'generate'" @click="generate('anchor')">生成锚点</el-button>
            <el-button type="primary" @click="generate('video')">生成视频片段</el-button>
          </div>
          <el-collapse>
            <el-collapse-item title="素材绑定" name="refs">
              <el-select v-model="referenceForm.assetId" filterable placeholder="选择素材">
                <el-option v-for="asset in selectableAssets" :key="asset.id" :label="`${String(asset.metadata.referenceRole ?? asset.role)} · ${String(asset.metadata.usage ?? asset.status)} · ${asset.semanticKey ?? asset.id.slice(0,8)}`" :value="asset.id" />
              </el-select>
              <div class="binding-row">
                <el-select v-model="referenceForm.usage"><el-option label="生成参考" value="generation_reference" /><el-option label="最终锚点" value="approved_anchor" /></el-select>
                <el-select v-model="referenceForm.role"><el-option v-for="item in ['identity','style','scene','prop','composition']" :key="item" :label="item" :value="item" /></el-select>
                <el-select v-model="referenceForm.applyTo"><el-option label="锚点" value="anchor" /><el-option label="视频" value="video" /><el-option label="两者" value="both" /></el-select>
              </div>
              <el-button size="small" @click="bindReference">加入镜头</el-button>
              <ul>
                <li v-for="item in selectedShot.referenceBindings" :key="item.assetId">
                  {{ item.usage }} / {{ item.role }} / {{ item.applyTo }}
                  <el-button text type="danger" size="small" @click="removeBinding(item.assetId)">移除</el-button>
                </li>
              </ul>
            </el-collapse-item>
            <el-collapse-item title="Prompt 与 Provider 尝试" name="trace">
              <div v-for="attempt in selectedShot.attempts" :key="attempt.id" class="attempt">
                <b>#{{ attempt.attempt }} {{ attempt.operationKey }}</b>
                <span>{{ attempt.status }} · {{ attempt.provider || '本地' }} · {{ attempt.providerTaskId || '未创建 Task' }}</span>
                <pre v-if="attempt.prompt">{{ attempt.prompt.text }}</pre>
                <details>
                  <summary>输入快照</summary>
                  <pre>{{ JSON.stringify(attempt.inputSnapshot, null, 2) }}</pre>
                </details>
                <details v-if="attempt.reviews.length">
                  <summary>AI建议与人工审核证据</summary>
                  <pre>{{ JSON.stringify(attempt.reviews, null, 2) }}</pre>
                </details>
                <el-alert v-if="attempt.error" type="error" :title="String(attempt.error.message ?? attempt.error.code)" :closable="false" />
                <el-button
                  v-if="['queued','running'].includes(attempt.status) && attempt.providerTaskId"
                  size="small"
                  @click="resumeAttempt(attempt.id)"
                >继续查询原任务</el-button>
                <el-button
                  v-if="attempt.status === 'submission_unknown' && attempt.kind === 'video'"
                  size="small"
                  type="warning"
                  @click="reconcileAttempt(attempt.id)"
                >查询候选并对账</el-button>
                <el-alert
                  v-else-if="attempt.status === 'submission_unknown'"
                  type="warning"
                  title="同步请求结果未知，不能查询原任务或直接重提；请先在供应商账单中人工核对。"
                  :closable="false"
                />
              </div>
            </el-collapse-item>
          </el-collapse>
          <div v-if="promptPreview" class="prompt-preview"><b>当前编译 Prompt · {{ promptPreview.charCount }} 字</b><pre>{{ promptPreview.prompt }}</pre></div>
          <div class="versions">
            <h4>媒体版本</h4>
            <div
              v-for="asset in selectedShot.assets.filter(item => ['shot_anchor','shot_video','shot_video_edit'].includes(item.role))"
              :key="asset.id"
              class="version-card"
            >
              <img v-if="asset.mediaType === 'image'" :src="assetContentUrl(asset.id)" />
              <video v-else controls :src="assetContentUrl(asset.id)" />
              <span>{{ asset.role }} · {{ asset.status }}</span>
              <div v-if="asset.status === 'candidate'">
                <el-button size="small" type="success" @click="review(asset, 'approved')">批准并选择</el-button>
                <el-button size="small" type="danger" @click="review(asset, 'rejected')">拒绝</el-button>
              </div>
              <el-button
                v-else-if="asset.mediaType === 'video' && asset.status === 'approved' && asset.id !== selectedShot.selectedVideoAssetId"
                size="small"
                @click="selectVideoVersion(asset)"
              >选择此历史版本</el-button>
            </div>
          </div>
        </template>
        <div v-else class="empty-state"><p>选择一个镜头卡查看 Prompt、素材、任务和版本。</p></div>
      </aside>
    </div>

    <section v-if="graph?.sequences.length" class="sequence-panel panel">
      <div class="sequence-heading">
        <div>
          <h3>项目总片版本</h3>
          <p>总片只引用已批准镜头片段；每次合成创建新的 EDL Revision，不覆盖镜头原文件。</p>
        </div>
      </div>
      <div class="sequence-grid">
        <article
          v-for="sequence in graph.sequences"
          :key="sequence.id"
          class="sequence-card"
          :class="{ selected: selectedSequenceId === sequence.id || graph.project.selectedSequenceId === sequence.id }"
        >
          <button class="sequence-open" @click="showSequence(sequence.id)">
            <b>Revision {{ sequence.revision }}</b>
            <span>{{ (sequence.plan.duration_ms / 1000).toFixed(2) }}s · {{ sequence.status }}</span>
          </button>
          <div>
            <el-button
              v-if="sequence.status === 'content_review'"
              size="small"
              type="success"
              @click="decideSequence(sequence, true)"
            >批准并设为总片</el-button>
            <el-button
              v-if="sequence.status === 'content_review'"
              size="small"
              type="danger"
              @click="decideSequence(sequence, false)"
            >拒绝</el-button>
            <el-button
              v-if="sequence.status === 'approved' && graph.project.selectedSequenceId !== sequence.id"
              size="small"
              @click="decideSequence(sequence, true)"
            >回退到此版本</el-button>
          </div>
        </article>
      </div>
    </section>

    <VideoTimeline
      v-if="selectedShot && selectedVideo && !selectedSequence"
      :shot-id="selectedShot.id"
      :asset-id="selectedVideo.id"
      :src="assetContentUrl(selectedVideo.id)"
      :duration-ms="selectedVideoDurationMs"
      :frames="selectedVideoFrames"
      :markers-ms="selectedVideoMarkersMs"
      @completed="loadGraph()"
    />
    <section v-else-if="selectedSequence && selectedSequenceAsset" class="master-timeline panel">
      <div>
        <h3>总片 Revision {{ selectedSequence.revision }}</h3>
        <p>单轨 EDL 由已批准镜头片段依序组成；需要修改某段时，请返回对应镜头卡重做或区间重拍后重新合成。</p>
      </div>
      <video controls :src="assetContentUrl(selectedSequenceAsset.id)" />
      <details>
        <summary>查看 EDL</summary>
        <pre>{{ JSON.stringify(selectedSequence.plan, null, 2) }}</pre>
      </details>
    </section>

    <el-dialog v-model="createVisible" title="新建镜头片段项目" width="620px">
      <el-form label-position="top">
        <el-form-item label="项目标题"><el-input v-model="createForm.title" placeholder="例如：池塘边钓鱼" /></el-form-item>
        <el-form-item label="第一场景标题"><el-input v-model="createForm.sceneTitle" /></el-form-item>
        <el-form-item label="第一段原始剧本"><el-input v-model="createForm.sourceText" type="textarea" :rows="8" placeholder="直接粘贴一段完整场景故事。创建项目本身不会调用 Ark。" /></el-form-item>
        <el-form-item label="可选参考图片"><input type="file" accept="image/*" @change="chooseCreateReference" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" @click="createProject">创建项目</el-button></template>
    </el-dialog>

    <el-dialog v-model="projectSettingsVisible" title="项目设置" width="520px">
      <el-form label-position="top">
        <el-form-item label="项目标题"><el-input v-model="projectSettingsForm.title" /></el-form-item>
        <el-form-item label="内容日期"><el-date-picker v-model="projectSettingsForm.contentDate" type="date" value-format="YYYY-MM-DD" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="projectSettingsVisible = false">取消</el-button><el-button type="primary" @click="saveProjectSettings">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="sceneVisible" :title="sceneForm.id ? '编辑场景' : '添加场景'" width="620px">
      <el-form label-position="top">
        <el-form-item label="场景标题"><el-input v-model="sceneForm.title" /></el-form-item>
        <el-form-item label="可选章节标签"><el-input v-model="sceneForm.chapterLabel" placeholder="例如：上午、河边、归家；仅作为文字标签" /></el-form-item>
        <el-form-item label="原始剧本"><el-input v-model="sceneForm.sourceText" type="textarea" :rows="7" /></el-form-item>
        <el-form-item label="可选上下文备注"><el-input v-model="sceneForm.contextNote" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="sceneVisible = false">取消</el-button><el-button type="primary" @click="saveScene">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="shotVisible" :title="shotForm.id ? '编辑镜头卡' : '手工添加镜头卡'" width="720px">
      <el-form label-position="top">
        <el-form-item label="镜头标题"><el-input v-model="shotForm.title" /></el-form-item>
        <el-form-item label="完整镜头描述"><el-input v-model="shotForm.direction" type="textarea" :rows="9" /></el-form-item>
        <div class="binding-row">
          <el-form-item label="时长"><el-input-number v-model="shotForm.durationSeconds" :min="8" :max="15" /></el-form-item>
          <el-form-item label="锚点方式"><el-select v-model="shotForm.anchorMode"><el-option label="纯文本直出" value="text_only" /><el-option label="使用已有图片（先在右侧绑定最终锚点）" value="existing" :disabled="!shotForm.referenceBindings.some(item => item.usage === 'approved_anchor')" /><el-option label="生成新锚点" value="generate" /></el-select></el-form-item>
        </div>
      </el-form>
      <template #footer><el-button @click="shotVisible = false">取消</el-button><el-button type="primary" @click="saveShot">保存镜头卡</el-button></template>
    </el-dialog>

    <el-dialog :model-value="Boolean(suggestion)" title="AI 镜头建议（确认后才写入场景）" width="720px" @update:model-value="closeSuggestion">
      <div v-if="suggestion">
        <p>场景：{{ suggestion.output.sceneTitle }}</p>
        <pre>{{ JSON.stringify(suggestion.output.shots, null, 2) }}</pre>
      </div>
      <template #footer><el-button @click="suggestion = null">取消</el-button><el-button type="primary" @click="acceptSuggestion">接受并建立镜头卡</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.studio { min-height: 100%; background: #0d1016; color: #e8eaf0; padding: 22px; }
.studio-header, .queue-heading, .scene-card > header, .shot-head, .shot-card footer { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.studio-header h1, .queue-heading h2 { margin: 0; }.studio-header p { color: #9299a8; margin: 6px 0 0; }
.persistent-alert { margin: 16px 0; }.workspace-grid { display: grid; grid-template-columns: 220px minmax(520px, 1fr) 390px; gap: 14px; margin-top: 18px; align-items: start; }
.panel { background: #151922; border: 1px solid #292f3b; border-radius: 12px; }.project-rail, .inspector { padding: 14px; position: sticky; top: 12px; max-height: calc(100vh - 40px); overflow: auto; }.queue { padding: 18px; min-height: 650px; }
.panel-title { color: #8c95a7; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 10px; }.reference-title { margin-top: 22px; }
.project-item { display: flex; flex-direction: column; width: 100%; color: #d9dde7; background: transparent; border: 0; border-radius: 8px; text-align: left; padding: 10px; cursor: pointer; }.project-item:hover, .project-item.active { background: #232a36; }.project-item span { color: #80899a; font-size: 12px; margin-top: 4px; }
.scene-card { border-top: 1px solid #2b313d; padding: 18px 0; }.scene-card small { color: #7d8798; margin-left: 8px; }.order-chip { color: #68a8ff; margin-right: 10px; }.source-text { color: #aeb5c3; line-height: 1.7; white-space: pre-wrap; }.scene-actions { margin: 12px 0; }
.shots { display: grid; gap: 10px; }.shot-card { padding: 14px; background: #10141b; border: 1px solid #292f3b; border-radius: 10px; cursor: pointer; }.shot-card.selected { border-color: #4d96ff; box-shadow: 0 0 0 1px #4d96ff55; }.shot-card p, .direction { color: #b6bdca; font-size: 13px; line-height: 1.65; white-space: pre-wrap; }.shot-card footer { color: #768092; font-size: 12px; }
.inspector h3 { margin: 4px 0 8px; }.inspector-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }.binding-row { display: flex; gap: 8px; margin: 8px 0; }.attempt { padding: 10px 0; border-bottom: 1px solid #292f3b; display: grid; gap: 5px; }.attempt span { color: #8992a3; font-size: 12px; } pre { white-space: pre-wrap; word-break: break-word; max-height: 320px; overflow: auto; background: #0c0f15; padding: 10px; border-radius: 8px; color: #cdd3dd; }
.asset-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; margin-top: 10px; }.asset-strip img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 5px; }.version-card { border: 1px solid #2b313d; border-radius: 8px; padding: 8px; margin: 8px 0; display: grid; gap: 6px; }.version-card img, .version-card video { width: 100%; max-height: 240px; object-fit: contain; background: #090b0f; }.empty-state { text-align: center; color: #8f98a7; padding: 80px 20px; }
.scene-attempts { margin: 10px 0; }.attempt details summary, .master-timeline summary { color: #8fa7c9; cursor: pointer; font-size: 12px; }.sequence-panel, .master-timeline { margin-top: 16px; padding: 16px; }.sequence-heading h3, .master-timeline h3 { margin: 0; }.sequence-heading p, .master-timeline p { color: #9299a8; }.sequence-grid { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }.sequence-card { border: 1px solid #2b313d; border-radius: 9px; padding: 10px; display: grid; gap: 8px; }.sequence-card.selected { border-color: #4d96ff; }.sequence-open { border: 0; background: transparent; color: #e8eaf0; text-align: left; cursor: pointer; display: grid; gap: 4px; }.sequence-open span { color: #8490a3; font-size: 12px; }.master-timeline video { width: 100%; max-height: 560px; background: #080a0e; }
@media (max-width: 1280px) { .workspace-grid { grid-template-columns: 190px 1fr; }.inspector { position: static; grid-column: 1 / -1; max-height: none; } }
</style>
