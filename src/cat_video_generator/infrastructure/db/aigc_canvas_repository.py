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

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import LandedAsset, StoredAsset
from ...domain.aigc_canvas import (
    CanvasConnection,
    CanvasNodeType,
    CanvasPortType,
    PromptRunDraft,
    StoryBrief,
    StoryCandidateOutput,
    StoryRevisionStatus,
    StoryScorecard,
    StoryStrategy,
    SubjectCompletionProposal,
    SubjectDraft,
    SubjectRole,
    approve_story_revision,
    subject_completion_missing_fields,
)
from ...domain.contracts import VisualProfileDraft
from ...domain.production_recipes import (
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    VisualPresetKey,
    build_temporal_beats,
)
from ...domain.rendering import MediaSource, VideoInputPlan, build_shot_input_plan
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
    CanvasGroup,
    CanvasGroupMember,
    CanvasLayout,
    CanvasNodeArchive,
    CharacterDesignAsset,
    CharacterDesignRevision,
    GenerationAttempt,
    HumanReviewDecisionRecord,
    MediaGenerationBatch,
    NodeGenerationConfig,
    ProductionRecipeInstance,
    ProductionRun,
    PromptRecord,
    ProviderCapability,
    Review,
    Scene,
    ShotBeat,
    ShotCard,
    ShotSubjectState,
    StoryBriefRecord,
    StoryRevisionRecord,
    StoryScore,
    Subject,
    SubjectCompletionRun,
    SubjectReference,
    SubjectRevision,
    VideoEditAnnotation,
    VideoEditRecipe,
    VideoEditReference,
    VideoSequence,
    VisualProfileRevision,
    WorkflowStep,
)
from .repositories import RecordNotFoundError, WorkflowConflictError
from .story_scenes import materialize_approved_story_scenes
from .story_scenes import normalized_story_scenes as _normalized_story_scenes
from .story_scenes import scene_look_plan_from_outline as _scene_look_plan_from_outline
from .visual_preset_profiles import (
    CANON_V3_REQUIRED_KEYS,
    ensure_canon_v3_subjects,
    ensure_canon_v3_visual_profile,
    episode_visual_profile_json,
    generation_reference_bindings,
    load_canon_assets,
    visual_preset_profile_json,
    visual_reference_json,
)


