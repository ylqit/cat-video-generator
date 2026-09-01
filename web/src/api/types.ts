export type CreatorReferenceRole =
  | "child_identity"
  | "cat_identity"
  | "style_board"
  | "child_appearance"
  | "cat_appearance"
  | "pair_scale"
  | "environment"
  | "prop"
  | "style_source";

export interface ProjectSummary {
  id: string;
  title: string;
  contentDate: string;
  status: string;
  storyTitle?: string | null;
  updatedAt: string;
}

export interface CreatorReferenceDto {
  assetId: string;
  role: CreatorReferenceRole;
  providerEligible: boolean;
  title: string;
  instruction: string;
}

export interface CreatorStoryDto {
  title: string;
  body: string;
  summary?: string | null;
}

export interface CreatorStateDto {
  projectId: string;
  projectTitle: string;
  contentDate: string;
  version: number;
  briefBody: string;
  storyCandidates: CreatorStoryDto[];
  currentStory: Partial<CreatorStoryDto>;
  targetDurationSeconds: number;
  aspectRatio: "9:16" | "16:9" | "1:1";
  qualityTier: "quick" | "standard" | "quality";
  referenceBindings: CreatorReferenceDto[];
  updatedAt: string;
}

export interface CreatorShotDto {
  id: string;
  projectId: string;
  sortOrder: number;
  version: number;
  title: string;
  direction: string;
  durationSeconds: number;
  sceneLabel?: string | null;
  referenceBindings: CreatorReferenceDto[];
  promptDraft?: string | null;
  selectedVideoAssetId?: string | null;
}

export interface CreatorAssetDto {
  id: string;
  projectId?: string | null;
  creatorShotId?: string | null;
  generationSnapshotId?: string | null;
  mediaType: "image" | "video" | "audio";
  role: string;
  status: "candidate" | "approved" | "rejected" | "ready";
  semanticKey?: string | null;
  sha256: string;
  byteSize: number;
  title: string;
  metadata: Record<string, unknown>;
  contentUrl: string;
  createdAt: string;
}

export type CreatorTaskStatus =
  | "local_queued"
  | "submitting"
  | "provider_queued"
  | "provider_running"
  | "awaiting_selection"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "submission_unknown"
  | "cancellation_unknown";

export interface TaskCancellationPolicyDto {
  allowed: boolean;
  mode: "local_before_provider" | "provider_queued" | "reconcile_required" | "unavailable";
  label: string;
  disabledReason?: string | null;
  providerStatus: string;
  costMayAlreadyApply: boolean;
}

export interface CreatorTaskDto {
  taskId: string;
  projectId: string;
  creatorShotId?: string | null;
  generationSnapshotId: string;
  status: CreatorTaskStatus;
  providerStatus: string;
  providerTaskId?: string | null;
  provider: string;
  model: string;
  inputHash: string;
  error?: Record<string, unknown> | null;
  submittedAt?: string | null;
  createdAt?: string;
  cancellation?: TaskCancellationPolicyDto;
}

export interface GenerationSnapshotDto {
  id: string;
  projectId: string;
  creatorShotId?: string | null;
  kind: "story_text" | "image" | "video" | "video_edit" | "composition";
  promptText: string;
  orderedReferences: CreatorReferenceDto[];
  providerConfig: Record<string, unknown>;
  inputHash: string;
  estimatedCostMicros?: number | null;
  confirmedAt?: string | null;
  createdAt: string;
}

export interface CreatorTimelineClipDto {
  creatorShotId: string;
  assetId: string;
  transition: "cut" | "fade_black" | "cross_dissolve";
  transitionDurationMs: number;
}

export interface CreatorTimelineDto {
  id?: string | null;
  projectId: string;
  version: number;
  clips: CreatorTimelineClipDto[];
  status: "draft" | "rendering" | "awaiting_selection" | "approved" | "failed";
  finalAssetId?: string | null;
}

export interface HealthDto {
  status: string;
  applicationVersion: string;
  alembicRevision: string;
  apiFeatures: string[];
}

export interface RuntimeSettingsDto {
  planningModel: string;
  imageModel: string;
  videoModel: string;
  videoResolution: string;
  preflight: Record<string, unknown>;
}
