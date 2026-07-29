/** 与后端 camelCase 读模型对齐的接口类型。 */

export interface RunSummary {
  id: string;
  contentDate: string;
  theme: string | null;
  status: string;
  selectedCandidate: number | null;
  createdAt: string;
  updatedAt: string;
}

export interface AppearancePlanDto {
  description: string;
  changes_from_previous: string[];
  change_reason: string | null;
}

export interface ActionStageDto {
  order: number;
  action: string;
  visible_result: string;
}

export interface CriticalRelationDto {
  subject: string;
  relation: string;
  expected: string;
}

export interface EpisodeScript {
  slot: string;
  title: string;
  main_event: string;
  scene: string;
  cast: string[];
  appearance: AppearancePlanDto;
  actions: ActionStageDto[];
  ending: string;
  duration_seconds: number;
  visual_strategy: string;
  required_reference_roles: string[];
  shared_element_ids: string[];
  critical_relations: CriticalRelationDto[];
}

export interface EpisodeDto {
  id: string;
  runId: string;
  slot: string;
  sortOrder: number;
  title: string;
  status: string;
  visualStrategy: string;
  selectedVideoAssetId: string | null;
  script: EpisodeScript;
}

export interface StepDto {
  id: string;
  runId: string;
  episodeId: string | null;
  kind: string;
  status: string;
  attempt: number;
  provider: string | null;
  providerTaskId: string | null;
  model: string | null;
  error: { code?: string; message?: string } | null;
  createdAt: string;
}

export interface PromptDto {
  id: string;
  stepId: string;
  purpose: string;
  model: string;
  sha256: string;
  charCount: number;
  createdAt: string;
}

export interface PromptFull extends PromptDto {
  text: string;
}

export interface AssetDto {
  id: string;
  episodeId: string | null;
  stepId: string | null;
  role: string;
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
  scope: string;
  status: string;
  sha256: string;
  metadata: Record<string, unknown>;
  contentUrl: string;
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
