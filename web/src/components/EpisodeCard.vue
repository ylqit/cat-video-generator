<script setup lang="ts">
import { ElMessage } from "element-plus";
import { computed, ref } from "vue";

import { api, ApiError } from "../api/client";
import type {
  AssetDto,
  EpisodeDto,
  PromptDto,
  ReviewDto,
  StepDto,
} from "../api/types";
import { useCanonStore } from "../stores/canon";
import AssetReviewPanel from "./AssetReviewPanel.vue";
import AssetThumb from "./AssetThumb.vue";
import GenerateButton from "./GenerateButton.vue";
import PromptCollapse from "./PromptCollapse.vue";
import StatusBadge from "./StatusBadge.vue";

const props = defineProps<{
  runId: string;
  episode: EpisodeDto;
  assets: AssetDto[];
  steps: StepDto[];
  prompts: PromptDto[];
  reviews: ReviewDto[];
}>();
const emit = defineEmits<{ changed: [] }>();

const canon = useCanonStore();

const SLOT_LABEL: Record<string, string> = {
  morning: "早晨",
  noon: "中午",
  evening: "傍晚",
};

/** Episode状态机顺序，用于进度条定位。 */
const STATUS_FLOW = [
  "planned",
  "preparing_visuals",
  "video_pending",
  "video_generating",
  "media_qc",
  "content_review",
  "ready",
];

/** 参考资产上传可用的role与对应文件类型说明。 */
const REFERENCE_ROLE_LABEL: Record<string, string> = {
  element: "元素参考图",
  scene: "场景参考图",
  motion: "动作参考视频",
  atmosphere: "氛围参考音频",
};
const REFERENCE_ACCEPT: Record<string, string> = {
  element: "image/*",
  scene: "image/*",
  motion: "video/*",
  atmosphere: "audio/*",
};

const episodeAssets = computed(() =>
  props.assets.filter((asset) => asset.episodeId === props.episode.id),
);
const frameAssets = computed(() =>
  episodeAssets.value.filter((asset) =>
    ["first_frame", "last_frame"].includes(asset.role),
  ),
);
const videoAsset = computed(() =>
  [...episodeAssets.value].reverse().find((asset) => asset.role === "video"),
);
const referenceAssets = computed(() =>
  episodeAssets.value.filter((asset) => asset.role in REFERENCE_ROLE_LABEL),
);
const episodeSteps = computed(() =>
  props.steps.filter((step) => step.episodeId === props.episode.id),
);
const stepIds = computed(() => new Set(episodeSteps.value.map((s) => s.id)));
const videoPrompts = computed(() =>
  props.prompts.filter(
    (prompt) => prompt.purpose === "video" && stepIds.value.has(prompt.stepId),
  ),
);
const imagePrompts = computed(() =>
  props.prompts.filter(
    (prompt) => prompt.purpose === "image" && stepIds.value.has(prompt.stepId),
  ),
);
const frozenStep = computed(() =>
  episodeSteps.value.find((step) => step.status === "submission_unknown"),
);
const videoStep = computed(() =>
  [...episodeSteps.value].reverse().find((step) => step.kind === "video"),
);
const activeIndex = computed(() => {
  const index = STATUS_FLOW.indexOf(props.episode.status);
  return index === -1 ? 0 : index;
});
const script = computed(() => props.episode.script);
const canonKeys = computed(() => {
  const style = script.value.style_context === "indoor" ? "style:indoor" : "style:outdoor";
  return ["person:front", "cat:front", "style:line_texture", style];
});

function canonAssetFor(key: string) {
  return key.includes(":")
    ? canon.bySemanticKey(key)
    : canon.latestByRole(key);
}

/** 可见世界声明但本集尚未导入的元素/场景参考。 */
const missingReferenceRoles = computed(() => {
  const requiredKeys = script.value.visible_world.entities
    .map((entity) => entity.semantic_key)
    .filter((key): key is string => Boolean(key && /^(element|scene):/.test(key)));
  const present = new Set(referenceAssets.value.map((asset) => asset.semanticKey));
  return requiredKeys.filter((key) => !present.has(key));
});

