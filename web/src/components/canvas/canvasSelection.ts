import type { CanvasEdgeDto, CanvasNodeDto } from "../../api/types";

const referenceSourceTypes = new Set<CanvasNodeDto["type"]>([
  "SubjectNode",
  "ReferenceAssetNode",
  "ImageAssetNode",
]);

export function referenceConnection(
  source: CanvasNodeDto,
  target: CanvasNodeDto,
): Omit<CanvasEdgeDto, "id"> | null {
  if (source.id === target.id) return null;
  if (source.type === "SubjectNode") {
    if (target.type === "StoryboardDirectorNode") {
      return edge(source, "subject[]", target, "subject[]");
    }
    if (target.type === "GenerationBatchNode") {
      return edge(source, "product_subject", target, "product_subject");
    }
    if (["ImageGenerationNode", "VideoGenerationNode"].includes(target.type)) {
      return edge(source, "subject[]", target, "subject[]");
    }
    return null;
  }
  if (source.type === "ReferenceAssetNode") {
    if (["GenerationBatchNode", "ImageGenerationNode", "VideoGenerationNode", "VideoEditNode"].includes(target.type)) {
      return edge(source, "media_reference[]", target, "media_reference[]");
    }
    return null;
  }
  if (source.type === "ImageAssetNode") {
    if (["ImageGenerationNode", "VideoGenerationNode"].includes(target.type)) {
      return edge(source, "image_asset", target, "image_reference[]");
    }
    if (["GenerationBatchNode", "VideoEditNode"].includes(target.type)) {
      return edge(source, "media_reference[]", target, "media_reference[]");
    }
  }
  return null;
}

export function planReferenceEdgeChanges(
  currentEdges: CanvasEdgeDto[],
  target: CanvasNodeDto,
  selectedSources: CanvasNodeDto[],
): { deleteEdgeIds: string[]; createEdges: Array<Omit<CanvasEdgeDto, "id">> } {
  const desired = selectedSources
    .map((source) => referenceConnection(source, target))
    .filter((item): item is Omit<CanvasEdgeDto, "id"> => item !== null);
  const current = currentEdges.filter((item) => (
    item.targetNodeId === target.id && referenceSourceTypes.has(item.sourceNodeType)
  ));
  const desiredKeys = new Set(desired.map(connectionKey));
  const currentKeys = new Set(current.map(connectionKey));
  return {
    deleteEdgeIds: current
      .filter((item) => !desiredKeys.has(connectionKey(item)))
      .flatMap((item) => item.id ? [item.id] : []),
    createEdges: desired.filter((item) => !currentKeys.has(connectionKey(item))),
  };
}

function edge(
  source: CanvasNodeDto,
  sourcePort: CanvasEdgeDto["sourcePort"],
  target: CanvasNodeDto,
  targetPort: CanvasEdgeDto["targetPort"],
): Omit<CanvasEdgeDto, "id"> {
  return {
    sourceNodeId: source.id,
    sourceNodeType: source.type,
    sourcePort,
    targetNodeId: target.id,
    targetNodeType: target.type,
    targetPort,
  };
}

function connectionKey(item: Omit<CanvasEdgeDto, "id">): string {
  return [item.sourceNodeId, item.sourcePort, item.targetNodeId, item.targetPort].join(":");
}
