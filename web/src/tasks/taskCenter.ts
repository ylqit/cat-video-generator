import { computed, readonly, ref } from "vue";

import { api } from "../api/client";
import type { JobDto, PersistentTaskDto } from "../api/types";

export type TaskCenterStatus =
  | "queued"
  | "pending"
  | "submitting"
  | "running"
  | "awaiting_review"
  | "succeeded"
  | "failed"
  | "submission_unknown"
  | "restart_pending"
  | "cancelled";

export interface TaskCenterItem {
  key: string;
  jobId?: string;
  stepId?: string;
  kind: string;
  label: string;
  status: TaskCenterStatus;
  projectId?: string;
  sceneId?: string;
  shotId?: string;
  operationKey?: string;
  attempt?: number;
  model?: string | null;
  providerTaskId?: string | null;
  result?: unknown;
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
  updatedAt: string;
  source: "runtime" | "workflow";
}

export interface RegisterTaskOptions {
  kind: string;
  label: string;
  projectId?: string;
  sceneId?: string;
  shotId?: string;
  operationKey?: string;
}

export interface TaskCenterEvent {
  item: TaskCenterItem;
  previousStatus: TaskCenterStatus;
}

const STORAGE_KEY = "cvg.v5.task-center";
const PROJECTS_KEY = "cvg.v5.task-projects";
const activeStatuses = new Set<TaskCenterStatus>([
  "queued",
  "pending",
  "submitting",
  "running",
  "restart_pending",
]);
const knownStatuses = new Set<TaskCenterStatus>([
  ...activeStatuses,
  "awaiting_review",
  "succeeded",
  "failed",
  "submission_unknown",
  "cancelled",
]);
const items = ref<TaskCenterItem[]>(loadStoredItems());
const knownProjectIds = new Set<string>(loadStoredProjects());
const revision = ref(0);
const lastEvent = ref<TaskCenterEvent | null>(null);
const connectionError = ref("");
let timer: number | undefined;
let refreshing = false;
const lastResumeAt = new Map<string, number>();

const orderedItems = computed(() => [...items.value].sort((left, right) => {
  const leftActive = activeStatuses.has(left.status) || left.status === "awaiting_review";
  const rightActive = activeStatuses.has(right.status) || right.status === "awaiting_review";
  if (leftActive !== rightActive) return leftActive ? -1 : 1;
  return String(right.createdAt ?? right.updatedAt).localeCompare(
    String(left.createdAt ?? left.updatedAt),
  );
}));
const activeCount = computed(() => items.value.filter(
  (item) => activeStatuses.has(item.status),
).length);
const attentionCount = computed(() => items.value.filter(
  (item) => item.status === "awaiting_review"
    || item.status === "failed"
    || item.status === "submission_unknown",
).length);

function loadStoredItems(): TaskCenterItem[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const record = item as TaskCenterItem;
      return typeof record.key === "string" && typeof record.status === "string"
        ? [{ ...record, status: activeStatuses.has(record.status) ? "restart_pending" : record.status }]
        : [];
    }).slice(0, 100);
  } catch {
    return [];
  }
}

function loadStoredProjects(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PROJECTS_KEY) ?? "[]") as unknown;
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function persist() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items.value.slice(0, 100)));
  window.localStorage.setItem(PROJECTS_KEY, JSON.stringify([...knownProjectIds].slice(-8)));
}

function normalizedStatus(value: string): TaskCenterStatus {
  return knownStatuses.has(value as TaskCenterStatus)
    ? value as TaskCenterStatus
    : "running";
}

function runtimeResultStatus(job: JobDto): TaskCenterStatus {
  if (job.status !== "succeeded" || !job.result || typeof job.result !== "object") {
    return normalizedStatus(job.status);
  }
  const value = (job.result as Record<string, unknown>).status;
  return typeof value === "string" && knownStatuses.has(value as TaskCenterStatus)
    ? value as TaskCenterStatus
    : "succeeded";
}

