import { expect, test } from "@playwright/test";

const runId = "11111111-1111-4111-8111-111111111111";
const episodeId = "22222222-2222-4222-8222-222222222222";
const assetId = "33333333-3333-4333-8333-333333333333";
const sequenceId = "44444444-4444-4444-8444-444444444444";

function node(
  semanticNodeId: string,
  type: string,
  label: string,
  availability: string,
  slot: string | null = null,
) {
  return {
    semanticNodeId,
    type,
    slot,
    label,
    status: availability === "locked" ? "locked" : "pending",
    availability,
    executionStatus: "not_created",
    lockReason: availability === "locked" ? "Morning结果卡尚未确认" : null,
    unlockRequirements: availability === "locked" ? ["确认Morning结果卡"] : [],
    providerStatus: "not_applicable",
    contractStatus: "not_applicable",
    semanticReviewStatus: "not_applicable",
    stepId: semanticNodeId === "morning:video" ? "55555555-5555-4555-8555-555555555555" : null,
    promptIds: [],
    assetIds: semanticNodeId === "morning:video" ? [assetId] : [],
    reviewIds: [],
    error: null,
    nextAction: availability === "locked" ? "确认Morning结果卡" : null,
    allowedActions: [],
    currentAttemptId: null,
    attemptIds: [],
    attempts: [],
  };
}

const workflowNodes = [
  node("run:day-director", "director", "全天总导演", "completed"),
  node("run:day-confirmation", "day_confirmation", "DayBrief人工确认", "completed"),
  node("morning:director", "director", "Morning导演", "completed", "morning"),
  node("morning:look", "look", "Morning定妆图", "completed", "morning"),
  node("morning:opening-anchor", "opening_anchor", "Morning开场锚点", "completed", "morning"),
  {
    ...node("morning:video", "video", "Morning视频与版本", "active", "morning"),
    executionStatus: "awaiting_review",
    status: "awaiting_review",
  },
  node("morning:review", "content_review", "Morning内容审核", "ready", "morning"),
  node("morning:outcome", "accepted_outcome", "Morning结果卡", "locked", "morning"),
  node("noon:director", "director", "Noon导演", "locked", "noon"),
  node("noon:look", "look", "Noon定妆图", "locked", "noon"),
  node("noon:opening-anchor", "opening_anchor", "Noon开场锚点", "locked", "noon"),
  node("noon:video", "video", "Noon视频", "locked", "noon"),
  node("noon:review", "content_review", "Noon内容审核", "locked", "noon"),
  node("noon:outcome", "accepted_outcome", "Noon结果卡", "locked", "noon"),
  node("evening:director", "director", "Evening导演", "locked", "evening"),
  node("evening:look", "look", "Evening定妆图", "locked", "evening"),
  node("evening:opening-anchor", "opening_anchor", "Evening开场锚点", "locked", "evening"),
  node("evening:video", "video", "Evening视频", "locked", "evening"),
  node("evening:review", "content_review", "Evening内容审核", "locked", "evening"),
  node("evening:outcome", "accepted_outcome", "Evening结果卡", "locked", "evening"),
  node("run:delivery", "delivery", "审核交付", "locked"),
];

const episode = {
  id: episodeId,
  runId,
  slot: "morning",
  sortOrder: 1,
  title: "池塘浮标的小信号",
  status: "content_review",
  activityFocus: "cat_lead",
  relationshipArc: "猫咪先发现浮标变化，人物随后稳定收竿回应。",
  renderPlan: {
    mode: "single_pass",
    total_duration_seconds: 12,
    sections: [{ order: 1, duration_seconds: 12, shot_orders: [1, 2] }],
  },
  nextAction: "人工审核视频",
  selectedVideoAssetId: null,
  promptOverrides: {},
  promptOverrideState: { enabled: false, stale: false, sourceScriptSha256: null, values: {} },
  script: {
    title: "池塘浮标的小信号",
    event_key: "morning_fishing_signal",
    location_key: "pond_bank",
    visual_context: "outdoor",
    activity_focus: "cat_lead",
    duration_seconds: 12,
    appearance: "同一个中性短发儿童和同一只灰白猫。",
    story_text: "猫咪先观察浮标，人物随后持竿回应。",
    relationship_arc: "猫咪提示信号，人物回应后形成回报。",
    shots: [
      { order: 1, direction: "固定中景，猫咪观察浮标。" },
      { order: 2, direction: "近景，人物稳定收竿回应。" },
    ],
    hard_constraints: [],
    sound_design: "水声、风声和线轮声，无对白。",
    ending: "猫咪回到人物脚边，浮标信号得到回应。",
  },
};

