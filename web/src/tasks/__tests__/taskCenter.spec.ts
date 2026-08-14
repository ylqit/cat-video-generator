import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({
  jobs: vi.fn(),
  projectTasks: vi.fn(),
  resumeStep: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: calls }));

describe("global task center", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("marks a persistent running provider step as needing recovery after restart", async () => {
    calls.jobs.mockResolvedValue([]);
    calls.projectTasks.mockResolvedValue([{
      stepId: "step-1",
      projectId: "project-1",
      sceneId: "scene-1",
      shotId: "shot-1",
      kind: "video",
      status: "running",
      attempt: 2,
      operationKey: "video:shot",
      provider: "fake",
      providerTaskId: "provider-1",
      model: "fake-seedance",
      inputSnapshot: {},
      error: null,
      createdAt: "2026-08-14T01:00:00Z",
    }]);
    calls.resumeStep.mockResolvedValue({ jobId: "resume-job-1" });
    const center = await import("../taskCenter");

    center.rememberProject("project-1");
    await center.refreshTaskCenter();

    const item = center.useTaskCenter().items.value[0];
    expect(item.stepId).toBe("step-1");
    expect(item.status).toBe("running");
    expect(item.providerTaskId).toBe("provider-1");
    expect(calls.resumeStep).toHaveBeenCalledWith("step-1");
  });

  it("registers a submitted job immediately without waiting for completion", async () => {
    calls.jobs.mockResolvedValue([{
      jobId: "job-1",
      kind: "generate_video",
      status: "running",
      context: { projectId: "project-1", shotId: "shot-1", operationKey: "video:shot" },
      result: null,
      error: null,
    }]);
    calls.projectTasks.mockResolvedValue([]);
    const center = await import("../taskCenter");

    center.registerTask("job-1", {
      kind: "generate_video",
      label: "视频片段",
      projectId: "project-1",
      shotId: "shot-1",
      operationKey: "video:shot",
    });

    expect(center.useTaskCenter().items.value[0].status).toBe("queued");
    await center.refreshTaskCenter();
    expect(center.useTaskCenter().items.value[0].status).toBe("running");
  });

  it("does not treat a completed sequence business status as a running task", async () => {
    calls.jobs.mockResolvedValue([{
      jobId: "sequence-job-1",
      kind: "build_sequence",
      status: "succeeded",
      context: { projectId: "project-1", operationKey: "sequence:build" },
      result: { id: "sequence-1", status: "content_review" },
      error: null,
    }]);
    calls.projectTasks.mockResolvedValue([]);
    const center = await import("../taskCenter");

    center.registerTask("sequence-job-1", {
      kind: "build_sequence",
      label: "本地成片合成",
      projectId: "project-1",
      operationKey: "sequence:build",
    });
    await center.refreshTaskCenter();

    expect(center.useTaskCenter().items.value[0].status).toBe("succeeded");
  });
});
