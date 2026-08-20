export interface PendingCanvasOperation {
  operationId: string;
  type: string;
  [key: string]: unknown;
}

const key = (projectId: string) => `aigc-canvas:${projectId}:pending-layout-operations`;

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
};
