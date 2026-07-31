/** 与后端 camelCase 读模型对齐的接口类型。
 * 以 src/cat_video_generator/infrastructure/db/records.py 与
 * infrastructure/db/query_repository.py 的返回结构为准；
 * EpisodeScript 对应 domain/contracts.py 的 EpisodePlan（model_dump JSON）。
 */

export interface RunSummary {
  id: string;
  contentDate: string;
  theme: string | null;
  status: string;
  selectedCandidate: number | null;
  archivedSource: string | null;
  nextAction: string | null;
  createdAt: string;
  updatedAt: string;
  /** 以下字段仅 /runs/{id}/graph 响应存在 */
  worldConsistencyStatus?: string;
  contradictions?: string[];
  renderRiskLevel?: string;
  renderRiskReasons?: string[];
  multiClipRecommended?: boolean;
  directorRepairAttempted?: boolean;
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

export interface SegmentPlanDto {
  order: number;
  shot_order: number;
  action_orders: number[];
  duration_seconds: number;
  requires_tail_link: boolean;
}

export type RelationKind =
  | "count"
  | "support"
  | "containment"
  | "boundary"
  | "handoff";

export interface CriticalRelationDto {
  subject: string;
  relation: RelationKind;
  initial_state: string;
  final_state: string;
}

export interface ElementUseDto {
  element_id: string;
  purpose: string;
  initial_state: string;
  final_state: string;
}

export interface ScenePropDto {
  name: string;
  placement: string;
  final_placement: string | null;
}

/** VisibleWorldPlan 的完整结构见 domain/continuity.py；
 * 前端只做展示，保留松散结构。 */
export interface VisibleWorldDto {
  scene_anchors: Array<Record<string, unknown>>;
  tracked_entities: Array<Record<string, unknown>>;
  action_transitions: Array<Record<string, unknown>>;
  shot_boundary_states: Array<Record<string, unknown>>;
}

export type VideoInputMode =
  | "multimodal_reference"
  | "strict_first_frame"
  | "strict_first_last";
export type GenerationStrategy = "single_pass" | "multi_clip";
export type StyleContext = "indoor" | "outdoor";

export interface EpisodeScript {
  slot: string;
  title: string;
  event_key: string | null;
  location_key: string | null;
  main_event: string;
  scene: string;
  style_context: StyleContext;
  cast: string[];
  appearance: AppearancePlanDto;
  actions: ActionStageDto[];
  shots: ShotPlanDto[];
  ending: string;
  duration_seconds: number;
  video_input_mode: VideoInputMode;
  required_reference_roles: string[];
  shared_element_ids: string[];
  element_uses: ElementUseDto[];
  critical_relations: CriticalRelationDto[];
  scene_inventory: ScenePropDto[];
  visible_world: VisibleWorldDto | null;
  reference_semantic_keys: string[];
  generation_strategy: GenerationStrategy;
  segments: SegmentPlanDto[];
}

export interface EpisodeDto {
  id: string;
  runId: string;
  slot: string;
  sortOrder: number;
  title: string;
  status: string;
  videoInputMode: string;
  generationStrategy: GenerationStrategy;
  worldConsistencyStatus: string;
  contradictions: string[];
  renderRiskLevel: string;
  renderRiskReasons: string[];
  multiClipRecommended: boolean;
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
  requestSummary: Record<string, unknown>;
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
  inputPlan?: Record<string, unknown> | null;
  promptAliases?: Record<string, string>;
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
