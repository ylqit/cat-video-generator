import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({
  taskCenter: vi.fn(),
  taskCenterEventsUrl: vi.fn(),
  resumeStep: vi.fn(),
}));

vi.mock("../../api/client", () => ({ api: calls }));

describe("global task center", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("keeps a persistent running provider step visible without resubmitting it", async () => {
    calls.taskCenter.mockResolvedValue({ runtimeJobs: [], persistentTasks: [{
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
    }] });
    calls.resumeStep.mockResolvedValue({ jobId: "resume-job-1" });
    const center = await import("../taskCenter");

    await center.refreshTaskCenter();

    const item = center.useTaskCenter().items.value[0];
    expect(item.stepId).toBe("step-1");
    expect(item.status).toBe("running");
    expect(item.providerTaskId).toBe("provider-1");
    expect(calls.resumeStep).not.toHaveBeenCalled();
  });

  it("registers a submitted job immediately without waiting for completion", async () => {
    calls.taskCenter.mockResolvedValue({ runtimeJobs: [{
      jobId: "job-1",
      kind: "generate_video",
      status: "running",
      context: { projectId: "project-1", shotId: "shot-1", operationKey: "video:shot" },
      result: null,
      error: null,
    }], persistentTasks: [] });
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
    calls.taskCenter.mockResolvedValue({ runtimeJobs: [{
      jobId: "sequence-job-1",
      kind: "build_sequence",
      status: "succeeded",
      context: { projectId: "project-1", operationKey: "sequence:build" },
      result: { id: "sequence-1", status: "content_review" },
      error: null,
    }], persistentTasks: [] });
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

  it("applies durable SSE progress once and refreshes the affected project projection", async () => {
    class FakeEventSource {
      static latest: FakeEventSource;
      readonly listeners = new Map<string, (event: MessageEvent<string>) => void>();
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      closed = false;

      constructor(readonly url: string) {
        FakeEventSource.latest = this;
      }

      addEventListener(type: string, listener: EventListener) {
        this.listeners.set(type, listener as (event: MessageEvent<string>) => void);
      }

      emit(type: string, sequence: number, data: Record<string, unknown>) {
        this.listeners.get(type)?.(new MessageEvent(type, {
          data: JSON.stringify(data),
          lastEventId: String(sequence),
        }));
      }

      close() {
        this.closed = true;
      }
    }

    vi.stubGlobal("EventSource", FakeEventSource);
    calls.taskCenter.mockResolvedValue({ runtimeJobs: [], persistentTasks: [] });
    calls.taskCenterEventsUrl.mockReturnValue("/api/v1/task-center/events?afterEventId=0");
    const center = await import("../taskCenter");

    center.startTaskCenter();
    await vi.waitFor(() => expect(calls.taskCenter).toHaveBeenCalled());
    FakeEventSource.latest.onopen?.();
    FakeEventSource.latest.emit("task_queued", 41, {
      stepId: "step-sse",
      projectId: "project-sse",
      operationKey: "recipe:story",
      kind: "director",
      status: "queued",
    });
    FakeEventSource.latest.emit("task_progress", 42, {
      stepId: "step-sse",
      projectId: "project-sse",
      operationKey: "recipe:story",
      kind: "director",
      status: "running",
      progress: { currentStep: 1, totalSteps: 3, percent: 33, message: "生成候选" },
    });
    FakeEventSource.latest.emit("task_failed", 42, {
      stepId: "step-sse",
      projectId: "project-sse",
      status: "failed",
    });

    const task = center.useTaskCenter().items.value[0];
    expect(task.status).toBe("running");
    expect(task.progress?.percent).toBe(33);
    expect(window.localStorage.getItem("cvg.v5.task-event-cursor")).toBe("42");

    FakeEventSource.latest.emit("canvas_projection_changed", 43, {
      projectId: "project-sse",
      operationKey: "recipe:story",
    });
    expect(center.useTaskCenter().projectSignals.value["project-sse"]?.revision).toBe(1);
    expect(window.localStorage.getItem("cvg.v5.task-event-cursor")).toBe("43");

    center.stopTaskCenter();
    expect(FakeEventSource.latest.closed).toBe(true);
    vi.unstubAllGlobals();
  });
});
