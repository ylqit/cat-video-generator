"""PostgreSQL persistence for production recipe choices and human reviews."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ...domain.aigc_canvas import CanvasNodeType, CanvasPortType, StoryRevisionStatus
from ...domain.production_recipes import (
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    CatBehaviorMode,
    CharacterDesignSlot,
    EpisodeRules,
    HumanReviewDecision,
    HumanReviewDraft,
    ProductionRecipeInstanceDraft,
    ProductionRecipeInstancePatch,
    build_temporal_beats,
    canon_reference_keys,
    recipe_task_source_hash,
)
from ...domain.workflow import StepKind, StepStatus
from .models import (
    Asset,
    CanvasEvent,
    CanvasGraphEdge,
    CanvasGraphNode,
    CanvasGroup,
    CanvasGroupMember,
    CanvasGroupTemplate,
    CharacterDesignAsset,
    CharacterDesignRevision,
    HumanReviewDecisionRecord,
    ProductionRecipeInstance,
    ProductionRun,
    PromptRecord,
    Review,
    Scene,
    ShotBeat,
    ShotCard,
    StoryBriefRecord,
    StoryRevisionRecord,
    StoryScore,
    Subject,
    VideoSequence,
    VisualProfileRevision,
    WorkflowStep,
)
from .repositories import RecordNotFoundError, WorkflowConflictError
from .story_scenes import materialize_approved_story_scenes
from .visual_preset_profiles import (
    CANON_V3_REQUIRED_KEYS,
    ensure_canon_v3_subjects,
    ensure_canon_v3_visual_profile,
    generation_reference_bindings,
    visual_profile_bindings,
    visual_reference_json,
)

_CANON_PROFILE_ID = CANON_V3_PROFILE_ID
_APPROVING_DECISIONS = {
    HumanReviewDecision.APPROVE.value,
    HumanReviewDecision.OVERRIDE.value,
}


class SqlAlchemyProductionRecipeRepository:
    """Owns recipe persistence separately from the general canvas repository."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create_instance(
        self,
        project_id: uuid.UUID,
        payload: ProductionRecipeInstanceDraft,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._required(session, ProductionRun, project_id, lock=True)
            existing = session.scalar(
                select(ProductionRecipeInstance).where(
                    ProductionRecipeInstance.production_run_id == project_id
                )
            )
            if existing is not None:
                raise WorkflowConflictError("项目已经存在创作配方实例")
            node_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(CanvasGraphNode)
                    .where(CanvasGraphNode.production_run_id == project_id)
                )
                or 0
            )
            if node_count:
                raise WorkflowConflictError("项目已经包含业务节点；请复制为空项目后使用组合包")
            self._initialize_fixed_ip_inputs(
                session,
                project_id=project_id,
                theme=payload.theme,
                target_duration_seconds=payload.target_duration_seconds,
                inspiration_key=payload.inspiration_key,
            )
            project.canvas_v2_enabled = True
            project.universal_canvas_enabled = True
            row = ProductionRecipeInstance(
                id=uuid.uuid4(),
                production_run_id=project_id,
                recipe_key=payload.recipe_key.value,
                recipe_version=1,
                revision=1,
                theme=payload.theme,
                inspiration_key=payload.inspiration_key,
                target_duration_seconds=payload.target_duration_seconds,
                quality_tier=payload.quality_tier.value,
                canon_profile_id=_CANON_PROFILE_ID,
            )
            session.add(row)
            session.flush()
            self._initialize_recipe_canvas_group(session, row)
            session.flush()
            return self._instance_json(session, row)

    def enqueue_task(
        self,
        instance_id: uuid.UUID,
        *,
        operation_key: str,
        kind: StepKind,
        payload: dict[str, Any],
        idempotency_key: str,
        expected_phase: str,
        expected_revision: int,
        canvas_node_id: uuid.UUID,
        shot_id: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        creation_mode: str | None = None,
    ) -> dict[str, Any]:
        durable_key = hashlib.sha256(
            f"recipe-task:{instance_id}:{shot_id or '-'}:{operation_key}:{idempotency_key}".encode()
        ).hexdigest()
        with self._sessions.begin() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id)
            existing = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == durable_key)
            )
            if existing is not None:
                return _workflow_task_json(existing)
            snapshot = {
                "payload": payload,
                "projectId": str(instance.production_run_id),
                "shotId": None if shot_id is None else str(shot_id),
                "canvasNodeId": str(canvas_node_id),
                "canvasGroupId": None if group_id is None else str(group_id),
                "recipeInstanceId": str(instance_id),
                "creationMode": creation_mode,
                "workflowStage": expected_phase,
                "phase": expected_phase,
                "operationKey": operation_key,
                "expectedInstanceRevision": expected_revision,
                "sourceContentHash": recipe_task_source_hash(
                    payload=payload,
                    instance_id=instance_id,
                    expected_revision=expected_revision,
                    phase=expected_phase,
                ),
            }
            input_hash = hashlib.sha256(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            attempt = 1
            if shot_id is not None:
                attempt = int(
                    session.scalar(
                        select(func.max(WorkflowStep.attempt)).where(
                            WorkflowStep.shot_card_id == shot_id,
                            WorkflowStep.operation_key == operation_key,
                        )
                    )
                    or 0
                ) + 1
            row = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=instance.production_run_id,
                shot_card_id=shot_id,
                kind=kind.value,
                status=StepStatus.PENDING.value,
                attempt=attempt,
                operation_key=operation_key,
                idempotency_key=durable_key,
                input_hash=input_hash,
                input_snapshot_json=snapshot,
                progress_json={
                    "currentStep": 0,
                    "totalSteps": 3,
                    "percent": 0,
                    "message": "任务已进入持久队列",
                },
            )
            session.add(row)
            session.flush()
            session.add(
                CanvasEvent(
                    production_run_id=instance.production_run_id,
                    event_type="task_queued",
                    data_json={
                        **{key: snapshot.get(key) for key in (
                            "projectId",
                            "shotId",
                            "canvasNodeId",
                            "canvasGroupId",
                            "recipeInstanceId",
                            "creationMode",
                            "phase",
                        )},
                        "stepId": str(row.id),
                        "status": row.status,
                        "operationKey": operation_key,
                        "kind": row.kind,
                        "progress": row.progress_json,
                    },
                )
            )
            return _workflow_task_json(row)

    def record_task_children(
        self,
        parent_step_id: uuid.UUID,
        child_step_ids: tuple[uuid.UUID, ...],
    ) -> tuple[dict[str, Any], ...]:
        ordered_ids = tuple(dict.fromkeys(child_step_ids))
        if not ordered_ids:
            return ()
        if parent_step_id in ordered_ids:
            raise WorkflowConflictError("任务不能将自身登记为子任务")
        with self._sessions.begin() as session:
            parent = self._required(session, WorkflowStep, parent_step_id, lock=True)
            children = list(
                session.scalars(
                    select(WorkflowStep)
                    .where(WorkflowStep.id.in_(ordered_ids))
                    .with_for_update()
                )
            )
            by_id = {child.id: child for child in children}
            missing = [step_id for step_id in ordered_ids if step_id not in by_id]
            if missing:
                raise RecordNotFoundError(
                    f"父任务引用了不存在的子任务：{', '.join(str(item) for item in missing)}"
                )
            if any(child.production_run_id != parent.production_run_id for child in children):
                raise WorkflowConflictError("父子任务必须属于同一个项目")
            for child in children:
                child.input_snapshot_json = {
                    **dict(child.input_snapshot_json or {}),
                    "parentStepId": str(parent.id),
                }
            parent.progress_json = {
                **dict(parent.progress_json or {}),
                "childStepIds": [str(step_id) for step_id in ordered_ids],
            }
            return tuple(
                {
                    "stepId": str(step_id),
                    "status": by_id[step_id].status,
                    "providerTaskId": by_id[step_id].provider_task_id,
                    "error": by_id[step_id].error_json,
                }
                for step_id in ordered_ids
            )

    @staticmethod
    def _initialize_recipe_canvas_group(
        session: Session,
        instance: ProductionRecipeInstance,
    ) -> None:
        project_id = instance.production_run_id
        brief = session.scalar(
            select(StoryBriefRecord)
            .where(StoryBriefRecord.production_run_id == project_id)
            .order_by(StoryBriefRecord.revision.desc())
            .limit(1)
        )
        if brief is None:
            raise RecordNotFoundError("组合包创作简报不存在")
        subjects = list(
            session.scalars(
                select(Subject)
                .where(Subject.production_run_id == project_id)
                .order_by(Subject.created_at)
            )
        )
        if len(subjects) != 2:
            raise WorkflowConflictError("一人一猫组合包必须初始化两个固定主体")

        ids = {
            "creative_gate": uuid.uuid5(project_id, "creative-brief-approval"),
            "planner": uuid.uuid5(project_id, "story-planner"),
            "story_gate": uuid.uuid5(project_id, "story-approval"),
            "style_preset": uuid.uuid5(project_id, "style-preset:line-texture"),
            "child_design": uuid.uuid5(project_id, "character-design:child"),
            "cat_design": uuid.uuid5(project_id, "character-design:cat"),
            "pair_design": uuid.uuid5(project_id, "character-design:pair-scale"),
            "character_gate": uuid.uuid5(project_id, "character-design-approval"),
            "storyboard": uuid.uuid5(project_id, "storyboard-director"),
            "anchors": uuid.uuid5(project_id, "recipe-anchor-stage"),
            "videos": uuid.uuid5(project_id, "recipe-video-stage"),
            "video_review": uuid.uuid5(project_id, "recipe-video-review"),
            "timeline": uuid.uuid5(project_id, "recipe-sequence"),
        }
        subject_by_kind = {subject.kind: subject for subject in subjects}
        child = subject_by_kind.get("person")
        cat = subject_by_kind.get("animal")
        if child is None or cat is None:
            raise WorkflowConflictError("一人一猫组合包缺少固定儿童或固定猫咪")

        node_specs = [
            (brief.id, CanvasNodeType.BRIEF, "story_brief", brief.id, "待确认创意简报", "creative"),
            (
                ids["creative_gate"],
                CanvasNodeType.APPROVAL_GATE,
                "creative_brief_approval",
                brief.id,
                "确认创意输入",
                "creative",
            ),
            (child.id, CanvasNodeType.SUBJECT, "subject", child.id, "固定儿童", "character_design"),
            (cat.id, CanvasNodeType.SUBJECT, "subject", cat.id, "固定猫咪", "character_design"),
            (
                ids["style_preset"],
                CanvasNodeType.STYLE_PRESET,
                "visual_preset",
                None,
                "线条材质",
                "character_design",
            ),
            (
                ids["planner"],
                CanvasNodeType.STORY_PLANNER,
                "story_planner",
                project_id,
                "AI 剧情生成",
                "story",
            ),
            (
                ids["story_gate"],
                CanvasNodeType.APPROVAL_GATE,
                "story_approval",
                project_id,
                "人工剧情定稿",
                "story",
            ),
            (
                ids["child_design"],
                CanvasNodeType.CHARACTER_DESIGN,
                "character_design_slot",
                None,
                "儿童本集造型图",
                "character_design",
            ),
            (
                ids["cat_design"],
                CanvasNodeType.CHARACTER_DESIGN,
                "character_design_slot",
                None,
                "猫咪本集造型图",
                "character_design",
            ),
            (
                ids["pair_design"],
                CanvasNodeType.CHARACTER_DESIGN,
                "character_design_slot",
                None,
                "一人一猫同框比例图",
                "character_design",
            ),
            (
                ids["character_gate"],
                CanvasNodeType.APPROVAL_GATE,
                "character_design_approval",
                project_id,
                "角色设计审核",
                "character_design",
            ),
            (
                ids["storyboard"],
                CanvasNodeType.STORYBOARD_DIRECTOR,
                "storyboard_director",
                project_id,
                "分镜生成",
                "storyboard",
            ),
            (
                ids["anchors"],
                CanvasNodeType.IMAGE_GENERATION,
                "recipe_anchor_stage",
                instance.id,
                "视觉锚点",
                "render",
            ),
            (
                ids["videos"],
                CanvasNodeType.VIDEO_GENERATION,
                "recipe_video_stage",
                instance.id,
                "逐镜视频渲染",
                "render",
            ),
            (
                ids["video_review"],
                CanvasNodeType.REVIEW,
                "recipe_video_review",
                instance.id,
                "逐镜视频审核",
                "render",
            ),
            (
                ids["timeline"],
                CanvasNodeType.TIMELINE,
                "recipe_sequence",
                instance.id,
                "成品导出",
                "export",
            ),
        ]
        slot_by_node = {
            ids["child_design"]: CharacterDesignSlot.CHILD.value,
            ids["cat_design"]: CharacterDesignSlot.CAT.value,
            ids["pair_design"]: CharacterDesignSlot.PAIR_SCALE.value,
        }
        graph_nodes: dict[uuid.UUID, CanvasGraphNode] = {}
        for node_id, node_type, object_type, object_id, title, phase in node_specs:
            node = session.get(CanvasGraphNode, node_id)
            data = {
                "title": title,
                "recipeInstanceId": str(instance.id),
                "phase": phase,
            }
            if node_id in slot_by_node:
                data["slot"] = slot_by_node[node_id]
            if node_id == ids["style_preset"]:
                style_asset = session.scalar(
                    select(Asset).where(
                        Asset.scope == "canon",
                        Asset.status.in_(("ready", "approved")),
                        Asset.semantic_key == "style:line_texture",
                    )
                )
                if style_asset is None:
                    raise WorkflowConflictError("Canon-v3 缺少线条材质参考")
                data.update(
                    {
                        "presetKey": "healing_child_cat_line_texture_v3",
                        "canonProfileId": CANON_V3_PROFILE_ID,
                        "references": [visual_reference_json(style_asset, required=True)],
                        "stylePositive": list(CANON_V3_STYLE_POSITIVE),
                        "styleExcluded": list(CANON_V3_STYLE_NEGATIVE),
                        "locked": True,
                    }
                )
            if node is None:
                node = CanvasGraphNode(
                    id=node_id,
                    production_run_id=project_id,
                    node_type=node_type.value,
                    object_type=object_type,
                    object_id=object_id,
                    status="ready" if phase in {"creative", "story"} else "blocked",
                    data_json=data,
                )
                session.add(node)
            graph_nodes[node_id] = node
        session.flush()

        group = CanvasGroup(
            id=uuid.uuid5(project_id, f"canvas-group:{instance.id}"),
            production_run_id=project_id,
            production_recipe_instance_id=instance.id,
            group_type="recipe",
            title="一人一猫治愈短片",
            lifecycle_status="active",
            color="#7c9cff",
            data_json={"recipeKey": instance.recipe_key, "sixStageWorkflow": True},
        )
        session.add(group)
        session.flush()
        for sort_order, node in enumerate(graph_nodes.values(), 1):
            session.add(
                CanvasGroupMember(
                    id=uuid.uuid5(group.id, f"member:{node.id}"),
                    group_id=group.id,
                    canvas_node_id=node.id,
                    sort_order=sort_order,
                )
            )

        edge_specs = [
            (
                brief.id,
                CanvasPortType.BRIEF,
                ids["creative_gate"],
                CanvasPortType.BRIEF,
                "creative_review",
            ),
            (
                ids["creative_gate"],
                CanvasPortType.BRIEF,
                ids["planner"],
                CanvasPortType.BRIEF,
                "approved_input",
            ),
            (
                child.id,
                CanvasPortType.SUBJECTS,
                ids["planner"],
                CanvasPortType.SUBJECTS,
                "story_subject",
            ),
            (
                cat.id,
                CanvasPortType.SUBJECTS,
                ids["planner"],
                CanvasPortType.SUBJECTS,
                "story_subject",
            ),
            (
                child.id,
                CanvasPortType.SUBJECTS,
                ids["child_design"],
                CanvasPortType.SUBJECTS,
                "identity_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["child_design"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["cat_design"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["pair_design"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["storyboard"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["anchors"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                cat.id,
                CanvasPortType.SUBJECTS,
                ids["cat_design"],
                CanvasPortType.SUBJECTS,
                "identity_source",
            ),
            (
                child.id,
                CanvasPortType.SUBJECTS,
                ids["pair_design"],
                CanvasPortType.SUBJECTS,
                "identity_source",
            ),
            (
                cat.id,
                CanvasPortType.SUBJECTS,
                ids["pair_design"],
                CanvasPortType.SUBJECTS,
                "identity_source",
            ),
            (
                ids["story_gate"],
                CanvasPortType.STORY_REVISION,
                ids["child_design"],
                CanvasPortType.STORY_REVISION,
                "approved_story",
            ),
            (
                ids["story_gate"],
                CanvasPortType.STORY_REVISION,
                ids["cat_design"],
                CanvasPortType.STORY_REVISION,
                "approved_story",
            ),
            (
                ids["story_gate"],
                CanvasPortType.STORY_REVISION,
                ids["pair_design"],
                CanvasPortType.STORY_REVISION,
                "approved_story",
            ),
            (
                ids["child_design"],
                CanvasPortType.CHARACTER_DESIGN,
                ids["character_gate"],
                CanvasPortType.CHARACTER_DESIGN,
                "design_review",
            ),
            (
                ids["cat_design"],
                CanvasPortType.CHARACTER_DESIGN,
                ids["character_gate"],
                CanvasPortType.CHARACTER_DESIGN,
                "design_review",
            ),
            (
                ids["pair_design"],
                CanvasPortType.CHARACTER_DESIGN,
                ids["character_gate"],
                CanvasPortType.CHARACTER_DESIGN,
                "design_review",
            ),
            (
                ids["character_gate"],
                CanvasPortType.CHARACTER_DESIGN,
                ids["storyboard"],
                CanvasPortType.CHARACTER_DESIGN,
                "approved_design",
            ),
            (
                ids["storyboard"],
                CanvasPortType.SCENE_PLAN,
                ids["anchors"],
                CanvasPortType.SHOT_BEATS,
                "storyboard_to_anchor",
            ),
            (
                ids["anchors"],
                CanvasPortType.IMAGE_ASSET,
                ids["videos"],
                CanvasPortType.IMAGE_ASSET,
                "anchor_to_video",
            ),
            (
                ids["videos"],
                CanvasPortType.VIDEO_ASSET,
                ids["video_review"],
                CanvasPortType.VIDEO_ASSET,
                "video_review",
            ),
            (
                ids["video_review"],
                CanvasPortType.APPROVED_ASSET,
                ids["timeline"],
                CanvasPortType.APPROVED_ASSET,
                "video_to_sequence",
            ),
        ]
        for source_id, source_port, target_id, target_port, relation_type in edge_specs:
            session.add(
                CanvasGraphEdge(
                    id=uuid.uuid5(
                        source_id, f"{source_port.value}:{target_id}:{target_port.value}"
                    ),
                    production_run_id=project_id,
                    source_node_id=source_id,
                    source_port=source_port.value,
                    target_node_id=target_id,
                    target_port=target_port.value,
                    relation_type=relation_type,
                    revision=1,
                )
            )

    @staticmethod
    def _initialize_fixed_ip_inputs(
        session: Session,
        *,
        project_id: uuid.UUID,
        theme: str,
        target_duration_seconds: int,
        inspiration_key: str | None,
    ) -> None:
        required_keys = CANON_V3_REQUIRED_KEYS
        assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.scope == "canon",
                    Asset.status.in_(("ready", "approved")),
                    Asset.semantic_key.in_(required_keys),
                )
            )
        )
        assets_by_key = {asset.semantic_key: asset for asset in assets}
        missing = [key for key in required_keys if key not in assets_by_key]
        if missing:
            raise ValueError(f"创建组合包前请补齐 Canon-v3 参考：{', '.join(missing)}")

        session.add(
            StoryBriefRecord(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=1,
                theme=theme,
                audience="喜欢低压力、治愈日常内容的竖屏短视频观众",
                genre="原创一人一猫治愈日常",
                tone="安静、温暖、克制、有生活细节",
                aspect_ratio="9:16",
                target_duration_seconds=target_duration_seconds,
                constraints_json=[
                    "固定儿童与固定猫咪身份",
                    "原创二维治愈数字插画，统一线条、材质与光线",
                    "无对白",
                    "原生环境声、动作声与轻音乐",
                    "每镜包含开始、小变化、温暖收尾三个时间节拍",
                    *([] if inspiration_key is None else [f"灵感卡：{inspiration_key}"]),
                ],
            )
        )
        ensure_canon_v3_subjects(
            session,
            project_id=project_id,
            assets_by_key=assets_by_key,
        )
        ensure_canon_v3_visual_profile(
            session,
            project_id=project_id,
            assets_by_key=assets_by_key,
        )

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            row = self._required(session, ProductionRecipeInstance, instance_id)
            return self._instance_json(session, row)

    def validate_storyboard_character_references(
        self,
        instance_id: uuid.UUID,
        reference_asset_ids: tuple[uuid.UUID, ...],
    ) -> None:
        """Enforce that character-led storyboards use the approved child and cat slots."""

        with self._sessions() as session:
            self._required(session, ProductionRecipeInstance, instance_id)
            revision = session.scalar(
                select(CharacterDesignRevision)
                .where(
                    CharacterDesignRevision.production_recipe_instance_id == instance_id,
                    CharacterDesignRevision.status == "approved",
                )
                .order_by(CharacterDesignRevision.revision.desc())
                .limit(1)
            )
            if revision is None:
                raise WorkflowConflictError(
                    "基于固定角色补充分镜需要先批准本集儿童、猫咪和同框比例设计"
                )

            selected_assets = list(
                session.scalars(
                    select(CharacterDesignAsset).where(
                        CharacterDesignAsset.character_design_revision_id == revision.id,
                        CharacterDesignAsset.selected.is_(True),
                    )
                )
            )
            selected_by_id = {asset.asset_id: asset for asset in selected_assets}
            provided_ids = set(reference_asset_ids)
            unapproved_ids = provided_ids.difference(selected_by_id)
            if unapproved_ids:
                raise WorkflowConflictError(
                    "基于固定角色补充分镜只能引用当前已批准的角色设计素材，"
                    "普通参考图不能替代 Canon 身份"
                )

            provided_slots = {
                selected_by_id[asset_id].slot
                for asset_id in provided_ids
                if asset_id in selected_by_id
            }
            required_slots = {
                CharacterDesignSlot.CHILD.value,
                CharacterDesignSlot.CAT.value,
            }
            missing_slots = required_slots.difference(provided_slots)
            if missing_slots:
                missing_labels = [
                    label
                    for slot, label in (
                        (CharacterDesignSlot.CHILD.value, "儿童"),
                        (CharacterDesignSlot.CAT.value, "猫咪"),
                    )
                    if slot in missing_slots
                ]
                raise WorkflowConflictError(
                    f"基于固定角色补充分镜必须同时包含已批准的{'和'.join(missing_labels)}角色设计素材"
                )

    def validate_anchor_prompt_readiness(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
    ) -> None:
        """Reject anchor generation when its audited layered prompt is absent or stale."""

        with self._sessions() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id)
            shot = self._required(session, ShotCard, shot_id)
            scene = self._required(session, Scene, shot.scene_id)
            if scene.production_run_id != instance.production_run_id or not scene.active:
                raise WorkflowConflictError("镜头所属场景已过期，请重新生成分镜")
            beat = session.scalar(
                select(ShotBeat).where(
                    ShotBeat.shot_card_id == shot.id,
                    ShotBeat.scene_id == scene.id,
                    ShotBeat.status == "approved",
                )
            )
            if beat is None or beat.prompt_id is None:
                raise WorkflowConflictError(
                    "镜头尚未完成服务端分层 Prompt 编译和分镜人工批准"
                )
            prompt = self._required(session, PromptRecord, beat.prompt_id)
            snapshot = dict(prompt.input_snapshot_json or {})
            project = self._required(session, ProductionRun, instance.production_run_id)
            if (
                prompt.status != "succeeded"
                or prompt.call_purpose != "storyboard_prompt_compilation"
                or str(snapshot.get("storyRevisionId")) != str(beat.story_revision_id)
                or str(snapshot.get("visualProfileRevisionId"))
                != str(project.current_visual_profile_revision_id)
                or int(snapshot.get("sceneLookDraftRevision") or 0)
                != scene.look_draft_revision
            ):
                raise WorkflowConflictError(
                    "镜头 Prompt 已因剧情、视觉档案或场景资产更新而过期，请重新编译"
                )

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            row = self._required(session, ProductionRecipeInstance, instance_id, lock=True)
            if row.revision != expected_revision:
                raise WorkflowConflictError(
                    f"配方实例版本冲突：当前 {row.revision}，提交 {expected_revision}"
                )
            changed = payload.model_dump(exclude_unset=True)
            semantic_changed = any(
                (
                    key == "theme"
                    and row.theme != value
                    or key == "inspiration_key"
                    and row.inspiration_key != value
                    or key == "target_duration_seconds"
                    and row.target_duration_seconds != value
                )
                for key, value in changed.items()
            )
            if "theme" in changed:
                row.theme = changed["theme"]
            if "inspiration_key" in changed:
                row.inspiration_key = changed["inspiration_key"]
            if "target_duration_seconds" in changed:
                row.target_duration_seconds = changed["target_duration_seconds"]
            if "quality_tier" in changed:
                row.quality_tier = changed["quality_tier"].value
            row.revision += 1
            if semantic_changed:
                latest_brief = session.scalar(
                    select(StoryBriefRecord)
                    .where(StoryBriefRecord.production_run_id == row.production_run_id)
                    .order_by(StoryBriefRecord.revision.desc())
                    .limit(1)
                )
                if latest_brief is None:
                    raise RecordNotFoundError("组合包创作简报不存在")
                constraints = [
                    item
                    for item in latest_brief.constraints_json
                    if not item.startswith("灵感卡：")
                ]
                if row.inspiration_key:
                    constraints.append(f"灵感卡：{row.inspiration_key}")
                session.add(
                    StoryBriefRecord(
                        id=uuid.uuid4(),
                        production_run_id=row.production_run_id,
                        revision=latest_brief.revision + 1,
                        theme=row.theme,
                        audience=latest_brief.audience,
                        genre=latest_brief.genre,
                        tone=latest_brief.tone,
                        aspect_ratio=latest_brief.aspect_ratio,
                        target_duration_seconds=row.target_duration_seconds,
                        constraints_json=constraints,
                    )
                )
                self._invalidate_downstream(session, row.production_run_id, row.revision)
            session.flush()
            return self._instance_json(session, row)

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            instance = self._required(
                session,
                ProductionRecipeInstance,
                instance_id,
                lock=True,
            )
            target = self._review_target(session, payload.target_type, payload.target_id)
            self._validate_target_snapshot(payload, target)
            if target["project_id"] != instance.production_run_id:
                raise ValueError("审核目标不属于当前配方项目")
            target_row = target["row"]
            if isinstance(target_row, StoryBriefRecord):
                latest_brief = session.scalar(
                    select(StoryBriefRecord)
                    .where(StoryBriefRecord.production_run_id == instance.production_run_id)
                    .order_by(StoryBriefRecord.revision.desc())
                    .limit(1)
                )
                if latest_brief is None or latest_brief.id != target_row.id:
                    raise WorkflowConflictError("只能审核当前最新创意简报版本")
                if target_row.revision < 2:
                    raise WorkflowConflictError("一句话创意尚未完成 AI 补全")
            server_blocking = False
            if isinstance(target_row, Asset):
                if target_row.status == "stale":
                    raise WorkflowConflictError("过期资产不能审核，请选择当前版本")
                if payload.target_type == "character_design":
                    binding = session.scalar(
                        select(CharacterDesignAsset).where(
                            CharacterDesignAsset.asset_id == target_row.id
                        )
                    )
                    if binding is None or target_row.media_type != "image":
                        raise ValueError("角色设计审核目标必须是当前角色图片候选")
                if payload.target_type == "anchor_asset" and (
                    target_row.media_type != "image" or target_row.role != "shot_anchor"
                ):
                    raise ValueError("视觉锚点审核目标必须是图片")
                if payload.target_type == "video_asset" and (
                    target_row.media_type != "video"
                    or target_row.role not in {"shot_video", "shot_video_edit"}
                ):
                    raise ValueError("视频镜头审核目标必须是视频")
                if target_row.role in {"shot_anchor", "shot_video", "shot_video_edit"}:
                    diagnostic_reviews = list(
                        session.scalars(
                            select(Review).where(
                                Review.asset_id == target_row.id,
                                Review.source == "ark_visual",
                            )
                        )
                    )
                    server_blocking = not diagnostic_reviews or any(
                        review.warnings_json for review in diagnostic_reviews
                    )
            if payload.decision is HumanReviewDecision.APPROVE and server_blocking:
                raise ValueError("专项语义诊断缺失或失败，必须修改或填写理由人工覆盖")
            effective_payload = payload.model_copy(
                update={
                    "blocking_diagnostic_present": (
                        payload.blocking_diagnostic_present or server_blocking
                    )
                }
            )
            row = HumanReviewDecisionRecord(
                id=uuid.uuid4(),
                production_recipe_instance_id=instance.id,
                production_run_id=instance.production_run_id,
                target_type=effective_payload.target_type,
                target_id=effective_payload.target_id,
                target_revision=effective_payload.target_revision,
                target_hash=effective_payload.target_hash,
                decision=effective_payload.decision.value,
                blocking_diagnostic_present=effective_payload.blocking_diagnostic_present,
                issues_json=effective_payload.issues,
                reason=effective_payload.reason,
            )
            session.add(row)
            if (
                isinstance(target_row, StoryRevisionRecord)
                and effective_payload.target_type in {"story_revision", "episode_rules"}
                and effective_payload.decision.value in _APPROVING_DECISIONS
            ):
                previous_approved = list(
                    session.scalars(
                        select(StoryRevisionRecord).where(
                            StoryRevisionRecord.production_run_id == instance.production_run_id,
                            StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                            StoryRevisionRecord.id != target_row.id,
                        )
                    )
                )
                if previous_approved:
                    self._invalidate_media_after_upstream_change(
                        session,
                        instance.production_run_id,
                        "故事或 EpisodeRules 已批准新版本",
                    )
                locked_rules = episode_rules or (
                    EpisodeRules.model_validate(target_row.episode_rules_json)
                    if target_row.episode_rules_json
                    else None
                )
                if locked_rules is None:
                    raise ValueError("批准配方故事时必须同时锁定 EpisodeRules")
                target_row.episode_rules_json = locked_rules.model_dump(mode="json", by_alias=True)
                self._lock_canon_profile(
                    session,
                    project_id=instance.production_run_id,
                    rules=locked_rules,
                )
                for previous in previous_approved:
                    previous.status = StoryRevisionStatus.SUPERSEDED.value
            self._apply_review_to_target(session, effective_payload, target_row)
            if (
                isinstance(target_row, StoryRevisionRecord)
                and effective_payload.target_type in {"story_revision", "episode_rules"}
                and effective_payload.decision.value in _APPROVING_DECISIONS
            ):
                materialize_approved_story_scenes(session, target_row)
            session.flush()
            return _review_json(row)

    def materialize_storyboard(
        self,
        instance_id: uuid.UUID,
        storyboard: dict[str, Any],
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            instance = self._required(
                session,
                ProductionRecipeInstance,
                instance_id,
                lock=True,
            )
            story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == instance.production_run_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
            )
            if story is None or not story.episode_rules_json:
                raise WorkflowConflictError("故事与 EpisodeRules 尚未锁定")
            character_design = session.scalar(
                select(CharacterDesignRevision)
                .where(
                    CharacterDesignRevision.production_recipe_instance_id == instance.id,
                    CharacterDesignRevision.status == "approved",
                )
                .order_by(CharacterDesignRevision.revision.desc())
                .limit(1)
            )
            if character_design is None:
                raise WorkflowConflictError("儿童、猫咪与同框比例角色设计尚未全部批准")
            rules = EpisodeRules.model_validate(story.episode_rules_json)
            materialized: list[dict[str, Any]] = []
            beat_documents = list(storyboard.get("beats") or [])
            beats = [
                self._required(session, ShotBeat, uuid.UUID(str(document["id"])), lock=True)
                for document in beat_documents
            ]
            scene_ids = {beat.scene_id for beat in beats}
            if scene_ids:
                existing_shot_ids = select(ShotCard.id).where(ShotCard.scene_id.in_(scene_ids))
                if session.scalar(
                    select(func.count())
                    .select_from(WorkflowStep)
                    .where(WorkflowStep.shot_card_id.in_(existing_shot_ids))
                ):
                    raise WorkflowConflictError("现有镜头已有付费历史，不能重建配方分镜")
                session.execute(delete(ShotCard).where(ShotCard.scene_id.in_(scene_ids)))
            order_by_scene: dict[uuid.UUID, int] = {}
            scene_contexts = {
                scene_id: _scene_continuity_document(
                    self._required(session, Scene, scene_id)
                )
                for scene_id in scene_ids
            }
            for beat, document in zip(beats, beat_documents, strict=True):
                order_by_scene[beat.scene_id] = order_by_scene.get(beat.scene_id, 0) + 1
                order = order_by_scene[beat.scene_id]
                cat_actions = _cat_beat_actions(rules.cat_behavior_mode)
                temporal_beats = build_temporal_beats(
                    beat.duration_seconds,
                    actions=(
                        (f"开始：{beat.action}", cat_actions[0], beat.camera or "固定中景"),
                        (f"变化：{beat.action}", cat_actions[1], beat.camera or "轻缓跟随"),
                        (f"收尾：{beat.action}", cat_actions[2], beat.camera or "温暖近景"),
                    ),
                )
                shot = ShotCard(
                    id=uuid.uuid4(),
                    scene_id=beat.scene_id,
                    sort_order=order,
                    title=beat.title[:100],
                    direction=_healing_shot_direction(
                        beat,
                        temporal_beats,
                        rules,
                        scene_contexts.get(beat.scene_id, {}),
                    ),
                    duration_seconds=beat.duration_seconds,
                    anchor_mode="generate",
                    reference_bindings_json=[],
                    inherit_project_references=True,
                    use_scene_look=True,
                    scene_look_usage="appearance_only",
                    draft_revision=1,
                    status="ready",
                )
                session.add(shot)
                session.flush()
                beat.shot_card_id = shot.id
                beat.temporal_beats_json = [
                    item.model_dump(mode="json", by_alias=True) for item in temporal_beats
                ]
                document = {
                    **document,
                    "shotId": str(shot.id),
                    "temporalBeats": beat.temporal_beats_json,
                }
                materialized.append(document)
            for scene_id, count in order_by_scene.items():
                scene = self._required(session, Scene, scene_id, lock=True)
                scene.story_mode = "single" if count == 1 else "multi"
                scene.target_shot_count = count
            return {**storyboard, "beats": materialized}

    def store_suggested_episode_rules(
        self,
        instance_id: uuid.UUID,
        candidate_ids: tuple[uuid.UUID, ...],
        rules: EpisodeRules,
    ) -> None:
        with self._sessions.begin() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id)
            rows = list(
                session.scalars(
                    select(StoryRevisionRecord).where(
                        StoryRevisionRecord.id.in_(candidate_ids),
                        StoryRevisionRecord.production_run_id == instance.production_run_id,
                    )
                )
            )
            if len(rows) != len(candidate_ids):
                raise WorkflowConflictError("故事候选与当前配方实例不一致")
            document = rules.model_dump(mode="json", by_alias=True)
            for row in rows:
                row.episode_rules_json = document

    def prepare_character_design(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
        candidate_count: int,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id, lock=True)
            if instance.lifecycle_status != "active":
                raise WorkflowConflictError("已归档配方不能生成角色设计")
            story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == instance.production_run_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
            )
            if story is None or not story.episode_rules_json:
                raise WorkflowConflictError("故事与 EpisodeRules 尚未人工批准")
            existing = session.scalar(
                select(CharacterDesignRevision).where(
                    CharacterDesignRevision.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                if existing.production_recipe_instance_id != instance.id:
                    raise WorkflowConflictError("角色设计幂等键已用于其他配方")
                return self._character_design_run_json(
                    session,
                    instance,
                    existing,
                    candidate_count=candidate_count,
                    prepare_nodes=False,
                )

            previous = list(
                session.scalars(
                    select(CharacterDesignRevision).where(
                        CharacterDesignRevision.production_recipe_instance_id == instance.id,
                        CharacterDesignRevision.status != "stale",
                    )
                )
            )
            for revision in previous:
                revision.status = "stale"
                bound_asset_ids = select(CharacterDesignAsset.asset_id).where(
                    CharacterDesignAsset.character_design_revision_id == revision.id
                )
                session.execute(
                    update(Asset)
                    .where(Asset.id.in_(bound_asset_ids), Asset.status != "stale")
                    .values(status="stale")
                )
            next_revision = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(CharacterDesignRevision.revision), 0)).where(
                            CharacterDesignRevision.production_recipe_instance_id == instance.id
                        )
                    )
                    or 0
                )
                + 1
            )
            revision = CharacterDesignRevision(
                id=uuid.uuid4(),
                production_recipe_instance_id=instance.id,
                production_run_id=instance.production_run_id,
                source_story_revision_id=story.id,
                revision=next_revision,
                idempotency_key=idempotency_key,
                status="generating",
            )
            session.add(revision)
            session.flush()
            return self._character_design_run_json(
                session, instance, revision, candidate_count=candidate_count
            )

    @staticmethod
    def _character_design_run_json(
        session: Session,
        instance: ProductionRecipeInstance,
        revision: CharacterDesignRevision,
        *,
        candidate_count: int,
        prepare_nodes: bool = True,
    ) -> dict[str, Any]:
        story = session.get(StoryRevisionRecord, revision.source_story_revision_id)
        if story is None or not story.episode_rules_json:
            raise WorkflowConflictError("角色设计来源故事不存在或规则未锁定")
        rules = EpisodeRules.model_validate(story.episode_rules_json)
        keys = canon_reference_keys(instance.canon_profile_id, rules.environment)
        canon_assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.scope == "canon",
                    Asset.status.in_(("ready", "approved")),
                    Asset.semantic_key.in_(keys),
                )
            )
        )
        by_key = {asset.semantic_key: asset for asset in canon_assets}
        missing = [key for key in keys if key not in by_key]
        if missing:
            raise WorkflowConflictError(f"角色设计缺少 Canon 参考：{', '.join(missing)}")
        project_id = instance.production_run_id
        nodes = {
            CharacterDesignSlot.CHILD: uuid.uuid5(project_id, "character-design:child"),
            CharacterDesignSlot.CAT: uuid.uuid5(project_id, "character-design:cat"),
            CharacterDesignSlot.PAIR_SCALE: uuid.uuid5(project_id, "character-design:pair-scale"),
        }
        references = {
            CharacterDesignSlot.CHILD: ("person:headshot", "person:fullbody", keys[-1]),
            CharacterDesignSlot.CAT: ("cat:front", "cat:side", keys[-1]),
            CharacterDesignSlot.PAIR_SCALE: keys,
        }
        prompts = {
            CharacterDesignSlot.CHILD: (
                "生成固定儿童的本集二维水彩造型设计图。必须保持 Canon 脸部、年龄、"
                "短发和儿童身体比例；展示全身服装、关键表情与动作姿态。"
            ),
            CharacterDesignSlot.CAT: (
                "生成固定猫咪的本集二维水彩造型设计图。必须保持 Canon 脸部、毛色分区、"
                "体型与环纹尾巴；保持四足猫科结构，展示关键姿态和允许配件。"
            ),
            CharacterDesignSlot.PAIR_SCALE: (
                "生成固定儿童与固定猫咪的一人一猫同框比例设计图。必须同时保持两个 Canon "
                "身份，展示稳定身高比例、空间关系和典型同框构图。"
            ),
        }
        semantic_roles = {
            CharacterDesignSlot.CHILD: "appearance",
            CharacterDesignSlot.CAT: "pose",
            CharacterDesignSlot.PAIR_SCALE: "scale",
        }
        batches: list[dict[str, Any]] = []
        for slot in CharacterDesignSlot:
            node = session.get(CanvasGraphNode, nodes[slot])
            if node is None:
                raise RecordNotFoundError(f"角色设计节点不存在：{slot.value}")
            if prepare_nodes:
                node.object_id = revision.id
                node.status = "pending"
                node.revision += 1
                node.data_json = {
                    **node.data_json,
                    "characterDesignRevisionId": str(revision.id),
                    "characterDesignRevision": revision.revision,
                    "slot": slot.value,
                    "candidateCount": candidate_count,
                    "status": "pending",
                    "candidates": [],
                }
            reference_ids = [str(by_key[key].id) for key in references[slot]]
            prompt = (
                f"{prompts[slot]}\n"
                f"本集故事：{story.title}。{story.synopsis}\n"
                f"本集规则：{json.dumps(story.episode_rules_json, ensure_ascii=False)}\n"
                "角色设计图只承担造型、姿态、比例或构图职责，不得改变身份。"
            )
            batches.append(
                {
                    "projectId": str(project_id),
                    "canvasNodeId": str(node.id),
                    "mediaKind": "image",
                    "candidateCount": candidate_count,
                    "idempotencyKey": f"{revision.idempotency_key}:{slot.value}",
                    "input": {
                        "prompt": prompt,
                        "referenceAssetIds": reference_ids,
                        "characterDesign": {
                            "revisionId": str(revision.id),
                            "slot": slot.value,
                            "semanticRole": semantic_roles[slot],
                            "candidateCount": candidate_count,
                        },
                    },
                }
            )
        return {
            "id": str(revision.id),
            "recipeInstanceId": str(instance.id),
            "projectId": str(project_id),
            "revision": revision.revision,
            "status": revision.status,
            "sourceStoryRevisionId": str(revision.source_story_revision_id),
            "batches": batches,
        }

    def get_group(self, group_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            group = self._required(session, CanvasGroup, group_id)
            return _canvas_group_json(session, group)

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions.begin() as session:
            group = self._required(session, CanvasGroup, group_id)
            members = list(
                session.scalars(
                    select(CanvasGroupMember)
                    .where(CanvasGroupMember.group_id == group.id)
                    .order_by(CanvasGroupMember.sort_order)
                )
            )
            node_ids = [member.canvas_node_id for member in members]
            nodes = list(
                session.scalars(select(CanvasGraphNode).where(CanvasGraphNode.id.in_(node_ids)))
            )
            edges = list(
                session.scalars(
                    select(CanvasGraphEdge).where(
                        CanvasGraphEdge.source_node_id.in_(node_ids),
                        CanvasGraphEdge.target_node_id.in_(node_ids),
                    )
                )
            )
            template_key = f"healing-child-cat-six-stage-v{group.revision}"
            definition = {
                "groupType": group.group_type,
                "nodes": [
                    {
                        "type": node.node_type,
                        "objectType": node.object_type,
                        "phase": node.data_json.get("phase"),
                        "slot": node.data_json.get("slot"),
                        "title": node.data_json.get("title"),
                    }
                    for node in nodes
                    if node.node_type != CanvasNodeType.RECIPE_GROUP.value
                ],
                "edges": [
                    {
                        "sourceNodeId": str(edge.source_node_id),
                        "sourcePort": edge.source_port,
                        "targetNodeId": str(edge.target_node_id),
                        "targetPort": edge.target_port,
                        "relationType": edge.relation_type,
                    }
                    for edge in edges
                ],
            }
            existing = session.scalar(
                select(CanvasGroupTemplate).where(CanvasGroupTemplate.template_key == template_key)
            )
            if existing is None:
                existing = CanvasGroupTemplate(
                    id=uuid.uuid4(),
                    template_key=template_key,
                    title="一人一猫六阶段工作流",
                    definition_json=definition,
                )
                session.add(existing)
                session.flush()
            return {
                "id": str(existing.id),
                "templateKey": existing.template_key,
                "title": existing.title,
                "definition": existing.definition_json,
            }

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            group = self._required(session, CanvasGroup, group_id, lock=True)
            if group.revision != expected_revision:
                raise WorkflowConflictError(
                    f"分组版本冲突：当前 {group.revision}，提交 {expected_revision}"
                )
            if group.lifecycle_status != "active":
                return {"id": str(group.id), "status": "detached", "archived": True}
            group.lifecycle_status = "detached"
            group.revision += 1
            session.execute(delete(CanvasGroupMember).where(CanvasGroupMember.group_id == group.id))
            if group.production_recipe_instance_id is not None:
                instance = self._required(
                    session,
                    ProductionRecipeInstance,
                    group.production_recipe_instance_id,
                    lock=True,
                )
                instance.lifecycle_status = "archived"
                instance.archived_at = datetime.now(UTC)
                instance.revision += 1
            return {
                "id": str(group.id),
                "status": group.lifecycle_status,
                "archived": True,
                "preserved": ["canon", "stories", "shots", "assets", "reviews", "history"],
            }

    def convert_to_shot_groups(self, group_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions.begin() as session:
            parent = self._required(session, CanvasGroup, group_id, lock=True)
            if parent.lifecycle_status != "active":
                raise WorkflowConflictError("已解组的配方不能转为分镜组")
            beats = list(
                session.scalars(
                    select(ShotBeat)
                    .join(Scene, Scene.id == ShotBeat.scene_id)
                    .where(
                        Scene.production_run_id == parent.production_run_id,
                        Scene.active.is_(True),
                        ShotBeat.status == "approved",
                    )
                    .order_by(Scene.sort_order, ShotBeat.sort_order)
                )
            )
            if not beats:
                raise WorkflowConflictError("分镜尚未人工批准，不能转为分镜组")
            groups: list[dict[str, Any]] = []
            for index, beat in enumerate(beats, 1):
                node = session.get(CanvasGraphNode, beat.id)
                if node is None:
                    node = CanvasGraphNode(
                        id=beat.id,
                        production_run_id=parent.production_run_id,
                        node_type=CanvasNodeType.SHOT_BEAT.value,
                        object_type="shot_beat",
                        object_id=beat.id,
                        status=beat.status,
                        data_json={"title": beat.title, "phase": "storyboard"},
                    )
                    session.add(node)
                    session.flush()
                child_id = uuid.uuid5(parent.id, f"shot-group:{beat.id}")
                child = session.get(CanvasGroup, child_id)
                if child is None:
                    child = CanvasGroup(
                        id=child_id,
                        production_run_id=parent.production_run_id,
                        production_recipe_instance_id=parent.production_recipe_instance_id,
                        parent_group_id=parent.id,
                        group_type="shot",
                        title=f"镜头 {index} · {beat.title}",
                        lifecycle_status="active",
                        color="#52b7c8",
                        data_json={
                            "shotBeatId": str(beat.id),
                            "shotCardId": str(beat.shot_card_id),
                        },
                    )
                    session.add(child)
                    session.flush()
                    session.add(
                        CanvasGroupMember(
                            id=uuid.uuid5(child.id, f"member:{node.id}"),
                            group_id=child.id,
                            canvas_node_id=node.id,
                            sort_order=1,
                        )
                    )
                groups.append(_canvas_group_json(session, child))
            return {"parentGroupId": str(parent.id), "groups": groups}

    def group_download_assets(self, group_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            group = self._required(session, CanvasGroup, group_id)
            assets = list(
                session.scalars(
                    select(Asset)
                    .where(
                        Asset.production_run_id == group.production_run_id,
                        Asset.status.in_(("approved", "ready")),
                        Asset.scope != "canon",
                    )
                    .order_by(Asset.created_at)
                )
            )
            return {
                "groupId": str(group.id),
                "title": group.title,
                "assets": [
                    {
                        "id": str(asset.id),
                        "mediaType": asset.media_type,
                        "role": asset.role,
                        "semanticKey": asset.semantic_key,
                        "storageKey": asset.storage_key,
                        "sha256": asset.sha256,
                    }
                    for asset in assets
                ],
            }

    def _instance_json(
        self,
        session: Session,
        row: ProductionRecipeInstance,
    ) -> dict[str, Any]:
        canvas_group = session.scalar(
            select(CanvasGroup)
            .where(
                CanvasGroup.production_recipe_instance_id == row.id,
                CanvasGroup.lifecycle_status == "active",
            )
            .order_by(CanvasGroup.created_at.desc())
            .limit(1)
        )
        latest_brief = session.scalar(
            select(StoryBriefRecord)
            .where(StoryBriefRecord.production_run_id == row.production_run_id)
            .order_by(StoryBriefRecord.revision.desc())
            .limit(1)
        )
        brief_decision = (
            None
            if latest_brief is None
            else session.scalar(
                select(HumanReviewDecisionRecord)
                .where(
                    HumanReviewDecisionRecord.production_recipe_instance_id == row.id,
                    HumanReviewDecisionRecord.target_type == "creative_brief",
                    HumanReviewDecisionRecord.target_id == latest_brief.id,
                )
                .order_by(HumanReviewDecisionRecord.created_at.desc())
                .limit(1)
            )
        )
        creative_approved = bool(
            brief_decision is not None and brief_decision.decision in _APPROVING_DECISIONS
        )
        creative_completed = bool(latest_brief is not None and latest_brief.revision >= 2)
        current_beats = list(
            session.scalars(
                select(ShotBeat)
                .join(Scene, Scene.id == ShotBeat.scene_id)
                .where(
                    Scene.production_run_id == row.production_run_id,
                    Scene.active.is_(True),
                    ShotBeat.status != "superseded",
                )
            )
        )
        shot_ids = [beat.shot_card_id for beat in current_beats if beat.shot_card_id is not None]
        shots = list(session.scalars(select(ShotCard).where(ShotCard.id.in_(shot_ids))))
        shots_by_id = {shot.id: shot for shot in shots}
        assets = list(
            session.scalars(
                select(Asset).where(Asset.shot_card_id.in_(shot_ids)).order_by(Asset.created_at)
            )
        )
        assets_by_shot: dict[uuid.UUID, list[Asset]] = {}
        for asset in assets:
            if asset.shot_card_id is not None:
                assets_by_shot.setdefault(asset.shot_card_id, []).append(asset)
        approved_story = session.scalar(
            select(StoryRevisionRecord)
            .where(
                StoryRevisionRecord.production_run_id == row.production_run_id,
                StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
            )
            .order_by(StoryRevisionRecord.revision.desc())
            .limit(1)
        )
        story_rows = list(
            session.scalars(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == row.production_run_id,
                    StoryRevisionRecord.status != StoryRevisionStatus.SUPERSEDED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
            )
        )
        story_ids = [story.id for story in story_rows]
        scores_by_story = {
            score.story_revision_id: score
            for score in session.scalars(
                select(StoryScore).where(StoryScore.story_revision_id.in_(story_ids))
            )
        }
        story_approved = approved_story is not None
        episode_rules_locked = bool(
            approved_story is not None and approved_story.episode_rules_json
        )
        character_revision = session.scalar(
            select(CharacterDesignRevision)
            .where(CharacterDesignRevision.production_recipe_instance_id == row.id)
            .order_by(CharacterDesignRevision.revision.desc())
            .limit(1)
        )
        character_design = (
            None
            if character_revision is None
            else _character_design_json(session, character_revision)
        )
        character_design_approved = bool(
            character_revision is not None and character_revision.status == "approved"
        )
        storyboard_hash = _storyboard_review_hash(current_beats)
        storyboard_decision = (
            None
            if approved_story is None or not current_beats
            else session.scalar(
                select(HumanReviewDecisionRecord)
                .where(
                    HumanReviewDecisionRecord.production_recipe_instance_id == row.id,
                    HumanReviewDecisionRecord.target_type == "storyboard_revision",
                    HumanReviewDecisionRecord.target_id == approved_story.id,
                    HumanReviewDecisionRecord.target_hash == storyboard_hash,
                )
                .order_by(HumanReviewDecisionRecord.created_at.desc())
                .limit(1)
            )
        )
        storyboard_approved = bool(
            current_beats
            and all(beat.prompt_id is not None for beat in current_beats)
            and storyboard_decision is not None
            and storyboard_decision.decision in _APPROVING_DECISIONS
        )
        approved_anchor_count = sum(shot.selected_anchor_asset_id is not None for shot in shots)
        approved_video_count = sum(shot.selected_video_asset_id is not None for shot in shots)
        latest_sequence = session.scalar(
            select(VideoSequence)
            .where(VideoSequence.production_run_id == row.production_run_id)
            .order_by(VideoSequence.revision.desc())
            .limit(1)
        )
        rendered_asset = (
            None
            if latest_sequence is None or latest_sequence.rendered_asset_id is None
            else session.get(Asset, latest_sequence.rendered_asset_id)
        )
        sequence_ready = bool(
            latest_sequence is not None and latest_sequence.status in {"content_review", "approved"}
        )
        final_approved = bool(latest_sequence is not None and latest_sequence.status == "approved")
        return {
            "id": str(row.id),
            "projectId": str(row.production_run_id),
            "recipeKey": row.recipe_key,
            "recipeVersion": row.recipe_version,
            "revision": row.revision,
            "theme": row.theme,
            "inspirationKey": row.inspiration_key,
            "targetDurationSeconds": row.target_duration_seconds,
            "qualityTier": row.quality_tier,
            "canonProfileId": row.canon_profile_id,
            "lifecycleStatus": row.lifecycle_status,
            "groupId": str(
                canvas_group.id
                if canvas_group is not None
                else uuid.uuid5(row.production_run_id, f"canvas-group:{row.id}")
            ),
            "creativeBrief": (
                None
                if latest_brief is None
                else {
                    "id": str(latest_brief.id),
                    "revision": latest_brief.revision,
                    "theme": latest_brief.theme,
                    "audience": latest_brief.audience,
                    "genre": latest_brief.genre,
                    "tone": latest_brief.tone,
                    "aspectRatio": latest_brief.aspect_ratio,
                    "targetDurationSeconds": latest_brief.target_duration_seconds,
                    "constraints": latest_brief.constraints_json,
                    "approvalStatus": ("approved" if creative_approved else "awaiting_review"),
                    "completionStatus": ("completed" if creative_completed else "seed"),
                }
            ),
            "characterDesign": character_design,
            "storyboardHash": storyboard_hash if current_beats else None,
            "episodeRules": (
                None
                if approved_story is None or not approved_story.episode_rules_json
                else approved_story.episode_rules_json
            ),
            "sequenceCandidate": (
                None
                if latest_sequence is None
                else _sequence_candidate_json(latest_sequence, rendered_asset)
            ),
            "storyCandidates": [
                _recipe_story_candidate_json(story, scores_by_story.get(story.id))
                for story in story_rows
            ],
            "shots": [
                _recipe_shot_json(
                    session,
                    beat,
                    shots_by_id.get(beat.shot_card_id),
                    assets_by_shot.get(beat.shot_card_id, []),
                )
                for beat in current_beats
            ],
            "progress": {
                "creativeApproved": creative_approved,
                "creativeCompleted": creative_completed,
                "storyApproved": story_approved,
                "episodeRulesLocked": episode_rules_locked,
                "characterDesignApproved": character_design_approved,
                "storyboardApproved": storyboard_approved,
                "shotCount": len(current_beats),
                "approvedAnchorCount": approved_anchor_count,
                "approvedVideoCount": approved_video_count,
                "sequenceReady": sequence_ready,
                "finalApproved": final_approved,
            },
            "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(),
        }

    @staticmethod
    def _lock_canon_profile(
        session: Session,
        *,
        project_id: uuid.UUID,
        rules: EpisodeRules,
    ) -> None:
        instance = session.scalar(
            select(ProductionRecipeInstance).where(
                ProductionRecipeInstance.production_run_id == project_id
            )
        )
        canon_profile_id = (
            rules.canon_profile_id if instance is None else instance.canon_profile_id
        )
        keys = canon_reference_keys(canon_profile_id, rules.environment)
        assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.scope == "canon",
                    Asset.status.in_(("ready", "approved")),
                    Asset.semantic_key.in_(keys),
                )
            )
        )
        by_key = {asset.semantic_key: asset for asset in assets}
        missing = [key for key in keys if key not in by_key]
        if missing:
            raise ValueError(f"{canon_profile_id} 缺少可用参考：{', '.join(missing)}")
        snapshot = [
            {
                "assetId": str(by_key[key].id),
                "semanticKey": key,
                "sha256": by_key[key].sha256,
                "role": "style" if key.startswith("style:") else "identity",
            }
            for key in keys
        ]
        digest = hashlib.sha256(
            json.dumps(
                {
                    "rules": rules.model_dump(mode="json", by_alias=True),
                    "references": snapshot,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        profile = session.scalar(
            select(VisualProfileRevision).where(
                VisualProfileRevision.production_run_id == project_id,
                VisualProfileRevision.profile_hash == digest,
            )
        )
        if profile is None:
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
            bindings = visual_profile_bindings(by_key, keys)
            profile = VisualProfileRevision(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=revision,
                profile_hash=digest,
                source_profile_id=rules.canon_profile_id,
                person_identity=f"沿用 {canon_profile_id} 人物脸部身份，不改变年龄与五官",
                person_hair=f"沿用 {canon_profile_id} 发型与发色",
                person_body=f"沿用 {canon_profile_id} 儿童身体比例",
                cat_identity=f"沿用 {canon_profile_id} 猫咪脸部、毛色分区、体型与尾巴环纹",
                style_positive_json=rules.style_positive,
                style_negative_json=rules.style_excluded,
                reference_bindings_json=bindings,
                reference_snapshot_json=snapshot,
            )
            session.add(profile)
            session.flush()
        project = session.get(ProductionRun, project_id)
        if project is None:
            raise RecordNotFoundError(f"ProductionRun not found: {project_id}")
        project.current_visual_profile_revision_id = profile.id
        project.default_reference_bindings_json = generation_reference_bindings(by_key, keys)

    @staticmethod
    def _review_target(
        session: Session,
        target_type: str,
        target_id: uuid.UUID,
    ) -> dict[str, Any]:
        if target_type == "creative_brief":
            row = session.get(StoryBriefRecord, target_id)
            project_id = None if row is None else row.production_run_id
        elif target_type in {"story_revision", "episode_rules"}:
            row = session.get(StoryRevisionRecord, target_id)
            project_id = None if row is None else row.production_run_id
        elif target_type == "storyboard_revision":
            row = session.get(StoryRevisionRecord, target_id)
            project_id = None if row is None else row.production_run_id
            beats = (
                []
                if project_id is None
                else list(
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
            )
            if not beats:
                raise WorkflowConflictError("分镜尚未生成，不能审核")
            missing_prompt_orders = [
                str(index)
                for index, beat in enumerate(beats, 1)
                if beat.prompt_id is None
            ]
            if missing_prompt_orders:
                raise WorkflowConflictError(
                    "以下镜头尚未完成服务端分层 Prompt 编译："
                    + "、".join(missing_prompt_orders)
                )
            return {
                "row": row,
                "project_id": project_id,
                "snapshot_hash": _storyboard_review_hash(beats),
            }
        elif target_type == "shot_beat":
            row = session.get(ShotBeat, target_id)
            scene = None if row is None else session.get(Scene, row.scene_id)
            project_id = None if scene is None else scene.production_run_id
        elif target_type in {"anchor_asset", "video_asset", "character_design"}:
            row = session.get(Asset, target_id)
            project_id = None if row is None else row.production_run_id
        elif target_type == "final_sequence":
            row = session.get(VideoSequence, target_id)
            project_id = None if row is None else row.production_run_id
        else:
            raise ValueError(f"不支持的审核目标类型：{target_type}")
        if row is None or project_id is None:
            raise RecordNotFoundError(f"审核目标不存在：{target_type}/{target_id}")
        return {"row": row, "project_id": project_id}

    @staticmethod
    def _validate_target_snapshot(
        payload: HumanReviewDraft,
        target: dict[str, Any],
    ) -> None:
        row = target["row"]
        snapshot_hash = target.get("snapshot_hash")
        if snapshot_hash is not None:
            if payload.target_hash is None:
                raise ValueError("分镜审核必须固定当前镜头表内容哈希")
            if payload.target_hash != snapshot_hash:
                raise WorkflowConflictError("分镜内容已变化，请重新审核最新版本")
            return
        if isinstance(row, Asset):
            if payload.target_hash is None:
                raise ValueError("媒体资产审核必须固定内容哈希")
            if payload.target_hash != row.sha256:
                raise WorkflowConflictError("媒体资产内容已变化，请重新审核最新版本")
            return
        current_revision = getattr(row, "revision", None)
        if payload.target_revision is None:
            raise ValueError("业务对象审核必须固定目标版本")
        if current_revision != payload.target_revision:
            raise WorkflowConflictError(
                f"审核目标版本冲突：当前 {current_revision}，提交 {payload.target_revision}"
            )

    @staticmethod
    def _apply_review_to_target(
        session: Session,
        payload: HumanReviewDraft,
        row: Any,
    ) -> None:
        accepted = payload.decision.value in _APPROVING_DECISIONS
        if isinstance(row, StoryBriefRecord):
            return
        if isinstance(row, StoryRevisionRecord):
            if payload.target_type == "storyboard_revision":
                scene_ids = select(Scene.id).where(
                    Scene.production_run_id == row.production_run_id,
                    Scene.active.is_(True),
                )
                session.execute(
                    update(ShotBeat)
                    .where(
                        ShotBeat.scene_id.in_(scene_ids),
                        ShotBeat.status != "superseded",
                    )
                    .values(
                        status="approved" if accepted else "changes_requested",
                        stale_reason=None if accepted else payload.reason,
                    )
                )
                return
            if accepted:
                row.status = StoryRevisionStatus.APPROVED.value
                row.approved_at = datetime.now(UTC)
            return
        if isinstance(row, ShotBeat):
            row.status = "approved" if accepted else "changes_requested"
            row.stale_reason = None if accepted else payload.reason
            return
        if isinstance(row, Asset):
            row.status = "approved" if accepted else "rejected"
            if payload.target_type == "character_design":
                binding = session.scalar(
                    select(CharacterDesignAsset)
                    .where(CharacterDesignAsset.asset_id == row.id)
                    .with_for_update()
                )
                if binding is None:
                    raise RecordNotFoundError("角色设计资产绑定不存在")
                revision = session.get(
                    CharacterDesignRevision, binding.character_design_revision_id
                )
                if revision is None or revision.status == "stale":
                    raise WorkflowConflictError("角色设计版本已过期")
                if accepted:
                    competing = list(
                        session.scalars(
                            select(CharacterDesignAsset).where(
                                CharacterDesignAsset.character_design_revision_id == revision.id,
                                CharacterDesignAsset.slot == binding.slot,
                                CharacterDesignAsset.id != binding.id,
                            )
                        )
                    )
                    for item in competing:
                        item.selected = False
                        asset = session.get(Asset, item.asset_id)
                        if asset is not None and asset.status != "stale":
                            asset.status = "rejected"
                    binding.selected = True
                else:
                    binding.selected = False
                selected_slots = set(
                    session.scalars(
                        select(CharacterDesignAsset.slot).where(
                            CharacterDesignAsset.character_design_revision_id == revision.id,
                            CharacterDesignAsset.selected.is_(True),
                        )
                    )
                )
                if accepted:
                    selected_slots.add(binding.slot)
                revision.status = (
                    "approved"
                    if selected_slots == {slot.value for slot in CharacterDesignSlot}
                    else "awaiting_review"
                )
                if row.canvas_node_id is not None:
                    node = session.get(CanvasGraphNode, row.canvas_node_id)
                    if node is not None:
                        candidates = [
                            {
                                **item,
                                "status": (
                                    "approved"
                                    if item.get("assetId") == str(row.id) and accepted
                                    else (
                                        "rejected" if accepted else item.get("status", "candidate")
                                    )
                                ),
                            }
                            for item in node.data_json.get("candidates", [])
                        ]
                        node.status = "approved" if accepted else "awaiting_review"
                        node.data_json = {
                            **node.data_json,
                            "status": node.status,
                            "selectedAssetId": str(row.id) if accepted else None,
                            "candidates": candidates,
                        }
                return
            if row.shot_card_id is not None:
                shot = session.get(ShotCard, row.shot_card_id)
                if shot is not None and row.media_type == "image":
                    if accepted:
                        shot.selected_anchor_asset_id = row.id
                        shot.status = "video_pending"
                    elif shot.selected_anchor_asset_id == row.id:
                        shot.selected_anchor_asset_id = None
                        shot.status = "ready"
                    shot.selected_video_asset_id = None
                    session.execute(
                        update(Asset)
                        .where(
                            Asset.shot_card_id == shot.id,
                            Asset.media_type == "video",
                            Asset.status != "stale",
                        )
                        .values(status="stale")
                    )
                    SqlAlchemyProductionRecipeRepository._invalidate_sequences(
                        session,
                        row.production_run_id,
                    )
                elif shot is not None and row.media_type == "video":
                    if accepted:
                        shot.selected_video_asset_id = row.id
                        shot.status = "approved"
                    elif shot.selected_video_asset_id == row.id:
                        shot.selected_video_asset_id = None
                        shot.status = "video_pending"
                    SqlAlchemyProductionRecipeRepository._invalidate_sequences(
                        session,
                        row.production_run_id,
                    )
            return
        if isinstance(row, VideoSequence):
            row.status = "approved" if accepted else "rejected"
            project = session.get(ProductionRun, row.production_run_id)
            if project is not None:
                project.selected_sequence_id = row.id if accepted else None

    @staticmethod
    def _invalidate_media_after_upstream_change(
        session: Session,
        project_id: uuid.UUID,
        reason: str,
    ) -> None:
        scene_ids = select(Scene.id).where(
            Scene.production_run_id == project_id,
            Scene.active.is_(True),
        )
        session.execute(
            update(CharacterDesignRevision)
            .where(
                CharacterDesignRevision.production_run_id == project_id,
                CharacterDesignRevision.status != "stale",
            )
            .values(status="stale")
        )
        character_asset_ids = (
            select(CharacterDesignAsset.asset_id)
            .join(
                CharacterDesignRevision,
                CharacterDesignRevision.id == CharacterDesignAsset.character_design_revision_id,
            )
            .where(CharacterDesignRevision.production_run_id == project_id)
        )
        session.execute(
            update(Asset)
            .where(Asset.id.in_(character_asset_ids), Asset.status != "stale")
            .values(status="stale")
        )
        session.execute(
            update(ShotBeat)
            .where(ShotBeat.scene_id.in_(scene_ids), ShotBeat.status != "superseded")
            .values(status="stale", stale_reason=reason)
        )
        session.execute(
            update(Asset)
            .where(Asset.production_run_id == project_id, Asset.scope != "canon")
            .values(status="stale")
        )
        SqlAlchemyProductionRecipeRepository._invalidate_sequences(session, project_id)

    @staticmethod
    def _invalidate_sequences(session: Session, project_id: uuid.UUID) -> None:
        session.execute(
            update(VideoSequence)
            .where(VideoSequence.production_run_id == project_id)
            .values(status="rejected")
        )
        project = session.get(ProductionRun, project_id)
        if project is not None:
            project.selected_sequence_id = None

    @staticmethod
    def _invalidate_downstream(
        session: Session,
        project_id: uuid.UUID,
        recipe_revision: int,
    ) -> None:
        reason = f"配方实例已更新到 revision {recipe_revision}"
        session.execute(
            update(StoryRevisionRecord)
            .where(
                StoryRevisionRecord.production_run_id == project_id,
                StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
            )
            .values(status=StoryRevisionStatus.SUPERSEDED.value)
        )
        SqlAlchemyProductionRecipeRepository._invalidate_media_after_upstream_change(
            session,
            project_id,
            reason,
        )

    @staticmethod
    def _required(
        session: Session,
        model: type[Any],
        object_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> Any:
        statement = select(model).where(model.id == object_id)
        if lock:
            statement = statement.with_for_update()
        row = session.scalar(statement)
        if row is None:
            raise RecordNotFoundError(f"{model.__name__} not found: {object_id}")
        return row


def _review_json(row: HumanReviewDecisionRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "recipeInstanceId": str(row.production_recipe_instance_id),
        "projectId": str(row.production_run_id),
        "targetType": row.target_type,
        "targetId": str(row.target_id),
        "targetRevision": row.target_revision,
        "targetHash": row.target_hash,
        "decision": row.decision,
        "blockingDiagnosticPresent": row.blocking_diagnostic_present,
        "issues": row.issues_json,
        "reason": row.reason,
        "createdAt": row.created_at.isoformat(),
    }


def _canvas_group_json(session: Session, group: CanvasGroup) -> dict[str, Any]:
    member_ids = list(
        session.scalars(
            select(CanvasGroupMember.canvas_node_id)
            .where(CanvasGroupMember.group_id == group.id)
            .order_by(CanvasGroupMember.sort_order)
        )
    )
    return {
        "id": str(group.id),
        "projectId": str(group.production_run_id),
        "recipeInstanceId": (
            None
            if group.production_recipe_instance_id is None
            else str(group.production_recipe_instance_id)
        ),
        "parentGroupId": (None if group.parent_group_id is None else str(group.parent_group_id)),
        "type": group.group_type,
        "title": group.title,
        "lifecycleStatus": group.lifecycle_status,
        "color": group.color,
        "revision": group.revision,
        "memberNodeIds": [str(item) for item in member_ids],
        "data": group.data_json,
    }


def _character_design_json(
    session: Session,
    revision: CharacterDesignRevision,
) -> dict[str, Any]:
    bindings = list(
        session.scalars(
            select(CharacterDesignAsset)
            .where(CharacterDesignAsset.character_design_revision_id == revision.id)
            .order_by(CharacterDesignAsset.slot, CharacterDesignAsset.candidate_index)
        )
    )
    slots: dict[str, list[dict[str, Any]]] = {slot.value: [] for slot in CharacterDesignSlot}
    for binding in bindings:
        asset = session.get(Asset, binding.asset_id)
        if asset is None:
            continue
        slots[binding.slot].append(
            {
                "bindingId": str(binding.id),
                "assetId": str(asset.id),
                "candidateIndex": binding.candidate_index,
                "semanticRole": binding.semantic_role,
                "selected": binding.selected,
                "status": asset.status,
                "sha256": asset.sha256,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
            }
        )
    return {
        "id": str(revision.id),
        "revision": revision.revision,
        "status": revision.status,
        "sourceStoryRevisionId": str(revision.source_story_revision_id),
        "slots": slots,
        "createdAt": revision.created_at.isoformat(),
        "updatedAt": revision.updated_at.isoformat(),
    }


def _storyboard_review_hash(beats: list[ShotBeat]) -> str:
    document = [
        {
            "id": str(beat.id),
            "revision": beat.revision,
            "sceneId": str(beat.scene_id),
            "order": beat.sort_order,
            "title": beat.title,
            "action": beat.action,
            "camera": beat.camera,
            "dialogue": beat.dialogue,
            "durationSeconds": beat.duration_seconds,
            "temporalBeats": beat.temporal_beats_json,
        }
        for beat in beats
    ]
    return hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sequence_candidate_json(
    sequence: VideoSequence,
    rendered_asset: Asset | None,
) -> dict[str, Any]:
    return {
        "id": str(sequence.id),
        "revision": sequence.revision,
        "status": sequence.status,
        "durationMs": sequence.duration_ms,
        "audioPolicy": sequence.audio_policy,
        "renderedAssetId": (
            None if sequence.rendered_asset_id is None else str(sequence.rendered_asset_id)
        ),
        "contentUrl": (
            None if rendered_asset is None else f"/api/v1/assets/{rendered_asset.id}/content"
        ),
        "sha256": None if rendered_asset is None else rendered_asset.sha256,
        "qc": (None if rendered_asset is None else rendered_asset.metadata_json.get("qc")),
    }


def _recipe_story_candidate_json(
    story: StoryRevisionRecord,
    score: StoryScore | None,
) -> dict[str, Any]:
    score_values = (
        []
        if score is None
        else [
            score.opening_hook,
            score.causal_completeness,
            score.subject_necessity,
            score.emotional_arc,
            score.visualizability,
            score.duration_fit,
            score.continuity_risk,
            score.safety,
        ]
    )
    return {
        "id": str(story.id),
        "revision": story.revision,
        "strategy": story.strategy,
        "status": story.status,
        "title": story.title,
        "logline": story.logline,
        "synopsis": story.synopsis,
        "episodeRules": story.episode_rules_json or None,
        "scoreAverage": (
            None if not score_values else round(sum(score_values) / len(score_values), 2)
        ),
        "scoreRationale": None if score is None else score.rationale,
    }


def _cat_beat_actions(mode: CatBehaviorMode) -> tuple[str, str, str]:
    if mode is CatBehaviorMode.NATURAL:
        return (
            "猫咪保持四足姿态自然观察",
            "猫咪用耳朵、尾巴或轻缓步态回应变化",
            "猫咪自然蜷卧或靠近儿童，不持工具、不直立劳动",
        )
    return (
        "猫咪保持猫科身体结构，佩戴固定小配件观察",
        "猫咪短暂进行简单道具互动，不生成手掌或人形肢体",
        "猫咪恢复自然猫科姿态，与儿童形成温暖收尾",
    )


def _recipe_shot_json(
    session: Session,
    beat: ShotBeat,
    shot: ShotCard | None,
    assets: list[Asset],
) -> dict[str, Any]:
    anchor_assets = [asset for asset in assets if asset.role == "shot_anchor"]
    video_assets = [asset for asset in assets if asset.role in {"shot_video", "shot_video_edit"}]
    return {
        "beatId": str(beat.id),
        "shotId": None if shot is None else str(shot.id),
        "title": beat.title,
        "durationSeconds": beat.duration_seconds,
        "status": beat.status,
        "temporalBeats": beat.temporal_beats_json,
        "selectedAnchorAssetId": (
            None
            if shot is None or shot.selected_anchor_asset_id is None
            else str(shot.selected_anchor_asset_id)
        ),
        "selectedVideoAssetId": (
            None
            if shot is None or shot.selected_video_asset_id is None
            else str(shot.selected_video_asset_id)
        ),
        "anchorCandidates": [_recipe_asset_json(session, asset) for asset in anchor_assets],
        "videoCandidates": [_recipe_asset_json(session, asset) for asset in video_assets],
    }


def _recipe_asset_json(session: Session, asset: Asset) -> dict[str, Any]:
    reviews = list(
        session.scalars(
            select(Review).where(Review.asset_id == asset.id).order_by(Review.created_at)
        )
    )
    diagnostics = [
        warning
        for review in reviews
        if review.source in {"ark_visual", "technical"}
        for warning in review.warnings_json
    ]
    visual_review_present = any(review.source == "ark_visual" for review in reviews)
    return {
        "id": str(asset.id),
        "sha256": asset.sha256,
        "status": asset.status,
        "mediaType": asset.media_type,
        "contentUrl": f"/api/v1/assets/{asset.id}/content",
        "qc": asset.metadata_json.get("qc"),
        "diagnosticStatus": (
            "not_run" if not visual_review_present else "failed" if diagnostics else "passed"
        ),
        "diagnostics": diagnostics,
    }


def _healing_shot_direction(
    beat: ShotBeat,
    temporal_beats: tuple[Any, ...],
    rules: EpisodeRules,
    scene_context: dict[str, Any],
) -> str:
    timeline = "；".join(
        f"{item.start_second}-{item.end_second}秒 {item.child_action}，{item.cat_action}，"
        f"镜头：{item.camera}"
        for item in temporal_beats
    )
    continuity = dict(scene_context.get("continuity") or {})
    location = str(continuity.get("location") or rules.main_scene)
    environment = str(continuity.get("environment") or rules.environment)
    time_weather = str(continuity.get("timeWeather") or rules.time_weather)
    decorations = "、".join(str(item) for item in continuity.get("decorations") or [])
    scene_props = "、".join(str(item) for item in continuity.get("props") or rules.core_props)
    return (
        f"当前场景内连续镜头。{timeline}。"
        f"服装固定：{rules.person_wardrobe}。时间天气固定：{rules.time_weather}。"
        f"所属场景：{location}（{environment}），时间天气：{time_weather}。"
        f"关键装饰：{decorations or '无额外装饰'}。场景道具：{scene_props or '无核心道具'}。"
        f"声音：环境声为{'、'.join(rules.sound_plan.ambient)}；"
        f"动作声为{'、'.join(rules.sound_plan.foley)}；"
        f"轻音乐情绪为{rules.sound_plan.music_mood}。"
        "只描述动作、微表情、运镜与声音变化；不得重写人物、猫咪或画风身份。无对白。"
    )


def _scene_continuity_document(scene: Scene) -> dict[str, Any]:
    if not scene.context_note:
        return {}
    try:
        document = json.loads(scene.context_note)
    except (TypeError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _workflow_task_json(row: WorkflowStep) -> dict[str, Any]:
    snapshot = dict(row.input_snapshot_json or {})
    progress = dict(row.progress_json or {})
    return {
        "jobId": str(row.id),
        "kind": row.kind,
        "status": row.status,
        "projectId": str(row.production_run_id),
        "shotId": None if row.shot_card_id is None else str(row.shot_card_id),
        "canvasNodeId": snapshot.get("canvasNodeId"),
        "canvasGroupId": snapshot.get("canvasGroupId"),
        "recipeInstanceId": snapshot.get("recipeInstanceId"),
        "creationMode": snapshot.get("creationMode"),
        "parentStepId": snapshot.get("parentStepId"),
        "childStepIds": progress.get("childStepIds", []),
        "workflowStage": snapshot.get("workflowStage"),
        "phase": snapshot.get("phase"),
        "operationKey": row.operation_key,
        "progress": progress,
        "resultSummary": progress.get("resultSummary"),
        "error": row.error_json,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
        "completedAt": None if row.completed_at is None else row.completed_at.isoformat(),
    }
