/** 与后端只读投影保持一致的生产工作台类型。 */

export type Slot = "morning" | "noon" | "evening";
export type StageMode = "auto" | "manual";
export type PlanningMode = "guided_sequential" | "auto_day";
export type ActivityFocus = "cat_lead" | "person_lead" | "balanced";
export type ActivityFocusMode = ActivityFocus | "inherit" | "adaptive";
export type DurationMode = "short" | "medium" | "long" | "adaptive";
export type StoryInputMode = "theme_expand" | "episode_scripts";
export type SceneRoute = "adaptive" | "progressive_locations" | "single_location";
export type StoryConnectionMode = "independent" | "selected_link" | "direct_continue";
export type CrossSlotReferenceRole = "identity" | "prop" | "scene" | "composition" | "motion";
export type CrossSlotReferenceTarget = "opening_anchor" | "video" | "both";

export interface StoryConnectionDto {
  useForDirector: boolean;
  mode: StoryConnectionMode;
  brief: string;
  confirmedAt?: string | null;
}

export interface CrossSlotReferenceDto {
  assetId: string;
  role: CrossSlotReferenceRole;
  applyTo: CrossSlotReferenceTarget;
}

export interface EligibleCrossSlotAssetDto {
  assetId: string;
  episodeId: string;
  sourceSlot: Slot;
  role: string;
  mediaType: "image" | "video";
  semanticKey?: string | null;
  sha256: string;
  suggestedRole: CrossSlotReferenceRole;
  recommendationReason: string;
}

export interface PipelineSettings {
  planningMode: PlanningMode;
  allowPaidGeneration: boolean;
  projectOutline: StageMode;
  script: StageMode;
  visual: StageMode;
  video: StageMode;
  review: StageMode;
}

export interface SlotCreativeControlDto {
  slot: Slot;
  activity_focus: ActivityFocusMode;
  duration_mode: DurationMode;
}

export interface RunCreativeControlsDto {
  default_activity_focus: Exclude<ActivityFocusMode, "inherit">;
  slot_controls: SlotCreativeControlDto[];
}

export interface EpisodeSourcesDto {
  morning: string | null;
  noon: string | null;
  evening: string | null;
}

export interface StoryProjectInputDto {
  theme: string;
  input_mode: StoryInputMode;
  scene_route: SceneRoute;
  episode_sources: EpisodeSourcesDto;
}

export interface OutlineEpisodeDto {
  scene: string;
  direction: string;
}

export interface ProjectOutlineDto {
  content_date: string;
  theme: string;
  day_arc: string;
  episodes: Record<Slot, OutlineEpisodeDto>;
  handoffs: Array<{
    name: string;
    from_slot: Slot;
    to_slot: Slot;
    continuity: string;
  }>;
}

export interface RunSummary {
  id: string;
  contractVersion: number;
  contentDate: string;
  theme: string | null;
  status: string;
  nextAction: string | null;
  availableActions: StepActionDto[];
  pipelineSettings?: PipelineSettings;
  createdAt: string;
  updatedAt: string;
  compatible?: boolean;
  incompatibilityReason?: string | null;
  projectInput?: StoryProjectInputDto | null;
  projectOutline?: ProjectOutlineDto | null;
  planningMetadata?: {
    creativeControls?: RunCreativeControlsDto;
    seriesProfile?: Record<string, unknown>;
    storyPatterns?: Record<string, Record<string, unknown>>;
  };
  currentStage?: string;
  projectOutlineConfirmed?: boolean;
  planningMode?: PlanningMode;
  acceptedOutcomes?: Partial<Record<Slot, AcceptedOutcomeDto>>;
  storyConnections?: Partial<Record<Slot, StoryConnectionDto>>;
  crossSlotReferences?: Partial<Record<Slot, CrossSlotReferenceDto[]>>;
  activeSlot?: Slot | null;
  nextSlot?: Slot | null;
  slotAvailability?: Partial<Record<Slot, "locked" | "ready" | "active" | "completed">>;
  connectionStatus?: Partial<Record<Slot, string>>;
  slotPlanning?: SlotPlanningStateDto[];
}

export interface AcceptedOutcomeDto {
  summary: string;
  carryForward: string[];
  doNotCarryForward: string[];
  confirmedAt?: string;
}

