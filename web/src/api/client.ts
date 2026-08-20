import type {
  AssetDto,
  AcceptedVisualAssetPlan,
  CanvasDto,
  CanvasEdgeDto,
  CanvasNodeType,
  CanvasTemplateDto,
  CanvasTemplateKey,
  CapabilityCompilationPlan,
  CreativeStepRecord,
  CreativeWorkflowDto,
  HealthDto,
  JobDto,
  PersistentTaskDto,
  TaskCenterDto,
  PreviousTailStatus,
  ProductionBoardDto,
  PromptRunDto,
  ProjectGraph,
  ProjectSummary,
  ReferenceBinding,
  ReferenceImageDraft,
  ReferenceRole,
  ReferenceUsage,
  RuntimeProductionConfig,
  RuntimeSettingsDto,
  SceneLookDraftDto,
  SceneLookDraftEnvelope,
  SceneDto,
  SceneLookPlan,
  SceneLookPromptPreview,
  SceneLookVersion,
  SceneVisualAssetsDto,
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
  StoryBriefInput,
  StoryExpansionOutput,
  StoryRewriteOutput,
  StoryRewriteStrategy,
  SubjectInput,
  VideoEditAnnotationInput,
  VideoEditRecipeDto,
  VisualAssetPurpose,
  VisualProfileDraft,
  VisualProfileRevisionDto,
} from "./types";

const BASE = "/api/v1";
const CANVAS_BASE = "/api/v2";

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

async function request<T>(path: string, init?: RequestInit, base = BASE): Promise<T> {
  const response = await fetch(`${base}${path}`, init);
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

function canvasJson<T>(path: string, method: string, body?: unknown, headers?: HeadersInit) {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  }, CANVAS_BASE);
}

function paidJson<T>(path: string, body: unknown, confirmedRevision?: number): Promise<T> {
  if (confirmedRevision === undefined) {
    return Promise.reject(new ApiError(409, "运行配置尚未加载，请刷新页面后重新确认"));
  }
  return request<T>(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CVG-Runtime-Config-Revision": String(confirmedRevision),
    },
    body: JSON.stringify(body),
  });
}

