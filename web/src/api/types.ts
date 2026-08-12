export type AnchorMode = "text_only" | "existing" | "generate";
export type ReferenceUsage = "approved_anchor" | "generation_reference";
export type ReferenceRole = "identity" | "style" | "scene" | "prop" | "composition";
export type ReferenceTarget = "anchor" | "video" | "both";
export type StoryMode = "single" | "multi";
export type EnvironmentStyle = "outdoor" | "indoor";
export type LookReferencePurpose =
  | "person_identity"
  | "person_body"
  | "cat_identity"
  | "style"
  | "wardrobe"
  | "prop"
  | "composition";

export interface ReferenceBinding {
  assetId: string;
  usage: ReferenceUsage;
  role: ReferenceRole;
  applyTo: ReferenceTarget;
}

export interface ProjectSummary {
  id: string;
  title: string;
  contentDate: string;
  status: string;
}

export interface AssetDto {
  id: string;
  role: string;
  mediaType: "image" | "video";
  scope: string;
  status: string;
  projectId?: string | null;
  sceneId?: string | null;
  shotId?: string | null;
  producingStepId?: string | null;
  sha256: string;
  semanticKey?: string | null;
  metadata: Record<string, unknown>;
  contentReady: boolean;
  displayName: string;
  referencePurpose?: LookReferencePurpose | null;
  visualProfileRevisionId?: string | null;
  lookDraftRevision?: number | null;
  createdAt?: string | null;
}

export interface LookReferenceBinding {
  assetId: string;
  purpose: LookReferencePurpose;
  instruction: string;
}

export interface VisualProfileDraft {
  personIdentity: string;
  personHair: string;
  personBody: string;
  catIdentity: string;
  stylePositive: string[];
  styleNegative: string[];
  referenceBindings: LookReferenceBinding[];
}

export interface VisualProfileRevisionDto extends VisualProfileDraft {
  id: string;
  projectId: string;
  revision: number;
  profileHash: string;
  sourceProfileId: string;
  createdAt?: string | null;
  canonDefaults?: VisualProfileDraft;
  referenceSnapshot: Array<LookReferenceBinding & { semanticKey?: string | null; sha256: string }>;
}

export interface SceneLookPlan {
  personWardrobe: string;
  personAccessories: string;
  catAppearance: string;
  keyProps: string;
  environmentStyle: EnvironmentStyle;
  personPose: string;
  catPose: string;
  composition: string;
  additionalInstructions: string;
  imageRecommended: boolean;
  recommendationReason?: string | null;
}

export interface SceneLookDraftDto {
  visualProfileRevisionId: string;
  lookPlan: SceneLookPlan;
  referenceBindings: LookReferenceBinding[];
}

export interface SceneLookDraftEnvelope {
  sceneId: string;
  revision: number;
  draft: SceneLookDraftDto;
}

export interface SceneLookPromptPreview {
  prompt: string;
  charCount: number;
  utf8Bytes: number;
  referenceCount: number;
  references: Array<{
    index: number;
    assetId: string;
    sha256: string;
    semanticKey?: string | null;
    purpose: LookReferencePurpose;
    instruction: string;
    contentReady: boolean;
  }>;
  warnings: string[];
  visualProfileRevisionId: string;
  visualProfileRevision: number;
  draftRevision: number;
}

export interface SceneLookVersion extends AssetDto {
  selected: boolean;
  attempt?: number | null;
  prompt?: PromptDto | null;
  inputSnapshot: Record<string, unknown>;
}

export interface ShotSuggestion {
  title: string;
  direction: string;
  suggestedDurationSeconds: number;
}

export interface ShotSuggestionOutput {
  sceneTitle: string;
  lookPlan: SceneLookPlan;
  shots: ShotSuggestion[];
}

export interface PromptDto {
  id: string;
  purpose: string;
  model: string;
  text: string;
  sha256: string;
}

export interface AttemptDto {
  id: string;
  kind: string;
  status: string;
  attempt: number;
  operationKey: string;
  provider?: string | null;
  providerTaskId?: string | null;
  model?: string | null;
  inputSnapshot: Record<string, unknown>;
  error?: Record<string, unknown> | null;
  prompt?: PromptDto | null;
  reviews: Array<{
    id: string;
    source: string;
    decision: string;
    reason?: string | null;
    warnings: Array<Record<string, unknown>>;
    evidence: Record<string, unknown>;
  }>;
}

export interface ShotDto {
  id: string;
  sceneId: string;
  order: number;
  title: string;
  direction: string;
  durationSeconds: number;
  anchorMode: AnchorMode;
  referenceBindings: ReferenceBinding[];
  inheritProjectReferences: boolean;
  useSceneLook: boolean;
  status: string;
  selectedAnchorAssetId?: string | null;
  selectedVideoAssetId?: string | null;
  assets: AssetDto[];
  attempts: AttemptDto[];
}

export interface SceneDto {
  id: string;
  order: number;
  title: string;
  sourceText: string;
  chapterLabel?: string | null;
  contextNote?: string | null;
  storyMode: StoryMode;
  targetShotCount: number;
  lookPlan?: SceneLookPlan | null;
  selectedLookAssetId?: string | null;
  lookDraftRevision: number;
  status: string;
  attempts: AttemptDto[];
  shots: ShotDto[];
}

export interface SequenceDto {
  id: string;
  projectId: string;
  revision: number;
  parentSequenceId?: string | null;
  renderedAssetId?: string | null;
  status: string;
  plan: {
    duration_ms: number;
    clips: Array<Record<string, unknown>>;
  };
}

export interface ProjectGraph {
  project: ProjectSummary & {
    selectedSequenceId?: string | null;
    contractVersion: number;
    defaultReferenceBindings: ReferenceBinding[];
    visualProfileRevisionId?: string | null;
  };
  assets: AssetDto[];
  scenes: SceneDto[];
  sequences: SequenceDto[];
}

export interface JobDto {
  jobId: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed";
  context: Record<string, string>;
  result?: unknown;
  error?: Record<string, unknown> | null;
}

export interface HealthDto {
  ready: boolean;
  databaseReady: boolean;
  contractVersion: number;
  alembicRevision: string;
  expectedAlembicRevision: string;
  arkImageModel?: string;
  arkVideoModel?: string;
  arkReady?: boolean;
  ffmpegAvailable?: boolean;
  ffprobeAvailable?: boolean;
  videoGenerationReady?: boolean;
  localCompositionReady?: boolean;
  configurationWarnings?: string[];
  generationConfigurationValid?: boolean;
}