export interface OutcomeDraftDto extends AcceptedOutcomeDto {
  slot: Slot;
  confirmed: boolean;
  source?: "video_diagnostic" | "script_ending";
  episodeStatus?: string;
}

export interface SlotPlanningStateDto {
  slot: Slot;
  planned: boolean;
  unlocked: boolean;
  blockReason: string | null;
  outcomeConfirmed: boolean;
}

export interface ShotDirectionDto {
  order: number;
  direction: string;
}

export interface HardConstraintDto {
  shot_orders: number[];
  text: string;
}

export interface EpisodeScript {
  title: string;
  event_key: string;
  location_key: string;
  visual_context: "indoor" | "outdoor";
  activity_focus: ActivityFocus;
  duration_seconds: number;
  appearance: string;
  story_text: string;
  relationship_arc: string;
  shots: ShotDirectionDto[];
  hard_constraints: HardConstraintDto[];
  sound_design: string;
  ending: string;
}

export interface PromptOverrideState {
  enabled: boolean;
  stale: boolean;
  sourceScriptSha256: string | null;
  values: PromptOverrides;
}

export interface RenderPlanDto {
  mode: "single_pass" | "extended";
  total_duration_seconds: number;
  sections: Array<{
    order: number;
    duration_seconds: number;
    shot_orders: number[];
  }>;
}

export interface EpisodeDto {
  id: string;
  runId: string;
  slot: Slot;
  sortOrder: number;
  title: string;
  status: string;
  activityFocus: ActivityFocus;
  relationshipArc: string;
  renderPlan: RenderPlanDto;
  nextAction: string | null;
  selectedVideoAssetId: string | null;
  promptOverrides: Record<string, string>;
  promptOverrideState: PromptOverrideState;
  script: EpisodeScript;
}

export interface StepError {
  code?: string;
  message?: string;
  requestId?: string;
}

export type StepActionType =
  | "retry"
  | "retry_unknown_image"
  | "continue_query"
  | "reconcile"
  | "review"
  | "deliver"
  | "regenerate"
  | "replan"
  | "none";

export interface StepActionDto {
  type: StepActionType;
  label: string;
  paid: boolean;
  requiresDuplicateBillingAck?: boolean;
}

export interface StepDto {
  id: string;
  runId: string;
  episodeId: string | null;
  parentStepId: string | null;
  kind: string;
  status: string;
  attempt: number;
  provider: string | null;
  providerTaskId: string | null;
  model: string | null;
  inputHash: string;
  operationKey: string;
  inputSnapshot: Record<string, unknown>;
  error: StepError | null;
  nextAction: string | null;
  availableActions: StepActionDto[];
  createdAt: string;
  submittedAt: string | null;
}

export interface PromptDto {
  id: string;
  stepId: string;
  parentPromptId: string | null;
  purpose: "director" | "image" | "video" | "review";
  model: string;
  sha256: string;
  charCount: number;
  utf8Bytes: number;
  createdAt: string;
}

export interface PromptFull extends PromptDto {
  text: string;
  inputSnapshot?: Record<string, unknown>;
}

export interface StepTraceDto {
  step: StepDto;
  inputSummary: Record<string, unknown>;
  actualPrompts: PromptFull[];
  currentCompiledPrompts: Array<{ purpose: string; label: string; text: string }>;
  currentStructuredOutput: Record<string, unknown> | null;
  providerOutput: Record<string, unknown> | null;
  normalizedOutput: Record<string, unknown> | null;
  effectiveOutput: Record<string, unknown> | null;
  normalizationWarnings: string[];
  inputBindings: Array<Record<string, unknown>>;
  assets: AssetDto[];
  reviews: ReviewDto[];
  attempts: StepDto[];
}

export interface AssetDto {
  id: string;
  episodeId: string | null;
  stepId: string | null;
  role: string;
  semanticKey: string | null;
  scope: string;
  status: string;
  mediaType: string;
  localPath: string;
  sha256: string;
  metadata: Record<string, unknown>;
}

export interface ReviewDto {
  id: string;
  stepId: string;
  assetId: string | null;
  source: string;
  decision: string;
  reason: string | null;
  warnings: Array<Record<string, unknown>>;
  evidence: Record<string, unknown>;
}

