import type {
  CanonAsset,
  CrossSlotReferenceDto,
  EligibleCrossSlotAssetDto,
  DeliveryPackageDto,
  EpisodePromptPreview,
  EpisodeScript,
  HealthStatus,
  Job,
  JobAccepted,
  OutcomeDraftDto,
  PipelineSettings,
  PromptFull,
  ProjectOutlineDto,
  PromptOverrides,
  ReconciliationCandidateDto,
  RunCreativeControlsDto,
  RunGraph,
  RunSummary,
  StoryProjectInputDto,
  StoryProjectPreview,
  StoryConnectionDto,
  StepTraceDto,
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

export interface ProjectPayload {
  contentDate: string;
  projectInput: StoryProjectInputDto;
  creativeProfile?: {
    personPersonality?: string;
    catPersonality?: string;
    humorStyle?: string;
  };
  allowPaidGeneration: boolean;
  pipelineSettings?: PipelineSettings;
  creativeControls?: RunCreativeControlsDto;
}

export interface StoryPreviewPayload {
  text: string;
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
  stepTrace: (stepId: string) =>
    request<StepTraceDto>(`/steps/${stepId}/trace`),
  previewStoryProject: (payload: StoryPreviewPayload) =>
    post<StoryProjectPreview>("/story-projects/preview", payload),
  createProject: (payload: ProjectPayload) =>
    post<JobAccepted>("/projects", payload),
  generate: (runId: string, payload: GeneratePayload) =>
    post<JobAccepted>(`/runs/${runId}/generate`, payload),
  planSlot: (
    runId: string,
    slot: string,
    allowPaidGeneration: boolean,
    generateFromTheme = false,
  ) =>
    post<JobAccepted>(`/runs/${runId}/slots/${slot}/plan`, {
      allowPaidGeneration,
      generateFromTheme,
    }),
  updateEpisodeSource: (runId: string, slot: string, sourceText: string) =>
    request<{ runId: string; slot: string; sourceText: string; saved: boolean }>(
      `/runs/${runId}/slots/${slot}/source`,
      { method: "PUT", body: JSON.stringify({ sourceText }) },
    ),
  outcome: (runId: string, slot: string) =>
    request<OutcomeDraftDto>(`/runs/${runId}/slots/${slot}/outcome`),
  confirmOutcome: (
    runId: string,
    slot: string,
    payload: {
      summary: string;
      carryForward: string[];
      doNotCarryForward: string[];
    },
  ) =>
    request<OutcomeDraftDto>(`/runs/${runId}/slots/${slot}/outcome`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  storyConnection: (runId: string, slot: string) =>
    request<{ runId: string; slot: string; storyConnection: StoryConnectionDto | null }>(
      `/runs/${runId}/slots/${slot}/connection`,
    ),
  saveStoryConnection: (runId: string, slot: string, payload: StoryConnectionDto) =>
    request<{ runId: string; slot: string; storyConnection: StoryConnectionDto }>(
      `/runs/${runId}/slots/${slot}/connection`,
      { method: "PUT", body: JSON.stringify(payload) },
    ),
  suggestStoryConnection: (runId: string, slot: string) =>
    post<JobAccepted>(`/runs/${runId}/slots/${slot}/connection/suggest`, {
      allowPaidGeneration: true,
    }),
  crossSlotReferences: (runId: string, slot: string) =>
    request<{
      runId: string;
      slot: string;
      references: CrossSlotReferenceDto[];
      eligibleAssets: EligibleCrossSlotAssetDto[];
    }>(`/runs/${runId}/slots/${slot}/references`),
  saveCrossSlotReferences: (
    runId: string,
    slot: string,
    references: CrossSlotReferenceDto[],
  ) =>
    request<{ runId: string; slot: string; crossSlotReferences: CrossSlotReferenceDto[] }>(
      `/runs/${runId}/slots/${slot}/references`,
      { method: "PUT", body: JSON.stringify({ references }) },
    ),
  saveShotNote: (
    episodeId: string,
    payload: { assetId: string; startMs: number; endMs: number; note: string },
  ) =>
    post<{ episodeId: string; assetId: string; reviewId: string; saved: boolean }>(
      `/episodes/${episodeId}/shot-notes`,
      payload,
    ),
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
    restartFromBeginning = false,
  ) =>
    post<JobAccepted>(`/steps/${stepId}/retry`, {
      reason,
      allowPaidGeneration,
      acknowledgeDuplicateBilling,
      restartFromBeginning,
    }),
  regenerateStep: (
    stepId: string,
    payload: {
      reason: string;
      promptOverride?: string;
      allowPaidGeneration: boolean;
      acknowledgeDownstreamReplacement: boolean;
    },
  ) => post<JobAccepted>(`/steps/${stepId}/regenerate`, payload),
  videoSequences: (episodeId: string) =>
    request<import("./types").VideoSequenceDto[]>(
      `/episodes/${episodeId}/video-sequences`,
    ),
  rangeEdit: (
    episodeId: string,
    sequenceId: string,
    payload: {
      startMs: number;
      endMs: number;
      boundaryMode: "snap_to_shot" | "exact";
      instruction: string;
      allowPaidGeneration: boolean;
    },
  ) => post<JobAccepted>(
    `/episodes/${episodeId}/video-sequences/${sequenceId}/range-edits`,
    payload,
  ),
  selectVideoSequence: (
    sequenceId: string,
    payload: { revokeConfirmedOutcome: boolean; keepConfirmedOutcome: boolean },
  ) => post<Record<string, unknown>>(`/video-sequences/${sequenceId}/select`, payload),
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
    acknowledgeDownstreamReplacement = false,
  ) =>
    post<JobAccepted>(`/runs/${runId}/episodes/${slot}/replan`, {
      reason,
      allowPaidGeneration,
      acknowledgeDownstreamReplacement,
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
  getPromptPreview: (episodeId: string) =>
    request<EpisodePromptPreview>(`/episodes/${episodeId}/prompt-preview`),
  previewScriptPrompts: (episodeId: string, script: unknown) =>
    post<EpisodePromptPreview>(`/episodes/${episodeId}/prompt-preview`, script),
  savePromptOverrides: (
    episodeId: string,
    overrides: PromptOverrides,
    enabled: boolean,
  ) =>
    request<{ episodeId: string; saved: boolean }>(
      `/episodes/${episodeId}/prompt-overrides`,
      {
        method: "PUT",
        body: JSON.stringify({ overrides, enabled }),
      },
    ),
  generateVisuals: (
    episodeId: string,
    allowPaidGeneration: boolean,
  ) =>
    post<JobAccepted>(`/episodes/${episodeId}/visuals`, {
      allowPaidGeneration,
    }),
  continueRun: (runId: string) =>
    post<JobAccepted>(`/runs/${runId}/continue`),
  updateScript: (episodeId: string, script: EpisodeScript) =>
    request<{
      episodeId: string;
      saved: boolean;
      promptOverrideStale: boolean;
      suggestions: string[];
    }>(`/episodes/${episodeId}/script`, {
      method: "PUT",
      body: JSON.stringify(script),
    }),
  updateProjectOutline: (runId: string, outline: ProjectOutlineDto) =>
    request<{
      runId: string;
      saved: boolean;
      confirmed: boolean;
      episodeDraftsCleared: boolean;
    }>(
      `/runs/${runId}/project-outline`,
      { method: "PUT", body: JSON.stringify(outline) },
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
