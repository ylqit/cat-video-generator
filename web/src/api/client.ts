import type {
  AssetDto,
  CreativeStepRecord,
  CreativeWorkflowDto,
  HealthDto,
  JobDto,
  PersistentTaskDto,
  PreviousTailStatus,
  ProductionBoardDto,
  ProjectGraph,
  ProjectSummary,
  ReferenceBinding,
  ReferenceRole,
  ReferenceUsage,
  SceneLookDraftDto,
  SceneLookDraftEnvelope,
  SceneDto,
  SceneLookPlan,
  SceneLookPromptPreview,
  SceneLookVersion,
  SequenceDto,
  SequenceTransitionDto,
  ShotAssistContext,
  ShotAssistPatch,
  ShotAssistRecord,
  ShotDto,
  ShotGenerationWorkspaceDto,
  ShotPromptPreview,
  ShotSuggestion,
  ShotSuggestionOutput,
  StoryDiagnosisOutput,
  StoryRewriteOutput,
  StoryRewriteStrategy,
  VisualProfileDraft,
  VisualProfileRevisionDto,
} from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = body.detail ?? detail;
    } catch {
      // Keep the transport status text for non-JSON errors.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  health: () => request<HealthDto>("/health"),
  projects: () => request<ProjectSummary[]>("/projects"),
  project: (id: string) => request<ProjectGraph>(`/projects/${id}`),
  productionBoard: (id: string) =>
    request<ProductionBoardDto>(`/projects/${id}/production-board`),
  createProject: (body: {
    project: { title: string; firstSceneTitle: string; firstSceneText: string };
    contentDate?: string;
  }) => json<{ projectId: string }>("/projects", "POST", body),
  updateProject: (projectId: string, body: { title: string; contentDate: string }) =>
    json<ProjectSummary>(`/projects/${projectId}`, "PATCH", body),
  updateProjectDefaultReferences: (projectId: string, references: ReferenceBinding[]) =>
    json<{ projectId: string; defaultReferenceBindings: ReferenceBinding[] }>(
      `/projects/${projectId}/default-references`,
      "PUT",
      { references },
    ),
  visualProfile: (projectId: string) =>
    request<VisualProfileRevisionDto>(`/projects/${projectId}/visual-profile`),
  updateVisualProfile: (projectId: string, draft: VisualProfileDraft) =>
    json<VisualProfileRevisionDto>(`/projects/${projectId}/visual-profile`, "PUT", draft),
  restoreProjectCanonReferences: (projectId: string) =>
    json<{
      projectId: string;
      visualProfileRevisionId: string;
      visualProfileRevision: number;
      referenceCount: number;
      cleanedShotCount: number;
    }>(`/projects/${projectId}/restore-canon-references`, "POST"),
  addScene: (projectId: string, body: Record<string, unknown>) =>
    json<SceneDto>(`/projects/${projectId}/scenes`, "POST", body),
  updateScene: (sceneId: string, body: Record<string, unknown>) =>
    json<SceneDto>(`/scenes/${sceneId}`, "PATCH", body),
  deleteScene: (sceneId: string) => request<void>(`/scenes/${sceneId}`, { method: "DELETE" }),
  reorderScenes: (projectId: string, ids: string[]) =>
    json<{ saved: boolean }>(`/projects/${projectId}/scene-order`, "PUT", { ids }),
  creativeWorkflow: (sceneId: string) =>
    request<CreativeWorkflowDto>(`/scenes/${sceneId}/creative-workflow`),
  diagnoseStory: (sceneId: string) =>
    json<{ jobId: string }>(`/scenes/${sceneId}/story-diagnoses`, "POST", {
      allowPaidGeneration: true,
    }),
  acceptStoryDiagnosis: (
    stepId: string,
    diagnosis: StoryDiagnosisOutput,
    selectedStrategy: StoryRewriteStrategy | null,
    additionalInstructions: string,
    preserveOriginal: boolean,
  ) => json<CreativeStepRecord>(`/steps/${stepId}/accept-story-diagnosis`, "POST", {
    diagnosis,
    selectedStrategy,
    additionalInstructions,
    preserveOriginal,
  }),
  rewriteStory: (sceneId: string, diagnosisStepId: string) =>
    json<{ jobId: string }>(`/scenes/${sceneId}/story-rewrites`, "POST", {
      diagnosisStepId,
      allowPaidGeneration: true,
    }),
  acceptStoryRewrite: (stepId: string, rewrite: StoryRewriteOutput) =>
    json<SceneDto>(`/steps/${stepId}/accept-story-rewrite`, "POST", { rewrite }),
  suggestShots: (sceneId: string) =>
    json<{ jobId: string }>(`/scenes/${sceneId}/shot-suggestions`, "POST", {
      allowPaidGeneration: true,
    }),
  acceptSuggestions: (
    stepId: string,
    lookPlan: SceneLookPlan | null,
    shots: ShotSuggestion[],
    applyMode: "replace" | "update_existing",
    sourceShotRevisions: Record<string, number>,
  ) => json<ShotDto[]>(`/steps/${stepId}/accept-suggestions`, "POST", {
    lookPlan,
    shots,
    applyMode,
    sourceShotRevisions,
  }),
  addShot: (sceneId: string, body: Record<string, unknown>) =>
    json<ShotDto>(`/scenes/${sceneId}/shots`, "POST", body),
  updateShot: (shotId: string, body: Record<string, unknown>) =>
    json<ShotDto>(`/shots/${shotId}`, "PATCH", body),
  deleteShot: (shotId: string) => request<void>(`/shots/${shotId}`, { method: "DELETE" }),
  reorderShots: (sceneId: string, ids: string[]) =>
    json<{ saved: boolean }>(`/scenes/${sceneId}/shot-order`, "PUT", { ids }),
  shot: (shotId: string) => request<ShotDto>(`/shots/${shotId}`),
  shotGenerationWorkspace: (shotId: string) =>
    request<ShotGenerationWorkspaceDto>(`/shots/${shotId}/generation-workspace`),
  promptPreview: (
    shotId: string,
    target: "anchor" | "video" = "video",
    regenerationInstruction?: string,
  ) => {
    const query = new URLSearchParams({ target });
    if (regenerationInstruction) {
      query.set("regeneration_instruction", regenerationInstruction);
    }
    return request<ShotPromptPreview>(`/shots/${shotId}/prompt-preview?${query.toString()}`);
  },
  shotAssistContext: (shotId: string) =>
    request<ShotAssistContext>(`/shots/${shotId}/assist-context`),
  assistShot: (
    shotId: string,
    sourceDraftRevision: number,
    candidateAssetIds: string[],
  ) => json<{ jobId: string }>(`/shots/${shotId}/assist`, "POST", {
    sourceDraftRevision,
    candidateAssetIds,
    allowPaidGeneration: true,
  }),
  shotAssistAnalyses: (shotId: string) =>
    request<ShotAssistRecord[]>(`/shots/${shotId}/assist-analyses`),
  acceptShotAssistance: (
    stepId: string,
    sourceDraftRevision: number,
    patch: ShotAssistPatch,
  ) => json<ShotDto>(`/steps/${stepId}/accept-shot-assistance`, "POST", {
    sourceDraftRevision,
    patch,
  }),
  previousTail: (shotId: string) =>
    request<PreviousTailStatus>(`/shots/${shotId}/previous-tail`),
  adoptPreviousTailAnchor: (shotId: string) =>
    json<ShotDto & { previousTail: PreviousTailStatus }>(
      `/shots/${shotId}/adopt-previous-tail-anchor`,
      "POST",
    ),
  updateReferences: (shotId: string, references: ReferenceBinding[]) =>
    json<ShotDto>(`/shots/${shotId}/references`, "PUT", { references }),
  selectSceneLook: (sceneId: string, assetId: string | null) =>
    json<SceneDto>(`/scenes/${sceneId}/look-asset`, "PUT", { assetId }),
  sceneLookDraft: (sceneId: string) =>
    request<SceneLookDraftEnvelope>(`/scenes/${sceneId}/look-draft`),
  saveSceneLookDraft: (
    sceneId: string,
    expectedRevision: number,
    draft: SceneLookDraftDto,
  ) => json<SceneLookDraftEnvelope>(`/scenes/${sceneId}/look-draft`, "PUT", {
    expectedRevision,
    draft,
  }),
  previewSceneLookPrompt: (sceneId: string) =>
    json<SceneLookPromptPreview>(`/scenes/${sceneId}/look-prompt-preview`, "POST"),
  sceneLookVersions: (sceneId: string) =>
    request<SceneLookVersion[]>(`/scenes/${sceneId}/look-versions`),
  uploadReference: async (
    projectId: string,
    usage: ReferenceUsage,
    role: ReferenceRole,
    displayName: string,
    file: File,
  ) => {
    const form = new FormData();
    form.append("usage", usage);
    form.append("role", role);
    form.append("displayName", displayName.trim() || file.name.replace(/\.[^.]+$/, ""));
    form.append("file", file);
    return request<AssetDto>(`/projects/${projectId}/references`, { method: "POST", body: form });
  },
  generateAnchor: (shotId: string, regenerate = false, reason?: string) =>
    json<{ jobId: string }>(`/shots/${shotId}/anchors`, "POST", {
      allowPaidGeneration: true,
      regenerate,
      reason,
    }),
  generateSceneLook: (
    sceneId: string,
    draftRevision: number,
    regenerate = false,
    reason?: string,
  ) =>
    json<{ jobId: string }>(`/scenes/${sceneId}/look-images`, "POST", {
      allowPaidGeneration: true,
      draftRevision,
      regenerate,
      reason,
    }),
  generateVideo: (shotId: string, regenerate = false, reason?: string) =>
    json<{ jobId: string }>(`/shots/${shotId}/videos`, "POST", {
      allowPaidGeneration: true,
      regenerate,
      reason,
    }),
  reviewAsset: (assetId: string, decision: "approved" | "rejected", reason: string) =>
    json(`/assets/${assetId}/review`, "POST", { decision, reason, select: true }),
  selectVersion: (shotId: string, assetId: string) =>
    json<ShotDto>(`/shots/${shotId}/versions/${assetId}/select`, "POST"),
  rangeEdit: (
    shotId: string,
    body: {
      sourceAssetId: string;
      startMs: number;
      endMs: number;
      instruction: string;
      allowPaidGeneration: true;
    },
  ) => json<{ jobId: string }>(`/shots/${shotId}/range-edits`, "POST", body),
  buildSequence: (
    projectId: string,
    transitions: Array<{ afterShotId: string; transition: SequenceTransitionDto }>,
  ) => json<{ jobId: string }>(`/projects/${projectId}/sequences`, "POST", { transitions }),
  sequences: (projectId: string) => request<SequenceDto[]>(`/projects/${projectId}/sequences`),
  selectSequence: (projectId: string, sequenceId: string, approve: boolean) =>
    json<SequenceDto>(`/projects/${projectId}/sequences/${sequenceId}/select`, "POST", {
      approve,
    }),
  resumeStep: (stepId: string) => json<{ jobId: string }>(`/steps/${stepId}/resume`, "POST"),
  reconciliationCandidates: (stepId: string) =>
    request<Array<Record<string, unknown>>>(`/steps/${stepId}/reconciliation-candidates`),
  reconcileStep: (stepId: string, providerTaskId: string) =>
    json(`/steps/${stepId}/reconcile`, "POST", { providerTaskId }),
  jobs: () => request<JobDto[]>("/jobs"),
  job: (jobId: string) => request<JobDto>(`/jobs/${jobId}`),
  projectTasks: (projectId: string) =>
    request<PersistentTaskDto[]>(`/projects/${projectId}/tasks`),
  canon: () => request<AssetDto[]>("/canon"),
};

export type SuggestionJobResult = { stepId: string; output: ShotSuggestionOutput };

export function assetContentUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}/content`;
}
