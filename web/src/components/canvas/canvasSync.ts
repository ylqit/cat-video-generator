import { ApiError } from "../../api/client";

import type { CanvasDto } from "../../api/types";

export interface PendingCanvasOperation {
  operationId: string;
  type: string;
  [key: string]: unknown;
}

export interface QuarantinedCanvasOperation extends PendingCanvasOperation {
  reason: unknown;
  quarantinedAt: string;
}

export function classifyCanvasSaveError(
  error: unknown,
  online: boolean,
): CanvasDto["syncStatus"] {
  if (!online) return "offline";
  if (error instanceof ApiError && [409, 412].includes(error.status)) return "conflict";
  return "service_error";
}

const key = (projectId: string) => `aigc-canvas:${projectId}:pending-layout-operations`;
const quarantineKey = (projectId: string) => `aigc-canvas:${projectId}:quarantined-layout-operations`;

function read(projectId: string): PendingCanvasOperation[] {
  const value = localStorage.getItem(key(projectId));
  if (!value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export const canvasSyncQueue = {
  pending: read,
  enqueue(projectId: string, operation: PendingCanvasOperation) {
    const pending = read(projectId).filter((item) => item.operationId !== operation.operationId);
    localStorage.setItem(key(projectId), JSON.stringify([...pending, operation]));
  },
  confirm(projectId: string, operationIds: string[]) {
    const confirmed = new Set(operationIds);
    localStorage.setItem(
      key(projectId),
      JSON.stringify(read(projectId).filter((item) => !confirmed.has(item.operationId))),
    );
  },
  quarantine(projectId: string, operationIds: string[], reason: unknown) {
    const rejected = new Set(operationIds);
    const operations = read(projectId);
    const existingValue = localStorage.getItem(quarantineKey(projectId));
    let existing: QuarantinedCanvasOperation[] = [];
    if (existingValue) {
      try {
        const parsed = JSON.parse(existingValue);
        if (Array.isArray(parsed)) existing = parsed;
      } catch {
        existing = [];
      }
    }
    const quarantinedAt = new Date().toISOString();
    const quarantined = operations
      .filter((item) => rejected.has(item.operationId))
      .map((item) => ({ ...item, reason, quarantinedAt }));
    localStorage.setItem(quarantineKey(projectId), JSON.stringify([...existing, ...quarantined]));
    localStorage.setItem(
      key(projectId),
      JSON.stringify(operations.filter((item) => !rejected.has(item.operationId))),
    );
  },
  quarantined(projectId: string): QuarantinedCanvasOperation[] {
    const value = localStorage.getItem(quarantineKey(projectId));
    if (!value) return [];
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  },
};
