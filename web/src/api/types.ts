export type AnchorMode = "text_only" | "existing" | "generate";
export type SceneLookUsage = "off" | "appearance_only" | "full_reference" | "derive_anchor";
export type ReferenceUsage = "approved_anchor" | "generation_reference";
export type ReferenceRole = "identity" | "style" | "scene" | "prop" | "composition";
export type ReferenceTarget = "anchor" | "video" | "both";
export type StoryMode = "single" | "multi";
export type StoryRewriteStrategy = "conservative" | "balanced" | "creative";
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

export interface StoryIssue {
  category:
    | "continuity"
    | "canon_conflict"
    | "physical_feasibility"
    | "action_density"
    | "causality"
    | "human_cat_interaction"
    | "generation_clarity"
    | "other";
  evidence: string;
  impact: string;
  suggestion: string;
}

export interface StoryDiagnosisOutput {
  overallAssessment: string;
  issues: StoryIssue[];
  rewriteOptions: Array<{
    strategy: StoryRewriteStrategy;
    title: string;
    summary: string;
    tradeoffs: string;
  }>;
}

export interface StoryRewriteOutput {
  rewrittenStory: string;
  changeSummary: string[];
  unresolvedQuestions: string[];
}

export interface CreativeStepRecord {
  stepId: string;
  operationKey: string;
  status: string;
  attempt: number;
  model?: string | null;
  sourceHash?: string | null;
  providerOutput?: Record<string, unknown> | null;
  acceptedOutput?: Record<string, unknown> | null;
  acceptedAt?: string | null;
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
}

export interface CreativeWorkflowDto {
  sceneId: string;
  originalStory: string;
  currentStory: string;
  currentStoryHash: string;
  currentStorySource: "scene_draft" | "preserved_original" | "accepted_rewrite";
  currentStorySourceStepId?: string | null;
  currentShotSnapshotHash: string;
  stages: {
    diagnosis: CreativeStepRecord[];
    rewrite: CreativeStepRecord[];
    storyboard: CreativeStepRecord[];
  };
  reviews: CreativeStepRecord[];
}

export type SuggestionApplyMode = "replace" | "update_existing";

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
  draftRevision: number;
  anchorMode: AnchorMode;
  referenceBindings: ReferenceBinding[];
  inheritProjectReferences: boolean;
  sceneLookUsage: SceneLookUsage;
  /** V5 compatibility projection; sceneLookUsage is authoritative. */
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
    clips: SequenceClipDto[];
  };
}

export type SequenceTransitionType = "cut" | "fade_black" | "cross_dissolve";

export interface SequenceTransitionDto {
  type: SequenceTransitionType;
  durationMs: number;
}

export interface SequenceClipDto {
  order: number;
  shot_card_id: string;
  source_asset_id: string;
  source_start_ms: number;
  source_end_ms: number;
  timeline_start_ms: number;
  timeline_end_ms: number;
  transitionFromPrevious?: SequenceTransitionDto | null;
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
  arkPlanningModel?: string;
  arkReviewModel?: string;
  arkReady?: boolean;
  ffmpegAvailable?: boolean;
  ffprobeAvailable?: boolean;
  videoGenerationReady?: boolean;
  localCompositionReady?: boolean;
  configurationWarnings?: string[];
  generationConfigurationValid?: boolean;
}

export interface ShotRuleFinding {
  code: string;
  severity: "info" | "warning";
  message: string;
}

export interface ShotLocalAnalysis {
  suggestedSubshotMin: number;
  suggestedSubshotMax: number;
  detectedSubshotCount: number;
  actionCount: number | null;
  cameraMoveCount: number | null;
  hasStableEnding: boolean | null;
  hasSound: boolean | null;
  qualitativePacing: string;
  findings: ShotRuleFinding[];
}

export interface PreviousTailStatus {
  available: boolean;
  reason?: string;
  previousShotId?: string;
  sourceVideoAssetId?: string | null;
  assetId?: string | null;
  boundAssetId?: string | null;
  stale: boolean;
}

export interface ShotPromptPreview {
  prompt: string;
  creativeBody: string;
  systemShell: string;
  charCount: number;
  utf8Bytes: number;
  draftRevision: number;
  sceneLookUsage: SceneLookUsage;
  localAnalysis: ShotLocalAnalysis;
  qualitativePacing: string;
  linkWarnings: string[];
  references: Array<{
    index: number;
    assetId: string;
    displayName: string;
    promptAlias: string;
    subjectLabel: string;
    sourceLayer: "shot" | "scene_look" | "project" | "previous_tail" | "candidate";
    responsibility: string;
    contentReady: boolean;
  }>;
  previousTail: PreviousTailStatus;
}

export interface ShotAssistPatch {
  title?: string;
  direction?: string;
  durationSeconds?: number;
  sceneLookUsage?: SceneLookUsage;
  anchorMode?: AnchorMode;
  referenceBindings?: ReferenceBinding[];
}

export interface ShotAssistAnalysis {
  actionDensityAssessment: string;
  assetCompatibilityAssessment?: string;
  pacingPlan: {
    recommendedDurationSeconds: number;
    rationale: string;
    beats: Array<{ ordinal: number; description: string; rhythm: "brief" | "standard" | "expanded" }>;
  };
  recommendedSceneLookUsage: SceneLookUsage;
  recommendedAnchorMode: AnchorMode;
  referenceDecisions: Array<{
    assetId: string;
    decision: "keep" | "remove" | "change_role";
    recommendedRole?: ReferenceRole | null;
    reason: string;
  }>;
  continuity: { previousIssues: string[]; nextIssues: string[]; recommendation: string };
  promptRisks: string[];
  creativeBody?: string | null;
  creativeAlternatives?: Array<{
    label: "conservative" | "stable";
    body: string;
    rationale: string;
  }>;
  patch?: ShotAssistPatch | null;
}

export interface ShotAssistRecord {
  stepId: string;
  status: string;
  sourceDraftRevision: number;
  stale: boolean;
  analysis?: ShotAssistAnalysis | null;
  acceptedOutput?: ShotAssistPatch | null;
  acceptedAt?: string | null;
  error?: Record<string, unknown> | null;
  createdAt?: string | null;
}

export interface ShotAssistContext {
  shotId: string;
  sourceDraftRevision: number;
  model?: string | null;
  localAnalysis: ShotLocalAnalysis;
  previousShot?: { id: string; title: string } | null;
  nextShot?: { id: string; title: string } | null;
  previousTail: PreviousTailStatus;
  candidates: Array<{
    assetId: string;
    displayName: string;
    sha256: string;
    sourceLayer: string;
    responsibility: string;
    contentReady: boolean;
    available: boolean;
    duplicate: boolean;
  }>;
  defaultCandidateAssetIds: string[];
  warnings: string[];
}