function updateItem(next: TaskCenterItem) {
  const matchingIndex = items.value.findIndex(
    (item) => item.key === next.key || (next.stepId && item.stepId === next.stepId),
  );
  const previous = matchingIndex < 0 ? null : items.value[matchingIndex];
  const merged = previous ? { ...previous, ...next } : next;
  if (matchingIndex < 0) items.value.push(merged);
  else items.value.splice(matchingIndex, 1, merged);
  if (merged.stepId) {
    let kept = false;
    items.value = items.value.filter((item) => {
      if (item.stepId !== merged.stepId) return true;
      if (!kept && item.key === merged.key) {
        kept = true;
        return true;
      }
      return false;
    });
  }
  if (previous && previous.status !== merged.status) {
    revision.value += 1;
    lastEvent.value = { item: merged, previousStatus: previous.status };
  }
}

function mergeRuntimeJob(job: JobDto) {
  const contextStepId = job.context.stepId;
  const existing = items.value.find((item) => item.jobId === job.jobId)
    ?? items.value.find((item) => contextStepId && item.stepId === contextStepId);
  const result = job.result && typeof job.result === "object"
    ? job.result as Record<string, unknown>
    : {};
  updateItem({
    key: existing?.key ?? `job:${job.jobId}`,
    jobId: job.jobId,
    stepId: typeof result.stepId === "string"
      ? result.stepId
      : contextStepId ?? existing?.stepId,
    kind: existing?.kind ?? job.kind,
    label: existing?.label ?? taskKindLabel(job.kind),
    status: runtimeResultStatus(job),
    projectId: existing?.projectId ?? job.context.projectId,
    sceneId: existing?.sceneId ?? job.context.sceneId,
    shotId: existing?.shotId ?? job.context.shotId,
    operationKey: existing?.operationKey ?? job.context.operationKey,
    result: job.result,
    error: job.error,
    createdAt: job.createdAt ?? existing?.createdAt,
    updatedAt: new Date().toISOString(),
    source: "runtime",
  });
}

function mergeWorkflowTask(task: PersistentTaskDto, activeRuntimeIds: ReadonlySet<string>) {
  const existing = items.value.find((item) => item.stepId === task.stepId)
    ?? items.value.find((item) => !item.stepId
      && item.projectId === task.projectId
      && item.sceneId === (task.sceneId ?? undefined)
      && item.shotId === (task.shotId ?? undefined)
      && item.operationKey === task.operationKey
      && activeStatuses.has(item.status));
  const hasLiveRuntime = Boolean(existing?.jobId && activeRuntimeIds.has(existing.jobId));
  const workflowStatus = normalizedStatus(task.status);
  const status = activeStatuses.has(workflowStatus)
    && !hasLiveRuntime
    && (!existing || existing.status === "restart_pending")
    ? "restart_pending"
    : workflowStatus;
  updateItem({
    key: existing?.key ?? `step:${task.stepId}`,
    jobId: existing?.jobId,
    stepId: task.stepId,
    kind: existing?.kind ?? task.kind,
    label: existing?.label ?? operationLabel(task.operationKey),
    status,
    projectId: task.projectId,
    sceneId: task.sceneId ?? undefined,
    shotId: task.shotId ?? undefined,
    operationKey: task.operationKey,
    attempt: task.attempt,
    model: task.model,
    providerTaskId: task.providerTaskId,
    result: existing?.result,
    error: task.error,
    createdAt: task.createdAt,
    updatedAt: new Date().toISOString(),
    source: "workflow",
  });
}

export function registerTask(jobId: string, options: RegisterTaskOptions) {
  if (options.projectId) rememberProject(options.projectId);
  updateItem({
    key: `job:${jobId}`,
    jobId,
    kind: options.kind,
    label: options.label,
    status: "queued",
    projectId: options.projectId,
    sceneId: options.sceneId,
    shotId: options.shotId,
    operationKey: options.operationKey,
    updatedAt: new Date().toISOString(),
    createdAt: new Date().toISOString(),
    source: "runtime",
  });
  persist();
  void refreshTaskCenter();
}

export function rememberProject(projectId: string) {
  if (!projectId) return;
  knownProjectIds.delete(projectId);
  knownProjectIds.add(projectId);
  while (knownProjectIds.size > 8) knownProjectIds.delete(knownProjectIds.values().next().value!);
  persist();
}