const uploadRole = ref("element");
const uploadKey = ref("");
const uploadFile = ref<File | null>(null);
const uploading = ref(false);

const replanVisible = ref(false);
const replanReason = ref("");
const replanPaid = ref(false);
const replanning = ref(false);

/** 局部重规划本集（保留其他集），需人工理由与付费授权。 */
async function submitReplan() {
  replanning.value = true;
  try {
    await api.replanEpisode(props.runId, props.episode.slot, replanReason.value, true);
    ElMessage.success("重规划任务已提交");
    replanVisible.value = false;
    replanReason.value = "";
    replanPaid.value = false;
    emit("changed");
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    replanning.value = false;
  }
}

function onReferenceFile(event: Event) {
  const input = event.target as HTMLInputElement;
  uploadFile.value = input.files?.[0] ?? null;
}

/** 导入Episode级参考资产（element/scene/motion/atmosphere）。 */
async function uploadReference() {
  if (!uploadFile.value || !uploadKey.value.trim()) {
    ElMessage.warning("请选择文件并填写语义键");
    return;
  }
  uploading.value = true;
  try {
    await api.uploadReference(
      props.episode.id,
      uploadRole.value,
      uploadKey.value.trim(),
      uploadFile.value,
    );
    ElMessage.success("参考资产已导入");
    uploadFile.value = null;
    uploadKey.value = "";
    emit("changed");
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error));
  } finally {
    uploading.value = false;
  }
}
</script>

