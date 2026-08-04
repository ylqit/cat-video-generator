import type {
  CanonAsset,
  DeliveryPackageDto,
  EpisodePromptPreview,
  HealthStatus,
  Job,
  JobAccepted,
  PipelineSettings,
  PromptFull,
  PromptOverrides,
  ReconciliationCandidateDto,
  RunGraph,
  RunSummary,
} from "./types";

const BASE = "/api/v1";

/** 统一的HTTP错误，detail保留后端原始结构。 */
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(
      typeof detail === "string"
        ? detail
        : ((detail as { message?: string })?.message ??
          JSON.stringify(detail)),
    );
  }
}

async function parseError(response: Response): Promise<never> {
  let detail: unknown = response.statusText;
  try {
    const body = (await response.json()) as { detail?: unknown };
    detail = body.detail ?? detail;
  } catch {
    // 非JSON错误体时保留statusText
  }
  throw new ApiError(response.status, detail);
}

export async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!response.ok) {
    await parseError(response);
  }
  return (await response.json()) as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export interface PlanPayload {
  targetDate: string;
  planningContext?: string;
  candidateCount?: number;
  allowPaidGeneration: boolean;
  pipelineSettings?: PipelineSettings;
}

export interface GeneratePayload {
  slot: "morning" | "noon" | "evening" | null;
  allowPaidGeneration: boolean;
}

export const api = {
  listRuns: () => request<RunSummary[]>("/runs"),
  runGraph: (runId: string) => request<RunGraph>(`/runs/${runId}/graph`),
  prompt: (promptId: string) =>
    request<PromptFull>(`/prompts/${promptId}`),
  createPlan: (payload: PlanPayload) => post<JobAccepted>("/plans", payload),
  generate: (runId: string, payload: GeneratePayload) =>
    post<JobAccepted>(`/runs/${runId}/generate`, payload),
  resume: (runId: string) =>
    post<JobAccepted>(`/runs/${runId}/resume`),
  review: (assetId: string, approve: boolean, reason: string) =>
    post<{ reviewId: string; decision: string }>(
      `/assets/${assetId}/review`,
      { approve, reason },
    ),
  deliver: (runId: string) =>
    post<{
      deliveryPackageId: string;
      revision: number;
      localPath: string;
      manifestSha256: string;
    }>(`/runs/${runId}/deliver`),
  listCanon: () => request<CanonAsset[]>("/canon"),
  uploadCanon: async (
    role: string,
    semanticKey: string,
    view: string | null,
    file: File,
  ) => {
    const form = new FormData();
    form.append("role", role);
    form.append("semantic_key", semanticKey);
    if (view !== null) {
      form.append("view", view);
    }
    form.append("file", file);
    const response = await fetch(`${BASE}/canon`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      await parseError(response);
    }
    return (await response.json()) as { assetId: string; role: string };
  },
  listDeliveries: (runId: string) =>
    request<DeliveryPackageDto[]>(`/runs/${runId}/deliveries`),
  deliveryManifest: (packageId: string) =>
    request<Record<string, unknown>>(`/deliveries/${packageId}/manifest`),
  job: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  listJobs: () => request<Job[]>("/jobs"),
  health: () => request<HealthStatus>("/health"),
  retryStep: (
    stepId: string,
    reason: string,
    allowPaidGeneration: boolean,
    acknowledgeDuplicateBilling = false,
  ) =>
    post<JobAccepted>(`/steps/${stepId}/retry`, {
      reason,
      allowPaidGeneration,
      acknowledgeDuplicateBilling,
    }),
  resumeStep: (stepId: string) =>
    post<JobAccepted>(`/steps/${stepId}/resume`),
  reconciliationCandidates: (stepId: string) =>
    request<ReconciliationCandidateDto[]>(
      `/steps/${stepId}/reconciliation-candidates`,
    ),
  reconcileStep: (stepId: string, providerTaskId: string) =>
    post<JobAccepted>(`/steps/${stepId}/reconcile`, { providerTaskId }),
  resumePlanning: (runId: string, allowPaidGeneration: boolean) =>
    post<JobAccepted>(`/runs/${runId}/resume-planning`, {
      allowPaidGeneration,
    }),
  replanEpisode: (
    runId: string,
    slot: string,
    reason: string,
    allowPaidGeneration: boolean,
  ) =>
    post<JobAccepted>(`/runs/${runId}/episodes/${slot}/replan`, {
      reason,
      allowPaidGeneration,
    }),
  uploadReference: async (
    episodeId: string,
    role: string,
    semanticKey: string,
    file: File,
  ) => {
    const form = new FormData();
    form.append("role", role);
    form.append("semantic_key", semanticKey);
    form.append("file", file);
    const response = await fetch(`${BASE}/episodes/${episodeId}/references`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      await parseError(response);
    }
    return (await response.json()) as { assetId: string; role: string };
  },
  deriveCrop: (
    assetId: string,
    payload: {
      role: string;
      semanticKey: string;
      box?: [number, number, number, number];
      subjectFree?: boolean;
      view?: string;
    },
  ) => post<{ assetId: string }>(`/canon/${assetId}/derive-crop`, payload),
  getPromptPreview: (episodeId: string, resolution: "480p" | "720p" = "480p") =>
    request<EpisodePromptPreview>(
      `/episodes/${episodeId}/prompt-preview?resolution=${resolution}`,
    ),
  savePromptOverrides: (episodeId: string, overrides: PromptOverrides) =>
    request<{ episodeId: string; saved: boolean }>(
      `/episodes/${episodeId}/prompt-overrides`,
      {
        method: "PUT",
        body: JSON.stringify({ overrides }),
      },
    ),
  generateStoryboard: (
    episodeId: string,
    allowPaidGeneration: boolean,
    overrides?: PromptOverrides,
  ) =>
    post<JobAccepted>(`/episodes/${episodeId}/storyboards`, {
      allowPaidGeneration,
      overrides: overrides ?? null,
    }),
  continueRun: (runId: string) =>
    post<JobAccepted>(`/runs/${runId}/continue`),
  updateScript: (episodeId: string, script: Record<string, unknown>) =>
    request<{
      episodeId: string;
      saved: boolean;
      promptOverridesKept: boolean;
    }>(`/episodes/${episodeId}/script`, {
      method: "PUT",
      body: JSON.stringify(script),
    }),
  updateDayBrief: (runId: string, brief: Record<string, unknown>) =>
    request<{ runId: string; saved: boolean; episodeDraftsCleared: boolean }>(
      `/runs/${runId}/day-brief`,
      { method: "PUT", body: JSON.stringify(brief) },
    ),
  savePipelineSettings: (runId: string, settings: PipelineSettings) =>
    request<{ runId: string; pipelineSettings: PipelineSettings }>(
      `/runs/${runId}/pipeline-settings`,
      { method: "PUT", body: JSON.stringify(settings) },
    ),
};

export function assetContentUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}/content`;
}