export interface WorkflowNodeDto {
  semanticNodeId: string;
  type:
    | "director"
    | "look"
    | "opening_anchor"
    | "video"
    | "content_review"
    | "accepted_outcome"
    | "story_connection"
    | "project_input"
    | "project_confirmation"
    | "delivery";
  slot: Slot | null;
  label: string;
  status: string;
  availability: "locked" | "ready" | "active" | "completed";
  executionStatus: string;
  lockReason: string | null;
  unlockRequirements: string[];
  providerStatus: string;
  contractStatus: string;
  semanticReviewStatus: string;
  stepId: string | null;
  promptIds: string[];
  assetIds: string[];
  reviewIds: string[];
  error: StepError | null;
  nextAction: string | null;
  allowedActions: StepActionDto[];
  currentAttemptId: string | null;
  attemptIds: string[];
  stale?: boolean;
  attempts: StepDto[];
}

export interface VideoSequenceClipDto {
  order: number;
  source_asset_id: string;
  source_start_ms: number;
  source_end_ms: number;
  timeline_start_ms: number;
  timeline_end_ms: number;
  origin: "original" | "generated";
  replacement_step_id: string | null;
}

export interface VideoSequenceDto {
  id: string;
  episodeId: string;
  revision: number;
  parentSequenceId: string | null;
  baseAssetId: string;
  renderedAssetId: string | null;
  status: "draft" | "generating" | "content_review" | "approved" | "rejected";
  durationMs: number;
  audioPolicy: "preserve_original";
  clips: VideoSequenceClipDto[];
  createdAt: string;
  updatedAt: string;
}

export interface RunGraph {
  run: RunSummary;
  episodes: EpisodeDto[];
  steps: StepDto[];
  prompts: PromptDto[];
  assets: AssetDto[];
  reviews: ReviewDto[];
  workflowNodes?: WorkflowNodeDto[];
  videoSequences?: VideoSequenceDto[];
}

export interface StoryProjectPreview {
  theme: string;
  episodeSources: EpisodeSourcesDto;
  issues: string[];
  canConfirm: boolean;
}

export interface ReconciliationCandidateDto {
  taskId: string;
  status: string;
  model: string | null;
  createdAt: string | null;
  durationSeconds: number | null;
  ratio: string | null;
  resolution: string | null;
  generateAudio: boolean | null;
}

export interface CanonAsset {
  id: string;
  role: string;
  semanticKey?: string | null;
  scope: string;
  status: string;
  sha256: string;
  metadata: Record<string, unknown>;
  contentUrl?: string;
}

export interface JobAccepted {
  jobId: string;
  kind: string;
  dedupKey: string;
  status: string;
  context: Record<string, string>;
}

export interface Job extends JobAccepted {
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  result: Record<string, unknown> | null;
  error: {
    code: string;
    message: string;
    runId?: string;
    episodeId?: string;
    slot?: string;
    operationKey?: string;
    details?: string[];
  } | null;
}

export interface DeliveryItemDto {
  id: string;
  episodeId: string;
  assetId: string;
  slot: Slot;
  sortOrder: number;
  filename: string;
  sha256: string;
}

export interface DeliveryPackageDto {
  id: string;
  runId: string;
  revision: number;
  status: string;
  localPath: string;
  manifestSha256: string | null;
  createdAt: string;
  items: DeliveryItemDto[];
}

export interface HealthStatus {
  database: string;
  user: string;
  alembicRevision: string | null;
  expectedAlembicRevision: string;
  ready: boolean;
  arkVideoModel?: string;
  arkVideoResolution?: "480p" | "720p";
  supportsVideoExtension?: boolean;
  arkDirectorRequestTimeoutSeconds?: number;
  arkImageRequestTimeoutSeconds?: number;
  arkImageTimeoutAutoRetries?: number;
  arkImageRetryDelaySeconds?: number;
  arkReviewRequestTimeoutSeconds?: number;
  arkVideoApiTimeoutSeconds?: number;
  arkTaskTimeoutSeconds?: number;
  arkPollIntervalSeconds?: number;
}

export interface PromptOverrides {
  look?: string;
  opening_anchor?: string;
  video?: string;
}

export interface EpisodePromptPreview {
  episodeId: string;
  slot: Slot;
  look: string;
  openingAnchor: string;
  videoSections: Array<{ order: number; durationSeconds: number; prompt: string }>;
  renderPlan: RenderPlanDto;
  resolution: "480p" | "720p";
  overrides: PromptOverrides;
  overrideState: PromptOverrideState;
}
