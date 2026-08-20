from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.aigc_canvas import (
    CanvasConnection,
    CanvasNodeType,
    CanvasPortType,
    PromptRunDraft,
    StoryBrief,
    StoryRevisionStatus,
    StoryScorecard,
    SubjectDraft,
    allocate_bounded_durations,
    allocate_durations,
    approve_story_revision,
    validate_story_inputs,
)


def _subject(name: str, kind: str, role: str) -> SubjectDraft:
    return SubjectDraft(
        name=name,
        kind=kind,
        role=role,
        identityAnchors=[f"{name}的稳定身份特征"],
        immutableTraits=[f"{name}不可静默改变"],
    )


def test_story_brief_and_generic_two_subject_contract() -> None:
    brief = StoryBrief(
        theme="小孩与猫在雨前收回晾晒的画",
        audience="亲子观众",
        genre="治愈生活短剧",
        tone="温暖、紧凑",
        aspectRatio="9:16",
        targetDurationSeconds=60,
        constraints=["不增加第三个主要角色"],
    )
    subjects = (
        _subject("小满", "person", "protagonist"),
        _subject("灰灰", "animal", "co_protagonist"),
        _subject("院子", "location", "environment"),
    )

    validate_story_inputs(brief, subjects)

    assert brief.target_duration_seconds == 60
    assert subjects[1].kind.value == "animal"
    assert subjects[2].role.value == "environment"


def test_story_generation_rejects_fewer_than_two_narrative_subjects() -> None:
    brief = StoryBrief(
        theme="一只猫整理散落的画纸",
        audience="家庭观众",
        genre="生活短剧",
        tone="轻松",
        aspectRatio="9:16",
        targetDurationSeconds=30,
    )

    with pytest.raises(ValueError, match="至少两个叙事主体"):
        validate_story_inputs(
            brief,
            (
                _subject("灰灰", "animal", "protagonist"),
                _subject("院子", "location", "environment"),
            ),
        )


def test_story_brief_rejects_duration_below_initial_provider_capability() -> None:
    with pytest.raises(ValidationError):
        StoryBrief(
            theme="超短测试",
            audience="内部验收",
            genre="生活短剧",
            tone="紧凑",
            aspectRatio="9:16",
            targetDurationSeconds=7,
        )


def test_duration_allocator_is_deterministic_and_exact() -> None:
    assert allocate_durations(60, (1, 2, 1), minimum_seconds=2) == (15, 30, 15)
    assert allocate_durations(10, (1, 1, 1), minimum_seconds=2) == (4, 3, 3)
    assert sum(allocate_durations(61, (3, 2, 1), minimum_seconds=2)) == 61

    with pytest.raises(ValueError, match="最小时长"):
        allocate_durations(5, (1, 1, 1), minimum_seconds=2)


def test_provider_duration_allocator_respects_exact_total_and_bounds() -> None:
    durations = allocate_bounded_durations(
        60,
        (1, 2, 1, 1, 1),
        minimum_seconds=8,
        maximum_seconds=15,
    )

    assert sum(durations) == 60
    assert all(8 <= duration <= 15 for duration in durations)
    assert durations == allocate_bounded_durations(
        60,
        (1, 2, 1, 1, 1),
        minimum_seconds=8,
        maximum_seconds=15,
    )

    with pytest.raises(ValueError, match="无法适配"):
        allocate_bounded_durations(
            60,
            (1, 1, 1),
            minimum_seconds=8,
            maximum_seconds=15,
        )


def test_canvas_connection_validates_typed_node_ports() -> None:
    valid = CanvasConnection(
        sourceNodeId=uuid.uuid4(),
        sourceNodeType=CanvasNodeType.BRIEF,
        sourcePort=CanvasPortType.BRIEF,
        targetNodeId=uuid.uuid4(),
        targetNodeType=CanvasNodeType.STORY_PLANNER,
        targetPort=CanvasPortType.BRIEF,
    )

    assert valid.source_port is CanvasPortType.BRIEF

    with pytest.raises(ValidationError, match="不接受"):
        CanvasConnection(
            sourceNodeId=uuid.uuid4(),
            sourceNodeType=CanvasNodeType.IMAGE_GENERATION,
            sourcePort=CanvasPortType.IMAGE_ASSET,
            targetNodeId=uuid.uuid4(),
            targetNodeType=CanvasNodeType.STORY_PLANNER,
            targetPort=CanvasPortType.BRIEF,
        )


def test_story_approval_requires_scorecard_and_both_subjects() -> None:
    child_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    scorecard = StoryScorecard(
        openingHook=8,
        causalCompleteness=8,
        subjectNecessity=9,
        emotionalArc=8,
        visualizability=9,
        durationFit=8,
        continuityRisk=7,
        safety=10,
        rationale="两个主体共同推动回收画作的行动链。",
    )

    assert (
        approve_story_revision(
            StoryRevisionStatus.CANDIDATE,
            scorecard=scorecard,
            revision_subject_ids=(child_id, cat_id),
            required_subject_ids=(child_id, cat_id),
        )
        is StoryRevisionStatus.APPROVED
    )

    with pytest.raises(ValueError, match="缺少主体"):
        approve_story_revision(
            StoryRevisionStatus.CANDIDATE,
            scorecard=scorecard,
            revision_subject_ids=(child_id,),
            required_subject_ids=(child_id, cat_id),
        )


def test_prompt_run_keeps_exact_application_prompt_separate_from_provider_unknowns() -> None:
    prompt = PromptRunDraft(
        purpose="story_candidate",
        nodeId=uuid.uuid4(),
        businessObjectType="story_revision",
        businessObjectId=uuid.uuid4(),
        templateName="story.relationship.v1",
        templateVersion="1.0.0",
        systemPrompt="你是短剧故事策划。",
        userPrompt="主题：雨前收画。",
        finalPrompt="你是短剧故事策划。\n主题：雨前收画。",
        provider="ark",
        model="doubao-test",
        providerRequestSnapshot={"temperature": 0.7},
        inputSnapshot={"subjectRevisionIds": [str(uuid.uuid4())]},
    )

    document = prompt.model_dump(by_alias=True, mode="json")

    assert document["finalPrompt"].endswith("主题：雨前收画。")
    assert document["providerInternalTransform"] == "not_observable"
    assert document["providerRequestSnapshot"] == {"temperature": 0.7}
