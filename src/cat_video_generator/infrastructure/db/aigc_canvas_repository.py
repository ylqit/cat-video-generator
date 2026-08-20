"""PostgreSQL persistence for the typed AIGC canvas.

Business objects and provider attempts are committed independently of canvas
coordinates.  Every method owns one short transaction so a browser disconnect
cannot leave an open transaction or an unrecorded paid intent.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import LandedAsset, StoredAsset
from ...domain.aigc_canvas import (
    CanvasConnection,
    CanvasNodeType,
    PromptRunDraft,
    StoryBrief,
    StoryCandidateOutput,
    StoryRevisionStatus,
    StoryScorecard,
    StoryStrategy,
    SubjectDraft,
    SubjectRole,
    approve_story_revision,
)
from ...domain.universal_canvas import (
    CanvasTemplateKey,
    ProviderEditCapability,
    VideoEditRecipeDraft,
    compile_video_edit_plan,
    list_template_specs,
)
from ...domain.workflow import PromptPurpose, SceneStatus, StepKind, StepStatus
from .models import (
    Asset,
    CanvasEvent,
    CanvasGraphEdge,
    CanvasGraphNode,
    CanvasLayout,
    GenerationAttempt,
    MediaGenerationBatch,
    ProductionRun,
    PromptRecord,
    ProviderCapability,
    Review,
    Scene,
    ShotBeat,
    ShotSubjectState,
    StoryBriefRecord,
    StoryRevisionRecord,
    StoryScore,
    Subject,
    SubjectReference,
    SubjectRevision,
    VideoEditAnnotation,
    VideoEditRecipe,
    VideoEditReference,
    WorkflowStep,
)
from .repositories import RecordNotFoundError, WorkflowConflictError


@dataclass(frozen=True, slots=True)
class StoredCanvasSubject:
    id: uuid.UUID
    revision_id: uuid.UUID
    draft: SubjectDraft
    status: str


class SqlAlchemyAigcCanvasRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        asset_root: Path | None = None,
    ) -> None:
        self._sessions = sessions
        self._asset_root = (
            Path("var/assets").resolve()
            if asset_root is None
            else asset_root.expanduser().resolve()
        )

    def save_brief(self, project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]:
        document = payload.model_dump(mode="json", by_alias=True)
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            project.canvas_v2_enabled = True
            latest = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            if latest is not None and _brief_document(latest) == document:
                return _brief_json(latest)
            row = StoryBriefRecord(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=1 if latest is None else latest.revision + 1,
                theme=payload.theme,
                audience=payload.audience,
                genre=payload.genre,
                tone=payload.tone,
                aspect_ratio=payload.aspect_ratio,
                target_duration_seconds=payload.target_duration_seconds,
                constraints_json=list(payload.constraints),
            )
            session.add(row)
            session.flush()
            self._mark_project_story_stale(session, project_id, "creative brief changed")
            return _brief_json(row)

    def get_current_brief(self, project_id: uuid.UUID) -> tuple[uuid.UUID, StoryBrief]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            row = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            if row is None:
                raise RecordNotFoundError(f"project {project_id} has no story brief")
            return row.id, StoryBrief.model_validate(_brief_document(row))

    def create_subject(self, project_id: uuid.UUID, payload: SubjectDraft) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            self._ensure_subject_name_available(session, project_id, payload.name)
            subject = Subject(
                id=uuid.uuid4(),
                production_run_id=project_id,
                kind=payload.kind.value,
                role=payload.role.value,
                status="draft",
            )
            session.add(subject)
            session.flush()
            revision = self._add_subject_revision(session, subject, payload)
            subject.current_revision_id = revision.id
            session.flush()
            if project.universal_canvas_enabled:
                graph_node = CanvasGraphNode(
                    id=subject.id,
                    production_run_id=project_id,
                    node_type=CanvasNodeType.SUBJECT.value,
                    object_type="subject",
                    object_id=subject.id,
                    status=subject.status,
                    data_json={
                        "title": payload.name,
                        "kind": payload.kind.value,
                        "role": payload.role.value,
                    },
                )
                session.add(graph_node)
                session.flush()
                batch_node = session.scalar(
                    select(CanvasGraphNode).where(
                        CanvasGraphNode.production_run_id == project_id,
                        CanvasGraphNode.node_type == CanvasNodeType.GENERATION_BATCH.value,
                    )
                )
                if batch_node is not None and payload.role is SubjectRole.HERO_PRODUCT:
                    session.add(
                        _graph_edge(
                            project_id,
                            CanvasConnection(
                                sourceNodeId=graph_node.id,
                                sourceNodeType=CanvasNodeType.SUBJECT,
                                sourcePort="product_subject",
                                targetNodeId=batch_node.id,
                                targetNodeType=CanvasNodeType.GENERATION_BATCH,
                                targetPort="product_subject",
                            ),
                        )
                    )
                if batch_node is not None:
                    for reference in payload.references:
                        asset = self._required(session, Asset, reference.asset_id, lock=True)
                        reference_node_id = uuid.uuid5(project_id, f"reference-asset:{asset.id}")
                        reference_node = session.get(CanvasGraphNode, reference_node_id)
                        if reference_node is None:
                            reference_node = CanvasGraphNode(
                                id=reference_node_id,
                                production_run_id=project_id,
                                node_type=CanvasNodeType.REFERENCE_ASSET.value,
                                object_type="asset",
                                object_id=asset.id,
                                status=asset.status,
                                data_json={
                                    "title": asset.semantic_key or asset.role,
                                    "assetId": str(asset.id),
                                    "semanticRole": reference.semantic_role,
                                    "thumbnailUrl": f"/api/v1/assets/{asset.id}/content",
                                },
                            )
                            session.add(reference_node)
                            session.flush()
                            asset.canvas_node_id = reference_node.id
                            asset.scope = "canvas_node"
                            session.add(
                                _graph_edge(
                                    project_id,
                                    CanvasConnection(
                                        sourceNodeId=reference_node.id,
                                        sourceNodeType=CanvasNodeType.REFERENCE_ASSET,
                                        sourcePort="media_reference[]",
                                        targetNodeId=batch_node.id,
                                        targetNodeType=CanvasNodeType.GENERATION_BATCH,
                                        targetPort="media_reference[]",
                                    ),
                                )
                            )
            self._mark_project_story_stale(session, project_id, "subject added")
            return _subject_json(subject, revision, self._subject_references(session, revision.id))

    def create_subject_revision(
        self,
        subject_id: uuid.UUID,
        payload: SubjectDraft,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            subject = self._required(session, Subject, subject_id, lock=True)
            self._ensure_subject_name_available(
                session,
                subject.production_run_id,
                payload.name,
                excluding_subject_id=subject_id,
            )
            current = (
                None
                if subject.current_revision_id is None
                else self._required(session, SubjectRevision, subject.current_revision_id)
            )
            revision_hash = _subject_hash(payload)
            if current is not None and current.revision_hash == revision_hash:
                return _subject_json(
                    subject,
                    current,
                    self._subject_references(session, current.id),
                )
            subject.kind = payload.kind.value
            subject.role = payload.role.value
            revision = self._add_subject_revision(session, subject, payload)
            subject.current_revision_id = revision.id
            session.flush()
            graph_node = session.get(CanvasGraphNode, subject.id)
            if graph_node is not None:
                graph_node.revision += 1
                graph_node.status = "stale"
                graph_node.data_json = {
                    **graph_node.data_json,
                    "title": payload.name,
                    "kind": payload.kind.value,
                    "role": payload.role.value,
                }
            self._mark_subject_downstream_stale(session, subject_id)
            return _subject_json(subject, revision, self._subject_references(session, revision.id))

    def list_subjects(self, project_id: uuid.UUID) -> tuple[StoredCanvasSubject, ...]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            rows = list(
                session.execute(
                    select(Subject)
                    .where(Subject.production_run_id == project_id, Subject.status != "archived")
                    .order_by(Subject.created_at)
                ).scalars()
            )
            result: list[StoredCanvasSubject] = []
            for subject in rows:
                if subject.current_revision_id is None:
                    continue
                revision = self._required(
                    session,
                    SubjectRevision,
                    subject.current_revision_id,
                )
                result.append(
                    StoredCanvasSubject(
                        id=subject.id,
                        revision_id=revision.id,
                        draft=_subject_draft(
                            subject,
                            revision,
                            self._subject_references(session, revision.id),
                        ),
                        status=subject.status,
                    )
                )
            return tuple(result)

    def begin_generation_attempt(self, **values: object) -> tuple[dict[str, Any], bool]:
        attempt_id = uuid.uuid4()
        with self._sessions.begin() as session:
            self._require_project(session, uuid.UUID(str(values["project_id"])))
            created_id = session.execute(
                insert(GenerationAttempt)
                .values(
                    id=attempt_id,
                    production_run_id=values["project_id"],
                    business_object_type=values["business_object_type"],
                    business_object_id=values["business_object_id"],
                    idempotency_key=values["idempotency_key"],
                    provider=values["provider"],
                    model=values["model"],
                    status="pending",
                    request_json=values["request"],
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(GenerationAttempt.id)
            ).scalar_one_or_none()
            row = session.scalar(
                select(GenerationAttempt).where(
                    GenerationAttempt.idempotency_key == values["idempotency_key"]
                )
            )
            if row is None:
                raise RuntimeError("generation attempt idempotency insert returned no row")
            return _attempt_json(row), created_id is not None

    def finish_generation_attempt(self, attempt_id: str, **values: object) -> None:
        with self._sessions.begin() as session:
            row = self._required(session, GenerationAttempt, uuid.UUID(attempt_id), lock=True)
            row.status = str(values["status"])
            if "response" in values:
                row.response_json = values["response"]  # type: ignore[assignment]
            if "error" in values:
                row.error_json = values["error"]  # type: ignore[assignment]

    def begin_prompt_run(
        self,
        *,
        project_id: uuid.UUID,
        draft: PromptRunDraft,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        input_hash = _json_hash(draft.input_snapshot)
        prompt_hash = hashlib.sha256(draft.final_prompt.encode("utf-8")).hexdigest()
        operation_key = f"director:{draft.purpose}"
        idempotency_key = hashlib.sha256(
            f"{project_id}:{operation_key}:{input_hash}:{prompt_hash}".encode()
        ).hexdigest()
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            attempt = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(WorkflowStep.attempt), 0)).where(
                            WorkflowStep.production_run_id == project_id,
                            WorkflowStep.operation_key == operation_key,
                        )
                    )
                    or 0
                )
                + 1
            )
            created_step_id = session.execute(
                insert(WorkflowStep)
                .values(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    kind=StepKind.DIRECTOR.value,
                    status=StepStatus.PENDING.value,
                    attempt=attempt,
                    operation_key=operation_key,
                    idempotency_key=idempotency_key,
                    provider=draft.provider,
                    model=draft.model,
                    input_hash=input_hash,
                    request_hash=_json_hash(draft.provider_request_snapshot),
                    input_snapshot_json=draft.input_snapshot,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(WorkflowStep.id)
            ).scalar_one_or_none()
            step = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == idempotency_key)
            )
            if step is None:
                raise RuntimeError("prompt workflow insert returned no row")
            prompt = session.scalar(
                select(PromptRecord).where(
                    PromptRecord.step_id == step.id,
                    PromptRecord.sha256 == prompt_hash,
                )
            )
            if prompt is None:
                prompt = PromptRecord(
                    id=uuid.uuid4(),
                    step_id=step.id,
                    purpose=PromptPurpose.DIRECTOR.value,
                    model=draft.model,
                    prompt_text=draft.final_prompt,
                    sha256=prompt_hash,
                    call_purpose=draft.purpose,
                    node_id=draft.node_id,
                    business_object_type=draft.business_object_type,
                    business_object_id=draft.business_object_id,
                    parent_prompt_id=draft.parent_run_id,
                    template_name=draft.template_name,
                    template_version=draft.template_version,
                    system_prompt=draft.system_prompt,
                    user_prompt=draft.user_prompt,
                    final_prompt=draft.final_prompt,
                    provider_request_json=draft.provider_request_snapshot,
                    provider_internal_transform=draft.provider_internal_transform,
                    input_snapshot_json=draft.input_snapshot,
                    parameters_json=draft.parameters,
                    status="pending",
                    input_hash=input_hash,
                )
                session.add(prompt)
                session.flush()
            if created_step_id is None and prompt.status == "succeeded":
                raise WorkflowConflictError("相同 Prompt 调用已经成功，不允许重复扣费提交")
            return prompt.id, step.id

    def complete_prompt_run(self, prompt_id: uuid.UUID, **values: object) -> None:
        with self._sessions.begin() as session:
            prompt = self._required(session, PromptRecord, prompt_id, lock=True)
            step = self._required(session, WorkflowStep, prompt.step_id, lock=True)
            status = str(values["status"])
            prompt.status = status
            prompt.completed_at = datetime.now(UTC)
            if "raw_response" in values:
                raw = dict(values["raw_response"])  # type: ignore[arg-type]
                response_id = values.get("provider_response_id")
                if response_id is not None:
                    raw["_providerResponseId"] = response_id
                prompt.raw_response_json = raw
            if "structured_response" in values:
                prompt.structured_response_json = values["structured_response"]  # type: ignore[assignment]
            if "output_hash" in values:
                prompt.output_hash = str(values["output_hash"])
            if "error" in values:
                prompt.error_json = values["error"]  # type: ignore[assignment]
            step.status = (
                StepStatus.SUCCEEDED.value if status == "succeeded" else StepStatus.FAILED.value
            )
            step.completed_at = prompt.completed_at
            step.error_json = prompt.error_json

    def save_story_candidate(self, **values: object) -> dict[str, Any]:
        project_id = uuid.UUID(str(values["project_id"]))
        candidate = values["candidate"]
        scorecard = values["scorecard"]
        if not isinstance(candidate, StoryCandidateOutput) or not isinstance(
            scorecard, StoryScorecard
        ):
            raise TypeError("candidate and scorecard must use Canvas V2 contracts")
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            revision = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(StoryRevisionRecord.revision), 0)).where(
                            StoryRevisionRecord.production_run_id == project_id
                        )
                    )
                    or 0
                )
                + 1
            )
            row = StoryRevisionRecord(
                id=uuid.uuid4(),
                production_run_id=project_id,
                brief_id=values["brief_id"],
                revision=revision,
                strategy=(
                    values["strategy"].value
                    if isinstance(values["strategy"], StoryStrategy)
                    else str(values["strategy"])
                ),
                status=StoryRevisionStatus.CANDIDATE.value,
                title=candidate.title,
                logline=candidate.logline,
                synopsis=candidate.synopsis,
                subject_ids_json=[str(item) for item in values["subject_ids"]],
                scene_plan_json=[
                    item.model_dump(mode="json", by_alias=True) for item in candidate.scenes
                ],
                candidate_prompt_id=values["candidate_prompt_id"],
                critic_prompt_id=values["critic_prompt_id"],
            )
            session.add(row)
            session.flush()
            score = StoryScore(
                id=uuid.uuid4(),
                story_revision_id=row.id,
                opening_hook=scorecard.opening_hook,
                causal_completeness=scorecard.causal_completeness,
                subject_necessity=scorecard.subject_necessity,
                emotional_arc=scorecard.emotional_arc,
                visualizability=scorecard.visualizability,
                duration_fit=scorecard.duration_fit,
                continuity_risk=scorecard.continuity_risk,
                safety=scorecard.safety,
                rationale=scorecard.rationale,
                warnings_json=list(scorecard.warnings),
            )
            session.add(score)
            session.flush()
            return _story_json(row, score)

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions.begin() as session:
            row = self._required(session, StoryRevisionRecord, revision_id, lock=True)
            score = session.scalar(
                select(StoryScore).where(StoryScore.story_revision_id == revision_id)
            )
            required_subject_ids = tuple(
                session.scalars(
                    select(Subject.id).where(
                        Subject.production_run_id == row.production_run_id,
                        Subject.status != "archived",
                        Subject.role.in_(
                            (
                                SubjectRole.PROTAGONIST.value,
                                SubjectRole.CO_PROTAGONIST.value,
                                SubjectRole.SUPPORT.value,
                            )
                        ),
                    )
                )
            )
            scorecard = None if score is None else _scorecard(score)
            status = approve_story_revision(
                StoryRevisionStatus(row.status),
                scorecard=scorecard,
                revision_subject_ids=tuple(uuid.UUID(item) for item in row.subject_ids_json),
                required_subject_ids=required_subject_ids,
            )
            session.execute(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == row.production_run_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                    StoryRevisionRecord.id != row.id,
                )
                .with_for_update()
            )
            for previous in session.scalars(
                select(StoryRevisionRecord).where(
                    StoryRevisionRecord.production_run_id == row.production_run_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                    StoryRevisionRecord.id != row.id,
                )
            ):
                previous.status = StoryRevisionStatus.SUPERSEDED.value
            row.status = status.value
            row.approved_at = datetime.now(UTC)
            return _story_json(row, score)

    def get_storyboard_context(self, project_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == project_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
            )
            if story is None:
                raise ValueError("故事尚未人工批准，不能生成分镜")
            brief = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            if brief is None:
                raise ValueError("项目尚未保存创意简报")
            score = session.scalar(
                select(StoryScore).where(StoryScore.story_revision_id == story.id)
            )
            subjects = list(
                session.scalars(
                    select(Subject)
                    .where(Subject.production_run_id == project_id, Subject.status != "archived")
                    .order_by(Subject.created_at)
                )
            )
            existing = list(
                session.scalars(select(ShotBeat).where(ShotBeat.story_revision_id == story.id))
            )
            return {
                "projectId": str(project_id),
                "storyId": str(story.id),
                "brief": _brief_json(brief),
                "story": _story_json(story, score),
                "subjects": [
                    _subject_json(
                        subject,
                        self._required(session, SubjectRevision, subject.current_revision_id),
                        self._subject_references(session, subject.current_revision_id),
                    )
                    for subject in subjects
                    if subject.current_revision_id is not None
                ],
                "existing": (
                    None if not existing else _storyboard_json(project_id, story.id, existing)
                ),
            }

    def save_storyboard_plan(
        self,
        project_id: uuid.UUID,
        *,
        story_id: uuid.UUID,
        plan: Any,
        durations: tuple[int, ...],
        prompt_id: uuid.UUID,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            story = self._required(session, StoryRevisionRecord, story_id, lock=True)
            if (
                story.production_run_id != project_id
                or story.status != StoryRevisionStatus.APPROVED.value
            ):
                raise WorkflowConflictError("分镜计划所引用的故事已不再是当前批准版本")
            existing = list(
                session.scalars(select(ShotBeat).where(ShotBeat.story_revision_id == story.id))
            )
            if existing:
                return _storyboard_json(project_id, story.id, existing)
            outlines = list(story.scene_plan_json)
            scenes = list(
                session.scalars(
                    select(Scene)
                    .where(Scene.production_run_id == project_id)
                    .order_by(Scene.sort_order)
                    .with_for_update()
                )
            )
            paid_media_exists = bool(
                session.scalar(
                    select(func.count())
                    .select_from(WorkflowStep)
                    .where(
                        WorkflowStep.production_run_id == project_id,
                        WorkflowStep.kind.in_((StepKind.IMAGE.value, StepKind.VIDEO.value)),
                    )
                )
            )
            if paid_media_exists and len(scenes) != len(outlines):
                raise WorkflowConflictError(
                    "现有场景已有付费媒体历史；请复制故事方案后局部创建分镜"
                )
            while len(scenes) < len(outlines):
                row = Scene(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    sort_order=len(scenes) + 1,
                    title="待编译场景",
                    source_text="待编译",
                    story_mode="single",
                    target_shot_count=1,
                    status=SceneStatus.READY.value,
                )
                session.add(row)
                session.flush()
                scenes.append(row)
            if len(scenes) > len(outlines):
                raise WorkflowConflictError(
                    "现有场景多于新故事规划；为避免删除旧分镜，请复制项目或保留原场景后局部重排"
                )
            beats_by_scene = dict.fromkeys(range(1, len(scenes) + 1), 0)
            for index, (outline, scene) in enumerate(zip(outlines, scenes, strict=True), 1):
                scene.sort_order = index
                scene.title = str(outline["title"])
                scene.source_text = str(outline["synopsis"])
                scene.story_mode = "single"
                scene.status = SceneStatus.READY.value
            beats: list[ShotBeat] = []
            for beat_plan, duration in zip(plan.beats, durations, strict=True):
                if beat_plan.scene_order > len(scenes):
                    raise ValueError("分镜 Beat 引用了不存在的场景")
                scene = scenes[beat_plan.scene_order - 1]
                beats_by_scene[beat_plan.scene_order] += 1
                beat = ShotBeat(
                    id=uuid.uuid4(),
                    scene_id=scene.id,
                    story_revision_id=story.id,
                    prompt_id=prompt_id,
                    sort_order=beats_by_scene[beat_plan.scene_order],
                    revision=1,
                    title=beat_plan.title,
                    action=beat_plan.action,
                    camera=beat_plan.camera,
                    dialogue=beat_plan.dialogue,
                    duration_seconds=duration,
                    status="ready",
                )
                session.add(beat)
                beats.append(beat)
            for index, scene in enumerate(scenes, 1):
                scene.target_shot_count = beats_by_scene[index]
            session.flush()
            return _storyboard_json(project_id, story.id, beats)

    def update_shot_beat(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        patch = payload.model_dump(exclude_none=True)
        with self._sessions.begin() as session:
            current = self._required(session, ShotBeat, beat_id, lock=True)
            if current.revision != expected_revision:
                raise WorkflowConflictError("分镜 Beat 已被更新，请比较版本后重试")
            current.status = "superseded"
            row = ShotBeat(
                id=uuid.uuid4(),
                scene_id=current.scene_id,
                shot_card_id=current.shot_card_id,
                story_revision_id=current.story_revision_id,
                prompt_id=current.prompt_id,
                sort_order=current.sort_order,
                revision=current.revision + 1,
                title=patch.get("title", current.title),
                action=patch.get("action", current.action),
                camera=patch.get("camera", current.camera),
                dialogue=patch.get("dialogue", current.dialogue),
                duration_seconds=patch.get("duration_seconds", current.duration_seconds),
                status="ready",
            )
            session.add(row)
            session.flush()
            subject_states = patch.get("subject_states")
            if subject_states is None:
                for state in session.scalars(
                    select(ShotSubjectState).where(ShotSubjectState.shot_beat_id == current.id)
                ):
                    session.add(
                        ShotSubjectState(
                            id=uuid.uuid4(),
                            shot_beat_id=row.id,
                            subject_revision_id=state.subject_revision_id,
                            start_state_json=state.start_state_json,
                            end_state_json=state.end_state_json,
                            action=state.action,
                            interaction=state.interaction,
                        )
                    )
            else:
                self._save_subject_states(session, row.id, subject_states)
            session.execute(
                GenerationAttempt.__table__.update()
                .where(
                    GenerationAttempt.business_object_type == "shot_beat",
                    GenerationAttempt.business_object_id == current.id,
                    GenerationAttempt.status.in_(("pending", "succeeded")),
                )
                .values(status="stale")
            )
            return _beat_json(row)

    def create_generation_attempt(self, payload: Any) -> dict[str, Any]:
        attempt, _created = self.begin_generation_attempt(
            project_id=payload.project_id,
            business_object_type=payload.business_object_type,
            business_object_id=payload.business_object_id,
            idempotency_key=payload.idempotency_key,
            provider=payload.provider,
            model=payload.model,
            request=payload.request,
        )
        return attempt

    def retry_generation_attempt(self, attempt_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions() as session:
            source = self._required(session, GenerationAttempt, attempt_id)
            if source.status == "submission_unknown":
                raise WorkflowConflictError(
                    "该任务可能已提交供应商，必须先用供应商任务号或人工对账，禁止直接重试"
                )
            if source.status != "failed":
                raise WorkflowConflictError("只有明确失败的生成尝试可以重试")
            values = {
                "project_id": source.production_run_id,
                "business_object_type": source.business_object_type,
                "business_object_id": source.business_object_id,
                "idempotency_key": payload.idempotency_key,
                "provider": source.provider,
                "model": source.model,
                "request": {**source.request_json, "retryReason": payload.reason},
            }
        attempt, created = self.begin_generation_attempt(**values)
        if created:
            with self._sessions.begin() as session:
                row = self._required(
                    session,
                    GenerationAttempt,
                    uuid.UUID(str(attempt["id"])),
                    lock=True,
                )
                row.retry_of_id = attempt_id
        return attempt

    def review_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions.begin() as session:
            asset = self._required(session, Asset, asset_id, lock=True)
            next_status = "approved" if payload.decision == "approve" else "rejected"
            if asset.status == next_status:
                return {
                    "assetId": str(asset.id),
                    "decision": payload.decision,
                    "status": asset.status,
                    "reason": payload.reason,
                }
            asset.status = next_status
            if asset.producing_step_id is not None:
                step = self._required(
                    session,
                    WorkflowStep,
                    asset.producing_step_id,
                    lock=True,
                )
                step.status = (
                    StepStatus.SUCCEEDED.value
                    if payload.decision == "approve"
                    else StepStatus.FAILED.value
                )
                step.completed_at = datetime.now(UTC)
                session.add(
                    Review(
                        id=uuid.uuid4(),
                        step_id=asset.producing_step_id,
                        asset_id=asset.id,
                        source="human",
                        decision=payload.decision,
                        reason=payload.reason,
                        warnings_json=[],
                        evidence_json={},
                    )
                )
            if asset.canvas_node_id is not None:
                asset_node = self._required(
                    session,
                    CanvasGraphNode,
                    asset.canvas_node_id,
                    lock=True,
                )
                asset_node.status = next_status
                asset_node.data_json = {
                    **asset_node.data_json,
                    "status": next_status,
                }
                if payload.decision == "approve":
                    review_node = session.scalar(
                        select(CanvasGraphNode)
                        .join(
                            CanvasGraphEdge,
                            CanvasGraphEdge.target_node_id == CanvasGraphNode.id,
                        )
                        .where(
                            CanvasGraphEdge.source_node_id == asset_node.id,
                            CanvasGraphNode.node_type == CanvasNodeType.REVIEW.value,
                        )
                    )
                    timeline_node = session.scalar(
                        select(CanvasGraphNode).where(
                            CanvasGraphNode.production_run_id == asset.production_run_id,
                            CanvasGraphNode.node_type == CanvasNodeType.TIMELINE.value,
                        )
                    )
                    if review_node is not None:
                        review_node.status = "approved"
                        review_node.data_json = {
                            **review_node.data_json,
                            "status": "approved",
                            "approvedAssetId": str(asset.id),
                        }
                    if review_node is not None and timeline_node is not None:
                        existing_edge = session.scalar(
                            select(CanvasGraphEdge.id).where(
                                CanvasGraphEdge.source_node_id == review_node.id,
                                CanvasGraphEdge.target_node_id == timeline_node.id,
                                CanvasGraphEdge.source_port == "approved_asset",
                                CanvasGraphEdge.target_port == "approved_asset",
                            )
                        )
                        if existing_edge is None:
                            session.add(
                                _graph_edge(
                                    asset.production_run_id,
                                    CanvasConnection(
                                        sourceNodeId=review_node.id,
                                        sourceNodeType=CanvasNodeType.REVIEW,
                                        sourcePort="approved_asset",
                                        targetNodeId=timeline_node.id,
                                        targetNodeType=CanvasNodeType.TIMELINE,
                                        targetPort="approved_asset",
                                    ),
                                )
                            )
                        timeline_node.status = "ready"
                        timeline_node.data_json = {
                            **timeline_node.data_json,
                            "status": "ready",
                            "approvedAssetIds": sorted(
                                {
                                    *timeline_node.data_json.get("approvedAssetIds", []),
                                    str(asset.id),
                                }
                            ),
                        }
            if asset.production_run_id is not None:
                self._record_event(
                    session,
                    asset.production_run_id,
                    "asset_reviewed",
                    {
                        "assetId": str(asset.id),
                        "decision": payload.decision,
                        "status": next_status,
                    },
                )
            return {
                "assetId": str(asset.id),
                "decision": payload.decision,
                "status": asset.status,
                "reason": payload.reason,
            }

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            prompt = self._required(session, PromptRecord, prompt_id)
            step = self._required(session, WorkflowStep, prompt.step_id)
            return _prompt_json(prompt, step)

    def list_canvas_templates(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json", by_alias=True) for item in list_template_specs()]

    def image_candidate_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("media:image:batch:"):
                raise ValueError("workflow step is not an image batch candidate")
            batch_id = uuid.UUID(str(step.input_snapshot_json["batchId"]))
            batch = self._required(session, MediaGenerationBatch, batch_id)
            if batch.production_run_id != step.production_run_id:
                raise WorkflowConflictError("图片候选任务与生成批次项目不一致")
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None or prompt.status != "pending":
                raise WorkflowConflictError("图片候选缺少待执行的精确 Prompt")
            asset_ids = {
                uuid.UUID(str(item)) for item in batch.input_json.get("referenceAssetIds", [])
            }
            incoming_nodes = list(
                session.scalars(
                    select(CanvasGraphNode)
                    .join(
                        CanvasGraphEdge,
                        CanvasGraphEdge.source_node_id == CanvasGraphNode.id,
                    )
                    .where(
                        CanvasGraphEdge.target_node_id == batch.canvas_node_id,
                        CanvasGraphEdge.target_port == "media_reference[]",
                        CanvasGraphNode.object_type == "asset",
                        CanvasGraphNode.object_id.is_not(None),
                    )
                )
            )
            asset_ids.update(
                node.object_id for node in incoming_nodes if node.object_id is not None
            )
            assets = [self._required(session, Asset, asset_id) for asset_id in asset_ids]
            return {
                "batchId": str(batch.id),
                "candidateIndex": int(step.input_snapshot_json["candidateIndex"]),
                "prompt": prompt.final_prompt or prompt.prompt_text,
                "referencePaths": tuple(
                    _resolve_asset_path(asset.storage_key, self._asset_root) for asset in assets
                ),
            }

    def complete_image_candidate(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
    ) -> str:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(select(Asset).where(Asset.producing_step_id == step.id))
            if existing is not None:
                return str(existing.id)
            batch = self._required(
                session,
                MediaGenerationBatch,
                uuid.UUID(str(step.input_snapshot_json["batchId"])),
                lock=True,
            )
            candidate_index = int(step.input_snapshot_json["candidateIndex"])
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None:
                raise WorkflowConflictError("图片候选缺少 Prompt 审计记录")
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=batch.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=batch.canvas_node_id,
                role="image_candidate",
                semantic_key=f"batch:{batch.id}:candidate:{candidate_index}",
                scope="canvas_node",
                status="candidate",
                media_type="image",
                storage_key=_asset_storage_key(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json={
                    "batchId": str(batch.id),
                    "candidateIndex": candidate_index,
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "promptId": str(prompt.id),
                },
            )
            session.add(asset)
            session.flush()
            output_ids = [*batch.output_asset_ids_json, str(asset.id)]
            batch.output_asset_ids_json = output_ids
            if len(output_ids) >= batch.candidate_count:
                batch.status = "awaiting_review"
            node = self._required(session, CanvasGraphNode, batch.canvas_node_id, lock=True)
            candidates = [
                *list(node.data_json.get("candidates", [])),
                {
                    "id": str(asset.id),
                    "assetId": str(asset.id),
                    "title": f"候选 {candidate_index}",
                    "thumbnailUrl": f"/api/v1/assets/{asset.id}/content",
                    "promptId": str(prompt.id),
                    "status": "candidate",
                },
            ]
            node.status = batch.status
            node.data_json = {
                **node.data_json,
                "status": batch.status,
                "candidates": sorted(
                    candidates,
                    key=lambda item: int(str(item["title"]).split()[-1]),
                ),
            }
            prompt.status = "succeeded"
            prompt.raw_response_json = {"url": provider_url, "model": provider_model}
            prompt.structured_response_json = {"assetId": str(asset.id)}
            prompt.output_hash = landed.sha256
            prompt.completed_at = datetime.now(UTC)
            self._record_event(
                session,
                batch.production_run_id,
                "generation_candidate_ready",
                {
                    "batchId": str(batch.id),
                    "assetId": str(asset.id),
                    "candidateIndex": candidate_index,
                },
            )
            return str(asset.id)

    def video_edit_anchor_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("video:edit-anchor:"):
                raise ValueError("workflow step is not a video edit control anchor")
            recipe = self._required(
                session,
                VideoEditRecipe,
                uuid.UUID(str(step.input_snapshot_json["recipeId"])),
            )
            source = self._required(session, Asset, recipe.source_asset_id)
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None or prompt.status != "pending":
                raise WorkflowConflictError("控制锚点缺少待执行的精确 Prompt")
            references = list(
                session.scalars(
                    select(Asset)
                    .join(VideoEditReference, VideoEditReference.asset_id == Asset.id)
                    .where(VideoEditReference.recipe_id == recipe.id)
                    .order_by(VideoEditReference.ordinal)
                )
            )
            return {
                "recipeId": str(recipe.id),
                "boundary": str(step.input_snapshot_json["boundary"]),
                "timestampMs": int(step.input_snapshot_json["timestampMs"]),
                "source": _stored_asset(source, self._asset_root),
                "prompt": prompt.final_prompt or prompt.prompt_text,
                "referencePaths": tuple(
                    _resolve_asset_path(asset.storage_key, self._asset_root) for asset in references
                ),
            }

    def record_control_anchor_input(
        self,
        step_id: uuid.UUID,
        *,
        boundary_frame: LandedAsset,
    ) -> str:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_edit_boundary",
                )
            )
            if existing is not None:
                return str(existing.id)
            recipe = self._required(
                session,
                VideoEditRecipe,
                uuid.UUID(str(step.input_snapshot_json["recipeId"])),
            )
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=recipe.canvas_node_id,
                role="video_edit_boundary",
                semantic_key=(
                    f"video-edit:{recipe.id}:boundary:{step.input_snapshot_json['boundary']}"
                ),
                scope="canvas_node",
                status="ready",
                media_type="image",
                storage_key=_asset_storage_key(boundary_frame.path, self._asset_root),
                sha256=boundary_frame.sha256,
                byte_size=boundary_frame.byte_size,
                metadata_json={
                    "recipeId": str(recipe.id),
                    "boundary": step.input_snapshot_json["boundary"],
                    "timestampMs": step.input_snapshot_json["timestampMs"],
                },
            )
            session.add(asset)
            session.flush()
            snapshot = {
                **step.input_snapshot_json,
                "boundaryAssetId": str(asset.id),
                "boundarySha256": asset.sha256,
            }
            step.input_snapshot_json = snapshot
            step.input_hash = _json_hash(snapshot)
            step.request_hash = step.input_hash
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is not None:
                prompt.input_snapshot_json = snapshot
                prompt.provider_request_json = snapshot
                prompt.input_hash = step.input_hash
            return str(asset.id)

    def complete_control_anchor(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
    ) -> str:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_edit_control_anchor",
                )
            )
            if existing is not None:
                return str(existing.id)
            recipe = self._required(
                session,
                VideoEditRecipe,
                uuid.UUID(str(step.input_snapshot_json["recipeId"])),
            )
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None:
                raise WorkflowConflictError("控制锚点缺少 Prompt 审计记录")
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=recipe.canvas_node_id,
                role="video_edit_control_anchor",
                semantic_key=(
                    f"video-edit:{recipe.id}:control-anchor:{step.input_snapshot_json['boundary']}"
                ),
                scope="canvas_node",
                status="ready",
                media_type="image",
                storage_key=_asset_storage_key(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json={
                    "recipeId": str(recipe.id),
                    "boundary": step.input_snapshot_json["boundary"],
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "promptId": str(prompt.id),
                },
            )
            session.add(asset)
            session.flush()
            prompt.status = "succeeded"
            prompt.raw_response_json = {"url": provider_url, "model": provider_model}
            prompt.structured_response_json = {"assetId": str(asset.id)}
            prompt.output_hash = landed.sha256
            prompt.completed_at = datetime.now(UTC)
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_control_anchor_ready",
                {
                    "recipeId": str(recipe.id),
                    "boundary": step.input_snapshot_json["boundary"],
                    "assetId": str(asset.id),
                },
            )
            return str(asset.id)

    def video_edit_video_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("video:edit-recipe:"):
                raise ValueError("workflow step is not a video edit recipe")
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None:
                raise WorkflowConflictError("视频重编缺少精确 Prompt")
            recipe_id = prompt.business_object_id
            if recipe_id is None:
                raise WorkflowConflictError("视频重编 Prompt 未绑定配方")
            recipe = self._required(session, VideoEditRecipe, recipe_id)
            source = self._required(session, Asset, recipe.source_asset_id)
            anchor_step_ids = [
                uuid.UUID(str(item))
                for item in step.input_snapshot_json.get("controlAnchorStepIds", [])
            ]
            anchors = (
                list(
                    session.scalars(
                        select(Asset).where(
                            Asset.producing_step_id.in_(anchor_step_ids),
                            Asset.role == "video_edit_control_anchor",
                        )
                    )
                )
                if anchor_step_ids
                else []
            )
            anchors.sort(key=lambda item: 0 if item.metadata_json.get("boundary") == "start" else 1)
            return {
                "ready": len(anchors) == len(anchor_step_ids),
                "recipeId": str(recipe.id),
                "prompt": prompt.final_prompt or prompt.prompt_text,
                "source": _stored_asset(source, self._asset_root),
                "sourceInput": (
                    source.metadata_json.get("providerUrl")
                    if isinstance(source.metadata_json.get("providerUrl"), str)
                    else _resolve_asset_path(source.storage_key, self._asset_root)
                ),
                "anchors": tuple(_stored_asset(item, self._asset_root) for item in anchors),
                "startMs": recipe.start_ms,
                "endMs": recipe.end_ms,
                "providerTaskId": step.provider_task_id,
                "compilation": recipe.compilation_json or {},
            }

    def record_video_edit_inputs(
        self,
        step_id: uuid.UUID,
        *,
        input_plan: dict[str, Any],
        input_assets: tuple[StoredAsset, StoredAsset, StoredAsset],
    ) -> None:
        """Freeze the exact provider bindings before the paid video request."""

        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None or prompt.business_object_id is None:
                raise WorkflowConflictError("视频重编缺少配方 Prompt")
            recipe = self._required(
                session,
                VideoEditRecipe,
                prompt.business_object_id,
            )
            persisted_assets: list[Asset] = []
            for index, source in enumerate(input_assets):
                asset = session.get(Asset, source.id)
                if asset is None:
                    if index == 0 or source.media_type != "image":
                        raise WorkflowConflictError("视频重编输入素材不存在")
                    landed_path = source.require_path()
                    asset = Asset(
                        id=source.id,
                        production_run_id=recipe.production_run_id,
                        producing_step_id=step.id,
                        canvas_node_id=recipe.canvas_node_id,
                        role="video_edit_boundary",
                        semantic_key=source.semantic_key,
                        scope="canvas_node",
                        status="ready",
                        media_type="image",
                        storage_key=_asset_storage_key(landed_path, self._asset_root),
                        sha256=source.sha256,
                        byte_size=landed_path.stat().st_size,
                        metadata_json={
                            **source.metadata,
                            "recipeId": str(recipe.id),
                            "derivedFromAssetId": str(recipe.source_asset_id),
                        },
                    )
                    session.add(asset)
                persisted_assets.append(asset)
            request = {
                **step.input_snapshot_json,
                "providerInputPlan": input_plan,
                "providerInputAssets": [
                    {
                        "assetId": str(asset.id),
                        "semanticKey": asset.semantic_key,
                        "mediaType": asset.media_type,
                        "sha256": asset.sha256,
                    }
                    for asset in persisted_assets
                ],
            }
            request_hash = _json_hash(request)
            step.input_snapshot_json = request
            step.input_hash = request_hash
            step.request_hash = request_hash
            prompt.provider_request_json = request
            prompt.input_snapshot_json = request
            prompt.input_hash = request_hash
            attempt = session.scalar(
                select(GenerationAttempt).where(GenerationAttempt.workflow_step_id == step.id)
            )
            if attempt is not None:
                attempt.request_json = request

    def record_failed_video_edit_candidate(
        self,
        step_id: uuid.UUID,
        *,
        provider_segment: LandedAsset,
        provider_url: str,
        provider_model: str,
        replacement_qc: dict[str, Any],
    ) -> str:
        """Retain a provider result that failed technical QC without approving it."""

        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_edit_failed_provider_segment",
                )
            )
            if existing is not None:
                return str(existing.id)
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None or prompt.business_object_id is None:
                raise WorkflowConflictError("视频重编缺少配方 Prompt")
            recipe = self._required(
                session,
                VideoEditRecipe,
                prompt.business_object_id,
                lock=True,
            )
            node = CanvasGraphNode(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                node_type=CanvasNodeType.VIDEO_SEGMENT.value,
                object_type="asset",
                status="failed_qc",
                data_json={},
            )
            session.add(node)
            session.flush()
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=node.id,
                role="video_edit_failed_provider_segment",
                semantic_key=f"video-edit:{recipe.id}:failed-provider-segment",
                scope="canvas_node",
                status="rejected",
                media_type="video",
                storage_key=_asset_storage_key(provider_segment.path, self._asset_root),
                sha256=provider_segment.sha256,
                byte_size=provider_segment.byte_size,
                metadata_json={
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "qc": replacement_qc,
                    "recipeId": str(recipe.id),
                    "rejectionReason": "technical_qc_failed",
                },
            )
            session.add(asset)
            session.flush()
            node.object_id = asset.id
            node.data_json = {
                "title": f"失败候选 · Revision {recipe.revision}",
                "assetId": str(asset.id),
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "status": "failed_qc",
                "qc": replacement_qc,
            }
            recipe.status = "failed_qc"
            edit_node = self._required(
                session,
                CanvasGraphNode,
                recipe.canvas_node_id,
                lock=True,
            )
            edit_node.status = "failed_qc"
            edit_node.data_json = {
                **edit_node.data_json,
                "status": "failed_qc",
                "failedCandidateAssetId": str(asset.id),
            }
            session.add(
                _graph_edge(
                    recipe.production_run_id,
                    CanvasConnection(
                        sourceNodeId=recipe.canvas_node_id,
                        sourceNodeType=CanvasNodeType.VIDEO_EDIT,
                        sourcePort="edit_recipe",
                        targetNodeId=node.id,
                        targetNodeType=CanvasNodeType.VIDEO_SEGMENT,
                        targetPort="edit_recipe",
                    ),
                )
            )
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_candidate_failed_qc",
                {
                    "recipeId": str(recipe.id),
                    "assetId": str(asset.id),
                    "qc": replacement_qc,
                },
            )
            return str(asset.id)

    def record_video_edit_submission(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            bound = session.scalar(
                select(WorkflowStep.id).where(
                    WorkflowStep.provider_task_id == provider_task_id,
                    WorkflowStep.id != step.id,
                )
            )
            if bound is not None:
                raise WorkflowConflictError("供应商任务号已绑定其他付费意图")
            step.provider_task_id = provider_task_id
            step.status = (
                StepStatus.RUNNING.value
                if provider_status == "running"
                else StepStatus.QUEUED.value
            )
            step.submitted_at = step.submitted_at or datetime.now(UTC)
            attempt = session.scalar(
                select(GenerationAttempt).where(GenerationAttempt.workflow_step_id == step.id)
            )
            if attempt is not None:
                attempt.provider_task_id = provider_task_id
                attempt.status = step.status

    def complete_video_edit(
        self,
        step_id: uuid.UUID,
        *,
        provider_segment: LandedAsset,
        full_video: LandedAsset,
        provider_url: str,
        provider_model: str,
        replacement_qc: dict[str, Any],
        full_qc: dict[str, Any],
    ) -> dict[str, str]:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_edit_full_version",
                )
            )
            if existing is not None:
                return {"assetId": str(existing.id)}
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None or prompt.business_object_id is None:
                raise WorkflowConflictError("视频重编缺少配方 Prompt")
            recipe = self._required(session, VideoEditRecipe, prompt.business_object_id, lock=True)
            segment_asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=recipe.canvas_node_id,
                role="video_edit_provider_segment",
                semantic_key=f"video-edit:{recipe.id}:provider-segment",
                scope="canvas_node",
                status="candidate",
                media_type="video",
                storage_key=_asset_storage_key(provider_segment.path, self._asset_root),
                sha256=provider_segment.sha256,
                byte_size=provider_segment.byte_size,
                metadata_json={
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "qc": replacement_qc,
                    "recipeId": str(recipe.id),
                },
            )
            session.add(segment_asset)
            session.flush()
            output_node = CanvasGraphNode(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                node_type=CanvasNodeType.VIDEO_SEGMENT.value,
                object_type="asset",
                status="candidate",
                data_json={},
            )
            session.add(output_node)
            session.flush()
            full_asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=output_node.id,
                role="video_edit_full_version",
                semantic_key=f"video-edit:{recipe.id}:full-version",
                scope="canvas_node",
                status="candidate",
                media_type="video",
                storage_key=_asset_storage_key(full_video.path, self._asset_root),
                sha256=full_video.sha256,
                byte_size=full_video.byte_size,
                metadata_json={
                    "providerSegmentAssetId": str(segment_asset.id),
                    "sourceAssetId": str(recipe.source_asset_id),
                    "recipeId": str(recipe.id),
                    "rangeEdit": {
                        "startMs": recipe.start_ms,
                        "endMs": recipe.end_ms,
                    },
                    "qc": full_qc,
                    "promptId": str(prompt.id),
                },
            )
            session.add(full_asset)
            session.flush()
            output_node.object_id = full_asset.id
            output_node.data_json = {
                "title": f"重编完整视频 · Revision {recipe.revision}",
                "assetId": str(full_asset.id),
                "providerSegmentAssetId": str(segment_asset.id),
                "contentUrl": f"/api/v1/assets/{full_asset.id}/content",
                "durationMs": full_qc.get("durationMs"),
                "promptId": str(prompt.id),
                "status": "candidate",
            }
            session.add(
                _graph_edge(
                    recipe.production_run_id,
                    CanvasConnection(
                        sourceNodeId=recipe.canvas_node_id,
                        sourceNodeType=CanvasNodeType.VIDEO_EDIT,
                        sourcePort="edit_recipe",
                        targetNodeId=output_node.id,
                        targetNodeType=CanvasNodeType.VIDEO_SEGMENT,
                        targetPort="edit_recipe",
                    ),
                )
            )
            review_node = session.scalar(
                select(CanvasGraphNode).where(
                    CanvasGraphNode.production_run_id == recipe.production_run_id,
                    CanvasGraphNode.node_type == CanvasNodeType.REVIEW.value,
                )
            )
            if review_node is not None:
                session.add(
                    _graph_edge(
                        recipe.production_run_id,
                        CanvasConnection(
                            sourceNodeId=output_node.id,
                            sourceNodeType=CanvasNodeType.VIDEO_SEGMENT,
                            sourcePort="video_asset",
                            targetNodeId=review_node.id,
                            targetNodeType=CanvasNodeType.REVIEW,
                            targetPort="video_asset",
                        ),
                    )
                )
            recipe.status = "awaiting_review"
            edit_node = self._required(session, CanvasGraphNode, recipe.canvas_node_id, lock=True)
            edit_node.status = "awaiting_review"
            edit_node.data_json = {
                **edit_node.data_json,
                "status": "awaiting_review",
                "outputAssetId": str(full_asset.id),
            }
            prompt.status = "succeeded"
            prompt.raw_response_json = {
                "providerUrl": provider_url,
                "providerModel": provider_model,
            }
            prompt.structured_response_json = {
                "providerSegmentAssetId": str(segment_asset.id),
                "fullVideoAssetId": str(full_asset.id),
            }
            prompt.output_hash = full_video.sha256
            prompt.completed_at = datetime.now(UTC)
            attempt = session.scalar(
                select(GenerationAttempt).where(GenerationAttempt.workflow_step_id == step.id)
            )
            if attempt is not None:
                attempt.status = "awaiting_review"
                attempt.response_json = prompt.structured_response_json
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_candidate_ready",
                {
                    "recipeId": str(recipe.id),
                    "providerSegmentAssetId": str(segment_asset.id),
                    "fullVideoAssetId": str(full_asset.id),
                },
            )
            return {
                "assetId": str(full_asset.id),
                "providerSegmentAssetId": str(segment_asset.id),
            }

    def instantiate_template(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        template_key = CanvasTemplateKey(payload.template_key)
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            existing_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(CanvasGraphNode)
                    .where(CanvasGraphNode.production_run_id == project_id)
                )
                or 0
            )
            if existing_count and project.canvas_template_key != template_key.value:
                raise WorkflowConflictError("项目已经包含业务节点；请复制项目后再切换画布模板")
            project.canvas_v2_enabled = True
            project.universal_canvas_enabled = True
            project.product_ad_template_enabled = template_key is CanvasTemplateKey.PRODUCT_AD
            project.video_edit_v2_enabled = True
            project.canvas_template_key = template_key.value
            created_node_ids: list[str] = []
            if template_key is CanvasTemplateKey.PRODUCT_AD and existing_count == 0:
                definitions = (
                    (
                        "product-reference",
                        CanvasNodeType.REFERENCE_ASSET,
                        "reference_slot",
                        {"title": "产品主体 / 包装参考", "status": "awaiting_input"},
                    ),
                    (
                        "talent-style-reference",
                        CanvasNodeType.REFERENCE_ASSET,
                        "reference_slot",
                        {"title": "模特 / 风格参考", "status": "awaiting_input"},
                    ),
                    (
                        "image-batch",
                        CanvasNodeType.GENERATION_BATCH,
                        "media_generation_batch",
                        {
                            "title": "产品图生成批次",
                            "candidateCount": 4,
                            "candidateRange": [1, 8],
                            "status": "awaiting_input",
                        },
                    ),
                    (
                        "video-generation",
                        CanvasNodeType.VIDEO_GENERATION,
                        "video_generation_stage",
                        {"title": "图生视频", "status": "awaiting_selection"},
                    ),
                    (
                        "video-edit",
                        CanvasNodeType.VIDEO_EDIT,
                        "video_edit_stage",
                        {"title": "视频局部重编", "status": "awaiting_video"},
                    ),
                    (
                        "review",
                        CanvasNodeType.REVIEW,
                        "asset_review_stage",
                        {"title": "人工审核", "status": "awaiting_asset"},
                    ),
                    (
                        "timeline",
                        CanvasNodeType.TIMELINE,
                        "timeline_stage",
                        {"title": "时间线", "status": "awaiting_approval"},
                    ),
                )
                nodes: dict[str, CanvasGraphNode] = {}
                for key, node_type, object_type, data in definitions:
                    node = CanvasGraphNode(
                        id=uuid.uuid5(project_id, f"product-ad:{key}"),
                        production_run_id=project_id,
                        node_type=node_type.value,
                        object_type=object_type,
                        status=str(data["status"]),
                        data_json=data,
                    )
                    session.add(node)
                    nodes[key] = node
                    created_node_ids.append(str(node.id))
                session.flush()
                for source_key in ("product-reference", "talent-style-reference"):
                    edge = CanvasConnection(
                        sourceNodeId=nodes[source_key].id,
                        sourceNodeType=CanvasNodeType.REFERENCE_ASSET,
                        sourcePort="media_reference[]",
                        targetNodeId=nodes["image-batch"].id,
                        targetNodeType=CanvasNodeType.GENERATION_BATCH,
                        targetPort="media_reference[]",
                    )
                    session.add(_graph_edge(project_id, edge))
            event = self._record_event(
                session,
                project_id,
                "template_instantiated",
                {"templateKey": template_key.value, "nodeIds": created_node_ids},
            )
            return {
                "projectId": str(project_id),
                "templateKey": template_key.value,
                "graphVersion": 1,
                "eventId": str(event.id),
                "nodeIds": created_node_ids,
            }

    def create_canvas_node(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            project.canvas_v2_enabled = True
            node = CanvasGraphNode(
                id=uuid.uuid4(),
                production_run_id=project_id,
                node_type=payload.node_type.value,
                object_type=payload.object_type,
                object_id=payload.object_id,
                status=str(payload.data.get("status", "ready")),
                data_json=payload.data,
            )
            session.add(node)
            session.flush()
            if payload.object_type == "asset" and payload.object_id is not None:
                asset = self._required(session, Asset, payload.object_id, lock=True)
                if asset.production_run_id != project_id and asset.scope != "canon":
                    raise ValueError("画布资产节点引用的素材不属于当前项目")
                expected_media_type = {
                    CanvasNodeType.IMAGE_ASSET: "image",
                    CanvasNodeType.VIDEO_ASSET: "video",
                    CanvasNodeType.REFERENCE_ASSET: "image",
                }.get(payload.node_type)
                if expected_media_type and asset.media_type != expected_media_type:
                    raise ValueError("画布资产节点类型与实际素材类型不一致")
                asset.canvas_node_id = node.id
                if asset.scope != "canon":
                    asset.scope = "canvas_node"
            self._record_event(
                session,
                project_id,
                "canvas_node_created",
                {"node": _graph_node_json(node)},
            )
            return _graph_node_json(node)

    def create_canvas_edge(
        self, project_id: uuid.UUID, payload: CanvasConnection
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            source = self._required(session, CanvasGraphNode, payload.source_node_id)
            target = self._required(session, CanvasGraphNode, payload.target_node_id)
            if source.production_run_id != project_id or target.production_run_id != project_id:
                raise ValueError("画布业务连接的两个节点必须属于同一项目")
            if (
                source.node_type != payload.source_node_type.value
                or target.node_type != payload.target_node_type.value
            ):
                raise WorkflowConflictError("节点类型已变化，请刷新画布后重新连接")
            existing = session.scalar(
                select(CanvasGraphEdge).where(
                    CanvasGraphEdge.source_node_id == source.id,
                    CanvasGraphEdge.source_port == payload.source_port.value,
                    CanvasGraphEdge.target_node_id == target.id,
                    CanvasGraphEdge.target_port == payload.target_port.value,
                )
            )
            if existing is not None:
                return _graph_edge_json(existing, source, target)
            edge = _graph_edge(project_id, payload)
            session.add(edge)
            if target.status not in {"awaiting_input", "draft"}:
                target.status = "stale"
            target.revision += 1
            session.flush()
            self._record_event(
                session,
                project_id,
                "canvas_edge_created",
                {"edge": _graph_edge_json(edge, source, target)},
            )
            return _graph_edge_json(edge, source, target)

    def delete_canvas_edge(self, edge_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions.begin() as session:
            edge = self._required(session, CanvasGraphEdge, edge_id, lock=True)
            target = self._required(session, CanvasGraphNode, edge.target_node_id, lock=True)
            project_id = edge.production_run_id
            target.status = "stale"
            target.revision += 1
            session.delete(edge)
            self._record_event(
                session,
                project_id,
                "canvas_edge_deleted",
                {"edgeId": str(edge_id), "targetNodeId": str(target.id)},
            )
            return {"id": str(edge_id), "deleted": True, "targetStatus": target.status}

    def create_generation_batch(self, payload: Any) -> dict[str, Any]:
        input_document = payload.model_dump(mode="json", by_alias=True)
        batch_id = uuid.uuid4()
        with self._sessions.begin() as session:
            project = self._require_project(session, payload.project_id, lock=True)
            project.universal_canvas_enabled = True
            if project.canvas_template_key == CanvasTemplateKey.PRODUCT_AD.value:
                project.product_ad_template_enabled = True
            node = self._required(session, CanvasGraphNode, payload.canvas_node_id, lock=True)
            if (
                node.production_run_id != payload.project_id
                or node.node_type != CanvasNodeType.GENERATION_BATCH.value
            ):
                raise ValueError("生成批次必须绑定当前项目的 GenerationBatchNode")
            existing = session.scalar(
                select(MediaGenerationBatch).where(
                    MediaGenerationBatch.idempotency_key == payload.idempotency_key
                )
            )
            if existing is not None:
                return _generation_batch_json(existing)
            batch = MediaGenerationBatch(
                id=batch_id,
                production_run_id=payload.project_id,
                canvas_node_id=node.id,
                media_kind=payload.media_kind,
                candidate_count=payload.candidate_count,
                provider=payload.provider,
                model=payload.model,
                status="pending",
                idempotency_key=payload.idempotency_key,
                input_json=payload.input,
            )
            session.add(batch)
            session.flush()
            base_prompt = str(
                payload.input.get("prompt") or json.dumps(payload.input, ensure_ascii=False)
            )
            candidate_step_ids: list[str] = []
            for candidate_index in range(1, payload.candidate_count + 1):
                candidate_snapshot = {
                    **input_document,
                    "batchId": str(batch.id),
                    "candidateIndex": candidate_index,
                }
                input_hash = _json_hash(candidate_snapshot)
                step = WorkflowStep(
                    id=uuid.uuid4(),
                    production_run_id=payload.project_id,
                    kind=(
                        StepKind.IMAGE.value
                        if payload.media_kind == "image"
                        else StepKind.VIDEO.value
                    ),
                    status=StepStatus.PENDING.value,
                    attempt=1,
                    operation_key=(
                        f"media:{payload.media_kind}:batch:{batch.id}:candidate:{candidate_index}"
                    ),
                    idempotency_key=hashlib.sha256(
                        f"{payload.idempotency_key}:{candidate_index}".encode("utf-8")
                    ).hexdigest(),
                    provider=payload.provider,
                    model=payload.model,
                    input_hash=input_hash,
                    request_hash=input_hash,
                    input_snapshot_json=candidate_snapshot,
                )
                session.add(step)
                if candidate_index == 1:
                    batch.workflow_step_id = step.id
                final_prompt = (
                    f"{base_prompt}\n\n候选 {candidate_index}/{payload.candidate_count}："
                    "保持全部主体身份锚点，提供与其他候选不同的构图或机位。"
                )
                session.add(
                    PromptRecord(
                        id=uuid.uuid4(),
                        step_id=step.id,
                        purpose=(
                            PromptPurpose.IMAGE.value
                            if payload.media_kind == "image"
                            else PromptPurpose.VIDEO.value
                        ),
                        model=payload.model,
                        prompt_text=final_prompt,
                        sha256=hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
                        call_purpose=(
                            f"{payload.media_kind}_generation_candidate_{candidate_index}"
                        ),
                        node_id=node.id,
                        business_object_type="media_generation_batch",
                        business_object_id=batch.id,
                        template_name=f"media.{payload.media_kind}.candidate.v1",
                        template_version="1.0.0",
                        user_prompt=base_prompt,
                        final_prompt=final_prompt,
                        provider_request_json={
                            "candidateIndex": candidate_index,
                            "candidateCount": payload.candidate_count,
                            "input": payload.input,
                        },
                        input_snapshot_json=candidate_snapshot,
                        status="pending",
                        input_hash=input_hash,
                    )
                )
                candidate_step_ids.append(str(step.id))
            node.status = "pending"
            node.data_json = {
                **node.data_json,
                "batchId": str(batch.id),
                "candidateCount": payload.candidate_count,
                "candidateStepIds": candidate_step_ids,
                "status": "pending",
            }
            self._record_event(
                session,
                payload.project_id,
                "generation_batch_queued",
                {"batchId": str(batch.id), "nodeId": str(node.id)},
            )
            return _generation_batch_json(batch)

    def create_video_edit_recipe(self, payload: VideoEditRecipeDraft) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, payload.project_id, lock=True)
            project.universal_canvas_enabled = True
            project.video_edit_v2_enabled = True
            source = self._required(session, Asset, payload.source_asset_id)
            self._validate_video_source(source, payload.project_id)
            node = CanvasGraphNode(
                id=uuid.uuid4(),
                production_run_id=payload.project_id,
                node_type=CanvasNodeType.VIDEO_EDIT.value,
                object_type="video_edit_recipe",
                status="draft",
                data_json={"title": "视频局部重编", "sourceAssetId": str(source.id)},
            )
            session.add(node)
            session.flush()
            recipe = self._add_video_edit_revision(
                session,
                node=node,
                draft=payload,
                revision=1,
                parent_recipe_id=None,
            )
            node.object_id = recipe.id
            if source.canvas_node_id is not None:
                source_node = self._required(session, CanvasGraphNode, source.canvas_node_id)
                if source_node.node_type == CanvasNodeType.VIDEO_ASSET.value:
                    connection = CanvasConnection(
                        sourceNodeId=source_node.id,
                        sourceNodeType=CanvasNodeType.VIDEO_ASSET,
                        sourcePort="video_asset",
                        targetNodeId=node.id,
                        targetNodeType=CanvasNodeType.VIDEO_EDIT,
                        targetPort="video_asset",
                    )
                    session.add(_graph_edge(payload.project_id, connection))
            self._record_event(
                session,
                payload.project_id,
                "video_edit_recipe_created",
                {"recipeId": str(recipe.id), "nodeId": str(node.id)},
            )
            return _video_edit_recipe_json(session, recipe)

    def update_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            current = self._required(session, VideoEditRecipe, recipe_id, lock=True)
            if current.revision != expected_revision:
                raise WorkflowConflictError(
                    f"视频重编版本冲突：当前 {current.revision}，提交 {expected_revision}"
                )
            document = _video_edit_draft_json(session, current)
            document.update(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
            draft = VideoEditRecipeDraft.model_validate(document)
            next_recipe = self._create_video_edit_revision(session, current, draft)
            return _video_edit_recipe_json(session, next_recipe)

    def replace_video_edit_annotations(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            current = self._required(session, VideoEditRecipe, recipe_id, lock=True)
            if current.revision != expected_revision:
                raise WorkflowConflictError(
                    f"视频重编版本冲突：当前 {current.revision}，提交 {expected_revision}"
                )
            document = _video_edit_draft_json(session, current)
            document["annotations"] = [
                item.model_dump(mode="json", by_alias=True) for item in payload.annotations
            ]
            draft = VideoEditRecipeDraft.model_validate(document)
            next_recipe = self._create_video_edit_revision(session, current, draft)
            return _video_edit_recipe_json(session, next_recipe)

    def compile_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        capability: ProviderEditCapability,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            recipe = self._required(session, VideoEditRecipe, recipe_id, lock=True)
            if recipe.status not in {"draft", "compiled"}:
                raise WorkflowConflictError("只有草稿或已编译配方可以重新编译")
            draft = VideoEditRecipeDraft.model_validate(_video_edit_draft_json(session, recipe))
            plan = compile_video_edit_plan(draft, capability)
            capability_row = session.scalar(
                select(ProviderCapability).where(
                    ProviderCapability.provider == capability.provider,
                    ProviderCapability.model == capability.model,
                )
            )
            if capability_row is None:
                capability_row = ProviderCapability(
                    id=uuid.uuid4(),
                    provider=capability.provider,
                    model=capability.model,
                    media_kind="video_edit",
                    capabilities_json=capability.model_dump(mode="json", by_alias=True),
                    active=True,
                )
                session.add(capability_row)
            else:
                capability_row.media_kind = "video_edit"
                capability_row.capabilities_json = capability.model_dump(mode="json", by_alias=True)
                capability_row.active = True
            recipe.compilation_json = {
                **plan.model_dump(mode="json", by_alias=True),
                "provider": capability.provider,
                "model": capability.model,
                "providerCapabilityId": str(capability_row.id),
            }
            recipe.estimated_cost_micros = plan.estimated_cost_micros
            recipe.status = "compiled"
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_recipe_compiled",
                {"recipeId": str(recipe.id), "plan": recipe.compilation_json},
            )
            return {"recipeId": str(recipe.id), **recipe.compilation_json}

    def submit_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        payload: Any,
        *,
        image_provider: str,
        image_model: str,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            recipe = self._required(session, VideoEditRecipe, recipe_id, lock=True)
            if recipe.status == "queued":
                existing_step = session.scalar(
                    select(WorkflowStep).where(
                        WorkflowStep.production_run_id == recipe.production_run_id,
                        WorkflowStep.operation_key == f"video:edit-recipe:{recipe.id}",
                    )
                )
                if existing_step is not None:
                    return {
                        "recipeId": str(recipe.id),
                        "jobId": str(existing_step.id),
                        "status": "queued",
                        "idempotencyKey": payload.idempotency_key,
                    }
            if recipe.status != "compiled" or recipe.compilation_json is None:
                raise WorkflowConflictError("提交前必须先编译视频重编能力计划")
            if recipe.estimated_cost_micros != payload.accept_estimated_cost_micros:
                raise WorkflowConflictError("预计费用已经变化，请重新确认后提交")
            draft_document = _video_edit_draft_json(session, recipe)
            anchor_step_ids: list[str] = []
            if recipe.compilation_json["mode"] == "two_stage":
                for boundary, timestamp_ms in (
                    ("start", recipe.start_ms),
                    ("end", recipe.end_ms),
                ):
                    anchor_snapshot = {
                        "recipeId": str(recipe.id),
                        "boundary": boundary,
                        "timestampMs": timestamp_ms,
                        "sourceAssetId": str(recipe.source_asset_id),
                        "referenceAssetIds": draft_document["referenceAssetIds"],
                        "annotations": draft_document["annotations"],
                    }
                    anchor_hash = _json_hash(anchor_snapshot)
                    anchor_step = WorkflowStep(
                        id=uuid.uuid4(),
                        production_run_id=recipe.production_run_id,
                        kind=StepKind.IMAGE.value,
                        status=StepStatus.PENDING.value,
                        attempt=1,
                        operation_key=f"video:edit-anchor:{recipe.id}:{boundary}",
                        idempotency_key=hashlib.sha256(
                            f"{payload.idempotency_key}:anchor:{boundary}".encode()
                        ).hexdigest(),
                        provider=image_provider,
                        model=image_model,
                        input_hash=anchor_hash,
                        request_hash=anchor_hash,
                        input_snapshot_json=anchor_snapshot,
                    )
                    session.add(anchor_step)
                    anchor_prompt = _control_anchor_prompt(draft_document, boundary)
                    session.add(
                        PromptRecord(
                            id=uuid.uuid4(),
                            step_id=anchor_step.id,
                            purpose=PromptPurpose.IMAGE.value,
                            model=image_model,
                            prompt_text=anchor_prompt,
                            sha256=hashlib.sha256(anchor_prompt.encode("utf-8")).hexdigest(),
                            call_purpose=f"video_edit_control_anchor_{boundary}",
                            node_id=recipe.canvas_node_id,
                            business_object_type="video_edit_recipe",
                            business_object_id=recipe.id,
                            template_name="video.edit.control-anchor.v1",
                            template_version="1.0.0",
                            user_prompt=recipe.instruction,
                            final_prompt=anchor_prompt,
                            provider_request_json=anchor_snapshot,
                            input_snapshot_json=anchor_snapshot,
                            status="pending",
                            input_hash=anchor_hash,
                        )
                    )
                    anchor_step_ids.append(str(anchor_step.id))
            request_document = {
                "recipe": draft_document,
                "compilation": recipe.compilation_json,
                "controlAnchorStepIds": anchor_step_ids,
            }
            input_hash = _json_hash(request_document)
            step_key = hashlib.sha256(payload.idempotency_key.encode("utf-8")).hexdigest()
            existing = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == step_key)
            )
            if existing is not None:
                return {
                    "recipeId": str(recipe.id),
                    "jobId": str(existing.id),
                    "status": existing.status,
                    "idempotencyKey": payload.idempotency_key,
                }
            provider = str(recipe.compilation_json["provider"])
            model = str(recipe.compilation_json["model"])
            step = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                kind=StepKind.VIDEO.value,
                status=StepStatus.PENDING.value,
                attempt=1,
                operation_key=f"video:edit-recipe:{recipe.id}",
                idempotency_key=step_key,
                provider=provider,
                model=model,
                input_hash=input_hash,
                request_hash=input_hash,
                input_snapshot_json=request_document,
            )
            session.add(step)
            final_prompt = _video_edit_prompt(draft_document)
            session.add(
                PromptRecord(
                    id=uuid.uuid4(),
                    step_id=step.id,
                    purpose=PromptPurpose.VIDEO.value,
                    model=model,
                    prompt_text=final_prompt,
                    sha256=hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
                    call_purpose="video_edit_recipe",
                    node_id=recipe.canvas_node_id,
                    business_object_type="video_edit_recipe",
                    business_object_id=recipe.id,
                    template_name="video.edit.recipe.v2",
                    template_version="2.0.0",
                    user_prompt=recipe.instruction,
                    final_prompt=final_prompt,
                    provider_request_json=request_document,
                    input_snapshot_json=request_document,
                    parameters_json={
                        "startMs": recipe.start_ms,
                        "endMs": recipe.end_ms,
                        "mode": recipe.compilation_json["mode"],
                    },
                    cost_micros=recipe.estimated_cost_micros,
                    status="pending",
                    input_hash=input_hash,
                )
            )
            session.add(
                GenerationAttempt(
                    id=uuid.uuid4(),
                    production_run_id=recipe.production_run_id,
                    workflow_step_id=step.id,
                    business_object_type="video_edit_recipe",
                    business_object_id=recipe.id,
                    idempotency_key=payload.idempotency_key,
                    provider=provider,
                    model=model,
                    status="pending",
                    request_json=request_document,
                    cost_micros=recipe.estimated_cost_micros,
                )
            )
            recipe.status = "queued"
            node = self._required(session, CanvasGraphNode, recipe.canvas_node_id, lock=True)
            node.status = "pending"
            node.data_json = {**node.data_json, "recipeId": str(recipe.id), "status": "pending"}
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_recipe_queued",
                {"recipeId": str(recipe.id), "stepId": str(step.id)},
            )
            return {
                "recipeId": str(recipe.id),
                "jobId": str(step.id),
                "status": "queued",
                "idempotencyKey": payload.idempotency_key,
            }

    def events(
        self, project_id: uuid.UUID, *, last_event_id: str | None = None
    ) -> tuple[dict[str, Any], ...]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            query = (
                select(CanvasEvent)
                .where(CanvasEvent.production_run_id == project_id)
                .order_by(CanvasEvent.created_at, CanvasEvent.id)
                .limit(200)
            )
            if last_event_id:
                cursor = self._required(session, CanvasEvent, uuid.UUID(last_event_id))
                query = query.where(CanvasEvent.created_at > cursor.created_at)
            return tuple(
                {
                    "id": str(event.id),
                    "type": event.event_type,
                    "data": event.data_json,
                }
                for event in session.scalars(query)
            )

    def get_canvas(self, project_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            project = self._require_project(session, project_id)
            layout = session.scalar(
                select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
            )
            brief = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            subjects = list(
                session.scalars(
                    select(Subject)
                    .where(Subject.production_run_id == project_id, Subject.status != "archived")
                    .order_by(Subject.created_at)
                )
            )
            stories = list(
                session.scalars(
                    select(StoryRevisionRecord)
                    .where(StoryRevisionRecord.production_run_id == project_id)
                    .order_by(StoryRevisionRecord.revision)
                )
            )
            scenes = list(
                session.scalars(
                    select(Scene)
                    .where(Scene.production_run_id == project_id)
                    .order_by(Scene.sort_order)
                )
            )
            beats = list(
                session.scalars(
                    select(ShotBeat)
                    .join(Scene, Scene.id == ShotBeat.scene_id)
                    .where(
                        Scene.production_run_id == project_id,
                        ShotBeat.status != "superseded",
                    )
                    .order_by(Scene.sort_order, ShotBeat.sort_order)
                )
            )
            result = _canvas_json(
                project_id,
                layout=layout,
                brief=brief,
                subjects=subjects,
                stories=stories,
                scenes=scenes,
                beats=beats,
                session=session,
                enabled=project.canvas_v2_enabled,
            )
            graph_nodes = list(
                session.scalars(
                    select(CanvasGraphNode)
                    .where(CanvasGraphNode.production_run_id == project_id)
                    .order_by(CanvasGraphNode.created_at, CanvasGraphNode.id)
                )
            )
            graph_edges = list(
                session.scalars(
                    select(CanvasGraphEdge)
                    .where(CanvasGraphEdge.production_run_id == project_id)
                    .order_by(CanvasGraphEdge.created_at, CanvasGraphEdge.id)
                )
            )
            legacy_assets = list(
                session.scalars(
                    select(Asset)
                    .where(
                        Asset.production_run_id == project_id,
                        Asset.canvas_node_id.is_(None),
                        Asset.media_type.in_(("image", "video")),
                    )
                    .order_by(Asset.created_at)
                )
            )
            _merge_universal_graph(
                result,
                layout=layout,
                graph_nodes=graph_nodes,
                graph_edges=graph_edges,
                legacy_assets=legacy_assets,
            )
            result["templateKey"] = project.canvas_template_key
            result["featureFlags"] = {
                "UNIVERSAL_CANVAS": project.universal_canvas_enabled,
                "PRODUCT_AD_TEMPLATE": project.product_ad_template_enabled,
                "VIDEO_EDIT_V2": project.video_edit_v2_enabled,
            }
            return result

    def save_canvas_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            row = session.scalar(
                select(CanvasLayout)
                .where(CanvasLayout.production_run_id == project_id)
                .with_for_update()
            )
            current_version = 0 if row is None else row.version
            document = payload.model_dump(mode="json", by_alias=True)
            rebased_from: int | None = None
            if current_version != expected_version:
                operation_types = {
                    str(operation.get("type", "")) for operation in document["operations"]
                }
                replayable = bool(operation_types) and operation_types <= {
                    "move_node",
                    "auto_layout",
                    "collapse_node",
                    "viewport",
                }
                if row is None or not replayable:
                    raise WorkflowConflictError(
                        f"画布布局版本冲突：当前 {current_version}，提交 {expected_version}"
                    )
                rebased_from = expected_version
                current_nodes = {str(item.get("nodeId")): dict(item) for item in row.nodes_json}
                for item in document["nodes"]:
                    current_nodes[str(item.get("nodeId"))] = item
                document["nodes"] = list(current_nodes.values())
                document["edges"] = row.edges_json
            if row is None:
                row = CanvasLayout(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    version=1,
                )
                session.add(row)
            else:
                row.version += 1
            row.nodes_json = document["nodes"]
            row.edges_json = document["edges"]
            row.viewport_json = document["viewport"]
            row.operations_json = [
                *(row.operations_json if row is not None else []),
                *document["operations"],
            ][-500:]
            row.sync_status = "saved"
            session.flush()
            return {
                "projectId": str(project_id),
                "layoutVersion": row.version,
                "nodes": row.nodes_json,
                "edges": row.edges_json,
                "viewport": row.viewport_json,
                "operations": row.operations_json,
                "syncStatus": row.sync_status,
                "rebasedFromVersion": rebased_from,
            }

    @staticmethod
    def _record_event(
        session: Session,
        project_id: uuid.UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> CanvasEvent:
        event = CanvasEvent(
            id=uuid.uuid4(),
            production_run_id=project_id,
            event_type=event_type,
            data_json=data,
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def _validate_video_source(source: Asset, project_id: uuid.UUID) -> None:
        if source.media_type != "video":
            raise ValueError("视频局部重编的源资产必须是视频")
        if source.status not in {"approved", "ready"}:
            raise ValueError("视频局部重编的源资产尚不可用")
        if source.production_run_id != project_id:
            raise ValueError("视频局部重编的源资产不属于当前项目")

    def _add_video_edit_revision(
        self,
        session: Session,
        *,
        node: CanvasGraphNode,
        draft: VideoEditRecipeDraft,
        revision: int,
        parent_recipe_id: uuid.UUID | None,
    ) -> VideoEditRecipe:
        source = self._required(session, Asset, draft.source_asset_id)
        self._validate_video_source(source, draft.project_id)
        row = VideoEditRecipe(
            id=uuid.uuid4(),
            production_run_id=draft.project_id,
            canvas_node_id=node.id,
            source_asset_id=draft.source_asset_id,
            parent_recipe_id=parent_recipe_id,
            revision=revision,
            start_ms=draft.start_ms,
            end_ms=draft.end_ms,
            instruction=draft.instruction,
            status="draft",
        )
        session.add(row)
        session.flush()
        for ordinal, annotation in enumerate(draft.annotations, 1):
            session.add(
                VideoEditAnnotation(
                    id=uuid.uuid4(),
                    recipe_id=row.id,
                    ordinal=ordinal,
                    frame_timestamp_ms=annotation.frame_timestamp_ms,
                    tool=annotation.tool.value,
                    points_json=[point.model_dump(mode="json") for point in annotation.points],
                    label=annotation.label,
                )
            )
        for ordinal, asset_id in enumerate(draft.reference_asset_ids, 1):
            asset = self._required(session, Asset, asset_id)
            if asset.media_type != "image" or asset.status not in {"approved", "ready"}:
                raise ValueError("视频重编参考必须是可用图片")
            if asset.scope != "canon" and asset.production_run_id != draft.project_id:
                raise ValueError("视频重编参考不属于当前项目")
            session.add(
                VideoEditReference(
                    id=uuid.uuid4(),
                    recipe_id=row.id,
                    asset_id=asset.id,
                    ordinal=ordinal,
                    semantic_role=asset.semantic_key or asset.role,
                    provider_included=True,
                )
            )
        session.flush()
        return row

    def _create_video_edit_revision(
        self,
        session: Session,
        current: VideoEditRecipe,
        draft: VideoEditRecipeDraft,
    ) -> VideoEditRecipe:
        if current.status == "queued":
            raise WorkflowConflictError("已提交的配方不可修改；请从该版本创建新的编辑节点")
        node = self._required(session, CanvasGraphNode, current.canvas_node_id, lock=True)
        current.status = "superseded"
        next_recipe = self._add_video_edit_revision(
            session,
            node=node,
            draft=draft,
            revision=current.revision + 1,
            parent_recipe_id=current.id,
        )
        node.object_id = next_recipe.id
        node.status = "draft"
        node.revision += 1
        node.data_json = {
            **node.data_json,
            "recipeId": str(next_recipe.id),
            "revision": next_recipe.revision,
            "status": "draft",
        }
        self._record_event(
            session,
            current.production_run_id,
            "video_edit_recipe_revised",
            {
                "recipeId": str(next_recipe.id),
                "parentRecipeId": str(current.id),
                "revision": next_recipe.revision,
            },
        )
        return next_recipe

    def _add_subject_revision(
        self,
        session: Session,
        subject: Subject,
        payload: SubjectDraft,
    ) -> SubjectRevision:
        revision = (
            int(
                session.scalar(
                    select(func.coalesce(func.max(SubjectRevision.revision), 0)).where(
                        SubjectRevision.subject_id == subject.id
                    )
                )
                or 0
            )
            + 1
        )
        row = SubjectRevision(
            id=uuid.uuid4(),
            subject_id=subject.id,
            revision=revision,
            name=payload.name,
            identity_anchors_json=list(payload.identity_anchors),
            immutable_traits_json=list(payload.immutable_traits),
            relationship_notes=payload.relationship_notes,
            dramatic_function=payload.dramatic_function,
            visual_risks_json=list(payload.visual_risks),
            revision_hash=_subject_hash(payload),
            approval_status="draft",
        )
        session.add(row)
        session.flush()
        for order, reference in enumerate(payload.references, 1):
            asset = self._required(session, Asset, reference.asset_id)
            if asset.media_type != "image" or asset.status not in {"approved", "ready"}:
                raise ValueError("主体参考必须是可用图片")
            if asset.scope != "canon" and asset.production_run_id != subject.production_run_id:
                raise ValueError("主体参考不属于当前项目")
            session.add(
                SubjectReference(
                    id=uuid.uuid4(),
                    subject_revision_id=row.id,
                    asset_id=asset.id,
                    semantic_role=reference.semantic_role,
                    sort_order=order,
                    instruction=reference.instruction,
                )
            )
        return row

    @staticmethod
    def _save_subject_states(
        session: Session,
        beat_id: uuid.UUID,
        states: list[dict[str, Any]],
    ) -> None:
        seen: set[uuid.UUID] = set()
        for state in states:
            revision_id = uuid.UUID(str(state["subjectRevisionId"]))
            if revision_id in seen:
                raise ValueError("同一主体在一个 Beat 中只能有一份状态")
            seen.add(revision_id)
            session.add(
                ShotSubjectState(
                    id=uuid.uuid4(),
                    shot_beat_id=beat_id,
                    subject_revision_id=revision_id,
                    start_state_json=state.get("startState", {}),
                    end_state_json=state.get("endState", {}),
                    action=str(state.get("action", "")),
                    interaction=str(state.get("interaction", "")),
                )
            )

    @staticmethod
    def _subject_references(
        session: Session,
        revision_id: uuid.UUID,
    ) -> tuple[SubjectReference, ...]:
        return tuple(
            session.scalars(
                select(SubjectReference)
                .where(SubjectReference.subject_revision_id == revision_id)
                .order_by(SubjectReference.sort_order)
            )
        )

    @staticmethod
    def _require_project(
        session: Session,
        project_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> ProductionRun:
        query = select(ProductionRun).where(ProductionRun.id == project_id)
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise RecordNotFoundError(f"ProductionRun {project_id} was not found")
        return row

    @staticmethod
    def _required(
        session: Session,
        model: type[Any],
        record_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> Any:
        query = select(model).where(model.id == record_id)
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise RecordNotFoundError(f"{model.__name__} {record_id} was not found")
        return row

    @staticmethod
    def _ensure_subject_name_available(
        session: Session,
        project_id: uuid.UUID,
        name: str,
        *,
        excluding_subject_id: uuid.UUID | None = None,
    ) -> None:
        query = (
            select(Subject.id)
            .join(SubjectRevision, SubjectRevision.id == Subject.current_revision_id)
            .where(
                Subject.production_run_id == project_id,
                func.lower(SubjectRevision.name) == name.casefold(),
                Subject.status != "archived",
            )
        )
        if excluding_subject_id is not None:
            query = query.where(Subject.id != excluding_subject_id)
        if session.scalar(query) is not None:
            raise ValueError("同一项目内主体名称不能重复")

    @staticmethod
    def _mark_project_story_stale(session: Session, project_id: uuid.UUID, reason: str) -> None:
        session.execute(
            GenerationAttempt.__table__.update()
            .where(
                GenerationAttempt.production_run_id == project_id,
                GenerationAttempt.business_object_type.in_(
                    ("project_story_strategy", "story_revision", "shot_beat")
                ),
                GenerationAttempt.status == "succeeded",
            )
            .values(status="stale", error_json={"reason": reason})
        )

    @staticmethod
    def _mark_subject_downstream_stale(session: Session, subject_id: uuid.UUID) -> None:
        subject = session.scalar(select(Subject).where(Subject.id == subject_id))
        if subject is not None:
            SqlAlchemyAigcCanvasRepository._mark_project_story_stale(
                session,
                subject.production_run_id,
                f"subject {subject_id} revision changed",
            )


def _graph_edge(project_id: uuid.UUID, connection: CanvasConnection) -> CanvasGraphEdge:
    return CanvasGraphEdge(
        id=uuid.uuid4(),
        production_run_id=project_id,
        source_node_id=connection.source_node_id,
        source_port=connection.source_port.value,
        target_node_id=connection.target_node_id,
        target_port=connection.target_port.value,
        relation_type=f"{connection.source_port.value}->{connection.target_port.value}",
        revision=1,
    )


def _asset_storage_key(path: Path, asset_root: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        key = resolved.relative_to(asset_root).as_posix()
    except ValueError as exc:
        raise ValueError("managed asset path is outside the configured asset root") from exc
    _resolve_asset_path(key, asset_root)
    return key


def _stored_asset(row: Asset, asset_root: Path) -> StoredAsset:
    return StoredAsset(
        id=row.id,
        project_id=row.production_run_id,
        scene_id=row.scene_id,
        shot_card_id=row.shot_card_id,
        step_id=row.producing_step_id,
        role=row.role,
        media_type=row.media_type,
        scope=row.scope,
        status=row.status,
        path=_resolve_asset_path(row.storage_key, asset_root),
        sha256=row.sha256,
        metadata=row.metadata_json,
        semantic_key=row.semantic_key,
        created_at=row.created_at,
    )


def _resolve_asset_path(storage_key: str, asset_root: Path) -> Path:
    key = storage_key.strip()
    pure = PurePosixPath(key)
    if not key or pure.is_absolute() or ".." in pure.parts:
        raise ValueError("asset storage key is invalid")
    resolved = asset_root.joinpath(*pure.parts).resolve()
    try:
        resolved.relative_to(asset_root)
    except ValueError as exc:
        raise ValueError("asset storage key escapes the configured root") from exc
    if not resolved.is_file():
        raise ValueError(f"asset content is unavailable: {storage_key}")
    return resolved


def _graph_node_json(row: CanvasGraphNode) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "type": row.node_type,
        "objectType": row.object_type,
        "objectId": None if row.object_id is None else str(row.object_id),
        "revision": row.revision,
        "status": row.status,
        "data": row.data_json,
    }


def _graph_edge_json(
    row: CanvasGraphEdge,
    source: CanvasGraphNode,
    target: CanvasGraphNode,
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "sourceNodeId": str(row.source_node_id),
        "sourceNodeType": source.node_type,
        "sourcePort": row.source_port,
        "targetNodeId": str(row.target_node_id),
        "targetNodeType": target.node_type,
        "targetPort": row.target_port,
        "relationType": row.relation_type,
        "revision": row.revision,
    }


def _generation_batch_json(row: MediaGenerationBatch) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "canvasNodeId": str(row.canvas_node_id),
        "workflowStepId": (None if row.workflow_step_id is None else str(row.workflow_step_id)),
        "mediaKind": row.media_kind,
        "candidateCount": row.candidate_count,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "outputAssetIds": row.output_asset_ids_json,
    }


def _video_edit_draft_json(session: Session, row: VideoEditRecipe) -> dict[str, Any]:
    annotations = list(
        session.scalars(
            select(VideoEditAnnotation)
            .where(VideoEditAnnotation.recipe_id == row.id)
            .order_by(VideoEditAnnotation.ordinal)
        )
    )
    references = list(
        session.scalars(
            select(VideoEditReference)
            .where(VideoEditReference.recipe_id == row.id)
            .order_by(VideoEditReference.ordinal)
        )
    )
    return {
        "projectId": str(row.production_run_id),
        "sourceAssetId": str(row.source_asset_id),
        "startMs": row.start_ms,
        "endMs": row.end_ms,
        "instruction": row.instruction,
        "referenceAssetIds": [str(item.asset_id) for item in references],
        "annotations": [
            {
                "frameTimestampMs": item.frame_timestamp_ms,
                "tool": item.tool,
                "points": item.points_json,
                "label": item.label,
            }
            for item in annotations
        ],
    }


def _video_edit_recipe_json(session: Session, row: VideoEditRecipe) -> dict[str, Any]:
    draft = _video_edit_draft_json(session, row)
    references = list(
        session.scalars(
            select(VideoEditReference)
            .where(VideoEditReference.recipe_id == row.id)
            .order_by(VideoEditReference.ordinal)
        )
    )
    return {
        "id": str(row.id),
        "canvasNodeId": str(row.canvas_node_id),
        "parentRecipeId": (None if row.parent_recipe_id is None else str(row.parent_recipe_id)),
        "revision": row.revision,
        "status": row.status,
        "compilation": row.compilation_json,
        "estimatedCostMicros": row.estimated_cost_micros,
        **draft,
        "references": [
            {
                "assetId": str(item.asset_id),
                "semanticRole": item.semantic_role,
                "providerIncluded": item.provider_included,
            }
            for item in references
        ],
    }


def _video_edit_prompt(draft: dict[str, Any]) -> str:
    annotation_instructions = [
        {
            "timeMs": item["frameTimestampMs"],
            "tool": item["tool"],
            "points": item["points"],
            "label": item["label"],
        }
        for item in draft["annotations"]
    ]
    return (
        "仅重编源视频的指定区间，保持区间外画面和原音轨不变。\n"
        f"区间：{draft['startMs']}ms - {draft['endMs']}ms\n"
        f"编辑要求：{draft['instruction']}\n"
        f"参考素材ID：{json.dumps(draft['referenceAssetIds'], ensure_ascii=False)}\n"
        f"归一化视觉标注：{json.dumps(annotation_instructions, ensure_ascii=False)}"
    )


def _control_anchor_prompt(draft: dict[str, Any], boundary: str) -> str:
    label = "起始" if boundary == "start" else "结束"
    return (
        f"生成视频局部重编区间的{label}控制锚点。保持未标注区域、人物和产品身份不变，"
        "只执行标注与文字要求；输出干净画面，不保留标注图形。\n"
        f"编辑要求：{draft['instruction']}\n"
        f"参考素材ID：{json.dumps(draft['referenceAssetIds'], ensure_ascii=False)}\n"
        f"归一化标注：{json.dumps(draft['annotations'], ensure_ascii=False)}"
    )


def _merge_universal_graph(
    canvas: dict[str, Any],
    *,
    layout: CanvasLayout | None,
    graph_nodes: list[CanvasGraphNode],
    graph_edges: list[CanvasGraphEdge],
    legacy_assets: list[Asset],
) -> None:
    positions = {str(item.get("nodeId")): item for item in (layout.nodes_json if layout else [])}
    existing_ids = {str(item["id"]) for item in canvas["nodes"]}
    stage_by_type = {
        CanvasNodeType.REFERENCE_ASSET.value: 0,
        CanvasNodeType.GENERATION_BATCH.value: 1,
        CanvasNodeType.IMAGE_ASSET.value: 2,
        CanvasNodeType.VIDEO_GENERATION.value: 3,
        CanvasNodeType.VIDEO_ASSET.value: 4,
        CanvasNodeType.VIDEO_EDIT.value: 5,
        CanvasNodeType.VIDEO_SEGMENT.value: 6,
        CanvasNodeType.REVIEW.value: 7,
        CanvasNodeType.TIMELINE.value: 8,
    }
    stage_rows: dict[int, int] = {}
    node_by_id: dict[uuid.UUID, CanvasGraphNode] = {}
    for row in graph_nodes:
        node_by_id[row.id] = row
        if str(row.id) in existing_ids:
            continue
        document = _graph_node_json(row)
        stage = stage_by_type.get(row.node_type, 9)
        row_index = stage_rows.get(stage, 0)
        stage_rows[stage] = row_index + 1
        stored = positions.get(str(row.id), {})
        document["position"] = {
            "x": stored.get("x", 80 + stage * 320),
            "y": stored.get("y", 80 + row_index * 240),
        }
        canvas["nodes"].append(document)
        existing_ids.add(str(row.id))
    for asset in legacy_assets:
        if str(asset.id) in existing_ids:
            continue
        node_type = (
            CanvasNodeType.VIDEO_ASSET.value
            if asset.media_type == "video"
            else CanvasNodeType.IMAGE_ASSET.value
        )
        stage = stage_by_type[node_type]
        row_index = stage_rows.get(stage, 0)
        stage_rows[stage] = row_index + 1
        stored = positions.get(str(asset.id), {})
        canvas["nodes"].append(
            {
                "id": str(asset.id),
                "type": node_type,
                "objectType": "legacy_asset",
                "objectId": str(asset.id),
                "revision": int(asset.metadata_json.get("version", 1)),
                "status": asset.status,
                "data": {
                    "title": asset.semantic_key or asset.role,
                    "assetId": str(asset.id),
                    "mediaType": asset.media_type,
                    "status": asset.status,
                    "metadata": asset.metadata_json,
                    "legacyProjection": True,
                },
                "position": {
                    "x": stored.get("x", 80 + stage * 320),
                    "y": stored.get("y", 80 + row_index * 240),
                },
            }
        )
        existing_ids.add(str(asset.id))
    existing_edge_ids = {str(item["id"]) for item in canvas["edges"]}
    for edge in graph_edges:
        source = node_by_id.get(edge.source_node_id)
        target = node_by_id.get(edge.target_node_id)
        if source is None or target is None or str(edge.id) in existing_edge_ids:
            continue
        canvas["edges"].append(_graph_edge_json(edge, source, target))


def _subject_hash(payload: SubjectDraft) -> str:
    return _json_hash(payload.model_dump(mode="json", by_alias=True))


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _brief_document(row: StoryBriefRecord) -> dict[str, Any]:
    return {
        "theme": row.theme,
        "audience": row.audience,
        "genre": row.genre,
        "tone": row.tone,
        "aspectRatio": row.aspect_ratio,
        "targetDurationSeconds": row.target_duration_seconds,
        "constraints": row.constraints_json,
    }


def _brief_json(row: StoryBriefRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "revision": row.revision,
        **_brief_document(row),
        "createdAt": None if row.created_at is None else row.created_at.isoformat(),
    }


def _subject_draft(
    subject: Subject,
    revision: SubjectRevision,
    references: tuple[SubjectReference, ...],
) -> SubjectDraft:
    return SubjectDraft(
        name=revision.name,
        kind=subject.kind,
        role=subject.role,
        identityAnchors=revision.identity_anchors_json,
        immutableTraits=revision.immutable_traits_json,
        relationshipNotes=revision.relationship_notes,
        dramaticFunction=revision.dramatic_function,
        visualRisks=revision.visual_risks_json,
        references=[
            {
                "assetId": item.asset_id,
                "semanticRole": item.semantic_role,
                "instruction": item.instruction,
            }
            for item in references
        ],
    )


def _subject_json(
    subject: Subject,
    revision: SubjectRevision,
    references: tuple[SubjectReference, ...],
) -> dict[str, Any]:
    return {
        "id": str(subject.id),
        "projectId": str(subject.production_run_id),
        "revisionId": str(revision.id),
        "revision": revision.revision,
        "status": subject.status,
        "approvalStatus": revision.approval_status,
        **_subject_draft(subject, revision, references).model_dump(mode="json", by_alias=True),
    }


def _scorecard(row: StoryScore) -> StoryScorecard:
    return StoryScorecard(
        openingHook=row.opening_hook,
        causalCompleteness=row.causal_completeness,
        subjectNecessity=row.subject_necessity,
        emotionalArc=row.emotional_arc,
        visualizability=row.visualizability,
        durationFit=row.duration_fit,
        continuityRisk=row.continuity_risk,
        safety=row.safety,
        rationale=row.rationale,
        warnings=row.warnings_json,
    )


def _story_json(row: StoryRevisionRecord, score: StoryScore | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "briefId": None if row.brief_id is None else str(row.brief_id),
        "revision": row.revision,
        "strategy": row.strategy,
        "status": row.status,
        "title": row.title,
        "logline": row.logline,
        "synopsis": row.synopsis,
        "subjectIds": row.subject_ids_json,
        "scenes": row.scene_plan_json,
        "candidatePromptId": (
            None if row.candidate_prompt_id is None else str(row.candidate_prompt_id)
        ),
        "criticPromptId": (None if row.critic_prompt_id is None else str(row.critic_prompt_id)),
        "scorecard": (
            None
            if score is None
            else {
                **_scorecard(score).model_dump(mode="json", by_alias=True),
                "average": _scorecard(score).average,
            }
        ),
        "approvedAt": None if row.approved_at is None else row.approved_at.isoformat(),
    }


def _attempt_json(row: GenerationAttempt) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "businessObjectType": row.business_object_type,
        "businessObjectId": str(row.business_object_id),
        "idempotencyKey": row.idempotency_key,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "providerTaskId": row.provider_task_id,
        "request": row.request_json,
        "response": row.response_json,
        "error": row.error_json,
        "retryOfId": None if row.retry_of_id is None else str(row.retry_of_id),
    }


def _beat_json(row: ShotBeat) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "sceneId": str(row.scene_id),
        "storyRevisionId": (None if row.story_revision_id is None else str(row.story_revision_id)),
        "promptId": None if row.prompt_id is None else str(row.prompt_id),
        "order": row.sort_order,
        "revision": row.revision,
        "title": row.title,
        "action": row.action,
        "camera": row.camera,
        "dialogue": row.dialogue,
        "durationSeconds": row.duration_seconds,
        "status": row.status,
        "staleReason": row.stale_reason,
    }


def _storyboard_json(
    project_id: uuid.UUID,
    story_revision_id: uuid.UUID,
    beats: list[ShotBeat],
) -> dict[str, Any]:
    return {
        "projectId": str(project_id),
        "storyRevisionId": str(story_revision_id),
        "status": "ready",
        "targetDurationSeconds": sum(item.duration_seconds for item in beats),
        "beats": [_beat_json(item) for item in beats],
    }


def _prompt_json(prompt: PromptRecord, step: WorkflowStep) -> dict[str, Any]:
    return {
        "id": str(prompt.id),
        "stepId": str(step.id),
        "purpose": prompt.call_purpose or prompt.purpose,
        "nodeId": None if prompt.node_id is None else str(prompt.node_id),
        "businessObjectType": prompt.business_object_type,
        "businessObjectId": (
            None if prompt.business_object_id is None else str(prompt.business_object_id)
        ),
        "parentRunId": (None if prompt.parent_prompt_id is None else str(prompt.parent_prompt_id)),
        "templateName": prompt.template_name or "legacy_unavailable",
        "templateVersion": prompt.template_version or "legacy_unavailable",
        "systemPrompt": prompt.system_prompt,
        "userPrompt": prompt.user_prompt,
        "finalPrompt": prompt.final_prompt or prompt.prompt_text,
        "providerInternalTransform": prompt.provider_internal_transform,
        "providerRequestSnapshot": prompt.provider_request_json,
        "inputSnapshot": prompt.input_snapshot_json,
        "provider": step.provider,
        "model": prompt.model,
        "parameters": prompt.parameters_json,
        "rawResponse": prompt.raw_response_json,
        "structuredResponse": prompt.structured_response_json,
        "acceptedResponse": prompt.accepted_response_json,
        "responseDiff": prompt.response_diff_json,
        "tokenUsage": prompt.token_usage_json,
        "costMicros": prompt.cost_micros,
        "durationMs": prompt.duration_ms,
        "status": prompt.status,
        "error": prompt.error_json,
        "inputHash": prompt.input_hash,
        "outputHash": prompt.output_hash,
        "retryChain": step.retry_chain_json,
        "createdAt": prompt.created_at.isoformat(),
        "completedAt": (None if prompt.completed_at is None else prompt.completed_at.isoformat()),
    }


def _canvas_json(
    project_id: uuid.UUID,
    *,
    layout: CanvasLayout | None,
    brief: StoryBriefRecord | None,
    subjects: list[Subject],
    stories: list[StoryRevisionRecord],
    scenes: list[Scene],
    beats: list[ShotBeat],
    session: Session,
    enabled: bool,
) -> dict[str, Any]:
    planner_id = uuid.uuid5(project_id, "story-planner")
    approval_id = uuid.uuid5(project_id, "story-approval")
    storyboard_id = uuid.uuid5(project_id, "storyboard-director")
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    if brief is not None:
        nodes.append(
            {
                "id": str(brief.id),
                "type": "BriefNode",
                "objectType": "story_brief",
                "objectId": str(brief.id),
                "data": _brief_json(brief),
            }
        )
    nodes.extend(
        (
            {
                "id": str(planner_id),
                "type": "StoryPlannerNode",
                "objectType": "story_planner",
                "objectId": str(project_id),
                "data": {"title": "三案故事策划"},
            },
            {
                "id": str(approval_id),
                "type": "ApprovalGateNode",
                "objectType": "story_approval",
                "objectId": str(project_id),
                "data": {"title": "人工故事定稿"},
            },
            {
                "id": str(storyboard_id),
                "type": "StoryboardDirectorNode",
                "objectType": "storyboard_director",
                "objectId": str(project_id),
                "data": {"title": "分镜编译"},
            },
        )
    )
    if brief is not None:
        edges.append(_edge(brief.id, "BriefNode", "brief", planner_id, "StoryPlannerNode", "brief"))
    for subject in subjects:
        revision = (
            None
            if subject.current_revision_id is None
            else session.get(SubjectRevision, subject.current_revision_id)
        )
        if revision is None:
            continue
        references = tuple(
            session.scalars(
                select(SubjectReference).where(SubjectReference.subject_revision_id == revision.id)
            )
        )
        nodes.append(
            {
                "id": str(subject.id),
                "type": "SubjectNode",
                "objectType": "subject",
                "objectId": str(subject.id),
                "data": _subject_json(subject, revision, references),
            }
        )
        edges.append(
            _edge(
                subject.id,
                "SubjectNode",
                "subject[]",
                planner_id,
                "StoryPlannerNode",
                "subject[]",
            )
        )
    for story in stories:
        score = session.scalar(select(StoryScore).where(StoryScore.story_revision_id == story.id))
        nodes.append(
            {
                "id": str(story.id),
                "type": "StoryCandidateNode",
                "objectType": "story_revision",
                "objectId": str(story.id),
                "data": _story_json(story, score),
            }
        )
        edges.append(
            _edge(
                planner_id,
                "StoryPlannerNode",
                "story_revision",
                story.id,
                "StoryCandidateNode",
                "story_revision",
            )
        )
        edges.append(
            _edge(
                story.id,
                "StoryCandidateNode",
                "story_revision",
                approval_id,
                "ApprovalGateNode",
                "story_revision",
            )
        )
        if story.status == StoryRevisionStatus.APPROVED.value:
            edges.append(
                _edge(
                    approval_id,
                    "ApprovalGateNode",
                    "story_revision",
                    storyboard_id,
                    "StoryboardDirectorNode",
                    "story_revision",
                )
            )
    scene_by_id = {scene.id: scene for scene in scenes}
    for scene in scenes:
        nodes.append(
            {
                "id": str(scene.id),
                "type": "SceneNode",
                "objectType": "scene",
                "objectId": str(scene.id),
                "data": {"title": scene.title, "order": scene.sort_order},
            }
        )
        edges.append(
            _edge(
                storyboard_id,
                "StoryboardDirectorNode",
                "scene_plan",
                scene.id,
                "SceneNode",
                "scene_plan",
            )
        )
    for beat in beats:
        nodes.append(
            {
                "id": str(beat.id),
                "type": "ShotBeatNode",
                "objectType": "shot_beat",
                "objectId": str(beat.id),
                "data": _beat_json(beat),
            }
        )
        if beat.scene_id in scene_by_id:
            edges.append(
                _edge(
                    beat.scene_id,
                    "SceneNode",
                    "shot_beat[]",
                    beat.id,
                    "ShotBeatNode",
                    "shot_beat[]",
                )
            )
    positions = {str(item.get("nodeId")): item for item in (layout.nodes_json if layout else [])}
    stage_by_type = {
        "BriefNode": 0,
        "SubjectNode": 0,
        "StoryPlannerNode": 1,
        "StoryCandidateNode": 2,
        "ApprovalGateNode": 3,
        "StoryboardDirectorNode": 4,
        "SceneNode": 5,
        "ShotBeatNode": 6,
    }
    row_by_stage: dict[int, int] = {}
    for node in nodes:
        stored = positions.get(node["id"])
        stage = stage_by_type.get(node["type"], 7)
        row = row_by_stage.get(stage, 0)
        row_by_stage[stage] = row + 1
        node["position"] = (
            {"x": stored.get("x", 80 + stage * 320), "y": stored.get("y", 80 + row * 220)}
            if stored is not None
            else {"x": 80 + stage * 320, "y": 80 + row * 220}
        )
    return {
        "projectId": str(project_id),
        "canvasV2Enabled": enabled,
        "layoutVersion": 0 if layout is None else layout.version,
        "nodes": nodes,
        "edges": edges,
        "viewport": ({"x": 0, "y": 0, "zoom": 1} if layout is None else layout.viewport_json),
        "syncStatus": "saved" if layout is None else layout.sync_status,
    }


def _edge(
    source_id: uuid.UUID,
    source_type: str,
    source_port: str,
    target_id: uuid.UUID,
    target_type: str,
    target_port: str,
) -> dict[str, str]:
    return {
        "id": str(uuid.uuid5(source_id, f"{source_port}:{target_id}:{target_port}")),
        "sourceNodeId": str(source_id),
        "sourceNodeType": source_type,
        "sourcePort": source_port,
        "targetNodeId": str(target_id),
        "targetNodeType": target_type,
        "targetPort": target_port,
    }
