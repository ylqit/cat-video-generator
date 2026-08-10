/** 与后端只读投影保持一致的生产工作台类型。 */

export type Slot = "morning" | "noon" | "evening";
export type StageMode = "auto" | "manual";
export type ActivityFocus = "cat_lead" | "person_lead" | "balanced";
export type ActivityFocusMode = ActivityFocus | "inherit" | "adaptive";
export type DurationMode = "short" | "medium" | "long" | "adaptive";

export interface PipelineSettings {
  allowPaidGeneration: boolean;
  dayBrief: StageMode;
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

export interface DurationIntentDto {
  requested_mode: DurationMode;
  resolved_band: "short" | "medium" | "long";
  resolution_reason: string;
}

export interface SlotBriefDto {
  slot: Slot;
  narrative_role: string;
  scene_direction: string;
  event_direction: string;
  appearance_intent: string;
  resolved_activity_focus: ActivityFocus;
  relationship_direction: string;
  duration_intent: DurationIntentDto;
}

export interface DayBriefDto {
  content_date: string;
  theme: string;
  day_objective: string;
  day_context: string;
  shared_motif: string;
  slot_briefs: SlotBriefDto[];
  handoffs: Array<{
    entity_key: string;
    from_slot: Slot;
    to_slot: Slot;
    state: string;
  }>;
}

export interface RunSummary {
  id: string;
  contentDate: string;
  theme: string | null;
  status: string;
  nextAction: string | null;
  availableActions: StepActionDto[];
  pipelineSettings?: PipelineSettings;
  createdAt: string;
  updatedAt: string;
  dayBrief?: DayBriefDto | null;
  planningMetadata?: {
    creativeControls?: RunCreativeControlsDto;
    seriesProfile?: Record<string, unknown>;
    storyPatterns?: Record<string, Record<string, unknown>>;
  };
  currentStage?: string;
}

export interface AppearancePlanDto {
  description: string;
  change_reason: string | null;
}

export interface RelationshipArcDto {
  lead_activity: string;
  secondary_activity: string;
  convergence: string;
}

export interface ActionStageDto {
  order: number;
  actor_id: string;
  action: string;
  visible_result: string;
}

export interface ShotPlanDto {
  order: number;
  action_orders: number[];
  framing: string;
  camera_move: "fixed" | "follow" | "push" | "pull" | "pan" | "track";
  direction: string;
}

export interface EpisodeScript {
  title: string;
  event_key: string;
  location_key: string;
  story_pattern:
    | "parallel_convergence"
    | "watch_trigger_payoff"
    | "setup_mishap_recovery"
    | "choice_reveal"
    | "routine_tag"
    | "process_montage";
  episode_question: string;
  main_event: string;
  scene: string;
  style_context: "indoor" | "outdoor";
  appearance: AppearancePlanDto;
  activity_focus: ActivityFocus;
  relationship_arc: RelationshipArcDto;
  guest: { id: string; name: string; role: string } | null;
  actions: ActionStageDto[];
  shots: ShotPlanDto[];
  ending: { result: string };
  sound_design: string;
  duration_seconds: number;
  critical_props: Array<{
    entity_key: string;
    name: string;
    start: string;
    end: string;
  }>;
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
  relationshipArc: RelationshipArcDto;
  renderPlan: RenderPlanDto;
  nextAction: string | null;
  selectedVideoAssetId: string | null;
  promptOverrides: Record<string, string>;
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
  id: string;
  type:
    | "director"
    | "look"
    | "opening_anchor"
    | "video"
    | "video_extension"
    | "content_review";
  slot: Slot | null;
  label: string;
  status: string;
  providerStatus: string;
  contractStatus: string;
  semanticReviewStatus: string;
  stepId: string | null;
  promptIds: string[];
  assetIds: string[];
  reviewIds: string[];
  error: StepError | null;
  nextAction: string | null;
  availableActions: StepActionDto[];
  attempts: StepDto[];
}

export interface RunGraph {
  run: RunSummary;
  episodes: EpisodeDto[];
  steps: StepDto[];
  prompts: PromptDto[];
  assets: AssetDto[];
  reviews: ReviewDto[];
  episodeDrafts?: Record<string, EpisodeScript>;
  workflowNodes?: WorkflowNodeDto[];
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
}
