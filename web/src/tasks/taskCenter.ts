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

export interface TaskCenterScopeSignal {
  revision: number;
  operationKey?: string;
}

export interface WorkspaceRefreshRequest {
  revision: number;
  projectId: string;
  shotId?: string;
}

const STORAGE_KEY = "cvg.v5.task-center";
const NOTIFICATIONS_KEY = "cvg.v5.task-notifications";
const ACTIVE_INTERVAL_MS = 4_000;
const IDLE_INTERVAL_MS = 25_000;
const activeStatuses = new Set<TaskCenterStatus>([
  "queued", "pending", "submitting", "running", "restart_pending",
]);
const knownStatuses = new Set<TaskCenterStatus>([
  ...activeStatuses,
  "awaiting_review", "succeeded", "failed", "submission_unknown", "cancelled",
]);
const items = ref<TaskCenterItem[]>(loadStoredItems());
const notifiedTerminalEvents = new Set<string>(loadNotifiedTerminalEvents());
const lastNotification = ref<TaskCenterEvent | null>(null);
const projectSignals = ref<Record<string, TaskCenterScopeSignal>>({});
const sceneSignals = ref<Record<string, TaskCenterScopeSignal>>({});
const shotSignals = ref<Record<string, TaskCenterScopeSignal>>({});
const workspaceRefreshRequest = ref<WorkspaceRefreshRequest | null>(null);
const connectionError = ref("");
const lastResumeAt = new Map<string, number>();
let timer: number | undefined;
let refreshing = false;
let hydrated = false;
let started = false;

const orderedItems = computed(() => [...items.value].sort((left, right) => {
  const leftActionable = activeStatuses.has(left.status) || left.status === "awaiting_review";
  const rightActionable = activeStatuses.has(right.status) || right.status === "awaiting_review";
  if (leftActionable !== rightActionable) return leftActionable ? -1 : 1;
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

function loadNotifiedTerminalEvents(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(NOTIFICATIONS_KEY) ?? "[]") as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

function persist() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items.value.slice(0, 100)));
  window.sessionStorage.setItem(
    NOTIFICATIONS_KEY,
    JSON.stringify([...notifiedTerminalEvents].slice(-200)),
  );
}

function normalizedStatus(value: string): TaskCenterStatus {
  return knownStatuses.has(value as TaskCenterStatus)
    ? value as TaskCenterStatus
    : "running";
}

function materialSignature(item: TaskCenterItem): string {
  return JSON.stringify({
    jobId: item.jobId,
    stepId: item.stepId,
    status: item.status,
    operationKey: item.operationKey,
    attempt: item.attempt,
    model: item.model,
    providerTaskId: item.providerTaskId,
    result: item.result,
    error: item.error,
  });
}

function bumpSignal(
  target: typeof projectSignals,
  id: string | undefined,
  operationKey: string | undefined,
) {
  if (!id) return;
  const current = target.value[id];
  target.value = {
    ...target.value,
    [id]: {
      revision: (current?.revision ?? 0) + 1,
      operationKey,
    },
  };
}

function signalMaterialChange(item: TaskCenterItem) {
  bumpSignal(projectSignals, item.projectId, item.operationKey);
  bumpSignal(sceneSignals, item.sceneId, item.operationKey);
  bumpSignal(shotSignals, item.shotId, item.operationKey);
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
  const index = items.value.findIndex((item) => item.key === next.key || (
    next.stepId
    && item.stepId === next.stepId
    && (!item.operationKey || !next.operationKey || item.operationKey === next.operationKey)
  ));
  const previous = index < 0 ? null : items.value[index];
  const merged = previous ? { ...previous, ...next } : next;
  const materialChanged = !previous
    || materialSignature(previous) !== materialSignature(merged);
  if (index < 0) items.value.push(merged);
  else items.value.splice(index, 1, merged);
  if (materialChanged) signalMaterialChange(merged);
  if (previous && previous.status !== merged.status) {
    const event = { item: merged, previousStatus: previous.status };
    const terminal = ["awaiting_review", "succeeded", "failed", "submission_unknown"].includes(
      merged.status,
    );
    const fingerprint = `${merged.stepId ?? merged.jobId ?? merged.key}:${merged.status}`;
    if (
      hydrated
      && terminal
      && activeStatuses.has(previous.status)
      && !notifiedTerminalEvents.has(fingerprint)
    ) {
      notifiedTerminalEvents.add(fingerprint);
      lastNotification.value = event;
    }
  }
}

