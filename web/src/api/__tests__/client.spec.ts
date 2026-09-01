import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiTimeoutError, creatorApi, request } from "../client";

describe("API request lifecycle", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("aborts a hanging request after the default 15 second deadline", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));

    const assertion = expect(request("/health")).rejects.toBeInstanceOf(ApiTimeoutError);
    await vi.advanceTimersByTimeAsync(15_000);
    await assertion;
  });

  it("preserves an external abort signal instead of misreporting it as a timeout", async () => {
    const external = new AbortController();
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));

    const pending = request("/health", { signal: external.signal });
    external.abort("route changed");

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("lets a Creator state load be cancelled when the project route changes", async () => {
    const external = new AbortController();
    vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));

    const pending = creatorApi.state("project-1", external.signal);
    external.abort("project changed");

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("loads Creator state and current shots without a Canvas read model", async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(new Response(JSON.stringify(
      url.endsWith("/creator-state")
        ? { projectId: "project-1", version: 1, briefBody: "brief", storyCandidates: [], currentStory: {}, targetDurationSeconds: 8, aspectRatio: "9:16", qualityTier: "quick", referenceBindings: [] }
        : [],
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);

    const state = await creatorApi.state("project-1");
    const shots = await creatorApi.shots("project-1");

    expect(state.projectId).toBe("project-1");
    expect(shots).toEqual([]);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v2/projects/project-1/creator-state",
      "/api/v2/projects/project-1/shots",
    ]);
  });
});
