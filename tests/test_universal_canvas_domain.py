from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.aigc_canvas import (
    CanvasConnection,
    CanvasNodeType,
    CanvasPortType,
    SubjectDraft,
)
from cat_video_generator.domain.universal_canvas import (
    AnnotationTool,
    CanvasTemplateKey,
    ProviderEditCapability,
    VideoEditAnnotation,
    VideoEditRecipeDraft,
    compile_video_edit_plan,
    template_spec,
)


def test_product_subject_and_reference_semantics_are_first_class() -> None:
    subject = SubjectDraft(
        name="蓝色汽水罐",
        kind="product",
        role="hero_product",
        identityAnchors=["蓝色罐身、红白圆形标志、330ml"],
        immutableTraits=["标签文字和罐体比例不能改变"],
        references=[
            {
                "assetId": str(uuid.uuid4()),
                "semanticRole": "packshot_front",
                "instruction": "保持正面包装结构",
            }
        ],
    )

    assert subject.kind.value == "product"
    assert subject.role.value == "hero_product"
    assert subject.references[0].semantic_role == "packshot_front"


def test_product_template_defaults_to_four_candidates_without_story_requirements() -> None:
    spec = template_spec(CanvasTemplateKey.PRODUCT_AD)

    assert spec.default_candidate_count == 4
    assert 1 <= spec.default_candidate_count <= 8
    assert "GenerationBatchNode" in spec.node_types
    assert "StoryPlannerNode" not in spec.node_types


def test_universal_canvas_accepts_reference_to_batch_and_video_to_edit_edges() -> None:
    reference_edge = CanvasConnection(
        sourceNodeId=uuid.uuid4(),
        sourceNodeType=CanvasNodeType.REFERENCE_ASSET,
        sourcePort=CanvasPortType.MEDIA_REFERENCES,
        targetNodeId=uuid.uuid4(),
        targetNodeType=CanvasNodeType.GENERATION_BATCH,
        targetPort=CanvasPortType.MEDIA_REFERENCES,
    )
    edit_edge = CanvasConnection(
        sourceNodeId=uuid.uuid4(),
        sourceNodeType=CanvasNodeType.VIDEO_ASSET,
        sourcePort=CanvasPortType.VIDEO_ASSET,
        targetNodeId=uuid.uuid4(),
        targetNodeType=CanvasNodeType.VIDEO_EDIT,
        targetPort=CanvasPortType.VIDEO_ASSET,
    )

    assert reference_edge.target_node_type is CanvasNodeType.GENERATION_BATCH
    assert edit_edge.target_node_type is CanvasNodeType.VIDEO_EDIT


def test_video_edit_recipe_enforces_one_provider_sized_interval_and_normalized_marks() -> None:
    recipe = VideoEditRecipeDraft(
        projectId=uuid.uuid4(),
        sourceAssetId=uuid.uuid4(),
        startMs=4_000,
        endMs=10_000,
        instruction="保持产品标签不变，让人物手指离开标志区域",
        referenceAssetIds=[uuid.uuid4()],
        annotations=[
            VideoEditAnnotation(
                frameTimestampMs=5_000,
                tool=AnnotationTool.RECTANGLE,
                points=[{"x": 0.25, "y": 0.2}, {"x": 0.65, "y": 0.72}],
                label="需要修复的手部区域",
            )
        ],
    )

    assert recipe.duration_ms == 6_000

    with pytest.raises(ValidationError, match="0.5 至 13 秒"):
        VideoEditRecipeDraft(
            projectId=uuid.uuid4(),
            sourceAssetId=uuid.uuid4(),
            startMs=0,
            endMs=14_000,
            instruction="修改整个视频",
        )

    with pytest.raises(ValidationError):
        VideoEditAnnotation(
            frameTimestampMs=1_000,
            tool="rectangle",
            points=[{"x": 1.2, "y": 0.2}, {"x": 0.5, "y": 0.5}],
        )


def test_ark_capability_compiler_exposes_two_stage_calls_and_cost_before_submit() -> None:
    recipe = VideoEditRecipeDraft(
        projectId=uuid.uuid4(),
        sourceAssetId=uuid.uuid4(),
        startMs=4_000,
        endMs=10_000,
        instruction="保持产品标签并修复手部",
        referenceAssetIds=[uuid.uuid4()],
        annotations=[
            {
                "frameTimestampMs": 5_000,
                "tool": "rectangle",
                "points": [{"x": 0.2, "y": 0.2}, {"x": 0.6, "y": 0.7}],
            }
        ],
    )
    capability = ProviderEditCapability(
        provider="ark",
        model="seedance-test",
        supportsDirectAnnotations=False,
        maxDirectReferenceImages=0,
        supportsControlAnchors=True,
        imageCallCostMicros=1_500,
        videoCallCostMicros=8_000,
    )

    plan = compile_video_edit_plan(recipe, capability)

    assert plan.mode == "two_stage"
    assert plan.image_call_count == 2
    assert plan.video_call_count == 1
    assert plan.estimated_cost_micros == 11_000
    assert [stage.kind for stage in plan.stages] == [
        "control_anchor",
        "control_anchor",
        "video_edit",
    ]