function mergeRuntimeJob(job: JobDto) {
  const result = job.result && typeof job.result === "object"
    ? job.result as Record<string, unknown>
    : {};
  const stepId = typeof result.stepId === "string" ? result.stepId : job.context.stepId;
  const existing = items.value.find((item) => item.jobId === job.jobId)
    ?? items.value.find((item) => Boolean(stepId)
      && item.stepId === stepId
      && (!item.operationKey || item.operationKey === job.context.operationKey));
  updateItem({
    key: existing?.key ?? `job:${job.jobId}`,
    jobId: job.jobId,
    stepId: stepId ?? existing?.stepId,
    kind: existing?.kind ?? job.kind,
    label: existing?.label ?? taskKindLabel(job.kind),
    status: runtimeResultStatus(job),
    projectId: existing?.projectId ?? job.context.projectId,
    sceneId: existing?.sceneId ?? job.context.sceneId,
    shotId: existing?.shotId ?? job.context.shotId,
    operationKey: job.context.operationKey ?? existing?.operationKey,
    result: job.result,
    error: job.error,
    createdAt: job.createdAt ?? existing?.createdAt,
    updatedAt: new Date().toISOString(),
    source: "runtime",
  });
}

function mergeWorkflowTask(task: PersistentTaskDto, activeRuntimeIds: ReadonlySet<string>) {
  const existing = items.value.find((item) => item.stepId === task.stepId
    && (!item.operationKey || item.operationKey === task.operationKey))
    ?? items.value.find((item) => !item.stepId
      && item.projectId === task.projectId
      && item.sceneId === (task.sceneId ?? undefined)
      && item.shotId === (task.shotId ?? undefined)
      && item.operationKey === task.operationKey
      && activeStatuses.has(item.status));
  const durableStatus = normalizedStatus(task.status);
  const hasRuntime = Boolean(existing?.jobId && activeRuntimeIds.has(existing.jobId));
  updateItem({
    key: existing?.key ?? `step:${task.stepId}`,
    jobId: existing?.jobId,
    stepId: task.stepId,
    kind: task.kind,
    label: operationLabel(task.operationKey),
    status: activeStatuses.has(durableStatus) && !hasRuntime ? "restart_pending" : durableStatus,
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

function scheduleNextRefresh() {
  if (!started || typeof window === "undefined") return;
  if (timer !== undefined) window.clearTimeout(timer);
  if (document.hidden) {
    timer = undefined;
    return;
  }
  timer = window.setTimeout(
    () => void refreshTaskCenter(),
    activeCount.value > 0 ? ACTIVE_INTERVAL_MS : IDLE_INTERVAL_MS,
  );
}

function handleVisibilityChange() {
  if (!started || typeof window === "undefined") return;
  if (document.hidden) {
    if (timer !== undefined) window.clearTimeout(timer);
    timer = undefined;
  } else {
    void refreshTaskCenter();
  }
}

export function registerTask(jobId: string, options: RegisterTaskOptions) {
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

export function requestWorkspaceRefresh(projectId: string, shotId?: string) {
  workspaceRefreshRequest.value = {
    revision: (workspaceRefreshRequest.value?.revision ?? 0) + 1,
    projectId,
    shotId,
  };
}

export async function refreshTaskCenter() {
  if (refreshing) return;
  refreshing = true;
  try {
    const payload = await api.taskCenter();
    const runtimeIds = new Set(payload.runtimeJobs.map((job) => job.jobId));
    const activeRuntimeIds = new Set(payload.runtimeJobs.flatMap((job) =>
      ["queued", "running"].includes(job.status) ? [job.jobId] : [],
    ));
    const activeRuntimeStepIds = new Set(payload.runtimeJobs.flatMap((job) =>
      ["queued", "running"].includes(job.status) && job.context.stepId
        ? [job.context.stepId]
        : [],
    ));
    payload.runtimeJobs.forEach(mergeRuntimeJob);
    payload.persistentTasks.forEach((task) => mergeWorkflowTask(task, activeRuntimeIds));

    const serverStepIds = new Set(payload.persistentTasks.map((task) => task.stepId));
    items.value = items.value.filter((item) => {
      if (item.source === "workflow") return Boolean(item.stepId && serverStepIds.has(item.stepId));
      if (!item.jobId || runtimeIds.has(item.jobId)) return true;
      return Boolean(item.stepId && serverStepIds.has(item.stepId));
    });

    const now = Date.now();
    const resumable = payload.persistentTasks.filter((task) =>
      ["queued", "running"].includes(task.status)
      && Boolean(task.providerTaskId)
      && !activeRuntimeStepIds.has(task.stepId)
      && now - (lastResumeAt.get(task.stepId) ?? 0) >= 30_000,
    );
    await Promise.all(resumable.map(async (task) => {
      lastResumeAt.set(task.stepId, now);
      try {
        const submitted = await api.resumeStep(task.stepId);
        const existing = items.value.find((item) => item.stepId === task.stepId);
        if (existing) {
          updateItem({ ...existing, jobId: submitted.jobId, status: "running", updatedAt: new Date().toISOString() });
        }
      } catch {
        // Resume queries the existing provider task and never resubmits unknown work.
      }
    }));
    connectionError.value = "";
    persist();
    hydrated = true;
  } catch (error) {
    connectionError.value = error instanceof Error ? error.message : String(error);
  } finally {
    refreshing = false;
    scheduleNextRefresh();
  }
}

export function startTaskCenter() {
  if (started || typeof window === "undefined") return;
  started = true;
  document.addEventListener("visibilitychange", handleVisibilityChange);
  void refreshTaskCenter();
}

export function stopTaskCenter() {
  if (!started || typeof window === "undefined") return;
  started = false;
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  if (timer !== undefined) window.clearTimeout(timer);
  timer = undefined;
}

export function clearCompletedTasks() {
  items.value = items.value.filter((item) => activeStatuses.has(item.status)
    || item.status === "awaiting_review"
    || item.status === "submission_unknown");
  persist();
}

export function taskKindLabel(kind: string): string {
  return ({
    story_expansion: "剧情扩写",
    story_diagnosis: "剧情诊断",
    story_rewrite: "剧情重写",
    shot_suggestions: "分镜导演",
    visual_asset_plan: "视觉资产规划",
    shot_assistance: "片段视觉与 Prompt 审稿",
    generate_scene_look: "场景视觉基准",
    generate_reference_image: "视觉参考图",
    generate_anchor: "片段开场图",
    generate_video: "视频片段",
    range_edit: "区间重拍",
    build_sequence: "本地成片合成",
    resume_step: "Provider 任务恢复",
  } as Record<string, string>)[kind] ?? kind;
}

function operationLabel(operationKey: string): string {
  return ({
    "director:story-expansion": "剧情扩写",
    "director:story-diagnosis": "剧情诊断",
    "director:story-rewrite": "剧情重写",
    "director:shot-suggestions": "分镜导演",
    "director:visual-asset-plan": "视觉资产规划",
    "director:shot-assistance": "片段视觉与 Prompt 审稿",
    "image:scene-look": "场景视觉基准",
    "image:anchor": "片段开场图",
    "video:shot": "视频片段",
    "video:range-edit": "区间重拍",
  } as Record<string, string>)[operationKey]
    ?? (operationKey.startsWith("image:reference:") ? "视觉参考图" : operationKey);
}

export function useTaskCenter() {
  return {
    items: orderedItems,
    activeCount,
    attentionCount,
    lastNotification: readonly(lastNotification),
    projectSignals: readonly(projectSignals),
    sceneSignals: readonly(sceneSignals),
    shotSignals: readonly(shotSignals),
    workspaceRefreshRequest: readonly(workspaceRefreshRequest),
    connectionError: readonly(connectionError),
  };
}
