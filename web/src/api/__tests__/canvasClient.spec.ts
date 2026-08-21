import { afterEach, describe, expect, it, vi } from "vitest";

import { canvasApi } from "../client";

describe("canvasApi.saveLayout", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("does not send domain edges in a layout-only request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      projectId: "project-1",
      layoutVersion: 2,
      syncStatus: "saved",
      viewport: { x: 0, y: 0, zoom: 1 },
      rebasedFromVersion: null,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await canvasApi.saveLayout("project-1", 1, {
      nodes: [{ nodeId: "node-1", x: 12, y: 18 }],
      viewport: { x: 0, y: 0, zoom: 1 },
      operations: [{ operationId: "op-1", type: "move_node" }],
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.edges).toBeUndefined();
    expect(body.nodes).toEqual([{ nodeId: "node-1", x: 12, y: 18 }]);
  });

  it("loads frozen subjects and binds semantic assets with optimistic concurrency", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "node-1",
        type: "ReferenceAssetNode",
        revision: 2,
        status: "ready",
        data: { assets: [] },
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await canvasApi.subjects("project-1");
    await canvasApi.bindNodeAssets("node-1", 1, [{
      assetId: "asset-1",
      semanticRole: "packshot_front",
    }], false);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v2/projects/project-1/subjects");
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("/api/v2/canvas/nodes/node-1/asset-bindings");
    expect(init.headers).toMatchObject({ "If-Match": "1" });
    expect(JSON.parse(String(init.body))).toEqual({
      bindings: [{ assetId: "asset-1", semanticRole: "packshot_front" }],
      allowMove: false,
    });
  });
});