<template>
  <el-card shadow="never" style="background: #16181d; border-color: #26282e">
    <template #header>
      <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
        <el-tag effect="dark" size="small">#{{ episode.sortOrder }}</el-tag>
        <el-tag type="info" size="small">
          {{ SLOT_LABEL[episode.slot] ?? episode.slot }}
        </el-tag>
        <strong>{{ episode.title }}</strong>
        <span class="muted">{{ script.duration_seconds }}s</span>
        <StatusBadge :status="episode.status" />
        <el-tag size="small" type="info">{{ episode.videoInputMode }}</el-tag>
        <span v-if="episode.nextAction" class="muted" style="font-size: 12px">
          下一步：{{ episode.nextAction }}
        </span>
        <div style="flex: 1" />
        <el-button size="small" @click="replanVisible = true">重规划</el-button>
        <GenerateButton
          :run-id="runId"
          :slot="episode.slot as 'morning' | 'noon' | 'evening'"
          label="生成视频"
          @submitted="emit('changed')"
        />
      </div>
    </template>

    <el-steps
      :active="activeIndex"
      align-center
      :process-status="episode.status === 'failed' ? 'error' : 'process'"
      finish-status="success"
      style="margin-bottom: 14px"
    >
      <el-step title="规划" />
      <el-step title="画面" />
      <el-step title="视频" />
      <el-step title="QC" />
      <el-step title="审核" />
      <el-step title="就绪" />
    </el-steps>

    <el-alert
      v-if="frozenStep"
      type="error"
      :closable="false"
      title="提交状态未知，已冻结"
      description="该步骤提交时断线，不确定供应商是否已受理。请先在 Ark 控制台人工对账，不要重复提交。"
      style="margin-bottom: 12px"
    />

    <div class="section">
      <div class="section-title">视频提示词（剧本）</div>
      <div><strong>主事件：</strong>{{ script.main_event }}</div>
      <div><strong>场景：</strong>{{ script.scene }}</div>
      <div>
        <strong>服饰：</strong>{{ script.appearance.description }}
        <span
          v-if="script.appearance.changes_from_previous.length"
          class="muted"
        >
          （变化：{{ script.appearance.changes_from_previous.join("、") }}；
          {{ script.appearance.change_reason }}）
        </span>
      </div>
      <ol style="margin: 6px 0; padding-left: 20px">
        <li v-for="action in [...script.actions].sort((a, b) => a.order - b.order)" :key="action.order">
          {{ action.action }}
          <span class="muted">→ {{ action.visible_result }}</span>
        </li>
      </ol>
      <div><strong>结尾：</strong>{{ script.ending }}</div>
      <PromptCollapse :prompts="videoPrompts" title="完整视频Prompt" />
      <PromptCollapse :prompts="imagePrompts" title="完整图片Prompt" />
    </div>

    <div class="section">
      <div class="section-title">出场资产</div>
      <template v-for="key in canonKeys" :key="key">
        <AssetThumb
          v-if="canonAssetFor(key)"
          :asset-id="canonAssetFor(key)!.id"
          :label="key"
        />
        <el-tag v-else type="danger" size="small" style="margin-right: 6px">
          缺少{{ key }}
        </el-tag>
      </template>
    </div>

    <div v-if="frameAssets.length" class="section">
      <div class="section-title">分镜图</div>
      <div style="display: flex; gap: 16px; flex-wrap: wrap">
        <div v-for="asset in frameAssets" :key="asset.id">
          <div class="muted" style="font-size: 12px; margin-bottom: 4px">
            {{ asset.role === "first_frame" ? "首帧" : "尾帧" }}
          </div>
          <AssetReviewPanel
            :asset="asset"
            :reviews="reviews"
            :max-width="220"
            @reviewed="emit('changed')"
          />
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">
        Episode 参考资产
        <el-tag
          v-for="role in missingReferenceRoles"
          :key="role"
          type="danger"
          size="small"
          style="margin-left: 6px"
        >
          缺少{{ role }}
        </el-tag>
      </div>
      <template v-if="referenceAssets.length">
        <AssetThumb
          v-for="asset in referenceAssets"
          :key="asset.id"
          :asset-id="asset.id"
          :label="asset.semanticKey ?? asset.role"
        />
      </template>
      <div
        style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 8px"
      >
        <el-select v-model="uploadRole" size="small" style="width: 150px">
          <el-option
            v-for="(label, key) in REFERENCE_ROLE_LABEL"
            :key="key"
            :label="label"
            :value="key"
          />
        </el-select>
        <el-input
          v-model="uploadKey"
          size="small"
          placeholder="语义键，如 element:paper_crane"
          style="width: 200px"
        />
        <input
          type="file"
          :accept="REFERENCE_ACCEPT[uploadRole]"
          style="font-size: 12px"
          @change="onReferenceFile"
        />
        <el-button
          size="small"
          :loading="uploading"
          :disabled="!uploadFile || !uploadKey.trim()"
          @click="uploadReference"
        >
          导入参考
        </el-button>
      </div>
    </div>

    <div class="section">
      <div class="section-title">视频</div>
      <div v-if="videoStep?.providerTaskId" class="muted">
        Ark任务：{{ videoStep.providerTaskId }}
        <StatusBadge :status="videoStep.status" style="margin-left: 6px" />
      </div>
      <AssetReviewPanel
        v-if="videoAsset"
        :asset="videoAsset"
        :reviews="reviews"
        @reviewed="emit('changed')"
      />
      <span v-else class="muted">尚未生成视频</span>
    </div>

    <el-dialog v-model="replanVisible" title="局部重规划本集" width="480px">
      <el-form label-width="90px">
        <el-form-item label="重规划理由" required>
          <el-input
            v-model="replanReason"
            type="textarea"
            :rows="3"
            placeholder="至少4个字；将记录到审计链，例如：午间主事件与服饰变化冲突"
          />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="replanPaid">
            <span style="color: #f56c6c">
              我已知晓重规划将产生 Ark 付费模型调用
            </span>
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="replanVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="replanning"
          :disabled="replanReason.trim().length < 4 || !replanPaid"
          @click="submitReplan"
        >
          提交重规划
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.section {
  margin-bottom: 14px;
  font-size: 13px;
  line-height: 1.7;
}
.section-title {
  color: #9ca3af;
  font-size: 12px;
  margin-bottom: 6px;
  border-left: 3px solid #409eff;
  padding-left: 8px;
}
</style>
