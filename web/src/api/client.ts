import type {
  CanonAsset,
  DeliveryPackageDto,
  EpisodePromptPreview,
  Job,
  JobAccepted,
  PromptFull,
  PromptOverrides,
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
  autoGenerateKeyframes?: boolean;
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
  health: () => request<Record<string, unknown>>("/health"),
  retryStep: (
    stepId: string,
    reason: string,
    allowPaidGeneration: boolean,
  ) =>
    post<JobAccepted>(`/steps/${stepId}/retry`, {
      reason,
      allowPaidGeneration,
    }),
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
  generateKeyframes: (
    episodeId: string,
    allowPaidGeneration: boolean,
    overrides?: PromptOverrides,
  ) =>
    post<JobAccepted>(`/episodes/${episodeId}/keyframes`, {
      allowPaidGeneration,
      overrides: overrides ?? null,
    }),
};

export function assetContentUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}/content`;
}