const graph = {
  run: {
    id: runId,
    contractVersion: 2,
    contentDate: "2026-08-12",
    theme: "池塘边钓鱼",
    status: "generating",
    nextAction: "人工审核morning视频",
    availableActions: [],
    pipelineSettings: {
      planningMode: "guided_sequential",
      allowPaidGeneration: true,
      dayBrief: "manual",
      script: "manual",
      visual: "manual",
      video: "manual",
      review: "manual",
    },
    createdAt: "2026-08-12T08:00:00Z",
    updatedAt: "2026-08-12T08:00:00Z",
    currentStage: "video",
    acceptedOutcomes: {},
    slotPlanning: [
      { slot: "morning", planned: true, unlocked: true, blockReason: null, outcomeConfirmed: false },
      { slot: "noon", planned: false, unlocked: false, blockReason: "Morning结果卡尚未确认", outcomeConfirmed: false },
      { slot: "evening", planned: false, unlocked: false, blockReason: "Noon结果卡尚未确认", outcomeConfirmed: false },
    ],
  },
  episodes: [episode],
  steps: [],
  prompts: [],
  assets: [{
    id: assetId,
    runId,
    episodeId,
    stepId: "55555555-5555-4555-8555-555555555555",
    role: "video",
    semanticKey: "video:morning:section-1",
    scope: "episode",
    status: "candidate",
    mediaType: "video",
    localPath: "ignored.mp4",
    sha256: "a".repeat(64),
    metadata: { qc: { durationMs: 12_000 }, shotBoundariesMs: [0, 6000, 12000] },
  }],
  reviews: [],
  workflowNodes,
  videoSequences: [{
    id: sequenceId,
    episodeId,
    revision: 1,
    parentSequenceId: null,
    baseAssetId: assetId,
    renderedAssetId: assetId,
    status: "content_review",
    durationMs: 12_000,
    audioPolicy: "preserve_original",
    clips: [{
      order: 1,
      source_asset_id: assetId,
      source_start_ms: 0,
      source_end_ms: 12_000,
      timeline_start_ms: 0,
      timeline_end_ms: 12_000,
      origin: "original",
      replacement_step_id: null,
    }],
    createdAt: "2026-08-12T08:00:00Z",
    updatedAt: "2026-08-12T08:00:00Z",
  }],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/health", (route) => route.fulfill({
    json: {
      database: "test",
      user: "test",
      alembicRevision: "0013_canvas_video_sequences",
      expectedAlembicRevision: "0013_canvas_video_sequences",
      ready: true,
    },
  }));
  await page.route(`**/api/v1/runs/${runId}/graph`, (route) => route.fulfill({ json: graph }));
  await page.route("**/api/v1/jobs", (route) => route.fulfill({ json: [] }));
  await page.route(`**/api/v1/assets/${assetId}/content`, (route) => route.abort());
});

test("顺序画布展示锁定未来节点且轮询不改变当前节点", async ({ page }) => {
  await page.goto(`/studio?run=${runId}&stage=script`);

  await expect(page.getByText("Noon导演", { exact: true })).toBeVisible();
  await expect(page.getByText("Morning结果卡尚未确认").first()).toBeVisible();
  await page.getByText("Noon导演", { exact: true }).click();
  await expect(page).toHaveURL(/node=noon:director/);

  await page.waitForTimeout(5200);
  await expect(page).toHaveURL(/stage=script/);
  await expect(page).toHaveURL(/node=noon:director/);
});

test("视频语义节点在同屏展开非破坏性时间轴并保持版本URL", async ({ page }) => {
  await page.goto(`/studio?run=${runId}&stage=video`);
  await page.getByText("Morning视频与版本", { exact: true }).click();

  await expect(page.getByText("池塘浮标的小信号 · 非破坏性时间轴")).toBeVisible();
  await expect(page.getByRole("button", { name: "整条重新生成" })).toBeVisible();
  await expect(page.getByRole("button", { name: "选区重新生成" })).toBeVisible();
  await expect(page).toHaveURL(/node=morning:video/);
  await expect(page).toHaveURL(new RegExp(`sequence=${sequenceId}`));
});
