/** 与后端 camelCase 读模型对齐的接口类型。
 * 以 src/cat_video_generator/infrastructure/db/records.py 与
 * infrastructure/db/query_repository.py 的返回结构为准；
 * EpisodeScript 对应 domain/contracts.py；slot由Episode关系字段单独返回。
 */

export type StageMode = "auto" | "manual";

export interface PipelineSettings {
  allowPaidGeneration: boolean;
  dayBrief: StageMode;
  script: StageMode;
  storyboard: StageMode;
  video: StageMode;
}

export interface SlotBriefDto {
  slot: string;
  narrative_purpose: string;
  scene_direction: string;
  event_direction: string;
  appearance_intent: string;
}

export interface DayBriefDto {
  content_date: string;
  theme: string;
  day_context: string;
  slots: SlotBriefDto[];
}

export interface RunSummary {
  id: string;
  contentDate: string;
  theme: string | null;
  status: string;
  nextAction: string | null;
  pipelineSettings?: PipelineSettings;
  createdAt: string;
  updatedAt: string;
  /** 以下字段仅 /runs/{id}/graph 响应存在 */
  worldConsistencyStatus?: string;
  contradictions?: string[];
  dayBrief?: DayBriefDto | null;
  currentStage?: string;
}

export interface AppearancePlanDto {
  description: string;
  changes_from_previous: string[];
  change_reason: string | null;
}

export interface ActionStageDto {
  order: number;
  actor_id: string;
  action: string;
  visible_result: string;
}

export type CameraMove = "fixed" | "follow" | "push" | "pull" | "pan" | "track";
export type DominantView = "front" | "side" | "back" | "mixed";

export interface ShotPlanDto {
  order: number;
  action_orders: number[];
  framing: string;
  camera_move: CameraMove;
  dominant_view: DominantView;
  direction: string;
}

export type PlacementKind = "anchor" | "held_by" | "inside" | "offscreen";

export interface PlacementDto {
  kind: PlacementKind;
  target_id: string | null;
}

export interface EntityStateDto {
  present: boolean;
  placement: PlacementDto;
}

export type EntityKind = "person" | "cat" | "prop";
export type EntityLifecycle = "persist" | "enter" | "exit" | "consume" | "transform";

export interface SceneContinuityDto {
  anchors: Array<{ id: string; name: string; type: string }>;
  entities: Array<{
    id: string;
    name: string;
    kind: EntityKind;
    entity_key: string;
    start_state: EntityStateDto;
    end_state: EntityStateDto;
    lifecycle: EntityLifecycle;
    form_key: string;
    final_form_key: string | null;
    change_reason: string | null;
  }>;
}

export type VideoInputMode = "storyboard_reference" | "strict_first_last";
export type StyleContext = "indoor" | "outdoor";

export interface EpisodeEndingDto {
  result: string;
  visual_critical: boolean;
  key_entity_ids: string[];
}

export interface EpisodeScript {
  title: string;
  event_key: string;
  location_key: string;
  main_event: string;
  scene: string;
  style_context: StyleContext;
  appearance: AppearancePlanDto;
  actions: ActionStageDto[];
  shots: ShotPlanDto[];
  ending: EpisodeEndingDto;
  duration_seconds: number;
  continuity: SceneContinuityDto;
}

export interface EpisodeDto {
  id: string;
  runId: string;
  slot: string;
  sortOrder: number;
  title: string;
  status: string;
  videoInputMode: string;
  worldConsistencyStatus: string;
  contradictions: string[];
  nextAction: string | null;
  selectedVideoAssetId: string | null;
  promptOverrides: Record<string, string>;
  script: EpisodeScript;
}

export interface StepError {
  code?: string;
  message?: string;
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
  createdAt: string;
}

export interface PromptDto {
  id: string;
  stepId: string;
  parentPromptId: string | null;
  purpose: "director" | "storyboard" | "storyboard_review" | "video" | "review";
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

export interface RunGraph {
  run: RunSummary;
  episodes: EpisodeDto[];
  steps: StepDto[];
  prompts: PromptDto[];
  assets: AssetDto[];
  reviews: ReviewDto[];
  /** 未定稿Run的时段剧本草稿（planning_json.episodeDrafts），键为slot */
  episodeDrafts?: Record<string, EpisodeScript>;
  workflowNodes?: WorkflowNodeDto[];
}

export interface WorkflowNodeDto {
  id: string;
  type: "director" | "storyboard" | "storyboard_review" | "video" | "content_review";
  slot: string | null;
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

export interface Job {
  jobId: string;
  kind: string;
  dedupKey: string;
  context: Record<string, string>;
  status: string;
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
  slot: string;
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
}

/** 主题创作台：单集 Prompt 预览与编辑覆盖。 */
export interface PromptOverrides {
  storyboard?: string;
  video?: string;
}

export interface EpisodePromptPreview {
  episodeId: string;
  slot: string;
  storyboard: string;
  video: string;
  overrides: PromptOverrides;
}