@dataclass(frozen=True, slots=True)
class StoredCanvasSubject:
    id: uuid.UUID
    revision_id: uuid.UUID
    revision: int
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
            return _subject_json(
                session, subject, revision, self._subject_references(session, revision.id)
            )

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
                    session,
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
            return _subject_json(
                session, subject, revision, self._subject_references(session, revision.id)
            )

    def create_subject_completion_run(
        self,
        project_id: uuid.UUID,
        payload: Any,
        *,
        provider: str,
        model: str,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            existing = session.scalar(
                select(SubjectCompletionRun).where(
                    SubjectCompletionRun.idempotency_key == payload.idempotency_key
                )
            )
            if existing is not None:
                if existing.production_run_id != project_id:
                    raise WorkflowConflictError("主体补全幂等键已被其他项目使用")
                return _subject_completion_json(existing)
            subject = self._required(session, Subject, payload.subject_id, lock=True)
            if subject.production_run_id != project_id:
                raise ValueError("主体不属于当前项目")
            if subject.current_revision_id is None:
                raise WorkflowConflictError("主体没有可分析的当前版本")
            revision = self._required(session, SubjectRevision, subject.current_revision_id)
            source = _subject_draft(
                subject,
                revision,
                self._subject_references(session, revision.id),
            )
            missing_fields = list(subject_completion_missing_fields(source))
            source_snapshot = source.model_dump(mode="json", by_alias=True)
            input_snapshot = {
                "projectId": str(project_id),
                "subjectId": str(subject.id),
                "sourceRevisionId": str(revision.id),
                "subject": source_snapshot,
                "missingFields": missing_fields,
                "instruction": payload.instruction,
            }
            input_hash = _json_hash(input_snapshot)
            step = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=project_id,
                kind=StepKind.DIRECTOR.value,
                status=StepStatus.PENDING.value,
                attempt=1,
                operation_key=f"subject:complete:{subject.id}:{revision.id}",
                idempotency_key=hashlib.sha256(payload.idempotency_key.encode()).hexdigest(),
                provider=provider,
                model=model,
                input_hash=input_hash,
                request_hash=input_hash,
                input_snapshot_json=input_snapshot,
            )
            session.add(step)
            system_prompt = (
                "你是AIGC媒体主体设定分析师。只补全有助于跨镜头一致性和戏剧功能的字段，"
                "不得更换主体身份、类型、角色或凭空删除用户锚点。输出必须可由用户逐项审核。"
            )
            user_prompt = (
                f"待补全字段：{json.dumps(missing_fields, ensure_ascii=False)}\n"
                f"用户要求：{payload.instruction or '无额外要求'}\n"
                f"主体快照：{json.dumps(source_snapshot, ensure_ascii=False, sort_keys=True)}"
            )
            final_prompt = f"{system_prompt}\n\n{user_prompt}"
            prompt = PromptRecord(
                id=uuid.uuid4(),
                step_id=step.id,
                purpose=PromptPurpose.DIRECTOR.value,
                model=model,
                prompt_text=final_prompt,
                sha256=hashlib.sha256(final_prompt.encode()).hexdigest(),
                call_purpose="subject_completion",
                node_id=subject.id,
                business_object_type="subject_completion_run",
                business_object_id=subject.id,
                template_name="subject.completion.v1",
                template_version="1.0.0",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                final_prompt=final_prompt,
                provider_request_json={
                    "outputName": "SubjectCompletionProposal",
                    "input": input_snapshot,
                },
                input_snapshot_json=input_snapshot,
                parameters_json={},
                status="pending",
                input_hash=input_hash,
            )
            session.add(prompt)
            run = SubjectCompletionRun(
                id=uuid.uuid4(),
                production_run_id=project_id,
                subject_id=subject.id,
                source_revision_id=revision.id,
                workflow_step_id=step.id,
                prompt_id=prompt.id,
                idempotency_key=payload.idempotency_key,
                status="pending",
                model=model,
                missing_fields_json=missing_fields,
            )
            session.add(run)
            graph_node = session.get(CanvasGraphNode, subject.id)
            if graph_node is not None:
                graph_node.data_json = {
                    **graph_node.data_json,
                    "subjectCompletionRunId": str(run.id),
                    "completionStatus": "pending",
                }
            self._record_event(
                session,
                project_id,
                "subject_completion_queued",
                {"runId": str(run.id), "subjectId": str(subject.id), "stepId": str(step.id)},
            )
            return _subject_completion_json(run)

    def subject_completion_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("subject:complete:"):
                raise ValueError("workflow step is not a subject completion")
            run = session.scalar(
                select(SubjectCompletionRun).where(SubjectCompletionRun.workflow_step_id == step.id)
            )
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if run is None or prompt is None or prompt.status != "pending":
                raise WorkflowConflictError("主体补全缺少待执行的精确 Prompt")
            return {"runId": str(run.id), "prompt": prompt.final_prompt or prompt.prompt_text}

    def complete_subject_completion(
        self,
        step_id: uuid.UUID,
        *,
        proposal: dict[str, object],
        raw_response: dict[str, object],
    ) -> str:
        validated = SubjectCompletionProposal.model_validate(proposal)
        proposal_json = validated.model_dump(mode="json", by_alias=True)
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            run = session.scalar(
                select(SubjectCompletionRun)
                .where(SubjectCompletionRun.workflow_step_id == step.id)
                .with_for_update()
            )
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if run is None or prompt is None:
                raise RecordNotFoundError(f"subject completion for step {step_id} was not found")
            if run.status == "awaiting_review":
                return str(run.id)
            if run.status != "pending":
                raise WorkflowConflictError("主体补全运行已不再等待供应商结果")
            run.status = "awaiting_review"
            run.proposal_json = proposal_json
            run.completed_at = now
            prompt.status = "succeeded"
            prompt.raw_response_json = raw_response
            prompt.structured_response_json = proposal_json
            prompt.output_hash = _json_hash(proposal_json)
            prompt.completed_at = now
            graph_node = session.get(CanvasGraphNode, run.subject_id)
            if graph_node is not None:
                graph_node.data_json = {
                    **graph_node.data_json,
                    "subjectCompletionRunId": str(run.id),
                    "completionStatus": "awaiting_review",
                    "completionMissingFields": run.missing_fields_json,
                }
            self._record_event(
                session,
                run.production_run_id,
                "subject_completion_ready",
                {"runId": str(run.id), "subjectId": str(run.subject_id)},
            )
            return str(run.id)

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            run = self._required(session, SubjectCompletionRun, run_id)
            return _subject_completion_json(run)

    def apply_subject_completion(self, run_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions.begin() as session:
            run = self._required(session, SubjectCompletionRun, run_id, lock=True)
            subject = self._required(session, Subject, run.subject_id, lock=True)
            if run.status == "applied":
                current = self._required(session, SubjectRevision, subject.current_revision_id)
                return {
                    "runId": str(run.id),
                    "status": run.status,
                    "acceptedFields": run.accepted_fields_json or [],
                    **_subject_json(
                        session,
                        subject,
                        current,
                        self._subject_references(session, current.id),
                    ),
                }
            if run.status != "awaiting_review" or run.proposal_json is None:
                raise WorkflowConflictError("主体补全建议尚未就绪，不能应用")
            if subject.current_revision_id != run.source_revision_id:
                raise WorkflowConflictError("主体版本已变化，请基于新版本重新运行补全")
            source_revision = self._required(session, SubjectRevision, run.source_revision_id)
            source = _subject_draft(
                subject,
                source_revision,
                self._subject_references(session, source_revision.id),
            )
            final_draft = payload.final_draft
            accepted = set(payload.accepted_fields)
            fixed_fields = ("name", "kind", "role", "references")
            if any(getattr(final_draft, field) != getattr(source, field) for field in fixed_fields):
                raise ValueError("主体补全不能修改名称、类型、角色或参考素材绑定")
            aliases = {
                "identityAnchors": "identity_anchors",
                "immutableTraits": "immutable_traits",
                "relationshipNotes": "relationship_notes",
                "dramaticFunction": "dramatic_function",
                "visualRisks": "visual_risks",
            }
            for alias, attribute in aliases.items():
                unchanged = getattr(final_draft, attribute) == getattr(source, attribute)
                if alias not in accepted and not unchanged:
                    raise ValueError(f"字段 {alias} 未被接受，不能修改")
            revision = self._add_subject_revision(session, subject, final_draft)
            subject.current_revision_id = revision.id
            run.status = "applied"
            run.accepted_fields_json = list(payload.accepted_fields)
            run.accepted_draft_json = final_draft.model_dump(mode="json", by_alias=True)
            prompt = (
                None
                if run.prompt_id is None
                else self._required(session, PromptRecord, run.prompt_id, lock=True)
            )
            if prompt is not None:
                prompt.accepted_response_json = run.accepted_draft_json
                prompt.response_diff_json = {
                    "acceptedFields": run.accepted_fields_json,
                    "sourceRevisionId": str(run.source_revision_id),
                    "createdRevisionId": str(revision.id),
                }
            graph_node = session.get(CanvasGraphNode, subject.id)
            if graph_node is not None:
                graph_node.revision += 1
                graph_node.status = "stale"
                graph_node.data_json = {
                    **graph_node.data_json,
                    "title": final_draft.name,
                    "completionStatus": "applied",
                    "subjectRevisionId": str(revision.id),
                }
            self._mark_subject_downstream_stale(session, subject.id)
            self._record_event(
                session,
                run.production_run_id,
                "subject_completion_applied",
                {
                    "runId": str(run.id),
                    "subjectId": str(subject.id),
                    "revisionId": str(revision.id),
                },
            )
            return {
                "runId": str(run.id),
                "status": run.status,
                "acceptedFields": run.accepted_fields_json,
                **_subject_json(
                    session,
                    subject,
                    revision,
                    self._subject_references(session, revision.id),
                ),
            }

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            query = select(Asset).where(Asset.production_run_id == project_id)
            if media_kind is not None:
                query = query.where(Asset.media_type == media_kind)
            rows = session.scalars(query.order_by(Asset.created_at.desc(), Asset.id).limit(200))
            return [
                {
                    "id": str(row.id),
                    "projectId": str(project_id),
                    "canvasNodeId": None if row.canvas_node_id is None else str(row.canvas_node_id),
                    "mediaType": row.media_type,
                    "role": row.role,
                    "status": row.status,
                    "semanticKey": row.semantic_key,
                    "sha256": row.sha256,
                    "metadata": row.metadata_json,
                    "contentUrl": f"/api/v1/assets/{row.id}/content",
                    "createdAt": None if row.created_at is None else row.created_at.isoformat(),
                }
                for row in rows
            ]

    def list_visual_presets(self) -> list[dict[str, Any]]:
        """Return reusable visual evidence packages without copying their assets."""

        with self._sessions() as session:
            return [visual_preset_profile_json(session)]

    def apply_visual_preset(
        self,
        project_id: uuid.UUID,
        preset_key: str,
    ) -> dict[str, Any]:
        try:
            resolved_key = VisualPresetKey(preset_key)
        except ValueError as exc:
            raise ValueError(f"不支持的视觉预设：{preset_key}") from exc
        if resolved_key is not VisualPresetKey.HEALING_CHILD_CAT_LINE_TEXTURE:
            raise ValueError(f"不支持的视觉预设：{preset_key}")

        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            assets_by_key = load_canon_assets(session, CANON_V3_REQUIRED_KEYS)
            profile = ensure_canon_v3_visual_profile(
                session,
                project_id=project_id,
                assets_by_key=assets_by_key,
            )
            subjects_by_role = ensure_canon_v3_subjects(
                session,
                project_id=project_id,
                assets_by_key=assets_by_key,
            )
            project.canvas_v2_enabled = True
            project.universal_canvas_enabled = True

            node_id = uuid.uuid5(project_id, "style-preset:line-texture")
            node = session.get(CanvasGraphNode, node_id)
            style_asset = assets_by_key["style:line_texture"]
            node_data = {
                "title": "线条材质画风",
                "presetKey": resolved_key.value,
                "canonProfileId": CANON_V3_PROFILE_ID,
                "references": [visual_reference_json(style_asset, required=True)],
                "stylePositive": list(CANON_V3_STYLE_POSITIVE),
                "styleExcluded": list(CANON_V3_STYLE_NEGATIVE),
                "locked": True,
            }
            if node is None:
                node = CanvasGraphNode(
                    id=node_id,
                    production_run_id=project_id,
                    node_type=CanvasNodeType.STYLE_PRESET.value,
                    object_type="visual_preset",
                    object_id=style_asset.id,
                    status="approved",
                    data_json=node_data,
                )
                session.add(node)
                session.flush()
            else:
                node.status = "approved"
                if node.data_json != node_data:
                    node.revision += 1
                    node.data_json = node_data

            applied_nodes = [node]
            for subject in subjects_by_role.values():
                revision = self._required(
                    session,
                    SubjectRevision,
                    subject.current_revision_id,
                )
                references = self._subject_references(session, revision.id)
                subject_data = {
                    **_subject_json(session, subject, revision, references),
                    "title": revision.name,
                    "canonProfileId": CANON_V3_PROFILE_ID,
                    "locked": True,
                }
                subject_node = session.get(CanvasGraphNode, subject.id)
                if subject_node is None:
                    subject_node = CanvasGraphNode(
                        id=subject.id,
                        production_run_id=project_id,
                        node_type=CanvasNodeType.SUBJECT.value,
                        object_type="subject",
                        object_id=subject.id,
                        status="ready",
                        data_json=subject_data,
                    )
                    session.add(subject_node)
                    session.flush()
                else:
                    subject_node.status = "ready"
                    if subject_node.data_json != subject_data:
                        subject_node.revision += 1
                        subject_node.data_json = subject_data
                applied_nodes.append(subject_node)

            group = session.scalar(
                select(CanvasGroup)
                .where(
                    CanvasGroup.production_run_id == project_id,
                    CanvasGroup.lifecycle_status == "active",
                    CanvasGroup.group_type == "recipe",
                )
                .order_by(CanvasGroup.created_at.desc())
                .limit(1)
            )
            if group is not None:
                max_order = int(
                    session.scalar(
                        select(func.coalesce(func.max(CanvasGroupMember.sort_order), 0)).where(
                            CanvasGroupMember.group_id == group.id
                        )
                    )
                    or 0
                )
                for applied_node in applied_nodes:
                    member = session.scalar(
                        select(CanvasGroupMember).where(
                            CanvasGroupMember.group_id == group.id,
                            CanvasGroupMember.canvas_node_id == applied_node.id,
                        )
                    )
                    if member is None:
                        max_order += 1
                        session.add(
                            CanvasGroupMember(
                                id=uuid.uuid5(group.id, f"member:{applied_node.id}"),
                                group_id=group.id,
                                canvas_node_id=applied_node.id,
                                sort_order=max_order,
                            )
                        )

            style_target_types = {
                CanvasNodeType.CHARACTER_DESIGN.value,
                CanvasNodeType.STORYBOARD_DIRECTOR.value,
                CanvasNodeType.IMAGE_GENERATION.value,
            }
            targets = list(
                session.scalars(
                    select(CanvasGraphNode).where(
                        CanvasGraphNode.production_run_id == project_id,
                        CanvasGraphNode.node_type.in_(
                            style_target_types
                            | {
                                CanvasNodeType.STORY_PLANNER.value,
                            }
                        ),
                    )
                )
            )
            for target in targets:
                if target.node_type in style_target_types:
                    existing_edge = session.scalar(
                        select(CanvasGraphEdge.id).where(
                            CanvasGraphEdge.source_node_id == node.id,
                            CanvasGraphEdge.source_port == CanvasPortType.IMAGE_REFERENCES.value,
                            CanvasGraphEdge.target_node_id == target.id,
                            CanvasGraphEdge.target_port == CanvasPortType.IMAGE_REFERENCES.value,
                        )
                    )
                    if existing_edge is None:
                        session.add(
                            CanvasGraphEdge(
                                id=uuid.uuid5(node.id, f"preset-edge:{target.id}"),
                                production_run_id=project_id,
                                source_node_id=node.id,
                                source_port=CanvasPortType.IMAGE_REFERENCES.value,
                                target_node_id=target.id,
                                target_port=CanvasPortType.IMAGE_REFERENCES.value,
                                relation_type="visual_preset_reference",
                                revision=1,
                            )
                        )
                for role, subject in subjects_by_role.items():
                    if not _preset_subject_targets_node(role, target):
                        continue
                    existing_edge = session.scalar(
                        select(CanvasGraphEdge.id).where(
                            CanvasGraphEdge.source_node_id == subject.id,
                            CanvasGraphEdge.source_port == CanvasPortType.SUBJECTS.value,
                            CanvasGraphEdge.target_node_id == target.id,
                            CanvasGraphEdge.target_port == CanvasPortType.SUBJECTS.value,
                        )
                    )
                    if existing_edge is None:
                        session.add(
                            CanvasGraphEdge(
                                id=uuid.uuid5(subject.id, f"preset-subject-edge:{target.id}"),
                                production_run_id=project_id,
                                source_node_id=subject.id,
                                source_port=CanvasPortType.SUBJECTS.value,
                                target_node_id=target.id,
                                target_port=CanvasPortType.SUBJECTS.value,
                                relation_type="canon_identity_reference",
                                revision=1,
                            )
                        )

            self._record_event(
                session,
                project_id,
                "canvas_projection_changed",
                {
                    "reason": "visual_preset_applied",
                    "presetKey": resolved_key.value,
                    "canvasNodeId": str(node.id),
                    "canvasNodeIds": [str(item.id) for item in applied_nodes],
                    "visualProfileRevisionId": str(profile.id),
                },
            )
            return {
                "preset": visual_preset_profile_json(session),
                "visualProfile": episode_visual_profile_json(profile),
                "canvasNodeId": str(node.id),
                "canvasNodeIds": [str(item.id) for item in applied_nodes],
                "reusedAssetIds": [str(assets_by_key[key].id) for key in CANON_V3_REQUIRED_KEYS],
            }

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            project = self._require_project(session, project_id)
            if project.current_visual_profile_revision_id is None:
                raise WorkflowConflictError("当前项目尚未应用视觉预设")
            profile = self._required(
                session,
                VisualProfileRevision,
                project.current_visual_profile_revision_id,
            )
            return episode_visual_profile_json(profile)

    def update_episode_visual_profile(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: VisualProfileDraft,
    ) -> dict[str, Any]:
        document = payload.model_dump(mode="json", by_alias=True)
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            if project.current_visual_profile_revision_id is None:
                raise WorkflowConflictError("当前项目尚未应用视觉预设")
            current = self._required(
                session,
                VisualProfileRevision,
                project.current_visual_profile_revision_id,
                lock=True,
            )
            if current.revision != expected_revision:
                raise WorkflowConflictError(
                    f"本集视觉档案版本冲突：当前 {current.revision}，提交 {expected_revision}"
                )

            current_bindings = {
                (str(item.get("assetId")), str(item.get("purpose")))
                for item in current.reference_bindings_json
            }
            submitted_bindings = {
                (str(item.asset_id), item.purpose.value) for item in payload.reference_bindings
            }
            if submitted_bindings != current_bindings:
                raise WorkflowConflictError("Canon-v3 必需身份与画风槽位不允许删除、换绑或改变职责")

            profile_document = {
                **document,
                "sourceProfileId": current.source_profile_id,
                "referenceSnapshot": current.reference_snapshot_json,
            }
            profile_hash = hashlib.sha256(
                json.dumps(
                    profile_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            existing = session.scalar(
                select(VisualProfileRevision).where(
                    VisualProfileRevision.production_run_id == project_id,
                    VisualProfileRevision.profile_hash == profile_hash,
                )
            )
            if existing is not None:
                project.current_visual_profile_revision_id = existing.id
                return episode_visual_profile_json(existing)

            revision = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(VisualProfileRevision.revision), 0)).where(
                            VisualProfileRevision.production_run_id == project_id
                        )
                    )
                    or 0
                )
                + 1
            )
            profile = VisualProfileRevision(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=revision,
                profile_hash=profile_hash,
                source_profile_id=current.source_profile_id,
                person_identity=payload.person_identity,
                person_hair=payload.person_hair,
                person_body=payload.person_body,
                cat_identity=payload.cat_identity,
                style_positive_json=list(payload.style_positive),
                style_negative_json=list(payload.style_negative),
                reference_bindings_json=document["referenceBindings"],
                reference_snapshot_json=current.reference_snapshot_json,
            )
            session.add(profile)
            session.flush()
            project.current_visual_profile_revision_id = profile.id

            assets_by_key = load_canon_assets(session, CANON_V3_REQUIRED_KEYS)
            project.default_reference_bindings_json = generation_reference_bindings(
                assets_by_key,
                CANON_V3_REQUIRED_KEYS,
            )
            session.execute(
                Asset.__table__.update()
                .where(
                    Asset.production_run_id == project_id,
                    Asset.scope != "canon",
                    Asset.status.in_(("ready", "approved")),
                    or_(
                        Asset.role.like("character_design_%"),
                        Asset.role.in_(
                            (
                                "scene_look",
                                "generated_reference",
                                "shot_anchor",
                                "shot_tail_frame",
                                "video_candidate",
                                "shot_video",
                                "shot_video_edit",
                                "project_sequence",
                            )
                        ),
                    ),
                )
                .values(status="stale")
            )
            sources = list(
                session.scalars(
                    select(CanvasGraphNode).where(
                        CanvasGraphNode.production_run_id == project_id,
                        CanvasGraphNode.node_type.in_(
                            (CanvasNodeType.SUBJECT.value, CanvasNodeType.STYLE_PRESET.value)
                        ),
                    )
                )
            )
            for source in sources:
                self._mark_graph_downstream_stale(session, source.id)
            self._record_event(
                session,
                project_id,
                "canvas_projection_changed",
                {
                    "reason": "visual_profile_changed",
                    "visualProfileRevisionId": str(profile.id),
                    "revision": profile.revision,
                },
            )
            return episode_visual_profile_json(profile)

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            asset = self._required(session, Asset, asset_id, lock=True)
            timestamps, filmstrip_key, idempotency_key = _filmstrip_identity(asset, frame_count)
            cached = _filmstrip_frames(session, asset, filmstrip_key)
            existing = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == idempotency_key)
            )
            if len(cached) == frame_count:
                return _filmstrip_json(asset, frame_count, "ready", cached, existing)
            if existing is not None:
                return _filmstrip_json(asset, frame_count, existing.status, cached, existing)
            if asset.media_type != "video":
                raise ValueError("只有视频资产可以生成真实帧带")
            if asset.production_run_id is None:
                raise ValueError("视频资产缺少项目归属，无法创建持久抽帧任务")
            input_snapshot = {
                "assetId": str(asset.id),
                "sourceSha256": asset.sha256,
                "frameCount": frame_count,
                "timestampsMs": list(timestamps),
                "filmstripKey": filmstrip_key,
            }
            step = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=asset.production_run_id,
                kind=StepKind.IMAGE.value,
                status=StepStatus.PENDING.value,
                attempt=1,
                operation_key=f"media:filmstrip:{asset.id}:{frame_count}",
                idempotency_key=idempotency_key,
                provider="local_ffmpeg",
                model="filmstrip-v1",
                input_hash=_json_hash(input_snapshot),
                request_hash=_json_hash(input_snapshot),
                input_snapshot_json=input_snapshot,
            )
            session.add(step)
            self._record_event(
                session,
                asset.production_run_id,
                "video_filmstrip_queued",
                {
                    "assetId": str(asset.id),
                    "stepId": str(step.id),
                    "frameCount": frame_count,
                    "timestampsMs": list(timestamps),
                },
            )
            return _filmstrip_json(asset, frame_count, step.status, cached, step)

    def get_video_filmstrip(self, asset_id: uuid.UUID, *, frame_count: int) -> dict[str, Any]:
        with self._sessions() as session:
            asset = self._required(session, Asset, asset_id)
            _timestamps, filmstrip_key, idempotency_key = _filmstrip_identity(asset, frame_count)
            step = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == idempotency_key)
            )
            frames = _filmstrip_frames(session, asset, filmstrip_key)
            status = (
                "ready"
                if len(frames) == frame_count
                else ("not_requested" if step is None else step.status)
            )
            return _filmstrip_json(asset, frame_count, status, frames, step)

    def filmstrip_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            snapshot = step.input_snapshot_json
            if not step.operation_key.startswith("media:filmstrip:"):
                raise ValueError("workflow step is not a filmstrip extraction")
            asset = self._required(session, Asset, uuid.UUID(str(snapshot["assetId"])))
            return {
                "source": _stored_asset(asset, self._asset_root),
                "timestampsMs": tuple(int(value) for value in snapshot["timestampsMs"]),
                "filmstripKey": str(snapshot["filmstripKey"]),
            }

    def complete_filmstrip(
        self,
        step_id: uuid.UUID,
        *,
        frames: tuple[LandedAsset, ...],
        timestamps_ms: tuple[int, ...],
    ) -> tuple[str, ...]:
        if len(frames) != len(timestamps_ms):
            raise ValueError("抽帧文件与时间点数量不一致")
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            snapshot = step.input_snapshot_json
            source = self._required(session, Asset, uuid.UUID(str(snapshot["assetId"])))
            filmstrip_key = str(snapshot["filmstripKey"])
            existing = {
                int(row.metadata_json["timestampMs"]): row
                for row in _filmstrip_frames(session, source, filmstrip_key)
            }
            asset_ids: list[str] = []
            for timestamp_ms, landed in zip(timestamps_ms, frames, strict=True):
                row = existing.get(timestamp_ms)
                if row is None:
                    row = Asset(
                        id=uuid.uuid4(),
                        production_run_id=source.production_run_id,
                        producing_step_id=step.id,
                        canvas_node_id=source.canvas_node_id,
                        role="filmstrip_frame",
                        semantic_key=f"filmstrip:{source.id}:{timestamp_ms}",
                        scope="project",
                        status="ready",
                        media_type="image",
                        storage_key=_asset_storage_key(landed.path, self._asset_root),
                        sha256=landed.sha256,
                        byte_size=landed.byte_size,
                        metadata_json={
                            "sourceAssetId": str(source.id),
                            "sourceSha256": source.sha256,
                            "filmstripKey": filmstrip_key,
                            "timestampMs": timestamp_ms,
                            "frameCount": len(timestamps_ms),
                        },
                    )
                    session.add(row)
                asset_ids.append(str(row.id))
            if source.production_run_id is not None:
                self._record_event(
                    session,
                    source.production_run_id,
                    "video_filmstrip_ready",
                    {
                        "assetId": str(source.id),
                        "stepId": str(step.id),
                        "frameAssetIds": asset_ids,
                    },
                )
            return tuple(asset_ids)

    def save_node_generation_config(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        document = payload.model_dump(mode="json", by_alias=True)
        input_hash = _json_hash(document)
        with self._sessions.begin() as session:
            node = self._required(session, CanvasGraphNode, node_id, lock=True)
            if node.revision != expected_revision:
                raise WorkflowConflictError(
                    f"生成节点版本冲突：当前 {node.revision}，提交 {expected_revision}"
                )
            revision = node.revision + 1
            row = NodeGenerationConfig(
                id=uuid.uuid4(),
                canvas_node_id=node.id,
                revision=revision,
                provider=payload.provider,
                model=payload.model,
                mode=payload.mode,
                config_json=document,
                actual_reference_bindings_json=[
                    item.model_dump(mode="json", by_alias=True)
                    for item in payload.actual_references
                ],
                input_hash=input_hash,
            )
            session.add(row)
            node.revision = revision
            node.data_json = {
                **node.data_json,
                "generationConfigId": str(row.id),
                "generationConfig": document,
                "actualReferences": row.actual_reference_bindings_json,
            }
            event = self._record_event(
                session,
                node.production_run_id,
                "node_generation_config_saved",
                {
                    "nodeId": str(node.id),
                    "configId": str(row.id),
                    "revision": revision,
                    "inputHash": input_hash,
                },
            )
            return {
                "id": str(row.id),
                "canvasNodeId": str(node.id),
                "revision": revision,
                "inputHash": input_hash,
                "confirmedEventId": str(event.id),
                **document,
            }

    def list_provider_capabilities(self, *, media_kind: str | None = None) -> list[dict[str, Any]]:
        with self._sessions() as session:
            query = select(ProviderCapability).where(ProviderCapability.active.is_(True))
            if media_kind is not None:
                query = query.where(ProviderCapability.media_kind == media_kind)
            rows = session.scalars(
                query.order_by(
                    ProviderCapability.provider,
                    ProviderCapability.model,
                )
            )
            documents: list[dict[str, Any]] = []
            for row in rows:
                capabilities = dict(row.capabilities_json)
                if row.media_kind == "video":
                    capabilities.setdefault(
                        "cameraMotions",
                        [dict(preset) for preset in _CAMERA_MOTION_PRESETS],
                    )
                    capabilities.setdefault(
                        "mediaActions",
                        [dict(action) for action in _VIDEO_ASSET_ACTIONS],
                    )
                documents.append(
                    {
                        "id": str(row.id),
                        "provider": row.provider,
                        "model": row.model,
                        "mediaKind": row.media_kind,
                        "capabilities": capabilities,
                        "active": row.active,
                        "updatedAt": (
                            None if row.updated_at is None else row.updated_at.isoformat()
                        ),
                    }
                )
            return documents

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
                        revision=revision.revision,
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
            materialize_approved_story_scenes(session, row)
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
                        session,
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
            outlines = _normalized_story_scenes(story)
            scenes = list(
                session.scalars(
                    select(Scene)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.story_revision_id == story.id,
                        Scene.active.is_(True),
                    )
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
                    story_revision_id=story.id,
                    scene_key=str(outlines[len(scenes)]["sceneKey"]),
                    active=True,
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
                scene.context_note = json.dumps(
                    {
                        "sceneKey": outline["sceneKey"],
                        "purpose": outline["purpose"],
                        "continuity": outline["continuity"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                next_look_plan = _scene_look_plan_from_outline(outline)
                if scene.look_plan_json != next_look_plan:
                    scene.look_plan_json = next_look_plan
                    scene.look_draft_json = {}
                    scene.look_draft_revision += 1
                    scene.selected_look_asset_id = None
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
                shot_count = beats_by_scene[index]
                if shot_count == 0:
                    raise ValueError(f"场景“{scene.title}”尚未被任何镜头覆盖")
                scene.target_shot_count = shot_count
                scene.story_mode = "single" if shot_count == 1 else "multi"
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
            duration_seconds = patch.get("duration_seconds", current.duration_seconds)
            temporal_beats = current.temporal_beats_json
            if len(temporal_beats) == 3 and 8 <= duration_seconds <= 15:
                temporal_beats = [
                    item.model_dump(mode="json", by_alias=True)
                    for item in build_temporal_beats(
                        duration_seconds,
                        actions=tuple(
                            (
                                str(item["childAction"]),
                                str(item["catAction"]),
                                str(item["camera"]),
                            )
                            for item in temporal_beats
                        ),
                    )
                ]
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
                duration_seconds=duration_seconds,
                temporal_beats_json=temporal_beats,
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
            if current.shot_card_id is not None:
                shot = self._required(session, ShotCard, current.shot_card_id, lock=True)
                shot.selected_anchor_asset_id = None
                shot.selected_video_asset_id = None
                shot.status = "ready"
                session.execute(
                    Asset.__table__.update()
                    .where(
                        Asset.shot_card_id == current.shot_card_id,
                        Asset.status != "stale",
                    )
                    .values(status="stale")
                )
                scene = self._required(session, Scene, current.scene_id)
                session.execute(
                    VideoSequence.__table__.update()
                    .where(VideoSequence.production_run_id == scene.production_run_id)
                    .values(status="rejected")
                )
                project = self._required(session, ProductionRun, scene.production_run_id)
                project.selected_sequence_id = None
            return _beat_json(row)

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == project_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            if story is None:
                raise WorkflowConflictError("故事尚未人工批准，不能保存分镜草稿")
            brief = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            active_beats = list(
                session.scalars(
                    select(ShotBeat)
                    .where(
                        ShotBeat.story_revision_id == story.id,
                        ShotBeat.status != "superseded",
                    )
                    .order_by(ShotBeat.sort_order)
                    .with_for_update()
                )
            )
            current_revision = max((beat.revision for beat in active_beats), default=0)
            if current_revision != expected_revision:
                raise WorkflowConflictError("人工分镜草稿已被更新，请比较最新版本后重试")
            if (
                payload.healing_recipe
                and brief is not None
                and sum(shot.duration_seconds for shot in payload.shots)
                != brief.target_duration_seconds
            ):
                raise ValueError("治愈组合包镜头总时长必须与项目目标时长完全一致")

            scenes = list(
                session.scalars(
                    select(Scene)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.story_revision_id == story.id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order)
                    .with_for_update()
                )
            )
            if not scenes:
                outlines = _normalized_story_scenes(story) or [
                    {"title": "人工分镜场景", "synopsis": "由人工镜头表建立"}
                ]
                for index, outline in enumerate(outlines, 1):
                    scene = Scene(
                        id=uuid.uuid4(),
                        production_run_id=project_id,
                        story_revision_id=story.id,
                        scene_key=str(outline.get("sceneKey") or f"scene-{index:02d}"),
                        active=True,
                        sort_order=index,
                        title=str(outline.get("title") or f"场景 {index}"),
                        source_text=str(outline.get("synopsis") or "人工分镜"),
                        context_note=(
                            None
                            if "continuity" not in outline
                            else json.dumps(
                                {
                                    "sceneKey": outline.get("sceneKey") or f"scene-{index:02d}",
                                    "purpose": outline.get("purpose") or "人工分镜",
                                    "continuity": outline["continuity"],
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                        look_plan_json=_scene_look_plan_from_outline(outline),
                        story_mode="single",
                        target_shot_count=1,
                        status=SceneStatus.READY.value,
                    )
                    session.add(scene)
                    scenes.append(scene)
                session.flush()

            active_by_id = {beat.id: beat for beat in active_beats}
            scenes_by_id = {scene.id: scene for scene in scenes}
            for beat in active_beats:
                beat.status = "superseded"
            per_scene_count = {scene.id: 0 for scene in scenes}
            next_revision = current_revision + 1
            saved: list[ShotBeat] = []
            for index, draft in enumerate(payload.shots):
                source = active_by_id.get(draft.id)
                if draft.scene_id is not None:
                    scene = scenes_by_id.get(draft.scene_id)
                    if scene is None:
                        raise ValueError("镜头所属场景不属于当前项目")
                elif source is not None:
                    scene = scenes_by_id.get(source.scene_id, scenes[0])
                else:
                    scene = scenes[min(index, len(scenes) - 1)]
                per_scene_count[scene.id] += 1
                if (draft.prompt_id is None) != (draft.prompt_input_hash is None):
                    raise ValueError("Prompt ID 与输入哈希必须同时提交")
                validated_prompt_id: uuid.UUID | None = None
                if draft.prompt_id is not None:
                    prompt = self._required(
                        session,
                        PromptRecord,
                        draft.prompt_id,
                        lock=True,
                    )
                    step = self._required(session, WorkflowStep, prompt.step_id)
                    snapshot = prompt.input_snapshot_json or {}
                    compiled_shot = snapshot.get("shot")
                    if (
                        step.production_run_id != project_id
                        or prompt.status != "succeeded"
                        or prompt.call_purpose != "storyboard_prompt_compilation"
                        or prompt.input_hash != draft.prompt_input_hash
                        or snapshot.get("storyRevisionId") != str(story.id)
                        or snapshot.get("visualProfileRevisionId")
                        != str(project.current_visual_profile_revision_id)
                        or snapshot.get("sceneId") != str(scene.id)
                        or not isinstance(compiled_shot, dict)
                        or int(compiled_shot.get("order", 0)) != draft.order
                        or int(compiled_shot.get("durationSeconds", 0))
                        != draft.duration_seconds
                        or str(compiled_shot.get("title") or "").strip()
                        != draft.title.strip()
                        or str(compiled_shot.get("action") or "").strip()
                        != draft.action.strip()
                        or str(compiled_shot.get("camera") or "").strip()
                        != draft.camera.strip()
                        or str(compiled_shot.get("dialogue") or "").strip()
                        != draft.dialogue.strip()
                    ):
                        raise WorkflowConflictError(
                            f"镜头 {draft.order} 的已编译 Prompt 与当前镜头或上游版本不一致"
                        )
                    validated_prompt_id = prompt.id
                temporal_beats = []
                if payload.healing_recipe:
                    phase_actions = (
                        (
                            draft.action,
                            "猫咪以固定行为模式自然参与",
                            draft.camera or "固定机位",
                        ),
                        (
                            "发生一个微小可见变化",
                            "猫咪对变化做出自然反应",
                            draft.camera or "固定机位",
                        ),
                        (
                            "儿童与猫咪在温暖状态中收尾",
                            "猫咪保持猫科身体结构",
                            draft.camera or "固定机位",
                        ),
                    )
                    temporal_beats = [
                        item.model_dump(mode="json", by_alias=True)
                        for item in build_temporal_beats(
                            draft.duration_seconds,
                            actions=phase_actions,
                        )
                    ]
                row = ShotBeat(
                    id=uuid.uuid4(),
                    scene_id=scene.id,
                    shot_card_id=None if source is None else source.shot_card_id,
                    story_revision_id=story.id,
                    prompt_id=validated_prompt_id,
                    sort_order=per_scene_count[scene.id],
                    revision=next_revision,
                    title=draft.title,
                    action=draft.action,
                    camera=draft.camera,
                    dialogue=draft.dialogue,
                    duration_seconds=draft.duration_seconds,
                    temporal_beats_json=temporal_beats,
                    status="ready",
                )
                session.add(row)
                saved.append(row)
            for scene in scenes:
                shot_count = per_scene_count[scene.id]
                if shot_count == 0:
                    raise ValueError(f"场景“{scene.title}”尚未被任何镜头覆盖")
                scene.target_shot_count = shot_count
                scene.story_mode = "single" if shot_count == 1 else "multi"
            session.execute(
                Asset.__table__.update()
                .where(
                    Asset.production_run_id == project_id,
                    Asset.status != "stale",
                )
                .values(status="stale")
            )
            session.execute(
                VideoSequence.__table__.update()
                .where(VideoSequence.production_run_id == project_id)
                .values(status="rejected")
            )
            project.selected_sequence_id = None
            session.flush()
            result = _storyboard_json(project_id, story.id, saved)
            result["revision"] = next_revision
            result["status"] = (
                "awaiting_review"
                if all(row.prompt_id is not None for row in saved)
                else "draft"
            )
            if result["status"] == "draft":
                result["blockers"] = ["仍有镜头未完成服务端分层 Prompt 编译"]
            return result

    def compile_storyboard_prompts(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        """Compile auditable per-shot prompts from approved layered visual evidence."""

        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            story = self._required(
                session,
                StoryRevisionRecord,
                payload.story_revision_id,
                lock=True,
            )
            if (
                story.production_run_id != project_id
                or story.status != StoryRevisionStatus.APPROVED.value
            ):
                raise WorkflowConflictError("剧情脚本已不是当前人工批准版本")
            profile = self._required(
                session,
                VisualProfileRevision,
                payload.visual_profile_revision_id,
                lock=True,
            )
            if (
                profile.production_run_id != project_id
                or project.current_visual_profile_revision_id != profile.id
            ):
                raise WorkflowConflictError("本集视觉档案已更新，请重新准备分镜资产")

            recipe = session.scalar(
                select(ProductionRecipeInstance).where(
                    ProductionRecipeInstance.production_run_id == project_id,
                    ProductionRecipeInstance.lifecycle_status == "active",
                )
            )
            global_blockers: list[str] = []
            if recipe is not None and profile.source_profile_id != CANON_V3_PROFILE_ID:
                global_blockers.append("一人一猫组合包必须使用当前 Canon-v3 本集视觉档案")

            profile_asset_ids = [
                uuid.UUID(str(binding["assetId"]))
                for binding in profile.reference_bindings_json
                if binding.get("assetId")
            ]
            profile_assets = {
                asset.id: asset
                for asset in session.scalars(select(Asset).where(Asset.id.in_(profile_asset_ids)))
            }
            missing_canon = [
                asset_id
                for asset_id in profile_asset_ids
                if (
                    (asset := profile_assets.get(asset_id)) is None
                    or asset.status not in {"approved", "ready"}
                )
            ]
            if missing_canon:
                global_blockers.append("固定儿童、猫咪或画风 Canon 引用缺失/未批准")
            if recipe is not None:
                semantic_keys = {
                    str(asset.semantic_key)
                    for asset in profile_assets.values()
                    if asset.semantic_key and asset.status in {"approved", "ready"}
                }
                absent_keys = sorted(set(CANON_V3_REQUIRED_KEYS).difference(semantic_keys))
                if absent_keys:
                    global_blockers.append(
                        "Canon-v3 必需证据槽位缺失：" + "、".join(absent_keys)
                    )

            appearance_bindings: list[dict[str, Any]] = []
            if recipe is not None:
                design = session.scalar(
                    select(CharacterDesignRevision)
                    .where(
                        CharacterDesignRevision.production_recipe_instance_id == recipe.id,
                        CharacterDesignRevision.source_story_revision_id == story.id,
                        CharacterDesignRevision.status == "approved",
                    )
                    .order_by(CharacterDesignRevision.revision.desc())
                    .limit(1)
                )
                selected_designs = [] if design is None else list(
                    session.scalars(
                        select(CharacterDesignAsset).where(
                            CharacterDesignAsset.character_design_revision_id == design.id,
                            CharacterDesignAsset.selected.is_(True),
                        )
                    )
                )
                selected_slots = {item.slot for item in selected_designs}
                if design is None or selected_slots != {"child", "cat", "pair_scale"}:
                    global_blockers.append("本集儿童、猫咪与同框比例图尚未逐槽批准")
                else:
                    design_assets = {
                        asset.id: asset
                        for asset in session.scalars(
                            select(Asset).where(
                                Asset.id.in_([item.asset_id for item in selected_designs])
                            )
                        )
                    }
                    for item in selected_designs:
                        asset = design_assets.get(item.asset_id)
                        if asset is None or asset.status not in {"approved", "ready"}:
                            global_blockers.append(f"本集角色槽位 {item.slot} 的图片尚未批准")
                            continue
                        appearance_bindings.append(
                            _compiled_reference_binding(
                                asset,
                                role=("composition" if item.slot == "pair_scale" else "appearance"),
                                purpose=item.slot,
                                source="character_design",
                            )
                        )

            scenes = {
                scene.id: scene
                for scene in session.scalars(
                    select(Scene).where(
                        Scene.production_run_id == project_id,
                        Scene.story_revision_id == story.id,
                        Scene.active.is_(True),
                    )
                )
            }
            results: list[dict[str, Any]] = []
            for shot in payload.shots:
                scene = scenes.get(shot.scene_id)
                if scene is None:
                    raise WorkflowConflictError("镜头所属场景不属于当前剧情脚本")
                beat = None
                if shot.beat_id is not None:
                    beat = self._required(session, ShotBeat, shot.beat_id, lock=True)
                    if (
                        beat.story_revision_id != story.id
                        or beat.scene_id != scene.id
                        or beat.status == "superseded"
                        or beat.revision != shot.expected_revision
                    ):
                        raise WorkflowConflictError(
                            f"镜头 {shot.order} 已更新，请基于最新 revision 重新编译"
                        )
                elif shot.expected_revision != 0:
                    raise WorkflowConflictError("新镜头的 expectedRevision 必须为 0")

                blockers = list(global_blockers)
                warnings: list[str] = []
                scene_bindings, scene_blockers, scene_warnings = _scene_prompt_bindings(
                    session,
                    scene,
                    profile,
                )
                blockers.extend(scene_blockers)
                warnings.extend(scene_warnings)
                composition_bindings: list[dict[str, Any]] = []
                if shot.composition_asset_ids:
                    composition_assets = {
                        asset.id: asset
                        for asset in session.scalars(
                            select(Asset).where(Asset.id.in_(shot.composition_asset_ids))
                        )
                    }
                    for asset_id in shot.composition_asset_ids:
                        asset = composition_assets.get(asset_id)
                        if (
                            asset is None
                            or asset.production_run_id not in {None, project_id}
                            or asset.media_type != "image"
                            or asset.status not in {"approved", "ready"}
                        ):
                            blockers.append(f"构图参考 {asset_id} 不可用或尚未批准")
                            continue
                        composition_bindings.append(
                            _compiled_reference_binding(
                                asset,
                                role="composition",
                                purpose="shot_composition",
                                source="shot",
                            )
                        )
                else:
                    warnings.append("当前镜头未绑定额外构图参考，将仅使用分镜描述与同框比例图")

                canon_bindings = [
                    _compiled_reference_binding(
                        asset,
                        role=(
                            "style"
                            if str(binding.get("purpose")) == "style"
                            else "identity"
                        ),
                        purpose=str(binding.get("purpose") or asset.semantic_key or "identity"),
                        source="canon",
                    )
                    for binding in profile.reference_bindings_json
                    if binding.get("assetId")
                    and (
                        asset := profile_assets.get(uuid.UUID(str(binding["assetId"])))
                    )
                    is not None
                ]
                reference_bindings = [
                    *canon_bindings,
                    *appearance_bindings,
                    *scene_bindings,
                    *composition_bindings,
                ]
                shot_document = shot.model_dump(mode="json", by_alias=True)
                input_snapshot = {
                    "storyRevisionId": str(story.id),
                    "storyRevision": story.revision,
                    "visualProfileRevisionId": str(profile.id),
                    "visualProfileRevision": profile.revision,
                    "sceneId": str(scene.id),
                    "sceneLookDraftRevision": scene.look_draft_revision,
                    "shot": shot_document,
                    "referenceBindings": reference_bindings,
                    "warnings": warnings,
                    "blockers": blockers,
                }
                input_hash = _json_hash(input_snapshot)
                result: dict[str, Any] = {
                    "beatId": None if beat is None else str(beat.id),
                    "order": shot.order,
                    "finalPrompt": "",
                    "promptId": None,
                    "referenceBindings": reference_bindings,
                    "warnings": warnings,
                    "blockers": list(dict.fromkeys(blockers)),
                    "estimatedCost": {"currency": "CNY", "amountMicros": 0},
                    "inputHash": input_hash,
                }
                if result["blockers"]:
                    results.append(result)
                    continue

                final_prompt = _compile_storyboard_prompt_text(
                    profile=profile,
                    story=story,
                    scene=scene,
                    shot=shot_document,
                    reference_bindings=reference_bindings,
                    healing_recipe=payload.healing_recipe,
                )
                prompt_hash = hashlib.sha256(final_prompt.encode("utf-8")).hexdigest()
                business_object_id = (
                    beat.id
                    if beat is not None
                    else uuid.uuid5(
                        project_id,
                        f"storyboard-draft:{story.id}:{scene.id}:{shot.order}",
                    )
                )
                operation_key = f"director:storyboard-prompt:{business_object_id}"
                idempotency_key = hashlib.sha256(
                    f"{project_id}:{operation_key}:{input_hash}:{prompt_hash}".encode()
                ).hexdigest()
                step = session.scalar(
                    select(WorkflowStep).where(
                        WorkflowStep.idempotency_key == idempotency_key
                    )
                )
                if step is None:
                    now = datetime.now(UTC)
                    step = WorkflowStep(
                        id=uuid.uuid4(),
                        production_run_id=project_id,
                        scene_id=scene.id,
                        shot_card_id=None if beat is None else beat.shot_card_id,
                        kind=StepKind.DIRECTOR.value,
                        status=StepStatus.SUCCEEDED.value,
                        attempt=1,
                        operation_key=operation_key,
                        idempotency_key=idempotency_key,
                        provider="internal",
                        model="layered-storyboard-prompt-compiler-v1",
                        input_hash=input_hash,
                        request_hash=prompt_hash,
                        input_snapshot_json=input_snapshot,
                        completed_at=now,
                    )
                    session.add(step)
                    session.flush()
                prompt = session.scalar(
                    select(PromptRecord).where(
                        PromptRecord.step_id == step.id,
                        PromptRecord.sha256 == prompt_hash,
                    )
                )
                if prompt is None:
                    now = datetime.now(UTC)
                    prompt = PromptRecord(
                        id=uuid.uuid4(),
                        step_id=step.id,
                        purpose=PromptPurpose.DIRECTOR.value,
                        model="layered-storyboard-prompt-compiler-v1",
                        prompt_text=final_prompt,
                        sha256=prompt_hash,
                        call_purpose="storyboard_prompt_compilation",
                        node_id=business_object_id,
                        business_object_type=(
                            "shot_beat" if beat is not None else "storyboard_draft_shot"
                        ),
                        business_object_id=business_object_id,
                        template_name="storyboard.layered-shot.v1",
                        template_version="1.0.0",
                        final_prompt=final_prompt,
                        provider_request_json={
                            "mode": "compile_only",
                            "referenceBindings": reference_bindings,
                        },
                        provider_internal_transform="none",
                        input_snapshot_json=input_snapshot,
                        structured_response_json={
                            "referenceBindings": reference_bindings,
                            "warnings": warnings,
                            "blockers": [],
                        },
                        parameters_json={"healingRecipe": payload.healing_recipe},
                        cost_micros=0,
                        status="succeeded",
                        input_hash=input_hash,
                        output_hash=prompt_hash,
                        completed_at=now,
                    )
                    session.add(prompt)
                    session.flush()
                if beat is not None and _shot_matches_persisted_beat(shot_document, beat):
                    beat.prompt_id = prompt.id
                result.update(finalPrompt=final_prompt, promptId=str(prompt.id))
                results.append(result)

            return {
                "projectId": str(project_id),
                "storyRevisionId": str(story.id),
                "visualProfileRevisionId": str(profile.id),
                "status": (
                    "blocked" if any(item["blockers"] for item in results) else "compiled"
                ),
                "shots": results,
            }

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
                role=(
                    f"character_design_{batch.input_json['characterDesign']['slot']}"
                    if isinstance(batch.input_json.get("characterDesign"), dict)
                    else "image_candidate"
                ),
                semantic_key=(
                    "character-design:"
                    f"{batch.input_json['characterDesign']['revisionId']}:"
                    f"{batch.input_json['characterDesign']['slot']}:"
                    f"candidate:{candidate_index}"
                    if isinstance(batch.input_json.get("characterDesign"), dict)
                    else f"batch:{batch.id}:candidate:{candidate_index}"
                ),
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
                    "characterDesign": batch.input_json.get("characterDesign"),
                },
            )
            session.add(asset)
            session.flush()
            character_design = batch.input_json.get("characterDesign")
            if isinstance(character_design, dict):
                revision_id = uuid.UUID(str(character_design["revisionId"]))
                revision = self._required(session, CharacterDesignRevision, revision_id, lock=True)
                if revision.production_run_id != batch.production_run_id:
                    raise WorkflowConflictError("角色设计版本与图片生成批次项目不一致")
                session.add(
                    CharacterDesignAsset(
                        id=uuid.uuid4(),
                        character_design_revision_id=revision.id,
                        asset_id=asset.id,
                        slot=str(character_design["slot"]),
                        candidate_index=candidate_index,
                        semantic_role=str(character_design["semanticRole"]),
                        selected=False,
                    )
                )
                session.flush()
                bindings = list(
                    session.scalars(
                        select(CharacterDesignAsset).where(
                            CharacterDesignAsset.character_design_revision_id == revision.id
                        )
                    )
                )
                expected = int(character_design["candidateCount"])
                counts = {
                    slot: sum(1 for item in bindings if item.slot == slot)
                    for slot in ("child", "cat", "pair_scale")
                }
                if all(count >= expected for count in counts.values()):
                    revision.status = "awaiting_review"
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

    def video_candidate_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("media:video:batch:"):
                raise ValueError("workflow step is not a video batch candidate")
            batch = self._required(
                session,
                MediaGenerationBatch,
                uuid.UUID(str(step.input_snapshot_json["batchId"])),
            )
            if batch.media_kind != "video" or batch.production_run_id != step.production_run_id:
                raise WorkflowConflictError("视频候选任务与生成批次项目不一致")
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None or prompt.status != "pending":
                raise WorkflowConflictError("视频候选缺少待执行的精确 Prompt")

            input_document = batch.input_json
            config = input_document.get("generationConfig", input_document)
            if not isinstance(config, dict):
                raise ValueError("视频生成配置必须是对象")
            included_references = [
                item
                for item in config.get("actualReferences", [])
                if isinstance(item, dict) and item.get("providerIncluded") is True
            ]
            assets: list[Asset] = []
            for item in included_references:
                asset = self._required(session, Asset, uuid.UUID(str(item["assetId"])))
                if asset.production_run_id != batch.production_run_id:
                    raise WorkflowConflictError("视频生成引用不属于当前项目")
                if asset.media_type != "image":
                    raise ValueError("首期视频生成只接受图片引用")
                assets.append(asset)
            sources = tuple(
                MediaSource(
                    asset_id=asset.id,
                    semantic_key=asset.semantic_key or f"asset:{asset.id}",
                    media_type=asset.media_type,
                    sha256=asset.sha256,
                    metadata=asset.metadata_json,
                )
                for asset in assets
            )
            mode = str(config.get("mode", "text_to_video"))
            if mode in {"image_to_video", "first_last_frame"} and len(sources) != 1:
                raise ValueError("当前 Ark 首帧视频模式必须且只能提交一张实际引用图")
            resolution = str(config.get("resolution", "720p")).lower()
            duration_seconds = int(config.get("durationSeconds", 8))
            plan: VideoInputPlan = build_shot_input_plan(
                resolution=resolution,
                duration_seconds=duration_seconds,
                anchor=sources[0] if mode in {"image_to_video", "first_last_frame"} else None,
                references=(() if mode in {"image_to_video", "first_last_frame"} else sources),
            )
            return {
                "batchId": str(batch.id),
                "candidateIndex": int(step.input_snapshot_json["candidateIndex"]),
                "prompt": prompt.final_prompt or prompt.prompt_text,
                "inputPlan": plan,
                "inputSources": tuple(
                    _resolve_asset_path(asset.storage_key, self._asset_root) for asset in assets
                ),
                "providerTaskId": step.provider_task_id,
            }

    def record_video_candidate_submission(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            if not step.operation_key.startswith("media:video:batch:"):
                raise ValueError("workflow step is not a video batch candidate")
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

    def complete_video_candidate(
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
                raise WorkflowConflictError("视频候选缺少 Prompt 审计记录")
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=batch.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=batch.canvas_node_id,
                role="video_candidate",
                semantic_key=f"batch:{batch.id}:candidate:{candidate_index}",
                scope="canvas_node",
                status="candidate",
                media_type="video",
                storage_key=_asset_storage_key(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json={
                    "batchId": str(batch.id),
                    "candidateIndex": candidate_index,
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "providerTaskId": step.provider_task_id,
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
            node.status = batch.status
            node.data_json = {
                **node.data_json,
                "status": batch.status,
                "candidates": [
                    *list(node.data_json.get("candidates", [])),
                    {
                        "id": str(asset.id),
                        "assetId": str(asset.id),
                        "title": f"候选 {candidate_index}",
                        "contentUrl": f"/api/v1/assets/{asset.id}/content",
                        "promptId": str(prompt.id),
                        "status": "candidate",
                    },
                ],
            }
            prompt.status = "succeeded"
            prompt.raw_response_json = {
                "url": provider_url,
                "model": provider_model,
                "providerTaskId": step.provider_task_id,
            }
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
                    "mediaKind": "video",
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

    def bind_canvas_node_assets(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            node = self._required(session, CanvasGraphNode, node_id, lock=True)
            if node.node_type != CanvasNodeType.REFERENCE_ASSET.value:
                raise ValueError("素材绑定命令只适用于 ReferenceAssetNode")
            if node.revision != expected_revision:
                raise WorkflowConflictError(
                    f"参考素材节点版本冲突：当前 {node.revision}，提交 {expected_revision}"
                )
            requested = {item.asset_id: item for item in payload.bindings}
            if len(requested) != len(payload.bindings):
                raise ValueError("同一素材不能在一个参考节点中重复绑定")
            current_assets = list(
                session.scalars(select(Asset).where(Asset.canvas_node_id == node.id))
            )
            affected_node_ids = {node.id}
            for asset in current_assets:
                if asset.id not in requested:
                    asset.canvas_node_id = None
                    if asset.scope == "canvas_node":
                        asset.scope = "project"
            for binding in payload.bindings:
                asset = self._required(session, Asset, binding.asset_id, lock=True)
                if asset.production_run_id != node.production_run_id and asset.scope != "canon":
                    raise ValueError("参考素材不属于当前项目")
                if asset.canvas_node_id not in {None, node.id}:
                    if not payload.allow_move:
                        raise WorkflowConflictError(
                            f"素材已绑定到节点 {asset.canvas_node_id}；确认移动后重试"
                        )
                    affected_node_ids.add(asset.canvas_node_id)
                asset.canvas_node_id = node.id
                if asset.scope != "canon":
                    asset.scope = "canvas_node"
                asset.metadata_json = {
                    **asset.metadata_json,
                    "canvasSemanticRole": binding.semantic_role,
                }
            session.flush()
            for affected_node_id in affected_node_ids:
                affected = session.get(CanvasGraphNode, affected_node_id)
                if affected is not None:
                    self._refresh_asset_node_projection(session, affected)
                    if affected.id != node.id:
                        affected.revision += 1
                        self._mark_graph_downstream_stale(session, affected.id)
            node.revision += 1
            self._mark_graph_downstream_stale(session, node.id)
            self._record_event(
                session,
                node.production_run_id,
                "canvas_asset_bindings_changed",
                {
                    "nodeId": str(node.id),
                    "revision": node.revision,
                    "assetIds": [str(asset_id) for asset_id in requested],
                },
            )
            session.flush()
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
            allowed_node_types = {
                "image": {
                    CanvasNodeType.GENERATION_BATCH.value,
                    CanvasNodeType.IMAGE_GENERATION.value,
                    CanvasNodeType.CHARACTER_DESIGN.value,
                },
                "video": {CanvasNodeType.VIDEO_GENERATION.value},
            }
            if (
                node.production_run_id != payload.project_id
                or node.node_type not in allowed_node_types[payload.media_kind]
            ):
                raise ValueError(f"{payload.media_kind}生成批次与画布节点类型不匹配")
            existing = session.scalar(
                select(MediaGenerationBatch).where(
                    MediaGenerationBatch.idempotency_key == payload.idempotency_key
                )
            )
            if existing is not None:
                return _generation_batch_json(session, existing)
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
            return _generation_batch_json(session, batch)

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
                    ProviderCapability.media_kind == "video_edit",
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
            references = list(
                session.scalars(
                    select(VideoEditReference)
                    .where(VideoEditReference.recipe_id == recipe.id)
                    .order_by(VideoEditReference.ordinal)
                )
            )
            reference_assets = {
                asset.id: asset
                for asset in session.scalars(
                    select(Asset).where(
                        Asset.id.in_([reference.asset_id for reference in references])
                    )
                )
            }
            reference_stage = "video" if plan.mode == "direct" else "control_anchor"
            actual_references = [
                {
                    "assetId": str(reference.asset_id),
                    "subjectRevisionId": reference_assets[reference.asset_id].metadata_json.get(
                        "subjectRevisionId"
                    ),
                    "semanticRole": reference.semantic_role,
                    "providerIncluded": True,
                    "providerSlot": f"{reference_stage}_reference_{reference.ordinal}",
                    "omissionReason": None,
                }
                for reference in references
            ]
            recipe.compilation_json = {
                **plan.model_dump(mode="json", by_alias=True),
                "provider": capability.provider,
                "model": capability.model,
                "providerCapabilityId": str(capability_row.id),
                "actualReferences": actual_references,
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
        self, project_id: uuid.UUID, *, after_sequence: int = 0
    ) -> tuple[dict[str, Any], ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        with self._sessions() as session:
            self._require_project(session, project_id)
            query = (
                select(CanvasEvent)
                .where(
                    CanvasEvent.production_run_id == project_id,
                    CanvasEvent.sequence > after_sequence,
                )
                .order_by(CanvasEvent.sequence)
                .limit(200)
            )
            return tuple(
                {
                    "id": str(event.id),
                    "sequence": event.sequence,
                    "type": event.event_type,
                    "data": event.data_json,
                    "createdAt": event.created_at.isoformat(),
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
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order)
                )
            )
            beats = list(
                session.scalars(
                    select(ShotBeat)
                    .join(Scene, Scene.id == ShotBeat.scene_id)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
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
                include_narrative_projection=(
                    project.canvas_template_key != CanvasTemplateKey.PRODUCT_AD.value
                    or any((brief, stories, scenes, beats))
                ),
            )
            graph_nodes = list(
                session.scalars(
                    select(CanvasGraphNode)
                    .where(
                        CanvasGraphNode.production_run_id == project_id,
                        CanvasGraphNode.node_type != CanvasNodeType.RECIPE_GROUP.value,
                    )
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
            groups = list(
                session.scalars(
                    select(CanvasGroup)
                    .where(
                        CanvasGroup.production_run_id == project_id,
                        CanvasGroup.lifecycle_status == "active",
                    )
                    .order_by(CanvasGroup.created_at, CanvasGroup.id)
                )
            )
            group_ids = [group.id for group in groups]
            group_members = (
                []
                if not group_ids
                else list(
                    session.scalars(
                        select(CanvasGroupMember)
                        .where(CanvasGroupMember.group_id.in_(group_ids))
                        .order_by(
                            CanvasGroupMember.group_id,
                            CanvasGroupMember.sort_order,
                        )
                    )
                )
            )
            group_states = {
                group.id: _recipe_canvas_group_state(session, group)
                for group in groups
                if group.production_recipe_instance_id is not None
            }
            _merge_canvas_groups(
                result,
                groups=groups,
                members=group_members,
                states=group_states,
            )
            active_archive_ids = {
                str(node_id)
                for node_id in session.scalars(
                    select(CanvasNodeArchive.canvas_node_id).where(
                        CanvasNodeArchive.production_run_id == project_id,
                        CanvasNodeArchive.restored_at.is_(None),
                    )
                )
            }
            _apply_canvas_node_archive_projection(result, active_archive_ids)
            result["templateKey"] = project.canvas_template_key
            result["featureFlags"] = {
                "UNIVERSAL_CANVAS": project.universal_canvas_enabled,
                "PRODUCT_AD_TEMPLATE": project.product_ad_template_enabled,
                "VIDEO_EDIT_V2": project.video_edit_v2_enabled,
            }
            return result

    def archive_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
        reason: str | None,
    ) -> dict[str, Any]:
        projected = self.get_canvas(project_id)
        target = next(
            (node for node in projected["nodes"] if str(node["id"]) == str(node_id)),
            None,
        )
        if target is None:
            with self._sessions() as session:
                existing = session.scalar(
                    select(CanvasNodeArchive).where(
                        CanvasNodeArchive.production_run_id == project_id,
                        CanvasNodeArchive.canvas_node_id == node_id,
                        CanvasNodeArchive.restored_at.is_(None),
                    )
                )
                if existing is None:
                    raise RecordNotFoundError(f"Canvas node {node_id} was not found")
                layout = session.scalar(
                    select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
                )
                return {
                    "projectId": str(project_id),
                    "nodeId": str(node_id),
                    "archived": True,
                    "layoutVersion": 0 if layout is None else layout.version,
                }
        archive_action = next(
            (
                action
                for action in target.get("availableActions", [])
                if action.get("key") == "archive_node"
            ),
            None,
        )
        if not archive_action or not archive_action.get("enabled"):
            disabled_reason = (
                None if archive_action is None else archive_action.get("disabledReason")
            )
            raise WorkflowConflictError(
                str(disabled_reason or "该节点受当前工作流保护，不能从画布移除")
            )
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            existing = session.scalar(
                select(CanvasNodeArchive)
                .where(
                    CanvasNodeArchive.production_run_id == project_id,
                    CanvasNodeArchive.canvas_node_id == node_id,
                )
                .with_for_update()
            )
            if existing is not None and existing.restored_at is None:
                layout = session.scalar(
                    select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
                )
                return {
                    "projectId": str(project_id),
                    "nodeId": str(node_id),
                    "archived": True,
                    "layoutVersion": 0 if layout is None else layout.version,
                }
            layout = self._advance_canvas_layout_version(
                session,
                project_id,
                expected_version=expected_version,
            )
            object_id = target.get("objectId")
            if existing is None:
                existing = CanvasNodeArchive(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    canvas_node_id=node_id,
                    node_type=str(target["type"]),
                    object_type=str(target.get("objectType") or target["type"]),
                    object_id=None if not object_id else uuid.UUID(str(object_id)),
                    reason=reason,
                    archived_at=now,
                )
                session.add(existing)
            else:
                existing.node_type = str(target["type"])
                existing.object_type = str(target.get("objectType") or target["type"])
                existing.object_id = None if not object_id else uuid.UUID(str(object_id))
                existing.reason = reason
                existing.archived_at = now
                existing.restored_at = None
                existing.revision += 1
            self._record_event(
                session,
                project_id,
                "canvas_projection_changed",
                {"canvasNodeId": str(node_id), "change": "archived"},
            )
            return {
                "projectId": str(project_id),
                "nodeId": str(node_id),
                "archived": True,
                "layoutVersion": layout.version,
            }

    def restore_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            existing = session.scalar(
                select(CanvasNodeArchive)
                .where(
                    CanvasNodeArchive.production_run_id == project_id,
                    CanvasNodeArchive.canvas_node_id == node_id,
                )
                .with_for_update()
            )
            if existing is None or existing.restored_at is not None:
                layout = session.scalar(
                    select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
                )
                return {
                    "projectId": str(project_id),
                    "nodeId": str(node_id),
                    "archived": False,
                    "layoutVersion": 0 if layout is None else layout.version,
                }
            layout = self._advance_canvas_layout_version(
                session,
                project_id,
                expected_version=expected_version,
            )
            existing.restored_at = datetime.now(UTC)
            existing.revision += 1
            self._record_event(
                session,
                project_id,
                "canvas_projection_changed",
                {"canvasNodeId": str(node_id), "change": "restored"},
            )
            return {
                "projectId": str(project_id),
                "nodeId": str(node_id),
                "archived": False,
                "layoutVersion": layout.version,
            }

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
            row.viewport_json = document["viewport"]
            row.operations_json = [
                *(row.operations_json or []),
                *document["operations"],
            ][-500:]
            row.sync_status = "saved"
            session.flush()
            return {
                "projectId": str(project_id),
                "layoutVersion": row.version,
                "viewport": row.viewport_json,
                "syncStatus": row.sync_status,
                "rebasedFromVersion": rebased_from,
            }

    @staticmethod
    def _advance_canvas_layout_version(
        session: Session,
        project_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> CanvasLayout:
        layout = session.scalar(
            select(CanvasLayout)
            .where(CanvasLayout.production_run_id == project_id)
            .with_for_update()
        )
        current_version = 0 if layout is None else layout.version
        if current_version != expected_version:
            raise WorkflowConflictError(
                f"画布布局版本冲突：当前 {current_version}，提交 {expected_version}"
            )
        if layout is None:
            layout = CanvasLayout(
                id=uuid.uuid4(),
                production_run_id=project_id,
                version=1,
                nodes_json=[],
                edges_json=[],
                viewport_json={"x": 0, "y": 0, "zoom": 1},
                operations_json=[],
            )
            session.add(layout)
        else:
            layout.version += 1
        session.flush()
        return layout

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
        self._sync_video_edit_reference_edges(
            session,
            node=node,
            reference_asset_ids=draft.reference_asset_ids,
        )
        session.flush()
        return row

    def _sync_video_edit_reference_edges(
        self,
        session: Session,
        *,
        node: CanvasGraphNode,
        reference_asset_ids: list[uuid.UUID],
    ) -> None:
        desired_sources: dict[uuid.UUID, CanvasGraphNode] = {}
        for asset_id in reference_asset_ids:
            asset = self._required(session, Asset, asset_id)
            if asset.canvas_node_id is None:
                continue
            source_node = self._required(session, CanvasGraphNode, asset.canvas_node_id)
            if (
                source_node.production_run_id == node.production_run_id
                and source_node.node_type
                in {
                    CanvasNodeType.REFERENCE_ASSET.value,
                    CanvasNodeType.IMAGE_ASSET.value,
                }
            ):
                desired_sources[source_node.id] = source_node

        current_edges = list(
            session.scalars(
                select(CanvasGraphEdge).where(
                    CanvasGraphEdge.target_node_id == node.id,
                    CanvasGraphEdge.source_port == CanvasPortType.MEDIA_REFERENCES.value,
                    CanvasGraphEdge.target_port == CanvasPortType.MEDIA_REFERENCES.value,
                )
            )
        )
        current_source_ids = {edge.source_node_id for edge in current_edges}
        for edge in current_edges:
            if edge.source_node_id not in desired_sources:
                session.delete(edge)
        for source_id, source_node in desired_sources.items():
            if source_id in current_source_ids:
                continue
            connection = CanvasConnection(
                sourceNodeId=source_node.id,
                sourceNodeType=CanvasNodeType(source_node.node_type),
                sourcePort=CanvasPortType.MEDIA_REFERENCES,
                targetNodeId=node.id,
                targetNodeType=CanvasNodeType.VIDEO_EDIT,
                targetPort=CanvasPortType.MEDIA_REFERENCES,
            )
            session.add(_graph_edge(node.production_run_id, connection))

        self._record_event(
            session,
            node.production_run_id,
            "video_edit_reference_edges_synced",
            {
                "nodeId": str(node.id),
                "sourceNodeIds": [str(source_id) for source_id in desired_sources],
            },
        )

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

    @staticmethod
    def _refresh_asset_node_projection(session: Session, node: CanvasGraphNode) -> None:
        assets = list(
            session.scalars(
                select(Asset)
                .where(Asset.canvas_node_id == node.id)
                .order_by(Asset.created_at, Asset.id)
            )
        )
        documents = [
            {
                "assetId": str(asset.id),
                "mediaType": asset.media_type,
                "semanticRole": asset.metadata_json.get("canvasSemanticRole")
                or asset.semantic_key
                or asset.role,
                "status": asset.status,
                "sha256": asset.sha256,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "thumbnailUrl": (
                    f"/api/v1/assets/{asset.id}/content" if asset.media_type == "image" else None
                ),
            }
            for asset in assets
        ]
        node.data_json = {
            **node.data_json,
            "assets": documents,
            "assetId": documents[0]["assetId"] if len(documents) == 1 else None,
            "thumbnailUrl": next(
                (item["thumbnailUrl"] for item in documents if item["thumbnailUrl"]),
                None,
            ),
            "semanticRole": documents[0]["semanticRole"] if len(documents) == 1 else None,
        }
        node.status = "ready" if assets else "awaiting_input"

    @staticmethod
    def _mark_graph_downstream_stale(session: Session, source_node_id: uuid.UUID) -> None:
        frontier = {source_node_id}
        visited = {source_node_id}
        while frontier:
            edges = list(
                session.scalars(
                    select(CanvasGraphEdge).where(CanvasGraphEdge.source_node_id.in_(frontier))
                )
            )
            frontier = set()
            for edge in edges:
                if edge.target_node_id in visited:
                    continue
                visited.add(edge.target_node_id)
                frontier.add(edge.target_node_id)
                target = session.get(CanvasGraphNode, edge.target_node_id)
                if target is not None:
                    target.status = "stale"
                    target.revision += 1


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


def _filmstrip_identity(asset: Asset, frame_count: int) -> tuple[tuple[int, ...], str, str]:
    if not 4 <= frame_count <= 12:
        raise ValueError("视频帧带数量必须在4至12之间")
    qc = asset.metadata_json.get("qc")
    duration_value = qc.get("durationMs") if isinstance(qc, dict) else None
    if duration_value is None:
        duration_value = asset.metadata_json.get("durationMs")
    if not isinstance(duration_value, int) or duration_value <= 0:
        raise ValueError("视频资产缺少可用于真实抽帧的durationMs")
    # Seeking at duration-1ms can land after the final decodable frame on CFR videos.
    # Keep a small tail margin so FFmpeg can always return the last visible frame.
    last_timestamp = max(0, duration_value - min(100, duration_value))
    timestamps = tuple(
        round(index * last_timestamp / (frame_count - 1)) for index in range(frame_count)
    )
    identity = {
        "sourceSha256": asset.sha256,
        "frameCount": frame_count,
        "timestampsMs": timestamps,
    }
    filmstrip_key = _json_hash(identity)
    idempotency_key = hashlib.sha256(f"filmstrip:{filmstrip_key}".encode()).hexdigest()
    return timestamps, filmstrip_key, idempotency_key


def _filmstrip_frames(session: Session, source: Asset, filmstrip_key: str) -> tuple[Asset, ...]:
    if source.production_run_id is None:
        return ()
    rows = session.scalars(
        select(Asset).where(
            Asset.production_run_id == source.production_run_id,
            Asset.role == "filmstrip_frame",
            Asset.status == "ready",
        )
    )
    matching = [
        row
        for row in rows
        if row.metadata_json.get("sourceAssetId") == str(source.id)
        and row.metadata_json.get("filmstripKey") == filmstrip_key
    ]
    return tuple(sorted(matching, key=lambda row: int(row.metadata_json["timestampMs"])))


def _filmstrip_json(
    source: Asset,
    frame_count: int,
    status: str,
    frames: tuple[Asset, ...],
    step: WorkflowStep | None,
) -> dict[str, Any]:
    return {
        "assetId": str(source.id),
        "frameCount": frame_count,
        "status": status,
        "stepId": None if step is None else str(step.id),
        "error": None if step is None else step.error_json,
        "frames": [
            {
                "assetId": str(frame.id),
                "timestampMs": int(frame.metadata_json["timestampMs"]),
                "contentUrl": f"/api/v1/assets/{frame.id}/content",
                "sha256": frame.sha256,
            }
            for frame in frames
        ],
    }


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


_CAMERA_MOTION_PRESETS: tuple[dict[str, Any], ...] = (
    {"value": "static", "label": "固定镜头", "enabled": True},
    {"value": "follow", "label": "跟随拍摄", "enabled": True},
    {"value": "push_in", "label": "缓慢推进", "enabled": True},
    {"value": "pull_out", "label": "缓慢拉远", "enabled": True},
    {"value": "pan_left", "label": "镜头左摇", "enabled": True},
    {"value": "pan_right", "label": "镜头右摇", "enabled": True},
    {"value": "tilt_up", "label": "镜头上摇", "enabled": True},
    {"value": "tilt_down", "label": "镜头下摇", "enabled": True},
    {"value": "crane_up", "label": "升降上升", "enabled": True},
    {"value": "crane_down", "label": "升降下降", "enabled": True},
    {"value": "dolly_left", "label": "镜头左移", "enabled": True},
    {"value": "dolly_right", "label": "镜头右移", "enabled": True},
    {"value": "zoom_in", "label": "变焦推近", "enabled": True},
    {"value": "zoom_out", "label": "变焦拉远", "enabled": True},
    {"value": "orbit", "label": "环绕主体", "enabled": True},
    {"value": "handheld", "label": "手持跟拍", "enabled": True},
    {"value": "drone", "label": "航拍运镜", "enabled": True},
)


_VIDEO_ASSET_ACTIONS: tuple[dict[str, Any], ...] = (
    {"key": "edit", "label": "编辑", "enabled": True, "execution": "client"},
    {
        "key": "segment_reshoot",
        "label": "片段重拍",
        "enabled": True,
        "execution": "provider",
    },
    {
        "key": "crop",
        "label": "裁剪",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前版本尚未配置非破坏裁剪执行器",
    },
    {
        "key": "upscale",
        "label": "高清",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前 Ark ProviderCapability 尚未配置视频高清执行器",
    },
    {
        "key": "frame_interpolation",
        "label": "插帧",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前版本尚未配置逐帧插值执行器",
    },
    {
        "key": "extend",
        "label": "智能续写",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前 Ark ProviderCapability 尚未配置视频续写执行器",
    },
    {
        "key": "subtitles",
        "label": "智能字幕",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前版本尚未配置字幕识别与回填执行器",
    },
    {
        "key": "audio_separation",
        "label": "音频分离",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前版本尚未配置音轨分离执行器",
    },
    {
        "key": "image_edit",
        "label": "画面编辑",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "请使用片段重拍；通用画面编辑执行器尚未配置",
    },
    {"key": "download", "label": "下载", "enabled": True, "execution": "client"},
    {"key": "fullscreen", "label": "全屏", "enabled": True, "execution": "client"},
)


def _graph_node_json(row: CanvasGraphNode) -> dict[str, Any]:
    data = dict(row.data_json)
    if row.node_type == CanvasNodeType.VIDEO_ASSET.value and row.object_id is not None:
        data.setdefault("assetId", str(row.object_id))
        data.setdefault("contentUrl", f"/api/v1/assets/{row.object_id}/content")
    contract = _canvas_node_contract(row.node_type, row.status, data)
    return {
        "id": str(row.id),
        "type": row.node_type,
        "objectType": row.object_type,
        "objectId": None if row.object_id is None else str(row.object_id),
        "revision": row.revision,
        "status": row.status,
        "data": data,
        **contract,
    }


def _preset_subject_targets_node(role: str, target: CanvasGraphNode) -> bool:
    """Keep identity edges aligned with the target's actual semantic slot."""

    if target.node_type == CanvasNodeType.CHARACTER_DESIGN.value:
        slot = str(target.data_json.get("slot") or "")
        if slot == "child":
            return role == "protagonist"
        if slot == "cat":
            return role == "co_protagonist"
        return slot == "pair_scale"
    return target.node_type in {
        CanvasNodeType.STORY_PLANNER.value,
        CanvasNodeType.STORYBOARD_DIRECTOR.value,
        CanvasNodeType.IMAGE_GENERATION.value,
    }


def _canvas_action(
    key: str,
    label: str,
    *,
    enabled: bool = True,
    execution: str = "client",
    disabled_reason: str | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "key": key,
        "label": label,
        "enabled": enabled,
        "execution": execution if enabled else "unavailable",
    }
    if disabled_reason:
        action["disabledReason"] = disabled_reason
    return action


def _canvas_node_contract(
    node_type: str,
    status: str | None,
    data: dict[str, Any],
) -> dict[str, Any]:
    actions_by_type: dict[str, list[dict[str, Any]]] = {
        "RecipeGroupNode": [
            _canvas_action("recipe_primary", str(data.get("primaryAction") or "继续制作")),
            _canvas_action("toggle_children", "展开子节点"),
        ],
        "BriefNode": [
            _canvas_action("complete_creative", "AI 补全创意", execution="provider"),
            _canvas_action("edit_brief", "编辑创意简报"),
        ],
        "SubjectNode": [
            _canvas_action("open_asset_library", "打开角色素材库"),
            _canvas_action("edit_episode_visual_profile", "编辑本集视觉档案"),
            _canvas_action("edit_subject", "编辑主体"),
            _canvas_action("assist_subject", "AI 补全主体", execution="provider"),
        ],
        "StylePresetNode": [
            _canvas_action("open_asset_library", "打开风格素材库"),
            _canvas_action("apply_visual_preset", "应用视觉预设"),
            _canvas_action("edit_episode_visual_profile", "编辑本集视觉档案"),
        ],
        "CharacterDesignNode": [
            _canvas_action(
                "generate_character_design",
                "生成角色设计" if not data.get("candidates") else "重新生成",
                execution="provider",
            ),
            _canvas_action(
                "review_character_design",
                "选择并审核候选",
                enabled=bool(data.get("candidates")),
                disabled_reason="角色设计候选尚未生成",
            ),
        ],
        "StoryPlannerNode": [
            _canvas_action("generate_stories", "生成三案", execution="provider"),
        ],
        "StoryCandidateNode": [
            _canvas_action(
                "approve_story",
                "批准定稿" if status != "approved" else "已批准",
                enabled=status != "approved",
                disabled_reason="该故事版本已经批准",
            ),
        ],
        "StoryCriticNode": [_canvas_action("inspect_prompt", "查看评审记录")],
        "ApprovalGateNode": [
            _canvas_action(
                (
                    "review_creative"
                    if data.get("phase") == "creative"
                    else (
                        "review_character_design"
                        if data.get("phase") == "character_design"
                        else "review_story"
                    )
                ),
                (
                    "审核创意简报"
                    if data.get("phase") == "creative"
                    else (
                        "审核角色设计"
                        if data.get("phase") == "character_design"
                        else "选择并审核故事"
                    )
                ),
            )
        ],
        "StoryboardDirectorNode": [
            _canvas_action("storyboard_from_story", "剧本生成分镜脚本", execution="provider"),
            _canvas_action(
                "storyboard_from_characters",
                "基于固定角色补充分镜",
                execution="provider",
            ),
            _canvas_action("storyboard_manual", "自己编写分镜脚本"),
            _canvas_action(
                "review_storyboard",
                "批准当前分镜" if not data.get("storyboardApproved") else "分镜已批准",
                enabled=bool(data.get("shotCount")) and not bool(data.get("storyboardApproved")),
                disabled_reason=(
                    "分镜已经人工批准"
                    if data.get("storyboardApproved")
                    else ("请先生成并保存镜头表" if not data.get("shotCount") else None)
                ),
            ),
        ],
        "SceneNode": [_canvas_action("open_scene", "查看场景与镜头")],
        "ShotBeatNode": [
            _canvas_action("edit_shot", "编辑镜头"),
            _canvas_action("generate_anchor", "生成视觉锚点", execution="provider"),
        ],
        "ImageGenerationNode": [
            _canvas_action("open_generator", "打开图片生成器"),
            _canvas_action("select_references", "选择参考"),
        ],
        "VideoGenerationNode": [
            _canvas_action("open_generator", "打开视频生成器"),
            _canvas_action("select_references", "选择参考"),
        ],
        "AudioGenerationNode": [_canvas_action("open_generator", "打开音频生成器")],
        "GenerationBatchNode": [_canvas_action("open_generator", "打开批量生成器")],
        "ReviewNode": [_canvas_action("review_asset", "审核候选资产")],
        "TimelineNode": [
            _canvas_action("compose_sequence", "合成最终音画", execution="local_worker"),
            _canvas_action(
                "export_sequence",
                "导出",
                enabled=bool(data.get("contentUrl")),
                disabled_reason="最终成片尚未批准，暂不能导出",
            ),
        ],
        "ReferenceAssetNode": [
            _canvas_action("upload_reference", "上传参考"),
            _canvas_action("select_history", "从历史选择"),
            _canvas_action("create_subject", "创建主体并绑定"),
        ],
        "ImageAssetNode": [
            _canvas_action("inspect_asset", "查看图片"),
            _canvas_action("download", "下载"),
        ],
        "VideoEditNode": [_canvas_action("edit", "打开视频编辑")],
        "VideoSegmentNode": [_canvas_action("inspect_asset", "查看重拍片段")],
        "PromptArtifactNode": [_canvas_action("inspect_prompt", "查看 Prompt 审计")],
    }
    actions = actions_by_type.get(node_type, [])
    if node_type == "VideoAssetNode":
        actions = [dict(action) for action in _VIDEO_ASSET_ACTIONS]
    if data.get("promptId") and not any(action["key"] == "inspect_prompt" for action in actions):
        actions.append(_canvas_action("inspect_prompt", "查看 Prompt 审计"))
    if not actions:
        actions = [
            _canvas_action(
                "unavailable",
                "暂无可执行操作",
                enabled=False,
                disabled_reason="该节点尚未配置可执行处理器",
            )
        ]
    return {
        "availableActions": actions,
        "executionScope": {
            "kind": "canvas_node",
            "objectType": str(data.get("objectType") or node_type),
        },
        "workflowSteps": list(data.get("workflowSteps") or []),
        "blocker": data.get("blocker"),
        "outputs": list(data.get("outputs") or []),
    }


_PROTECTED_RECIPE_SKELETON_NODE_TYPES = {
    "ApprovalGateNode",
    "BriefNode",
    "CharacterDesignNode",
    "ImageGenerationNode",
    "ReviewNode",
    "SceneNode",
    "ShotBeatNode",
    "StoryboardDirectorNode",
    "StoryPlannerNode",
    "StylePresetNode",
    "SubjectNode",
    "TimelineNode",
    "VideoGenerationNode",
}


def _canvas_node_archive_blocker(
    node: dict[str, Any],
    *,
    active_group_member_ids: set[str],
    outgoing_node_ids: set[str],
) -> str | None:
    node_id = str(node["id"])
    node_type = str(node["type"])
    data = node.get("data") or {}
    status = str(node.get("status") or data.get("status") or "")
    if node_type == "SubjectNode":
        return "固定儿童与猫咪 Canon 是身份来源，不能从画布移除"
    if node_type == "StoryCandidateNode" and status == "approved":
        return "当前批准故事已被下游流程使用，不能从画布移除"
    if node_id in active_group_member_ids and node_type in _PROTECTED_RECIPE_SKELETON_NODE_TYPES:
        return "该节点属于活跃六阶段流程骨架，不能从画布移除"
    if (
        node_type in {"ImageAssetNode", "VideoAssetNode"}
        and status == "approved"
        and node_id in outgoing_node_ids
    ):
        return "该批准资产已被下游节点引用，不能从画布移除"
    return None


def _apply_canvas_node_archive_projection(
    canvas: dict[str, Any],
    active_archive_ids: set[str],
) -> None:
    active_group_member_ids = {
        str(node_id)
        for group in canvas.get("groups", [])
        if str(group.get("lifecycleStatus") or "active") == "active"
        for node_id in group.get("memberNodeIds", [])
    }
    outgoing_node_ids = {str(edge["sourceNodeId"]) for edge in canvas.get("edges", [])}
    for node in canvas.get("nodes", []):
        blocker = _canvas_node_archive_blocker(
            node,
            active_group_member_ids=active_group_member_ids,
            outgoing_node_ids=outgoing_node_ids,
        )
        actions = [
            action
            for action in node.get("availableActions", [])
            if action.get("key") not in {"archive_node", "restore_node"}
        ]
        actions.append(
            _canvas_action(
                "archive_node",
                "从画布移除",
                enabled=blocker is None,
                disabled_reason=blocker,
            )
        )
        node["availableActions"] = actions
    if not active_archive_ids:
        return
    canvas["nodes"] = [
        node for node in canvas.get("nodes", []) if str(node["id"]) not in active_archive_ids
    ]
    canvas["edges"] = [
        edge
        for edge in canvas.get("edges", [])
        if str(edge["sourceNodeId"]) not in active_archive_ids
        and str(edge["targetNodeId"]) not in active_archive_ids
    ]
    for group in canvas.get("groups", []):
        group["memberNodeIds"] = [
            node_id
            for node_id in group.get("memberNodeIds", [])
            if str(node_id) not in active_archive_ids
        ]


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


def _generation_batch_json(session: Session, row: MediaGenerationBatch) -> dict[str, Any]:
    candidate_steps = list(
        session.scalars(
            select(WorkflowStep)
            .where(
                WorkflowStep.production_run_id == row.production_run_id,
                WorkflowStep.operation_key.startswith(
                    f"media:{row.media_kind}:batch:{row.id}:candidate:"
                ),
            )
            .order_by(WorkflowStep.created_at, WorkflowStep.id)
        )
    )
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
        "candidateStepIds": [str(step.id) for step in candidate_steps],
        "candidateSteps": [
            {
                "stepId": str(step.id),
                "status": step.status,
                "providerTaskId": step.provider_task_id,
                "error": step.error_json,
            }
            for step in candidate_steps
        ],
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
                "coordinateSpace": "source_normalized",
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
    compiled_references = {
        str(item.get("assetId")): item
        for item in (row.compilation_json or {}).get("actualReferences", [])
        if isinstance(item, dict) and item.get("assetId")
    }
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
                "providerIncluded": compiled_references.get(str(item.asset_id), {}).get(
                    "providerIncluded", item.provider_included
                ),
                "providerSlot": compiled_references.get(str(item.asset_id), {}).get("providerSlot"),
                "omissionReason": compiled_references.get(str(item.asset_id), {}).get(
                    "omissionReason"
                ),
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
            existing = next(item for item in canvas["nodes"] if str(item["id"]) == str(row.id))
            existing["data"] = {**row.data_json, **dict(existing.get("data") or {})}
            existing["status"] = existing.get("status") or row.status
            existing.update(
                _canvas_node_contract(
                    row.node_type,
                    str(existing.get("status") or row.status),
                    existing["data"],
                )
            )
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
        projected_asset = {
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
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "posterUrl": asset.metadata_json.get("posterUrl"),
            },
            "position": {
                "x": stored.get("x", 80 + stage * 320),
                "y": stored.get("y", 80 + row_index * 240),
            },
        }
        projected_asset.update(
            _canvas_node_contract(node_type, asset.status, projected_asset["data"])
        )
        canvas["nodes"].append(projected_asset)
        existing_ids.add(str(asset.id))
    existing_edge_ids = {str(item["id"]) for item in canvas["edges"]}
    for edge in graph_edges:
        source = node_by_id.get(edge.source_node_id)
        target = node_by_id.get(edge.target_node_id)
        if source is None or target is None or str(edge.id) in existing_edge_ids:
            continue
        canvas["edges"].append(_graph_edge_json(edge, source, target))


def _merge_canvas_groups(
    canvas: dict[str, Any],
    *,
    groups: list[CanvasGroup],
    members: list[CanvasGroupMember],
    states: dict[uuid.UUID, dict[str, Any]],
) -> None:
    members_by_group: dict[uuid.UUID, list[str]] = {}
    for member in members:
        members_by_group.setdefault(member.group_id, []).append(str(member.canvas_node_id))
    all_node_ids = [str(node["id"]) for node in canvas["nodes"]]
    projected_groups: list[dict[str, Any]] = []
    for group in groups:
        state = states.get(group.id, {})
        storyboard_ready = bool(state.get("storyboardApproved"))
        complete = state.get("phase") == "complete"
        projected_groups.append(
            {
                "id": str(group.id),
                "projectId": str(group.production_run_id),
                "recipeInstanceId": (
                    None
                    if group.production_recipe_instance_id is None
                    else str(group.production_recipe_instance_id)
                ),
                "parentGroupId": (
                    None if group.parent_group_id is None else str(group.parent_group_id)
                ),
                "type": group.group_type,
                "title": group.title,
                "lifecycleStatus": group.lifecycle_status,
                "color": group.color,
                "revision": group.revision,
                "memberNodeIds": (
                    all_node_ids
                    if group.group_type == "recipe"
                    else members_by_group.get(group.id, [])
                ),
                "phase": state.get("phase"),
                "phaseProgress": list(state.get("phaseProgress", [])),
                "blocker": state.get("blocker"),
                "availableActions": [
                    {
                        "key": "run_group",
                        "label": "整组执行",
                        "enabled": not complete,
                        "disabledReason": "六阶段已全部完成" if complete else None,
                    },
                    {"key": "save_group_template", "label": "添加到工具箱", "enabled": True},
                    {
                        "key": "convert_shot_groups",
                        "label": "转分镜组",
                        "enabled": storyboard_ready,
                        "disabledReason": None if storyboard_ready else "分镜人工批准后才能转换",
                    },
                    {"key": "ungroup", "label": "解组", "enabled": True},
                    {"key": "download_group", "label": "批量下载", "enabled": True},
                ],
                "data": {**group.data_json, **state},
            }
        )
    canvas["groups"] = projected_groups


def _recipe_canvas_group_state(
    session: Session,
    group: CanvasGroup,
) -> dict[str, Any]:
    instance = session.get(
        ProductionRecipeInstance,
        group.production_recipe_instance_id,
    )
    if instance is None:
        return {}
    brief = session.scalar(
        select(StoryBriefRecord)
        .where(StoryBriefRecord.production_run_id == group.production_run_id)
        .order_by(StoryBriefRecord.revision.desc())
        .limit(1)
    )
    creative_decision = (
        None
        if brief is None
        else session.scalar(
            select(HumanReviewDecisionRecord)
            .where(
                HumanReviewDecisionRecord.production_recipe_instance_id == instance.id,
                HumanReviewDecisionRecord.target_type == "creative_brief",
                HumanReviewDecisionRecord.target_id == brief.id,
                HumanReviewDecisionRecord.decision.in_(("approve", "override")),
            )
            .order_by(HumanReviewDecisionRecord.created_at.desc())
            .limit(1)
        )
    )
    story = session.scalar(
        select(StoryRevisionRecord)
        .where(
            StoryRevisionRecord.production_run_id == group.production_run_id,
            StoryRevisionRecord.status == "approved",
        )
        .order_by(StoryRevisionRecord.revision.desc())
        .limit(1)
    )
    character_design = session.scalar(
        select(CharacterDesignRevision)
        .where(CharacterDesignRevision.production_recipe_instance_id == instance.id)
        .order_by(CharacterDesignRevision.revision.desc())
        .limit(1)
    )
    beats = list(
        session.scalars(
            select(ShotBeat)
            .join(Scene, Scene.id == ShotBeat.scene_id)
            .where(
                Scene.production_run_id == group.production_run_id,
                Scene.active.is_(True),
                ShotBeat.status != "superseded",
            )
        )
    )
    storyboard_approved = bool(beats) and all(beat.status == "approved" for beat in beats)
    shot_ids = [beat.shot_card_id for beat in beats if beat.shot_card_id is not None]
    shots = list(session.scalars(select(ShotCard).where(ShotCard.id.in_(shot_ids))))
    render_approved = (
        bool(shots)
        and len(shots) == len(beats)
        and all(
            shot.selected_anchor_asset_id is not None and shot.selected_video_asset_id is not None
            for shot in shots
        )
    )
    sequence = session.scalar(
        select(VideoSequence)
        .where(VideoSequence.production_run_id == group.production_run_id)
        .order_by(VideoSequence.revision.desc())
        .limit(1)
    )
    complete_by_key = {
        "creative": creative_decision is not None,
        "story": story is not None,
        "character_design": bool(
            character_design is not None and character_design.status == "approved"
        ),
        "storyboard": storyboard_approved,
        "render": render_approved,
        "export": bool(sequence is not None and sequence.status == "approved"),
    }
    labels = {
        "creative": "补全创意输入",
        "story": "AI剧情生成",
        "character_design": "角色设计",
        "storyboard": "分镜生成",
        "render": "视频渲染",
        "export": "成品导出",
    }
    first_incomplete = next(
        (key for key, value in complete_by_key.items() if not value),
        "complete",
    )
    return {
        "phase": first_incomplete,
        "storyboardApproved": storyboard_approved,
        "blocker": (
            None
            if first_incomplete == "complete"
            else f"{labels[first_incomplete]}尚未完成并通过人工审核"
        ),
        "phaseProgress": [
            {
                "key": key,
                "label": labels[key],
                "status": (
                    "complete" if complete else "current" if key == first_incomplete else "blocked"
                ),
            }
            for key, complete in complete_by_key.items()
        ],
    }


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
    session: Session,
    subject: Subject,
    revision: SubjectRevision,
    references: tuple[SubjectReference, ...],
) -> dict[str, Any]:
    asset_ids = [item.asset_id for item in references]
    assets_by_id = {
        asset.id: asset for asset in session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
    }
    draft = _subject_draft(subject, revision, references).model_dump(
        mode="json", by_alias=True, exclude={"references"}
    )
    return {
        "id": str(subject.id),
        "projectId": str(subject.production_run_id),
        "revisionId": str(revision.id),
        "revision": revision.revision,
        "status": subject.status,
        "approvalStatus": revision.approval_status,
        **draft,
        "references": [
            {
                "assetId": str(item.asset_id),
                "semanticRole": item.semantic_role,
                "instruction": item.instruction,
                "semanticKey": (
                    None
                    if assets_by_id.get(item.asset_id) is None
                    else assets_by_id[item.asset_id].semantic_key
                ),
                "title": (
                    "视觉参考"
                    if assets_by_id.get(item.asset_id) is None
                    else str(
                        assets_by_id[item.asset_id].metadata_json.get("title")
                        or assets_by_id[item.asset_id].semantic_key
                        or assets_by_id[item.asset_id].role
                    )
                ),
                "contentUrl": f"/api/v1/assets/{item.asset_id}/content",
                "thumbnailUrl": f"/api/v1/assets/{item.asset_id}/content",
                "approvalStatus": (
                    "missing"
                    if assets_by_id.get(item.asset_id) is None
                    else assets_by_id[item.asset_id].status
                ),
                "sha256": (
                    None
                    if assets_by_id.get(item.asset_id) is None
                    else assets_by_id[item.asset_id].sha256
                ),
                "required": bool(
                    assets_by_id.get(item.asset_id) is not None
                    and assets_by_id[item.asset_id].semantic_key
                    in {"person:headshot", "person:fullbody", "cat:front", "cat:side"}
                ),
            }
            for item in references
        ],
    }


def _subject_completion_json(row: SubjectCompletionRun) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "subjectId": str(row.subject_id),
        "sourceRevisionId": str(row.source_revision_id),
        "workflowStepId": (None if row.workflow_step_id is None else str(row.workflow_step_id)),
        "promptId": None if row.prompt_id is None else str(row.prompt_id),
        "status": row.status,
        "model": row.model,
        "missingFields": row.missing_fields_json,
        "proposal": row.proposal_json,
        "acceptedFields": row.accepted_fields_json,
        "acceptedDraft": row.accepted_draft_json,
        "error": row.error_json,
        "createdAt": None if row.created_at is None else row.created_at.isoformat(),
        "completedAt": None if row.completed_at is None else row.completed_at.isoformat(),
    }


def _compiled_reference_binding(
    asset: Asset,
    *,
    role: str,
    purpose: str,
    source: str,
) -> dict[str, Any]:
    metadata = asset.metadata_json or {}
    return {
        "assetId": str(asset.id),
        "role": role,
        "purpose": purpose,
        "source": source,
        "semanticKey": asset.semantic_key,
        "title": str(
            metadata.get("displayName")
            or metadata.get("title")
            or asset.semantic_key
            or asset.role
        ),
        "sha256": asset.sha256,
    }


def _scene_prompt_bindings(
    session: Session,
    scene: Scene,
    profile: VisualProfileRevision,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    accepted_plan_step = session.scalar(
        select(WorkflowStep)
        .where(
            WorkflowStep.production_run_id == scene.production_run_id,
            WorkflowStep.scene_id == scene.id,
            WorkflowStep.operation_key == "director:visual-asset-plan",
            WorkflowStep.status == StepStatus.SUCCEEDED.value,
        )
        .order_by(WorkflowStep.completed_at.desc().nullslast(), WorkflowStep.created_at.desc())
        .limit(1)
    )
    accepted_plan = (
        None
        if accepted_plan_step is None
        else accepted_plan_step.input_snapshot_json.get("acceptedOutput")
    )
    if not isinstance(accepted_plan, dict):
        blockers.append("尚未人工接受当前场景的视觉资产规划")

    look_draft = scene.look_draft_json or {}
    if not look_draft:
        blockers.append("当前场景尚未准备视觉档案与素材引用")
    elif str(look_draft.get("visualProfileRevisionId")) != str(profile.id):
        blockers.append("场景视觉档案已过期，请基于当前本集视觉档案重新准备")

    raw_bindings = look_draft.get("referenceBindings", [])
    if not isinstance(raw_bindings, list):
        raw_bindings = []
        blockers.append("场景参考绑定格式无效")
    bound_ids: list[uuid.UUID] = []
    binding_by_id: dict[uuid.UUID, dict[str, Any]] = {}
    for document in raw_bindings:
        if not isinstance(document, dict) or not document.get("assetId"):
            continue
        try:
            asset_id = uuid.UUID(str(document["assetId"]))
        except ValueError:
            blockers.append("场景参考中包含无效素材标识")
            continue
        bound_ids.append(asset_id)
        binding_by_id[asset_id] = document
    assets_by_id = {
        asset.id: asset
        for asset in session.scalars(select(Asset).where(Asset.id.in_(bound_ids)))
    }
    result: list[dict[str, Any]] = []
    ready_purposes: set[str] = set()
    ready_prop_titles: list[str] = []
    purpose_roles = {
        "wardrobe": "appearance",
        "environment": "environment",
        "prop": "prop",
        "composition": "composition",
    }
    for asset_id in bound_ids:
        document = binding_by_id[asset_id]
        purpose = str(document.get("purpose") or "")
        if purpose not in purpose_roles:
            continue
        asset = assets_by_id.get(asset_id)
        if (
            asset is None
            or asset.production_run_id not in {None, scene.production_run_id}
            or asset.media_type != "image"
            or asset.status not in {"approved", "ready"}
        ):
            blockers.append(f"场景素材 {asset_id} 不可用或尚未批准")
            continue
        ready_purposes.add(purpose)
        binding = _compiled_reference_binding(
            asset,
            role=purpose_roles[purpose],
            purpose=purpose,
            source="scene",
        )
        result.append(binding)
        if purpose == "prop":
            ready_prop_titles.append(str(binding["title"]))

    if "wardrobe" not in ready_purposes:
        blockers.append("缺少已批准并绑定的本集服饰/配件素材")
    if "environment" not in ready_purposes:
        blockers.append("缺少已批准并绑定的当前场景环境素材")

    continuity: dict[str, Any] = {}
    if scene.context_note:
        try:
            context = json.loads(scene.context_note)
        except (TypeError, json.JSONDecodeError):
            context = {}
        if isinstance(context, dict) and isinstance(context.get("continuity"), dict):
            continuity = context["continuity"]
    required_objects: list[str] = []
    for field in ("decorations", "props"):
        values = continuity.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            label = str(value).strip()
            if label and label not in required_objects:
                required_objects.append(label)
    normalized_prop_titles = ["".join(item.lower().split()) for item in ready_prop_titles]
    for label in required_objects:
        normalized_label = "".join(label.lower().split())
        if not any(
            normalized_label in title or title in normalized_label
            for title in normalized_prop_titles
            if title
        ):
            blockers.append(f"缺少已批准并绑定的场景道具/装饰：{label}")
    if not required_objects:
        warnings.append("剧情未声明必需装饰或道具，本镜头仅使用场景环境与文本约束")

    selected_look = (
        None
        if scene.selected_look_asset_id is None
        else session.get(Asset, scene.selected_look_asset_id)
    )
    if selected_look is None:
        blockers.append("尚未选择已批准的场景视觉基准（Scene Look）")
    else:
        look_revision = (selected_look.metadata_json or {}).get("lookDraftRevision")
        if (
            selected_look.production_run_id not in {None, scene.production_run_id}
            or selected_look.media_type != "image"
            or selected_look.status not in {"approved", "ready"}
            or look_revision not in {None, scene.look_draft_revision}
        ):
            blockers.append("已选择的场景视觉基准不可用或已过期")
        elif selected_look.id not in {uuid.UUID(item["assetId"]) for item in result}:
            result.append(
                _compiled_reference_binding(
                    selected_look,
                    role="environment",
                    purpose="scene_look",
                    source="scene",
                )
            )

    return result, list(dict.fromkeys(blockers)), list(dict.fromkeys(warnings))


def _compile_storyboard_prompt_text(
    *,
    profile: VisualProfileRevision,
    story: StoryRevisionRecord,
    scene: Scene,
    shot: dict[str, Any],
    reference_bindings: list[dict[str, Any]],
    healing_recipe: bool,
) -> str:
    episode_rules = story.episode_rules_json or {}
    continuity: dict[str, Any] = {}
    if scene.context_note:
        try:
            context = json.loads(scene.context_note)
        except (TypeError, json.JSONDecodeError):
            context = {}
        if isinstance(context, dict) and isinstance(context.get("continuity"), dict):
            continuity = context["continuity"]
    temporal_beats = shot.get("temporalBeats") or []
    if healing_recipe and not temporal_beats:
        temporal_beats = [
            item.model_dump(mode="json", by_alias=True)
            for item in build_temporal_beats(
                int(shot["durationSeconds"]),
                actions=(
                    (
                        str(shot["action"]),
                        "猫咪以已批准行为模式自然参与",
                        str(shot.get("camera") or "固定机位"),
                    ),
                    (
                        "发生一个微小可见变化",
                        "猫咪对变化做出自然反应",
                        str(shot.get("camera") or "固定机位"),
                    ),
                    (
                        "儿童与猫咪在温暖状态中收尾",
                        "猫咪保持猫科身体结构",
                        str(shot.get("camera") or "固定机位"),
                    ),
                ),
            )
        ]
    reference_manifest = [
        {
            "index": index,
            "role": item["role"],
            "purpose": item["purpose"],
            "semanticKey": item.get("semanticKey"),
            "title": item["title"],
        }
        for index, item in enumerate(reference_bindings, 1)
    ]
    sections = [
        "【全局 Canon：身份与画风不变量】\n"
        f"儿童身份：{profile.person_identity}；发型：{profile.person_hair}；体型比例：{profile.person_body}。\n"
        f"猫咪身份：{profile.cat_identity}。\n"
        f"画风正向：{'、'.join(profile.style_positive_json)}。",
        "【本集造型与规则】\n"
        f"{json.dumps(episode_rules, ensure_ascii=False, sort_keys=True)}\n"
        "已批准的儿童造型、猫咪造型与同框比例图只负责 "
        "appearance/pose/scale/composition，不替换 Canon 身份。",
        "【所属场景】\n"
        f"场景：{scene.title}。剧情语义：{scene.source_text}。\n"
        f"连续性：{json.dumps(continuity, ensure_ascii=False, sort_keys=True)}。\n"
        "只能使用本场景 environment/prop 绑定，禁止串用其他场景素材。",
        "【镜头画面】\n"
        f"镜头 {shot['order']}《{shot['title']}》，时长 {shot['durationSeconds']} 秒，"
        f"景别 {shot.get('shotSize') or '中景'}，动作：{shot['action']}，"
        f"光影：{shot.get('lighting') or '遵循 Scene Look'}，构图与表情遵循已绑定镜头参考。\n"
        f"三个时间节拍：{json.dumps(temporal_beats, ensure_ascii=False, sort_keys=True)}。",
        "【视频运动与声音】\n"
        f"运镜：{shot.get('camera') or '固定机位'}。"
        f"关键动作声与环境声：{shot.get('soundEffect') or '保留当前环境的自然原生声'}。"
        "无对白，不做口型；微表情和动作变化必须遵循时间节拍。",
        "【引用职责审计】\n"
        f"{json.dumps(reference_manifest, ensure_ascii=False, sort_keys=True)}",
        "【排除项】\n"
        + "、".join(
            [
                *profile.style_negative_json,
                "儿童身份漂移或年龄变化",
                "猫咪毛色、脸型或身体结构变化",
                "猫咪出现人手、人形肢体或未批准的直立劳动",
                "画风污染或复制风格参考中的具体物体与构图",
                "跨场景环境与道具串用",
                "额外人物、额外猫咪、文字、水印、对白与口型",
            ]
        ),
    ]
    return "\n\n".join(sections)


def _shot_matches_persisted_beat(shot: dict[str, Any], beat: ShotBeat) -> bool:
    return bool(
        int(shot["order"]) == beat.sort_order
        and str(shot["title"]).strip() == beat.title.strip()
        and str(shot["action"]).strip() == beat.action.strip()
        and str(shot.get("camera") or "").strip() == (beat.camera or "").strip()
        and str(shot.get("dialogue") or "").strip() == (beat.dialogue or "").strip()
        and int(shot["durationSeconds"]) == beat.duration_seconds
    )


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
    is_approved = row.status == StoryRevisionStatus.APPROVED.value
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "briefId": None if row.brief_id is None else str(row.brief_id),
        "revision": row.revision,
        "strategy": row.strategy,
        "status": row.status,
        "artifactLabel": "剧情脚本定稿" if is_approved else "剧情候选",
        "isCanonicalStory": is_approved,
        "title": row.title,
        "logline": row.logline,
        "synopsis": row.synopsis,
        "subjectIds": row.subject_ids_json,
        "scenes": _normalized_story_scenes(row),
        "episodeRules": row.episode_rules_json or None,
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


def _beat_json(row: ShotBeat, prompt: PromptRecord | None = None) -> dict[str, Any]:
    document = {
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
        "temporalBeats": row.temporal_beats_json,
        "status": row.status,
        "staleReason": row.stale_reason,
    }
    if prompt is not None:
        structured = prompt.structured_response_json or {}
        document.update(
            {
                "finalPrompt": prompt.final_prompt or prompt.prompt_text,
                "promptInputHash": prompt.input_hash,
                "referenceBindings": structured.get("referenceBindings", []),
                "promptWarnings": structured.get("warnings", []),
                "promptBlockers": structured.get("blockers", []),
            }
        )
    return document


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
    include_narrative_projection: bool = True,
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
    if include_narrative_projection:
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
                    "data": {
                        "title": "分镜编译",
                        "shotCount": len(beats),
                        "storyboardApproved": bool(beats)
                        and all(
                            beat.status == "approved" and beat.prompt_id is not None
                            for beat in beats
                        ),
                    },
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
                "data": _subject_json(session, subject, revision, references),
            }
        )
        if include_narrative_projection:
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
    approved_story = next(
        (
            story
            for story in stories
            if story.status == StoryRevisionStatus.APPROVED.value
        ),
        None,
    )
    scene_by_id = {scene.id: scene for scene in scenes}
    for scene in scenes:
        scene_data: dict[str, Any] = {
            "title": scene.title,
            "order": scene.sort_order,
            "storyRevisionId": (
                None if scene.story_revision_id is None else str(scene.story_revision_id)
            ),
            "sceneKey": scene.scene_key,
            "active": scene.active,
            "staleReason": scene.stale_reason,
        }
        if scene.context_note:
            try:
                context_document = json.loads(scene.context_note)
            except (TypeError, json.JSONDecodeError):
                context_document = None
            if isinstance(context_document, dict):
                scene_data.update(
                    {
                        "sceneKey": scene.scene_key or context_document.get("sceneKey"),
                        "purpose": context_document.get("purpose"),
                        "continuity": context_document.get("continuity"),
                    }
                )
        nodes.append(
            {
                "id": str(scene.id),
                "type": "SceneNode",
                "objectType": "scene",
                "objectId": str(scene.id),
                "data": scene_data,
            }
        )
        if approved_story is not None:
            edges.append(
                _edge(
                    approved_story.id,
                    "StoryCandidateNode",
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
                "data": _beat_json(
                    beat,
                    None if beat.prompt_id is None else session.get(PromptRecord, beat.prompt_id),
                ),
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
        node.update(
            _canvas_node_contract(
                str(node["type"]),
                str(node.get("status") or node["data"].get("status") or ""),
                node["data"],
            )
        )
        if node["type"] == "StoryPlannerNode" and (brief is None or len(subjects) < 2):
            reason = "请先完成创意简报并准备至少两个叙事主体"
            node["blocker"] = reason
            node["availableActions"][0].update(
                enabled=False,
                execution="unavailable",
                disabledReason=reason,
            )
        if node["type"] == "StoryboardDirectorNode":
            approved = any(story.status == StoryRevisionStatus.APPROVED.value for story in stories)
            if not approved:
                reason = "请先人工批准一个故事版本"
                node["blocker"] = reason
                node["availableActions"][0].update(
                    enabled=False,
                    execution="unavailable",
                    disabledReason=reason,
                )
        if node["type"] == "ShotBeatNode" and not node["data"].get("promptId"):
            reason = "请先在分镜三步工作流中完成服务端分层 Prompt 编译并批准分镜"
            node["blocker"] = reason
            for action in node["availableActions"]:
                if action["key"] == "generate_anchor":
                    action.update(
                        enabled=False,
                        execution="unavailable",
                        disabledReason=reason,
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