export async function refreshTaskCenter() {
  if (refreshing) return;
  refreshing = true;
  try {
    const runtimeJobs = await api.jobs();
    const runtimeIds = new Set(runtimeJobs.map((job) => job.jobId));
    const activeRuntimeIds = new Set(runtimeJobs.flatMap((job) =>
      ["queued", "running"].includes(job.status) ? [job.jobId] : [],
    ));
    const activeRuntimeStepIds = new Set(runtimeJobs.flatMap((job) =>
      ["queued", "running"].includes(job.status) && job.context.stepId
        ? [job.context.stepId]
        : [],
    ));
    runtimeJobs.forEach(mergeRuntimeJob);
    for (const item of items.value) {
      if (item.jobId && activeStatuses.has(item.status) && !runtimeIds.has(item.jobId)) {
        updateItem({ ...item, status: "restart_pending", updatedAt: new Date().toISOString() });
      }
    }
    const taskGroups = await Promise.all(
      [...knownProjectIds].map(async (projectId) => {
        try {
          return await api.projectTasks(projectId);
        } catch {
          return [];
        }
      }),
    );
    const persistentStatuses = new Set([
      "pending",
      "submitting",
      "queued",
      "running",
      "awaiting_review",
      "submission_unknown",
    ]);
    taskGroups.flat().forEach((task) => {
      if (
        persistentStatuses.has(task.status)
        || items.value.some((item) => item.stepId === task.stepId)
      ) {
        mergeWorkflowTask(task, activeRuntimeIds);
      }
    });
    const now = Date.now();
    const resumable = taskGroups.flat().filter((task) =>
      ["queued", "running"].includes(task.status)
      && Boolean(task.providerTaskId)
      && !activeRuntimeStepIds.has(task.stepId)
      && now - (lastResumeAt.get(task.stepId) ?? 0) >= 10_000,
    );
    await Promise.all(resumable.map(async (task) => {
      lastResumeAt.set(task.stepId, now);
      try {
        const submitted = await api.resumeStep(task.stepId);
        const existing = items.value.find((item) => item.stepId === task.stepId);
        if (existing) {
          updateItem({
            ...existing,
            jobId: submitted.jobId,
            status: "running",
            updatedAt: new Date().toISOString(),
          });
        }
      } catch {
        // The durable step remains visible and will be retried after the throttle window.
      }
    }));
    connectionError.value = "";
    persist();
  } catch (error) {
    connectionError.value = error instanceof Error ? error.message : String(error);
  } finally {
    refreshing = false;
  }
}

export function startTaskCenter() {
  if (timer !== undefined || typeof window === "undefined") return;
  void refreshTaskCenter();
  timer = window.setInterval(() => void refreshTaskCenter(), 2500);
}

export function stopTaskCenter() {
  if (timer === undefined || typeof window === "undefined") return;
  window.clearInterval(timer);
  timer = undefined;
}

export function clearCompletedTasks() {
  items.value = items.value.filter(
    (item) => activeStatuses.has(item.status)
      || item.status === "awaiting_review"
      || item.status === "submission_unknown",
  );
  persist();
}

export function taskKindLabel(kind: string): string {
  return ({
    story_diagnosis: "剧情诊断",
    story_rewrite: "剧情重写",
    shot_suggestions: "分镜导演",
    shot_assistance: "片段视觉与 Prompt 审稿",
    generate_scene_look: "场景视觉基准",
    generate_anchor: "片段开场锚点",
    generate_video: "视频片段",
    range_edit: "区间重拍",
    build_sequence: "本地成片合成",
    resume_step: "Provider 任务恢复",
  } as Record<string, string>)[kind] ?? kind;
}

function operationLabel(operationKey: string): string {
  return ({
    "director:story-diagnosis": "剧情诊断",
    "director:story-rewrite": "剧情重写",
    "director:shot-suggestions": "分镜导演",
    "director:shot-assistance": "片段视觉与 Prompt 审稿",
    "image:scene-look": "场景视觉基准",
    "image:anchor": "片段开场锚点",
    "video:shot": "视频片段",
    "video:range-edit": "区间重拍",
  } as Record<string, string>)[operationKey] ?? operationKey;
}

export function useTaskCenter() {
  return {
    items: orderedItems,
    activeCount,
    attentionCount,
    revision: readonly(revision),
    lastEvent: readonly(lastEvent),
    connectionError: readonly(connectionError),
  };
}
