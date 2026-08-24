import type { CanvasNodeDto } from "../../api/types";
import type { TaskCenterItem } from "../../tasks/taskCenter";

function matchesExecutionScope(task: TaskCenterItem, node: CanvasNodeDto): boolean {
  const scope = node.executionScope;
  if (!scope) return false;
  if (task.canvasNodeId) return task.canvasNodeId === node.id;
  if (scope.businessObjectId && task.businessObjectId) {
    return task.businessObjectId === scope.businessObjectId;
  }
  if (scope.sceneId && task.sceneId) return task.sceneId === scope.sceneId;
  if (scope.shotId && task.shotId) return task.shotId === scope.shotId;

  const operations = scope.operationKeys ?? [];
  const phases = scope.phases ?? [];
  if (!task.operationKey || !operations.includes(task.operationKey)) return false;
  if (phases.length && !phases.some((phase) => phase === task.phase)) return false;
  if (scope.recipeInstanceId && task.recipeInstanceId !== scope.recipeInstanceId) return false;
  if (scope.canvasGroupId && task.canvasGroupId !== scope.canvasGroupId) return false;
  return Boolean(scope.recipeInstanceId || scope.canvasGroupId);
}

export function tasksForCanvasNode(
  tasks: readonly TaskCenterItem[],
  node: CanvasNodeDto,
): TaskCenterItem[] {
  const direct = tasks.filter((task) => matchesExecutionScope(task, node));
  if (!node.executionScope?.includeChildTasks || !direct.length) {
    return newestFirst(direct);
  }

  const parentIds = new Set(direct.flatMap((task) => task.stepId ? [task.stepId] : []));
  const declaredChildIds = new Set(direct.flatMap((task) => task.childStepIds ?? []));
  const related = tasks.filter((task) => (
    direct.includes(task)
    || Boolean(task.parentStepId && parentIds.has(task.parentStepId))
    || Boolean(task.stepId && declaredChildIds.has(task.stepId))
  ));
  return newestFirst([...new Map(related.map((task) => [task.key, task])).values()]);
}

export function latestTaskForCanvasNode(
  tasks: readonly TaskCenterItem[],
  node: CanvasNodeDto,
): TaskCenterItem | undefined {
  return tasksForCanvasNode(tasks, node)[0];
}

function newestFirst(tasks: TaskCenterItem[]): TaskCenterItem[] {
  return [...tasks].sort((left, right) => String(
    right.createdAt ?? right.updatedAt,
  ).localeCompare(String(left.createdAt ?? left.updatedAt)));
}
