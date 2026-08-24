"""Application boundary for fixed-IP production recipe instances."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from ..domain.production_recipes import (
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    HEALING_CHILD_CAT_RECIPE,
    CanvasGroupRunRequest,
    CatBehaviorMode,
    CharacterDesignBatchDraft,
    EpisodeRules,
    HumanReviewDraft,
    PaidRecipeRunRequest,
    ProductionRecipeInstanceDraft,
    ProductionRecipeInstancePatch,
    RecipePhaseKey,
    RecipeSequenceRunRequest,
    RecipeStage,
    SoundPlan,
    StoryboardCreationMode,
    StoryboardRecipeRunRequest,
    recipe_task_source_hash,
    split_shot_durations,
)
from ..domain.workflow import StepKind, StepStatus
from .universal_media_worker import MediaExecutionResult


class ProductionRecipeRepository(Protocol):
    def create_instance(
        self,
        project_id: uuid.UUID,
        payload: ProductionRecipeInstanceDraft,
    ) -> dict[str, Any]: ...

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, Any]: ...

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, Any]: ...

    def materialize_storyboard(
        self,
        instance_id: uuid.UUID,
        storyboard: dict[str, Any],
    ) -> dict[str, Any]: ...

    def store_suggested_episode_rules(
        self,
        instance_id: uuid.UUID,
        candidate_ids: tuple[uuid.UUID, ...],
        rules: EpisodeRules,
    ) -> None: ...

    def prepare_character_design(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
        candidate_count: int,
    ) -> dict[str, Any]: ...

    def validate_storyboard_character_references(
        self,
        instance_id: uuid.UUID,
        reference_asset_ids: tuple[uuid.UUID, ...],
    ) -> None: ...

    def validate_anchor_prompt_readiness(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
    ) -> None: ...

    def record_task_children(
        self,
        parent_step_id: uuid.UUID,
        child_step_ids: tuple[uuid.UUID, ...],
    ) -> tuple[dict[str, Any], ...]: ...

    def get_group(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, Any]: ...

    def convert_to_shot_groups(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def group_download_assets(self, group_id: uuid.UUID) -> dict[str, Any]: ...


class StoryRecipeWorkflow(Protocol):
    def complete_creative_brief(
        self,
        project_id: uuid.UUID,
        *,
        theme: str,
        target_duration_seconds: int,
    ) -> dict[str, Any]: ...

    def run_story_strategies(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]: ...

    def run_story_event_strategies(
        self,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]: ...

    def expand_selected_story_event(
        self,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]: ...

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        exact_durations: tuple[int, ...] | None = None,
        healing_recipe: bool = False,
        idempotency_key: str | None = None,
        creation_mode: str = StoryboardCreationMode.FROM_STORY.value,
        reference_asset_ids: tuple[uuid.UUID, ...] = (),
        instruction: str | None = None,
    ) -> dict[str, Any]: ...

    def create_generation_batch(self, payload: Any) -> dict[str, Any]: ...


class ShotRecipeWorkflow(Protocol):
    def generate_anchor(self, shot_id: uuid.UUID, **values: Any) -> dict[str, Any]: ...

    def generate_video(self, shot_id: uuid.UUID, **values: Any) -> dict[str, Any]: ...


class SequenceRecipeWorkflow(Protocol):
    def build_project_sequence(self, project_id: uuid.UUID, **values: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class RecipeStoryCommand:
    idempotency_key: str
    rewrite_instruction: str


class ProductionRecipeService:
    """Validates recipe choices and derives workflow state from durable facts."""

    def __init__(
        self,
        *,
        repository: ProductionRecipeRepository,
        story_workflow: StoryRecipeWorkflow | None = None,
        shot_workflow: ShotRecipeWorkflow | None = None,
        sequence_workflow: SequenceRecipeWorkflow | None = None,
        director_call_cost_micros: int | None = None,
        image_call_cost_micros: int | None = None,
        video_call_cost_micros: int | None = None,
        asset_root: Path | None = None,
    ) -> None:
        self._repository = repository
        self._story_workflow = story_workflow
        self._shot_workflow = shot_workflow
        self._sequence_workflow = sequence_workflow
        self._director_call_cost_micros = director_call_cost_micros
        self._image_call_cost_micros = image_call_cost_micros
        self._video_call_cost_micros = video_call_cost_micros
        self._asset_root = None if asset_root is None else asset_root.expanduser().resolve()

    def list_recipes(self) -> list[dict[str, Any]]:
        return [HEALING_CHILD_CAT_RECIPE.model_dump(mode="json", by_alias=True)]

    def create_instance(
        self,
        project_id: uuid.UUID,
        payload: ProductionRecipeInstanceDraft,
    ) -> dict[str, Any]:
        if payload.recipe_key != HEALING_CHILD_CAT_RECIPE.key:
            raise ValueError("当前版本只支持一人一猫治愈短片配方")
        return self._project_instance(self._repository.create_instance(project_id, payload))

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, Any]:
        return self._project_instance(self._repository.get_instance(instance_id))

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, Any]:
        return self._project_instance(
            self._repository.update_instance(
                instance_id,
                expected_revision=expected_revision,
                payload=payload,
            )
        )

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, Any]:
        return self._repository.record_review(
            instance_id,
            payload,
            episode_rules=episode_rules,
        )

    def enqueue_recipe_task(
        self,
        instance_id: uuid.UUID,
        *,
        operation_key: str,
        payload: PaidRecipeRunRequest,
        shot_id: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        creation_mode: str | None = None,
    ) -> dict[str, Any]:
        instance = self.get_instance(instance_id)
        self._validate_enqueued_operation(
            instance,
            operation_key=operation_key,
            payload=payload,
            shot_id=shot_id,
        )
        project_id = uuid.UUID(str(instance["projectId"]))
        return self._repository.enqueue_task(
            instance_id,
            operation_key=operation_key,
            kind=_task_kind(operation_key, instance),
            payload=payload.model_dump(mode="json", by_alias=True),
            idempotency_key=payload.idempotency_key,
            expected_phase=str(instance["phase"]),
            expected_revision=int(instance["revision"]),
            canvas_node_id=_task_canvas_node_id(project_id, operation_key),
            shot_id=shot_id,
            group_id=group_id,
            creation_mode=creation_mode,
        )

    def enqueue_group_task(
        self,
        group_id: uuid.UUID,
        payload: CanvasGroupRunRequest,
    ) -> dict[str, Any]:
        compiled = self.compile_group(group_id)
        instance_id = uuid.UUID(str(compiled["recipeInstanceId"]))
        return self.enqueue_recipe_task(
            instance_id,
            operation_key="canvas-group:run",
            payload=payload,
            group_id=group_id,
        )

    def execute_queued_task(
        self,
        step_id: uuid.UUID,
        *,
        operation_key: str,
        input_snapshot: dict[str, object],
    ) -> MediaExecutionResult:
        instance_id = uuid.UUID(str(input_snapshot["recipeInstanceId"]))
        instance = self.get_instance(instance_id)
        expected_revision = int(input_snapshot["expectedInstanceRevision"])
        expected_phase = str(input_snapshot["phase"])
        if int(instance["revision"]) != expected_revision:
            raise ValueError("任务输入版本已经过期，请重新提交")
        if str(instance["phase"]) != expected_phase:
            raise ValueError("任务阶段已经变化，请重新提交")
        payload_document = dict(input_snapshot["payload"])  # type: ignore[arg-type]
        current_hash = recipe_task_source_hash(
            payload=payload_document,
            instance_id=instance_id,
            expected_revision=expected_revision,
            phase=expected_phase,
        )
        if current_hash != input_snapshot.get("sourceContentHash"):
            raise ValueError("任务固定输入内容校验失败，请重新提交")
        if operation_key == "recipe:storyboard":
            payload = StoryboardRecipeRunRequest.model_validate(payload_document)
            result = self.run_storyboard(instance_id, payload)
        elif operation_key == "recipe:sequence":
            sequence_payload = RecipeSequenceRunRequest.model_validate(payload_document)
            result = self.run_sequence(instance_id, sequence_payload)
        elif operation_key == "canvas-group:run":
            group_id = uuid.UUID(str(input_snapshot["canvasGroupId"]))
            group_payload = CanvasGroupRunRequest.model_validate(payload_document)
            result = self.run_group(group_id, group_payload)
        else:
            payload = PaidRecipeRunRequest.model_validate(payload_document)
            shot_value = input_snapshot.get("shotId")
            if operation_key == "recipe:creative":
                result = self.run_creative_brief(instance_id, payload)
            elif operation_key == "recipe:story_events":
                result = self.run_story_events(instance_id, payload)
            elif operation_key == "recipe:story_script":
                result = self.run_story_script(instance_id, payload)
            elif operation_key == "recipe:story":
                result = self.run_story(instance_id, payload)
            elif operation_key == "recipe:character_design":
                result = self.run_character_design(instance_id, payload)
            elif operation_key == "recipe:anchor" and shot_value is not None:
                result = self.run_anchor(instance_id, uuid.UUID(str(shot_value)), payload)
            elif operation_key == "recipe:video" and shot_value is not None:
                result = self.run_video(instance_id, uuid.UUID(str(shot_value)), payload)
            else:
                raise ValueError(f"不支持的持久配方任务：{operation_key}")
        summary = _task_result_summary(operation_key, result)
        child_step_ids = _result_child_step_ids(result)
        if not child_step_ids:
            return MediaExecutionResult(
                payload=summary,
                status=StepStatus.AWAITING_REVIEW,
            )

        child_steps = self._repository.record_task_children(step_id, child_step_ids)
        child_statuses = {str(item["status"]) for item in child_steps}
        summary["parentStepId"] = str(step_id)
        summary["childStepIds"] = [str(item["stepId"]) for item in child_steps]
        summary["childStatuses"] = [
            {"stepId": str(item["stepId"]), "status": str(item["status"])}
            for item in child_steps
        ]
        failed_steps = [item for item in child_steps if item["status"] == StepStatus.FAILED.value]
        if failed_steps:
            failed_ids = ", ".join(str(item["stepId"]) for item in failed_steps)
            raise RuntimeError(f"子任务执行失败：{failed_ids}")
        if StepStatus.SUBMISSION_UNKNOWN.value in child_statuses:
            summary["message"] = "Provider 提交状态未知，请打开对应子任务进行人工对账"
            return MediaExecutionResult(payload=summary, status=StepStatus.SUBMISSION_UNKNOWN)
        active_statuses = {
            StepStatus.PENDING.value,
            StepStatus.SUBMITTING.value,
            StepStatus.QUEUED.value,
            StepStatus.RUNNING.value,
        }
        if child_statuses.intersection(active_statuses):
            summary["status"] = "running"
            summary["message"] = "子任务仍在生成或查询 Provider 状态"
            return MediaExecutionResult(
                payload=summary,
                status=StepStatus.QUEUED,
                next_retry_at=datetime.now(UTC) + timedelta(seconds=2),
            )
        summary["message"] = "全部子任务已完成，等待人工审核"
        return MediaExecutionResult(payload=summary, status=StepStatus.AWAITING_REVIEW)

    def run_creative_brief(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.CREATIVE.value:
            raise ValueError("只有创意阶段可以执行 AI 创意补全")
        self._accept_cost(payload, self._director_call_cost_micros)
        result = workflow.complete_creative_brief(
            uuid.UUID(str(instance["projectId"])),
            theme=str(instance["theme"]),
            target_duration_seconds=int(instance["targetDurationSeconds"]),
        )
        return {**result, "recipeInstanceId": str(instance_id), "status": "awaiting_review"}

    def run_story(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.STORY.value:
            raise ValueError("创意简报人工批准后才能生成故事候选")
        if not instance.get("progress", {}).get("creativeApproved", True):
            raise ValueError("创意简报尚未人工批准")
        self._accept_cost(payload, self._director_call_cost_micros)
        rules = _suggest_episode_rules(str(instance["theme"]))
        command = RecipeStoryCommand(
            idempotency_key=payload.idempotency_key,
            rewrite_instruction=(
                "生成三个原创、低压力的日常小事件。每个候选必须包含儿童行动、"
                "猫咪参与、一个小变化和温暖收尾；固定儿童与固定猫咪、原创柔和水彩画风、"
                "无对白。8至15秒只能设计一个场景；16至60秒仅在叙事必要时换场，"
                "场景数不得超过总时长除以15秒向上取整，每次换场必须说明叙事目的。"
                "自然猫不得直立劳动、持工具或产生人形肢体。"
            ),
        )
        result = workflow.run_story_strategies(
            uuid.UUID(str(instance["projectId"])),
            command,
        )
        candidates = list(result.get("candidates") or [])
        candidate_ids = tuple(uuid.UUID(str(item["id"])) for item in candidates)
        self._repository.store_suggested_episode_rules(instance_id, candidate_ids, rules)
        return {
            **result,
            "recipeInstanceId": str(instance_id),
            "suggestedEpisodeRules": rules.model_dump(mode="json", by_alias=True),
            "candidates": [
                {
                    **candidate,
                    "episodeRules": rules.model_dump(mode="json", by_alias=True),
                }
                for candidate in candidates
            ],
        }

    def run_story_events(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.STORY.value:
            raise ValueError("创意简报人工批准后才能生成事件方案")
        if not instance.get("progress", {}).get("creativeApproved", True):
            raise ValueError("创意简报尚未人工批准")
        self._accept_cost(
            payload,
            _multiply_cost(self._director_call_cost_micros, 6),
        )
        command = RecipeStoryCommand(
            idempotency_key=payload.idempotency_key,
            rewrite_instruction=(
                "生成三个原创、低压力、可在目标时长内完成的一人一猫事件方案。"
                "只输出儿童行动、猫咪参与、小变化、温暖收尾和换场必要性，"
                "不要提前扩写成长篇剧情脚本；固定儿童、固定猫咪、固定线条材质画风，"
                "无对白，自然猫不得直立劳动、持工具或产生人形肢体。"
            ),
        )
        result = workflow.run_story_event_strategies(
            uuid.UUID(str(instance["projectId"])),
            instance_id,
            command,
        )
        return {
            **result,
            "recipeInstanceId": str(instance_id),
            "status": "awaiting_review",
        }

    def run_story_script(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.STORY.value:
            raise ValueError("只有剧情阶段可以扩写完整剧情脚本")
        story_workflow = dict(instance.get("storyWorkflow") or {})
        if story_workflow.get("status") != "expand_script":
            raise ValueError("请先从最新一批事件方案中人工选择一个事件")
        self._accept_cost(
            payload,
            _multiply_cost(self._director_call_cost_micros, 2),
        )
        command = RecipeStoryCommand(
            idempotency_key=payload.idempotency_key,
            rewrite_instruction=(
                "把已选择事件扩写为完整、可拍摄的剧情脚本，明确因果链、稳定 sceneKey、"
                "场景目的、换场原因、声音计划、角色造型与场景资产需求；保持无对白和"
                "固定儿童、猫咪及画风约束。"
            ),
        )
        result = workflow.expand_selected_story_event(
            uuid.UUID(str(instance["projectId"])),
            instance_id,
            command,
        )
        story = dict(result.get("story") or {})
        story_id_value = story.get("id")
        if story_id_value is None:
            raise RuntimeError("剧情脚本扩写没有返回版本标识")
        rules = _suggest_episode_rules(str(instance["theme"]))
        self._repository.store_suggested_episode_rules(
            instance_id,
            (uuid.UUID(str(story_id_value)),),
            rules,
        )
        return {
            **result,
            "recipeInstanceId": str(instance_id),
            "revisionId": str(story_id_value),
            "suggestedEpisodeRules": rules.model_dump(mode="json", by_alias=True),
            "status": "awaiting_review",
        }

    def run_character_design(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.CHARACTER_DESIGN.value:
            raise ValueError("故事与 EpisodeRules 人工批准后才能生成角色设计")
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[instance["qualityTier"]]
        candidate_count = tier.character_design_candidate_count
        self._accept_cost(
            payload,
            _multiply_cost(self._image_call_cost_micros, candidate_count * 3),
        )
        prepared = self._repository.prepare_character_design(
            instance_id,
            idempotency_key=payload.idempotency_key,
            candidate_count=candidate_count,
        )
        batches = [
            workflow.create_generation_batch(CharacterDesignBatchDraft.model_validate(batch))
            for batch in prepared["batches"]
        ]
        return {
            **prepared,
            "status": "generating",
            "generationBatches": batches,
        }

    def run_storyboard(
        self,
        instance_id: uuid.UUID,
        payload: StoryboardRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.STORYBOARD.value:
            raise ValueError("角色设计全部人工批准后才能生成分镜")
        progress = dict(instance.get("progress") or {})
        if not progress.get("episodeRulesLocked"):
            raise ValueError("EpisodeRules 尚未随故事审核锁定")
        if not progress.get("characterDesignApproved", True):
            raise ValueError("三个角色设计槽位尚未全部人工批准")
        if int(progress.get("shotCount") or 0) > 0:
            raise ValueError("当前分镜已经生成，请先完成审核或创建明确的新版本")
        reference_asset_ids = tuple(payload.reference_asset_ids)
        if payload.creation_mode is StoryboardCreationMode.FROM_CHARACTERS:
            self._repository.validate_storyboard_character_references(
                instance_id,
                reference_asset_ids,
            )
        self._accept_cost(payload, self._director_call_cost_micros)
        durations = split_shot_durations(int(instance["targetDurationSeconds"]))
        storyboard = workflow.create_storyboard(
            uuid.UUID(str(instance["projectId"])),
            exact_durations=durations,
            healing_recipe=True,
            idempotency_key=payload.idempotency_key,
            creation_mode=payload.creation_mode.value,
            reference_asset_ids=reference_asset_ids,
            instruction=payload.instruction,
        )
        return self._repository.materialize_storyboard(instance_id, storyboard)

    def run_anchor(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        if self._shot_workflow is None:
            raise RuntimeError("视觉锚点工作流未配置")
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["stage"] != RecipeStage.ANCHORS.value:
            raise ValueError("当前阶段不能生成视觉锚点")
        if not instance.get("progress", {}).get("storyboardApproved", True):
            raise ValueError("分镜尚未人工批准")
        self._repository.validate_anchor_prompt_readiness(instance_id, shot_id)
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[instance["qualityTier"]]
        self._accept_cost(
            payload,
            _multiply_cost(self._image_call_cost_micros, tier.anchor_candidate_count),
        )
        results = [
            self._shot_workflow.generate_anchor(
                shot_id,
                allow_paid_generation=True,
                regenerate=index > 0 or payload.reason is not None,
                reason=(payload.reason if index == 0 else f"配方锚点候选 {index + 1}"),
                request_idempotency_key=f"{payload.idempotency_key}:anchor:{index + 1}",
            )
            for index in range(tier.anchor_candidate_count)
        ]
        return {"shotId": str(shot_id), "status": "awaiting_review", "candidates": results}

    def run_video(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        if self._shot_workflow is None:
            raise RuntimeError("逐镜视频工作流未配置")
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["stage"] != RecipeStage.VIDEO.value:
            raise ValueError("全部视觉锚点人工批准后才能生成视频")
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[instance["qualityTier"]]
        self._accept_cost(
            payload,
            _multiply_cost(self._video_call_cost_micros, tier.video_candidate_count),
        )
        results = [
            self._shot_workflow.generate_video(
                shot_id,
                allow_paid_generation=True,
                regenerate=index > 0 or payload.reason is not None,
                reason=(payload.reason if index == 0 else f"配方视频候选 {index + 1}"),
                request_idempotency_key=f"{payload.idempotency_key}:video:{index + 1}",
            )
            for index in range(tier.video_candidate_count)
        ]
        return {"shotId": str(shot_id), "status": "submitted", "candidates": results}

    def run_sequence(
        self,
        instance_id: uuid.UUID,
        payload: RecipeSequenceRunRequest,
    ) -> dict[str, Any]:
        if self._sequence_workflow is None:
            raise RuntimeError("最终音画工作流未配置")
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["stage"] != RecipeStage.SEQUENCE.value:
            raise ValueError("全部视频镜头人工批准后才能合成最终音画")
        self._accept_cost(payload, 0)
        sequence = self._sequence_workflow.build_project_sequence(
            uuid.UUID(str(instance["projectId"])),
            transitions={item.after_shot_id: item.transition for item in payload.transitions},
            request_idempotency_key=payload.idempotency_key,
        )
        return {
            "id": str(sequence.id),
            "projectId": str(sequence.project_id),
            "revision": sequence.revision,
            "status": sequence.status.value,
            "durationMs": sequence.plan.duration_ms,
            "renderedAssetId": (
                None if sequence.rendered_asset_id is None else str(sequence.rendered_asset_id)
            ),
        }

    def compile_group(self, group_id: uuid.UUID) -> dict[str, Any]:
        group = self._repository.get_group(group_id)
        instance_id = group.get("recipeInstanceId")
        if instance_id is None:
            raise ValueError("该分组未绑定可执行的一人一猫配方")
        instance = self.get_instance(uuid.UUID(str(instance_id)))
        return {
            "groupId": str(group_id),
            "recipeInstanceId": str(instance_id),
            "projectId": instance["projectId"],
            "phase": instance["phase"],
            "primaryAction": instance["primaryAction"],
            "blocker": instance["currentBlocker"],
            "estimatedCostMicros": instance["estimatedCostMicros"],
            "costEstimateStatus": instance["costEstimateStatus"],
            "costEstimateLabel": instance["costEstimateLabel"],
            "stopsAtReviewGate": instance["phase"] != RecipePhaseKey.COMPLETE.value,
            "reviewStages": instance["reviewStages"],
        }

    def run_group(
        self,
        group_id: uuid.UUID,
        payload: CanvasGroupRunRequest,
    ) -> dict[str, Any]:
        group = self._repository.get_group(group_id)
        if group.get("lifecycleStatus") != "active":
            raise ValueError("已解组或归档的分组不能执行")
        instance_id_value = group.get("recipeInstanceId")
        if instance_id_value is None:
            raise ValueError("该分组未绑定可执行的一人一猫配方")
        instance_id = uuid.UUID(str(instance_id_value))
        instance = self.get_instance(instance_id)
        self._accept_cost(payload, instance["estimatedCostMicros"])
        phase = RecipePhaseKey(instance["phase"])

        if phase is RecipePhaseKey.COMPLETE:
            return self._group_stop(group_id, instance, "complete")
        if phase is RecipePhaseKey.CREATIVE:
            brief = instance.get("creativeBrief") or {}
            if int(brief.get("revision") or 0) >= 2:
                return self._group_stop(group_id, instance, "awaiting_review")
            result = self.run_creative_brief(instance_id, payload)
        elif phase is RecipePhaseKey.STORY:
            story_workflow = dict(instance.get("storyWorkflow") or {})
            story_status = str(story_workflow.get("status") or "generate_events")
            if story_status in {"select_event", "approve_script"}:
                return self._group_stop(group_id, instance, "awaiting_review")
            if story_status == "expand_script":
                result = self.run_story_script(instance_id, payload)
            else:
                result = self.run_story_events(instance_id, payload)
        elif phase is RecipePhaseKey.CHARACTER_DESIGN:
            character_design = instance.get("characterDesign") or {}
            if character_design.get("status") in {"generating", "awaiting_review"}:
                return self._group_stop(group_id, instance, "awaiting_review")
            result = self.run_character_design(instance_id, payload)
        elif phase is RecipePhaseKey.STORYBOARD:
            if int(instance.get("progress", {}).get("shotCount") or 0) > 0:
                return self._group_stop(group_id, instance, "awaiting_review")
            result = self.run_storyboard(
                instance_id,
                StoryboardRecipeRunRequest(
                    idempotencyKey=payload.idempotency_key,
                    acceptEstimatedCostMicros=payload.accept_estimated_cost_micros,
                    reason=payload.reason,
                    creationMode=StoryboardCreationMode.FROM_STORY,
                ),
            )
        elif phase is RecipePhaseKey.RENDER:
            result = self._run_next_render_step(instance_id, instance, payload)
        else:
            if instance.get("sequenceCandidate") is not None:
                return self._group_stop(group_id, instance, "awaiting_review")
            result = self.run_sequence(
                instance_id,
                RecipeSequenceRunRequest(
                    idempotencyKey=payload.idempotency_key,
                    acceptEstimatedCostMicros=payload.accept_estimated_cost_micros,
                    reason=payload.reason,
                    transitions=[],
                ),
            )
        return {
            "groupId": str(group_id),
            "recipeInstanceId": str(instance_id),
            "executedPhase": phase.value,
            "status": "awaiting_review",
            "stoppedAtReviewGate": True,
            "result": result,
        }

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.save_group_template(group_id)

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        return self._repository.ungroup(
            group_id,
            expected_revision=expected_revision,
        )

    def convert_group_to_shots(self, group_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.convert_to_shot_groups(group_id)

    def group_download_manifest(self, group_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.group_download_assets(group_id)

    def build_group_download(self, group_id: uuid.UUID) -> tuple[bytes, str]:
        if self._asset_root is None:
            raise RuntimeError("批量下载未配置资产根目录")
        manifest = self._repository.group_download_assets(group_id)
        missing: list[dict[str, str]] = []
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in manifest.get("assets", []):
                storage_key = str(item.get("storageKey") or "")
                asset_id = str(item.get("id") or "unknown")
                if not storage_key:
                    missing.append({"assetId": asset_id, "reason": "资产没有存储键"})
                    continue
                source = (self._asset_root / Path(storage_key)).resolve()
                if not source.is_relative_to(self._asset_root):
                    missing.append({"assetId": asset_id, "reason": "资产路径越出允许目录"})
                    continue
                if not source.is_file():
                    missing.append({"assetId": asset_id, "reason": "资产文件不存在"})
                    continue
                role = str(item.get("role") or "asset").replace("/", "-")
                archive.write(source, f"media/{role}/{asset_id}{source.suffix.lower()}")
            manifest_document = {**manifest, "missing": missing}
            archive.writestr(
                "manifest.json",
                json.dumps(manifest_document, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "人工审核清单.md",
                "# 一人一猫成片审核清单\n\n"
                "- [ ] 创意简报已批准\n"
                "- [ ] 故事与 EpisodeRules 已批准\n"
                "- [ ] 儿童、猫咪、同框比例角色图已批准\n"
                "- [ ] 分镜已批准\n"
                "- [ ] 每镜锚点与视频已批准\n"
                "- [ ] 原生音轨与最终技术 QC 已通过\n"
                "- [ ] 完整播放并批准最终成片\n\n"
                f"缺失或不可读取文件：{len(missing)} 个。详情见 manifest.json。\n",
            )
        title = str(manifest.get("title") or "one-child-one-cat")
        safe_title = "".join(character for character in title if character not in '<>:"/\\|?*')
        return buffer.getvalue(), f"{safe_title or 'one-child-one-cat'}-assets.zip"

    def _run_next_render_step(
        self,
        instance_id: uuid.UUID,
        instance: dict[str, Any],
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        shots = list(instance.get("shots") or [])
        for shot in shots:
            if shot.get("selectedAnchorAssetId"):
                continue
            candidates = [
                item
                for item in shot.get("anchorCandidates") or []
                if item.get("status") not in {"rejected", "stale", "failed"}
            ]
            if candidates:
                return self._group_stop(
                    uuid.UUID(str(instance["groupId"])),
                    instance,
                    "awaiting_review",
                )
            return self.run_anchor(
                instance_id,
                uuid.UUID(str(shot["shotId"])),
                payload,
            )
        for shot in shots:
            if shot.get("selectedVideoAssetId"):
                continue
            candidates = [
                item
                for item in shot.get("videoCandidates") or []
                if item.get("status") not in {"rejected", "stale", "failed"}
            ]
            if candidates:
                return self._group_stop(
                    uuid.UUID(str(instance["groupId"])),
                    instance,
                    "awaiting_review",
                )
            return self.run_video(
                instance_id,
                uuid.UUID(str(shot["shotId"])),
                payload,
            )
        return self._group_stop(
            uuid.UUID(str(instance["groupId"])),
            instance,
            "awaiting_review",
        )

    @staticmethod
    def _group_stop(
        group_id: uuid.UUID,
        instance: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        return {
            "groupId": str(group_id),
            "recipeInstanceId": str(instance["id"]),
            "phase": instance["phase"],
            "status": status,
            "stoppedAtReviewGate": status == "awaiting_review",
            "blocker": instance.get("currentBlocker"),
            "primaryAction": instance.get("primaryAction"),
        }

    def _require_story_workflow(self) -> StoryRecipeWorkflow:
        if self._story_workflow is None:
            raise RuntimeError("故事工作流未配置")
        return self._story_workflow

    def _project_instance(self, source: dict[str, Any]) -> dict[str, Any]:
        document = _recipe_projection(source)
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[document["qualityTier"]]
        if document["phase"] == RecipePhaseKey.STORY.value:
            story_status = str(
                (document.get("storyWorkflow") or {}).get("status") or "generate_events"
            )
            estimate = _multiply_cost(
                self._director_call_cost_micros,
                2 if story_status == "expand_script" else 6,
            )
            if story_status in {"select_event", "approve_script"}:
                estimate = 0
        elif document["phase"] in {
            RecipePhaseKey.CREATIVE.value,
            RecipePhaseKey.STORYBOARD.value,
        }:
            estimate = self._director_call_cost_micros
        elif document["phase"] == RecipePhaseKey.CHARACTER_DESIGN.value:
            estimate = _multiply_cost(
                self._image_call_cost_micros,
                tier.character_design_candidate_count * 3,
            )
        elif document["stage"] == RecipeStage.ANCHORS.value:
            estimate = _multiply_cost(
                self._image_call_cost_micros,
                tier.anchor_candidate_count,
            )
        elif document["stage"] == RecipeStage.VIDEO.value:
            estimate = _multiply_cost(
                self._video_call_cost_micros,
                tier.video_candidate_count,
            )
        else:
            estimate = 0
        estimate_status = "unmetered_paid" if estimate is None else "metered"
        return {
            **document,
            "estimatedCostMicros": estimate,
            "costEstimateStatus": estimate_status,
            "costEstimateLabel": (
                "付费调用·暂未计量"
                if estimate is None
                else f"预计费用 ¥{estimate / 1_000_000:.3f}"
            ),
        }

    def _validate_enqueued_operation(
        self,
        instance: dict[str, Any],
        *,
        operation_key: str,
        payload: PaidRecipeRunRequest,
        shot_id: uuid.UUID | None,
    ) -> None:
        if instance.get("lifecycleStatus") != "active":
            raise ValueError("已归档的配方不能提交新任务")
        required_phase = {
            "recipe:creative": RecipePhaseKey.CREATIVE.value,
            "recipe:story": RecipePhaseKey.STORY.value,
            "recipe:story_events": RecipePhaseKey.STORY.value,
            "recipe:story_script": RecipePhaseKey.STORY.value,
            "recipe:character_design": RecipePhaseKey.CHARACTER_DESIGN.value,
            "recipe:storyboard": RecipePhaseKey.STORYBOARD.value,
            "recipe:sequence": RecipePhaseKey.EXPORT.value,
        }.get(operation_key)
        required_stage = {
            "recipe:anchor": RecipeStage.ANCHORS.value,
            "recipe:video": RecipeStage.VIDEO.value,
        }.get(operation_key)
        if required_phase is not None and instance["phase"] != required_phase:
            raise ValueError(f"当前阶段不能执行 {operation_key}")
        if required_stage is not None and instance["stage"] != required_stage:
            raise ValueError(f"当前阶段不能执行 {operation_key}")
        if operation_key in {"recipe:anchor", "recipe:video"} and shot_id is None:
            raise ValueError("逐镜任务必须指定镜头")
        if (
            operation_key == "canvas-group:run"
            and instance["phase"] == RecipePhaseKey.COMPLETE.value
        ):
            raise ValueError("已完成的分组没有可执行阶段")
        estimated_cost = instance["estimatedCostMicros"]
        if operation_key in {"recipe:creative", "recipe:story"}:
            estimated_cost = self._director_call_cost_micros
        elif operation_key == "recipe:story_events":
            estimated_cost = _multiply_cost(self._director_call_cost_micros, 6)
        elif operation_key == "recipe:story_script":
            estimated_cost = _multiply_cost(self._director_call_cost_micros, 2)
        self._accept_cost(payload, estimated_cost)

    @staticmethod
    def _accept_cost(
        payload: PaidRecipeRunRequest,
        estimated_cost_micros: int | None,
    ) -> None:
        if estimated_cost_micros is None:
            if payload.accept_estimated_cost_micros != 0:
                raise ValueError(
                    "该 Provider 调用属于付费但暂未计量；"
                    "请以 0 作为未计量费用确认值，实际费用以供应商账单为准"
                )
            return
        if payload.accept_estimated_cost_micros != estimated_cost_micros:
            raise ValueError(
                "费用预估已变化："
                f"当前 {estimated_cost_micros}，提交 {payload.accept_estimated_cost_micros}"
            )


def _multiply_cost(unit_cost_micros: int | None, count: int) -> int | None:
    return None if unit_cost_micros is None else unit_cost_micros * count


def _recipe_projection(source: dict[str, Any]) -> dict[str, Any]:
    document = dict(source)
    progress = dict(document.get("progress") or {})
    shot_count = int(progress.get("shotCount") or 0)
    approved_anchors = int(progress.get("approvedAnchorCount") or 0)
    approved_videos = int(progress.get("approvedVideoCount") or 0)
    creative_approved = bool(progress.get("creativeApproved", True))
    creative_completed = bool(progress.get("creativeCompleted", True))
    story_approved = bool(progress.get("storyApproved"))
    character_design_approved = bool(progress.get("characterDesignApproved", True))
    storyboard_approved = bool(progress.get("storyboardApproved", shot_count > 0))
    story_candidates_exist = bool(document.get("storyCandidates"))
    story_workflow = dict(document.get("storyWorkflow") or {})
    story_workflow_status = str(story_workflow.get("status") or "generate_events")
    legacy_story_candidates = any(
        not candidate.get("sourceEventCandidateId")
        for candidate in document.get("storyCandidates") or []
    )
    sequence_ready = bool(progress.get("sequenceReady"))
    final_approved = bool(progress.get("finalApproved"))

    if final_approved:
        stage = RecipeStage.COMPLETE
        phase = RecipePhaseKey.COMPLETE
        blocker = None
        primary_action = "导出最终成片"
    elif sequence_ready or (shot_count > 0 and approved_videos >= shot_count):
        stage = RecipeStage.SEQUENCE
        phase = RecipePhaseKey.EXPORT
        blocker = "最终音画尚未人工批准"
        primary_action = "审核最终成片" if sequence_ready else "合成最终成片"
    elif storyboard_approved and shot_count > 0 and approved_anchors >= shot_count:
        stage = RecipeStage.VIDEO
        phase = RecipePhaseKey.RENDER
        blocker = "有视频镜头待生成或审核"
        primary_action = "生成下一镜视频"
    elif storyboard_approved and shot_count > 0:
        stage = RecipeStage.ANCHORS
        phase = RecipePhaseKey.RENDER
        blocker = "有视觉锚点待生成或审核"
        primary_action = "生成下一镜视觉锚点"
    elif story_approved and character_design_approved:
        stage = RecipeStage.STORYBOARD
        phase = RecipePhaseKey.STORYBOARD
        blocker = "分镜尚未生成并确认" if shot_count == 0 else "分镜等待人工审核"
        primary_action = "生成分镜" if shot_count == 0 else "审核分镜"
    elif story_approved:
        stage = RecipeStage.CONCEPT
        phase = RecipePhaseKey.CHARACTER_DESIGN
        blocker = "儿童、猫咪与同框比例角色图片尚未全部批准"
        primary_action = "审核角色设计" if document.get("characterDesign") else "生成角色设计"
    elif creative_approved:
        stage = RecipeStage.CONCEPT
        phase = RecipePhaseKey.STORY
        if legacy_story_candidates and not document.get("storyEvents"):
            blocker = "旧版剧情候选等待人工选择与规则确认"
            primary_action = "选择并批准故事"
        elif story_workflow_status == "select_event":
            blocker = "三个事件方案等待人工选择"
            primary_action = "选择一个事件方案"
        elif story_workflow_status == "expand_script":
            blocker = "所选事件尚未扩写为完整剧情脚本"
            primary_action = "扩写剧情脚本"
        elif story_workflow_status == "approve_script" or story_candidates_exist:
            blocker = "完整剧情脚本等待编辑、批准并锁定 EpisodeRules"
            primary_action = "审核剧情脚本"
        else:
            blocker = "事件方案尚未生成"
            primary_action = "生成三个事件方案"
    else:
        stage = RecipeStage.CONCEPT
        phase = RecipePhaseKey.CREATIVE
        blocker = "创意简报尚未人工批准"
        primary_action = "审核创意简报" if creative_completed else "补全创意输入"

    target_duration = int(document["targetDurationSeconds"])
    document.update(
        {
            "stage": stage.value,
            "phase": phase.value,
            "shotDurations": list(split_shot_durations(target_duration)),
            "currentBlocker": blocker,
            "primaryAction": primary_action,
            "reviewStages": [
                {"key": "creative", "complete": creative_approved},
                {"key": "story", "complete": story_approved},
                {"key": "character_design", "complete": character_design_approved},
                {"key": "storyboard", "complete": storyboard_approved},
                {
                    "key": "render",
                    "complete": (
                        shot_count > 0
                        and approved_anchors >= shot_count
                        and approved_videos >= shot_count
                    ),
                },
                {"key": "export", "complete": final_approved},
            ],
        }
    )
    return document


def _suggest_episode_rules(theme: str) -> EpisodeRules:
    indoor_markers = ("室内", "屋", "厨房", "窗", "睡前", "床", "餐桌")
    environment = "indoor" if any(marker in theme for marker in indoor_markers) else "outdoor"
    return EpisodeRules(
        personWardrobe="沿用儿童 Canon 身份，本集固定米白上衣与棕色背带裤",
        timeWeather="依据主题确定整集时间推进与天气基调，逐场细节由场景连续性规则锁定",
        mainScene="以批准故事的 scenes 为准；只有叙事必要时换场",
        environment=environment,
        coreProps=[],
        catBehaviorMode=CatBehaviorMode.NATURAL,
        soundPlan=SoundPlan(
            ambient=["与场景一致的轻柔原生环境声"],
            foley=["关键动作的克制拟音"],
            musicMood="轻柔、留白、不抢动作的治愈配乐",
            dialoguePolicy="none",
        ),
        stylePositive=list(CANON_V3_STYLE_POSITIVE),
        styleExcluded=list(CANON_V3_STYLE_NEGATIVE),
        canonProfileId=CANON_V3_PROFILE_ID,
    )


def _task_kind(operation_key: str, instance: dict[str, Any]) -> StepKind:
    if operation_key in {"recipe:character_design", "recipe:anchor"}:
        return StepKind.IMAGE
    if operation_key in {"recipe:video", "recipe:sequence"}:
        return StepKind.VIDEO
    if operation_key == "canvas-group:run":
        phase = str(instance["phase"])
        if phase == RecipePhaseKey.CHARACTER_DESIGN.value:
            return StepKind.IMAGE
        if phase in {RecipePhaseKey.RENDER.value, RecipePhaseKey.EXPORT.value}:
            return StepKind.VIDEO
    return StepKind.DIRECTOR


def _task_canvas_node_id(project_id: uuid.UUID, operation_key: str) -> uuid.UUID:
    semantic_key = {
        "recipe:creative": "creative-brief-approval",
        "recipe:story": "story-planner",
        "recipe:story_events": "story-planner",
        "recipe:story_script": "story-script-expander",
        "recipe:character_design": "character-design-approval",
        "recipe:storyboard": "storyboard-director",
        "recipe:anchor": "recipe-anchor-stage",
        "recipe:video": "recipe-video-stage",
        "recipe:sequence": "recipe-sequence",
        "canvas-group:run": "story-planner",
    }[operation_key]
    return uuid.uuid5(project_id, semantic_key)


def _task_result_summary(operation_key: str, result: dict[str, Any]) -> dict[str, object]:
    candidates = result.get("candidates") or result.get("generationBatches") or []
    return {
        "operationKey": operation_key,
        "status": str(result.get("status") or "awaiting_review"),
        "message": "任务已完成，等待人工审核",
        "candidateCount": len(candidates) if isinstance(candidates, list) else 0,
        "outputCount": len(candidates) if isinstance(candidates, list) else 1,
        "recipeInstanceId": result.get("recipeInstanceId"),
        "shotId": result.get("shotId"),
        "assetId": result.get("renderedAssetId") or result.get("id"),
        "revisionId": result.get("revisionId"),
    }


def _result_child_step_ids(result: dict[str, Any]) -> tuple[uuid.UUID, ...]:
    """Collect durable child work created by a recipe operation without guessing asset IDs."""

    ordered: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()

    def add(value: object) -> None:
        if value is None or value == "":
            return
        try:
            step_id = uuid.UUID(str(value))
        except (TypeError, ValueError):
            return
        if step_id not in seen:
            seen.add(step_id)
            ordered.append(step_id)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate_ids = value.get("candidateStepIds")
            if isinstance(candidate_ids, list):
                for item in candidate_ids:
                    add(item)
            if "stepId" in value:
                add(value["stepId"])
            for key in ("candidates", "candidateSteps", "generationBatches", "result"):
                nested = value.get(key)
                if nested is not None:
                    visit(nested)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return tuple(ordered)
