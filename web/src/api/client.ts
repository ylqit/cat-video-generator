import type {
  CreatorAssetDto,
  CreatorReferenceDto,
  CreatorShotDto,
  CreatorStateDto,
  CreatorStoryDto,
  CreatorTaskDto,
  CreatorTimelineClipDto,
  CreatorTimelineDto,
  GenerationSnapshotDto,
  HealthDto,
  ProjectSummary,
  RuntimeSettingsDto,
  TaskCancellationPolicyDto,
} from "./types";

const BASE = "/api/v2";
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

export class ApiTimeoutError extends Error {
  constructor(public timeoutMs: number) {
    super(`请求在 ${Math.round(timeoutMs / 1_000)} 秒内没有响应，请检查服务状态后重试`);
  }
}

export async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const externalSignal = init?.signal;
  let timedOut = false;
  const forwardAbort = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) forwardAbort();
  else externalSignal?.addEventListener("abort", forwardAbort, { once: true });
  const timeoutId = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const response = await fetch(`${BASE}${path}`, { ...init, signal: controller.signal });
    if (!response.ok) {
      let detail: unknown = response.statusText;
      try {
        const body = (await response.json()) as { detail?: unknown };
        detail = body.detail ?? detail;
      } catch {
        // Non-JSON transport failures keep the status text.
      }
      throw new ApiError(response.status, detail);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } catch (error) {
    if (timedOut) throw new ApiTimeoutError(timeoutMs);
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", forwardAbort);
  }
}

function json<T>(path: string, method: string, body?: unknown, headers?: HeadersInit) {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const creatorApi = {
  health: () => request<HealthDto>("/health"),
  runtimeSettings: () => request<RuntimeSettingsDto>("/runtime-settings"),
  projects: (signal?: AbortSignal) => request<ProjectSummary[]>(
    "/creator-projects", signal ? { signal } : undefined,
  ),
  canon: (signal?: AbortSignal) => request<{ ready: boolean; references: CreatorReferenceDto[] }>(
    "/creator-canon", signal ? { signal } : undefined,
  ),
  state: (projectId: string, signal?: AbortSignal) => request<CreatorStateDto>(
    `/projects/${projectId}/creator-state`, signal ? { signal } : undefined,
  ),
  assets: (projectId: string, signal?: AbortSignal) => request<CreatorAssetDto[]>(
    `/projects/${projectId}/creator-assets`, signal ? { signal } : undefined,
  ),
  updateState: (
    projectId: string,
    version: number,
    body: Partial<{
      briefBody: string;
      targetDurationSeconds: number;
      aspectRatio: CreatorStateDto["aspectRatio"];
      qualityTier: CreatorStateDto["qualityTier"];
      referenceBindings: CreatorReferenceDto[];
    }>,
  ) => json<CreatorStateDto>(
    `/projects/${projectId}/creator-state`, "PATCH", body, { "If-Match": String(version) },
  ),
  createProject: (body: {
    title: string;
    contentDate?: string;
    brief: {
      body: string;
      durationSeconds: number;
      aspectRatio: string;
      qualityTier: string;
    };
    references: CreatorReferenceDto[];
  }) => json<{ projectId: string; version: number; providerCallCount: number }>(
    "/creator-projects", "POST", body,
  ),
  storyCandidateSnapshot: (projectId: string, body: {
    briefBody: string;
    requestedCount: number;
  }) => json<GenerationSnapshotDto>(`/projects/${projectId}/story-candidates`, "POST", body),
  saveStory: (projectId: string, version: number, story: CreatorStoryDto) =>
    json<CreatorStateDto>(
      `/projects/${projectId}/story`, "PUT", { story }, { "If-Match": String(version) },
    ),
  shots: (projectId: string, signal?: AbortSignal) => request<CreatorShotDto[]>(
    `/projects/${projectId}/shots`, signal ? { signal } : undefined,
  ),
  replaceShots: (projectId: string, version: number, shots: Array<Partial<CreatorShotDto>>) =>
    json<{ projectVersion: number; shots: CreatorShotDto[] }>(
      `/projects/${projectId}/shots`, "PUT", { shots }, { "If-Match": String(version) },
    ),
  updateShot: (shotId: string, version: number, body: Partial<CreatorShotDto>) =>
    json<CreatorShotDto>(
      `/creator-shots/${shotId}`, "PATCH", body, { "If-Match": String(version) },
    ),
  createSnapshot: (shotId: string, body: {
    kind: GenerationSnapshotDto["kind"];
    promptText: string;
    orderedReferences: CreatorReferenceDto[];
    providerConfig: Record<string, unknown>;
  }) => json<GenerationSnapshotDto>(
    `/creator-shots/${shotId}/generation-snapshots`, "POST", body,
  ),
  submitSnapshot: (
    snapshot: GenerationSnapshotDto,
    acceptedEstimatedCostMicros: number,
    idempotencyKey: string,
  ) => json<CreatorTaskDto>(
    `/generation-snapshots/${snapshot.id}/submit`,
    "POST",
    { inputHash: snapshot.inputHash, acceptedEstimatedCostMicros },
    { "Idempotency-Key": idempotencyKey },
  ),
  decideAsset: (assetId: string, decision: "adopt" | "reject", reason?: string) =>
    json<{ assetId: string; decision: string; status: string }>(
      `/assets/${assetId}/decision`, "POST", { decision, reason },
    ),
  selectVideo: (shotId: string, version: number, assetId: string) =>
    json<CreatorShotDto>(
      `/creator-shots/${shotId}/selected-video`,
      "PUT",
      { assetId },
      { "If-Match": String(version) },
    ),
  diagnostics: (projectId: string, signal?: AbortSignal) => request<{
    projectId: string;
    items: Array<{ code: string; severity: "warning" | "blocker"; message: string }>;
    tasks: CreatorTaskDto[];
  }>(`/projects/${projectId}/diagnostics`, signal ? { signal } : undefined),
  tasks: (projectId?: string, signal?: AbortSignal) => request<CreatorTaskDto[]>(
    `/generation-tasks${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    signal ? { signal } : undefined,
  ),
  cancellation: (taskId: string) => request<TaskCancellationPolicyDto>(
    `/generation-tasks/${taskId}/cancellation`,
  ),
  cancelTask: (
    task: CreatorTaskDto,
    reason?: string,
  ) => json<CreatorTaskDto>(
    `/generation-tasks/${task.taskId}/cancellation`,
    "POST",
    {
      expectedStatus: task.status,
      expectedProviderTaskId: task.providerTaskId ?? null,
      reason,
    },
  ),
  timeline: (projectId: string, signal?: AbortSignal) => request<CreatorTimelineDto>(
    `/projects/${projectId}/timeline`, signal ? { signal } : undefined,
  ),
  saveTimeline: (projectId: string, version: number, clips: CreatorTimelineClipDto[]) =>
    json<CreatorTimelineDto>(
      `/projects/${projectId}/timeline`, "PUT", { clips }, { "If-Match": String(version) },
    ),
};

export function assetContentUrl(assetId: string): string {
  return `${BASE}/media-assets/${assetId}/content`;
}