export const api = {
  health: () => request<HealthDto>("/health"),
  runtimeSettings: () => request<RuntimeSettingsDto>("/runtime-settings"),
  updateRuntimeSettings: (
    expectedRevision: number,
    config: Omit<RuntimeProductionConfig, "revision" | "updatedAt" | "usingOverride">,
  ) => json<RuntimeSettingsDto>("/runtime-settings", "PUT", {
    expectedRevision,
    ...config,
  }),
  restoreRuntimeSettings: (expectedRevision: number) => request<RuntimeSettingsDto>(
    "/runtime-settings/override",
    {
      method: "DELETE",
      headers: { "X-CVG-Runtime-Config-Revision": String(expectedRevision) },
    },
  ),
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
  expandStory: (sceneId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/story-expansions`, {
      allowPaidGeneration: true,
    }, runtimeRevision),
  acceptStoryExpansion: (stepId: string, expansion: StoryExpansionOutput) =>
    json<SceneDto>(`/steps/${stepId}/accept-story-expansion`, "POST", { expansion }),
  diagnoseStory: (sceneId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/story-diagnoses`, {
      allowPaidGeneration: true,
    }, runtimeRevision),
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
  rewriteStory: (sceneId: string, diagnosisStepId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/story-rewrites`, {
      diagnosisStepId,
      allowPaidGeneration: true,
    }, runtimeRevision),
  acceptStoryRewrite: (stepId: string, rewrite: StoryRewriteOutput) =>
    json<SceneDto>(`/steps/${stepId}/accept-story-rewrite`, "POST", { rewrite }),
  suggestShots: (sceneId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/shot-suggestions`, {
      allowPaidGeneration: true,
    }, runtimeRevision),
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
  planVisualAssets: (sceneId: string, runtimeRevision?: number) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/visual-asset-plans`, {
      allowPaidGeneration: true,
    }, runtimeRevision),
  acceptVisualAssetPlan: (stepId: string, plan: AcceptedVisualAssetPlan) =>
    json<CreativeStepRecord>(`/steps/${stepId}/accept-visual-asset-plan`, "POST", {
      plan,
    }),
  sceneVisualAssets: (sceneId: string) =>
    request<SceneVisualAssetsDto>(`/scenes/${sceneId}/visual-assets`),
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
  saveAnchorBrief: (shotId: string, sourceDraftRevision: number, brief: string) =>
    json<ShotGenerationWorkspaceDto>(`/shots/${shotId}/anchor-briefs`, "POST", {
      sourceDraftRevision,
      brief,
    }),
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
    runtimeRevision?: number,
  ) => paidJson<{ jobId: string }>(`/shots/${shotId}/assist`, {
    sourceDraftRevision,
    candidateAssetIds,
    allowPaidGeneration: true,
  }, runtimeRevision),
  shotAssistAnalyses: (shotId: string) =>
    request<ShotAssistRecord[]>(`/shots/${shotId}/assist-analyses`),
  acceptShotAssistance: (
    stepId: string,
    sourceDraftRevision: number,
    patch: ShotAssistPatch | null,
    acceptedAnchorBrief?: string | null,
  ) => json<ShotDto>(`/steps/${stepId}/accept-shot-assistance`, "POST", {
    sourceDraftRevision,
    patch,
    acceptedAnchorBrief,
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
  uploadVisualReference: async (
    target: { projectId: string; sceneId?: string | null },
    purpose: VisualAssetPurpose,
    displayName: string,
    file: File,
  ) => {
    const form = new FormData();
    form.append("purpose", purpose);
    form.append("displayName", displayName.trim() || file.name.replace(/\.[^.]+$/, ""));
    form.append("file", file);
    const path = target.sceneId
      ? `/scenes/${target.sceneId}/visual-references`
      : `/projects/${target.projectId}/visual-references`;
    return request<AssetDto>(path, { method: "POST", body: form });
  },
  generateAnchor: (
    shotId: string,
    regenerate = false,
    reason?: string,
    expectedInputHash?: string,
    runtimeRevision?: number,
  ) =>
    paidJson<{ jobId: string }>(`/shots/${shotId}/anchors`, {
      allowPaidGeneration: true,
      regenerate,
      reason,
      expectedInputHash,
    }, runtimeRevision),
  generateSceneLook: (
    sceneId: string,
    draftRevision: number,
    regenerate = false,
    reason?: string,
    runtimeRevision?: number,
  ) =>
    paidJson<{ jobId: string }>(`/scenes/${sceneId}/look-images`, {
      allowPaidGeneration: true,
      draftRevision,
      regenerate,
      reason,
    }, runtimeRevision),
  generateReferenceImage: (
    target: { projectId: string; sceneId?: string | null },
    draft: ReferenceImageDraft,
    regenerate = false,
    reason?: string,
    runtimeRevision?: number,
  ) => paidJson<{ jobId: string; operationKey: string }>(
    target.sceneId
      ? `/scenes/${target.sceneId}/reference-images`
      : `/projects/${target.projectId}/reference-images`,
    {
      allowPaidGeneration: true,
      regenerate,
      reason,
      draft,
    },
    runtimeRevision,
  ),
  generateVideo: (
    shotId: string,
    regenerate = false,
    reason?: string,
    expectedInputHash?: string,
    runtimeRevision?: number,
  ) =>
    paidJson<{ jobId: string }>(`/shots/${shotId}/videos`, {
      allowPaidGeneration: true,
      regenerate,
      reason,
      expectedInputHash,
    }, runtimeRevision),
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
    runtimeRevision?: number,
  ) => paidJson<{ jobId: string }>(`/shots/${shotId}/range-edits`, body, runtimeRevision),
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
  taskCenter: () => request<TaskCenterDto>("/task-center"),
  projectTasks: (projectId: string) =>
    request<PersistentTaskDto[]>(`/projects/${projectId}/tasks`),
  canon: () => request<AssetDto[]>("/canon"),
};

export const canvasApi = {
  templates: () => request<CanvasTemplateDto[]>("/canvas-templates", undefined, CANVAS_BASE),
  instantiateTemplate: (projectId: string, templateKey: CanvasTemplateKey) =>
    canvasJson<Record<string, unknown>>(
      `/projects/${projectId}/template-instances`,
      "POST",
      { templateKey },
    ),
  canvas: (projectId: string) =>
    request<CanvasDto>(`/projects/${projectId}/canvas`, undefined, CANVAS_BASE),
  createNode: (
    projectId: string,
    payload: {
      nodeType: CanvasNodeType;
      objectType: string;
      objectId?: string | null;
      data?: Record<string, unknown>;
    },
  ) => canvasJson<Record<string, unknown>>(
    `/projects/${projectId}/canvas/nodes`, "POST", payload,
  ),
  createEdge: (projectId: string, edge: Omit<CanvasEdgeDto, "id">) =>
    canvasJson<CanvasEdgeDto>(`/projects/${projectId}/canvas/edges`, "POST", edge),
  deleteEdge: (edgeId: string) =>
    canvasJson<Record<string, unknown>>(`/canvas/edges/${edgeId}`, "DELETE"),
  saveBrief: (projectId: string, brief: StoryBriefInput) =>
    canvasJson<Record<string, unknown>>(`/projects/${projectId}/brief`, "PUT", brief),
  createSubject: (projectId: string, subject: SubjectInput) =>
    canvasJson<Record<string, unknown>>(`/projects/${projectId}/subjects`, "POST", subject),
  runStoryStrategies: (projectId: string, rewriteInstruction?: string) =>
    canvasJson<{ id: string; status: string; candidates: Array<Record<string, unknown>> }>(
      `/projects/${projectId}/story-strategy-runs`,
      "POST",
      {
        idempotencyKey: crypto.randomUUID(),
        rewriteInstruction: rewriteInstruction || undefined,
      },
    ),
  approveStory: (revisionId: string) =>
    canvasJson<Record<string, unknown>>(`/story-revisions/${revisionId}/approve`, "POST", {}),
  createStoryboard: (projectId: string) =>
    canvasJson<Record<string, unknown>>(`/projects/${projectId}/storyboard-runs`, "POST", {}),
  updateBeat: (beatId: string, revision: number, patch: Record<string, unknown>) =>
    canvasJson<Record<string, unknown>>(
      `/shot-beats/${beatId}`,
      "PATCH",
      patch,
      { "If-Match": String(revision) },
    ),
  createGenerationBatch: (payload: {
    projectId: string;
    canvasNodeId: string;
    mediaKind: "image" | "video";
    candidateCount: number;
    provider?: string;
    model?: string;
    idempotencyKey: string;
    input: Record<string, unknown>;
  }) => canvasJson<Record<string, unknown>>("/generation-batches", "POST", payload),
  createVideoEditRecipe: (payload: {
    projectId: string;
    sourceAssetId: string;
    startMs: number;
    endMs: number;
    instruction: string;
    referenceAssetIds: string[];
    annotations: VideoEditAnnotationInput[];
  }) => canvasJson<VideoEditRecipeDto>("/video-edit-recipes", "POST", payload),
  updateVideoEditRecipe: (
    recipeId: string,
    revision: number,
    payload: Partial<Pick<VideoEditRecipeDto,
      "startMs" | "endMs" | "instruction" | "referenceAssetIds">>,
  ) => canvasJson<VideoEditRecipeDto>(
    `/video-edit-recipes/${recipeId}`,
    "PATCH",
    payload,
    { "If-Match": String(revision) },
  ),
  replaceVideoEditAnnotations: (
    recipeId: string,
    revision: number,
    annotations: VideoEditAnnotationInput[],
  ) => canvasJson<VideoEditRecipeDto>(
    `/video-edit-recipes/${recipeId}/annotations`,
    "PUT",
    { annotations },
    { "If-Match": String(revision) },
  ),
  compileVideoEditRecipe: (recipeId: string) =>
    canvasJson<CapabilityCompilationPlan>(
      `/video-edit-recipes/${recipeId}/compile`, "POST", {},
    ),
  submitVideoEditRecipe: (
    recipeId: string,
    idempotencyKey: string,
    acceptEstimatedCostMicros: number,
  ) => canvasJson<Record<string, unknown>>(
    `/video-edit-recipes/${recipeId}/submit`,
    "POST",
    { idempotencyKey, acceptEstimatedCostMicros },
  ),
  promptRun: (promptId: string) =>
    request<PromptRunDto>(`/prompt-runs/${promptId}`, undefined, CANVAS_BASE),
  saveLayout: (
    projectId: string,
    version: number,
    payload: {
      nodes: Array<{ nodeId: string; x: number; y: number }>;
      edges: CanvasEdgeDto[];
      viewport: { x: number; y: number; zoom: number };
      operations: Array<Record<string, unknown>>;
    },
  ) => canvasJson<CanvasDto>(
    `/projects/${projectId}/canvas/layout`,
    "PATCH",
    {
      ...payload,
      edges: payload.edges.map(({ id: _id, ...edge }) => edge),
    },
    { "If-Match": String(version) },
  ),
  eventsUrl: (projectId: string) => `${CANVAS_BASE}/projects/${projectId}/events`,
};

export type SuggestionJobResult = { stepId: string; output: ShotSuggestionOutput };

export function assetContentUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}/content`;
}
