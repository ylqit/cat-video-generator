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
  keyframes: StageMode;
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

export type AppearanceContinuity = "continue" | "changed";

export interface AppearancePlanDto {
  description: string;
  continuity: AppearanceContinuity | null;
  changes_from_previous: string[];
  change_reason: string | null;
}

export type ActorId = "person" | "cat" | "guest" | "environment";

export interface ActionStageDto {
  order: number;
  actor_id: ActorId;
  action: string;
  visible_result: string;
  transitions: EntityTransitionDto[];
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

export interface EntityStateDto {
  anchor_id: string | null;
  support_id: string | null;
  container_id: string | null;
  active: boolean;
  appearance_signature: string;
}

export interface EntityTransitionDto {
  entity_id: string;
  before: EntityStateDto;
  after: EntityStateDto;
  reason: string;
}

export interface VisibleWorldDto {
  anchors: Array<{ id: string; name: string; type: string }>;
  entities: Array<{
    id: string;
    name: string;
    type: string;
    semantic_key: string | null;
    initial_state: EntityStateDto;
  }>;
}

export type VideoInputMode =
  | "multimodal_reference"
  | "strict_first_frame"
  | "strict_first_last";
export type StyleContext = "indoor" | "outdoor";

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
  ending: string;
  duration_seconds: number;
  video_input_mode: VideoInputMode;
  visible_world: VisibleWorldDto;
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
  purpose: string;
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
}

export interface Job {
  jobId: string;
  kind: string;
  dedupKey: string;
  status: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  result: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
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
  connected: boolean;
  database: string;
  user: string;
  serverVersionNum: number;
  ssl: boolean;
  alembicRevision: string;
  alembicHead: string;
  migrationCurrent: boolean;
}

/** 主题创作台：单集 Prompt 预览与编辑覆盖。 */
export interface PromptOverrides {
  first_frame?: string;
  last_frame?: string;
  video?: string;
}

export interface EpisodePromptPreview {
  episodeId: string;
  slot: string;
  firstFrame: string;
  lastFrame: string;
  video: string;
  overrides: PromptOverrides;
}
