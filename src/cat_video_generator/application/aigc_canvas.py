"""Application service for auditable story strategy and typed canvas commands."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from typing import Any, Protocol

from ..domain.aigc_canvas import (
    PromptRunDraft,
    StoryboardPlanOutput,
    StoryBrief,
    StoryCandidateOutput,
    StoryEventCandidateOutput,
    StoryScorecard,
    StoryStrategy,
    allocate_bounded_durations,
    validate_story_event_candidate,
    validate_story_inputs,
    validate_story_scene_plan,
)
from ..domain.universal_canvas import ProviderEditCapability
from .ports import DirectorGateway, GatewayError


class CanvasRepository(Protocol):
    def get_current_brief(self, project_id: uuid.UUID) -> tuple[uuid.UUID, Any]: ...

    def list_subjects(self, project_id: uuid.UUID) -> tuple[Any, ...]: ...

    def begin_generation_attempt(self, **values: object) -> tuple[dict[str, Any], bool]: ...

    def begin_prompt_run(self, **values: object) -> tuple[uuid.UUID, uuid.UUID]: ...

    def complete_prompt_run(self, prompt_id: uuid.UUID, **values: object) -> None: ...

    def save_story_candidate(self, **values: object) -> dict[str, Any]: ...

    def save_story_event_candidate(self, **values: object) -> dict[str, Any]: ...

    def get_selected_story_event(self, recipe_instance_id: uuid.UUID) -> dict[str, Any]: ...

    def finish_generation_attempt(self, attempt_id: str, **values: object) -> None: ...

    def save_brief(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def create_subject(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def bind_canvas_node_assets(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def create_subject_revision(self, subject_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def create_subject_completion_run(
        self,
        project_id: uuid.UUID,
        payload: Any,
        *,
        provider: str,
        model: str,
    ) -> dict[str, Any]: ...

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, Any]: ...

    def apply_subject_completion(self, run_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]: ...

    def list_visual_presets(self) -> list[dict[str, Any]]: ...

    def apply_visual_preset(self, project_id: uuid.UUID, preset_key: str) -> dict[str, Any]: ...

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def update_episode_visual_profile(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def compile_storyboard_prompts(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]: ...

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]: ...

    def get_video_filmstrip(self, asset_id: uuid.UUID, *, frame_count: int) -> dict[str, Any]: ...

    def save_node_generation_config(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def list_provider_capabilities(
        self, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]: ...

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]: ...

    def get_storyboard_context(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def save_storyboard_plan(self, project_id: uuid.UUID, **values: object) -> dict[str, Any]: ...

    def update_shot_beat(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def create_generation_attempt(self, payload: Any) -> dict[str, Any]: ...

    def retry_generation_attempt(self, attempt_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def review_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, Any]: ...

    def get_canvas(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def save_canvas_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def archive_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
        reason: str | None,
    ) -> dict[str, Any]: ...

    def restore_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> dict[str, Any]: ...

    def list_canvas_templates(self) -> list[dict[str, Any]]: ...

    def instantiate_template(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def create_canvas_node(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def create_canvas_edge(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def delete_canvas_edge(self, edge_id: uuid.UUID) -> dict[str, Any]: ...

    def create_generation_batch(self, payload: Any) -> dict[str, Any]: ...

    def create_video_edit_recipe(self, payload: Any) -> dict[str, Any]: ...

    def update_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def replace_video_edit_annotations(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def compile_video_edit_recipe(
        self, recipe_id: uuid.UUID, capability: ProviderEditCapability
    ) -> dict[str, Any]: ...

    def submit_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        payload: Any,
        *,
        image_provider: str,
        image_model: str,
    ) -> dict[str, Any]: ...

    def events(
        self, project_id: uuid.UUID, *, after_sequence: int = 0
    ) -> tuple[dict[str, Any], ...]: ...


class AigcCanvasService:
    """Coordinates paid story calls without hiding persistence boundaries."""

    def __init__(
        self,
        *,
        repository: CanvasRepository,
        director: DirectorGateway,
        provider_name: str,
        video_edit_capability: ProviderEditCapability | None = None,
    ) -> None:
        self._repository = repository
        self._director = director
        self._provider_name = provider_name
        self._image_model = getattr(director, "image_model", director.model)
        self._video_edit_capability = video_edit_capability or ProviderEditCapability(
            provider=provider_name,
            model=getattr(director, "video_model", director.model),
            supportsDirectAnnotations=False,
            maxDirectReferenceImages=1,
            supportsControlAnchors=True,
            imageCallCostMicros=0,
            videoCallCostMicros=0,
        )

    def save_brief(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.save_brief(project_id, payload)

    def complete_creative_brief(
        self,
        project_id: uuid.UUID,
        *,
        theme: str,
        target_duration_seconds: int,
    ) -> dict[str, Any]:
        prompt = (
            "你是治愈系无对白竖屏短片的创意编辑。把用户的一句话主题补全为结构化创意简报。"
            "保持固定儿童、固定猫咪、原创二维水彩、单一低压力事件、无对白、原生环境声。"
            "constraints 必须明确季节天气、主要场景、核心事件、核心道具、猫咪行为模式和温暖收尾。\n"
            f"用户主题：{theme}\n目标时长：{target_duration_seconds} 秒；画幅：9:16。"
        )
        result = self._director.generate_structured(
            prompt=prompt,
            schema=StoryBrief.model_json_schema(),
            output_name="HealingCreativeBrief",
        )
        proposed = StoryBrief.model_validate(result.payload)
        fixed_constraints = [
            *proposed.constraints,
            "固定儿童与固定猫咪身份",
            "原创二维水彩画风",
            "禁止对白",
            "原生环境声、动作声与轻音乐",
        ]
        brief = proposed.model_copy(
            update={
                "theme": theme,
                "aspect_ratio": "9:16",
                "target_duration_seconds": target_duration_seconds,
                "constraints": list(dict.fromkeys(fixed_constraints))[:30],
            }
        )
        return self._repository.save_brief(project_id, brief)

    def create_subject(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.create_subject(project_id, payload)

    def list_subjects(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        return [
            {
                "id": str(item.id),
                "projectId": str(project_id),
                "revisionId": str(item.revision_id),
                "revision": item.revision,
                "status": item.status,
                **item.draft.model_dump(mode="json", by_alias=True),
            }
            for item in self._repository.list_subjects(project_id)
        ]

    def bind_canvas_node_assets(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.bind_canvas_node_assets(
            node_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def create_subject_revision(self, subject_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.create_subject_revision(subject_id, payload)

    def create_subject_completion_run(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.create_subject_completion_run(
            project_id,
            payload,
            provider=self._provider_name,
            model=self._director.model,
        )

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_subject_completion_run(run_id)

    def apply_subject_completion(self, run_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.apply_subject_completion(run_id, payload)

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]:
        return self._repository.list_project_assets(project_id, media_kind=media_kind)

    def list_visual_presets(self) -> list[dict[str, Any]]:
        return self._repository.list_visual_presets()

    def apply_visual_preset(self, project_id: uuid.UUID, preset_key: str) -> dict[str, Any]:
        return self._repository.apply_visual_preset(project_id, preset_key)

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_episode_visual_profile(project_id)

    def update_episode_visual_profile(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.update_episode_visual_profile(
            project_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]:
        return self._repository.create_video_filmstrip_run(asset_id, frame_count=frame_count)

    def get_video_filmstrip(self, asset_id: uuid.UUID, *, frame_count: int) -> dict[str, Any]:
        return self._repository.get_video_filmstrip(asset_id, frame_count=frame_count)

    def save_node_generation_config(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.save_node_generation_config(
            node_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def list_provider_capabilities(self, *, media_kind: str | None = None) -> list[dict[str, Any]]:
        return self._repository.list_provider_capabilities(media_kind=media_kind)

    def run_story_event_strategies(
        self,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        brief_id, brief = self._repository.get_current_brief(project_id)
        subjects = self._repository.list_subjects(project_id)
        validate_story_inputs(brief, tuple(item.draft for item in subjects))
        subject_snapshot = [
            {
                "subjectId": str(item.id),
                "revisionId": str(item.revision_id),
                **item.draft.model_dump(mode="json", by_alias=True),
            }
            for item in subjects
        ]
        input_snapshot = {
            "briefId": str(brief_id),
            "brief": brief.model_dump(mode="json", by_alias=True),
            "subjects": subject_snapshot,
            "recipeInstanceId": str(recipe_instance_id),
            "creativeDirection": str(getattr(payload, "rewrite_instruction", "") or ""),
            "narrativeContract": {
                "candidateCount": 3,
                "requiredBeats": [
                    "childAction",
                    "catParticipation",
                    "smallChange",
                    "warmEnding",
                ],
                "dialogueAllowed": False,
            },
        }
        input_hash = _json_hash(input_snapshot)
        idempotency_key = (
            getattr(payload, "idempotency_key", None)
            or hashlib.sha256(
                f"{recipe_instance_id}:story-events:{input_hash}".encode()
            ).hexdigest()
        )
        attempt, created = self._repository.begin_generation_attempt(
            project_id=project_id,
            business_object_type="recipe_story_event_batch",
            business_object_id=recipe_instance_id,
            idempotency_key=idempotency_key,
            provider=self._provider_name,
            model=self._director.model,
            request=input_snapshot,
        )
        if not created:
            return attempt

        attempt_id = str(attempt["id"])
        batch_id = uuid.UUID(attempt_id)
        candidates: list[dict[str, Any]] = []
        try:
            for candidate_index, strategy in enumerate(
                (
                    StoryStrategy.RELATIONSHIP,
                    StoryStrategy.PROBLEM_SOLVING,
                    StoryStrategy.TWIST_HOOK,
                ),
                1,
            ):
                candidate, prompt_id = self._generate_event_candidate(
                    project_id=project_id,
                    recipe_instance_id=recipe_instance_id,
                    strategy=strategy,
                    input_snapshot=input_snapshot,
                )
                score, _critic_prompt_id = self._score_event_candidate(
                    project_id=project_id,
                    recipe_instance_id=recipe_instance_id,
                    strategy=strategy,
                    candidate=candidate,
                    input_snapshot=input_snapshot,
                    parent_prompt_id=prompt_id,
                )
                candidates.append(
                    self._repository.save_story_event_candidate(
                        project_id=project_id,
                        recipe_instance_id=recipe_instance_id,
                        brief_id=brief_id,
                        batch_id=batch_id,
                        candidate_index=candidate_index,
                        strategy=strategy,
                        candidate=candidate,
                        scorecard=score,
                        generation_prompt_id=prompt_id,
                    )
                )
        except GatewayError as exc:
            status = "submission_unknown" if exc.submission_unknown else "failed"
            self._repository.finish_generation_attempt(
                attempt_id,
                status=status,
                error={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
            )
            raise
        except Exception as exc:
            self._repository.finish_generation_attempt(
                attempt_id,
                status="failed",
                error={"code": "internal", "message": str(exc)},
            )
            raise
        self._repository.finish_generation_attempt(
            attempt_id,
            status="succeeded",
            response={
                "candidateIds": [str(item["id"]) for item in candidates],
                "candidateCount": len(candidates),
            },
        )
        return {"id": attempt_id, "status": "succeeded", "candidates": candidates}

    def expand_selected_story_event(
        self,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        event = self._repository.get_selected_story_event(recipe_instance_id)
        brief_id, brief = self._repository.get_current_brief(project_id)
        subjects = self._repository.list_subjects(project_id)
        validate_story_inputs(brief, tuple(item.draft for item in subjects))
        input_snapshot = {
            "briefId": str(brief_id),
            "brief": brief.model_dump(mode="json", by_alias=True),
            "selectedEvent": event,
            "subjects": [
                {
                    "subjectId": str(item.id),
                    "revisionId": str(item.revision_id),
                    **item.draft.model_dump(mode="json", by_alias=True),
                }
                for item in subjects
            ],
            "instruction": str(getattr(payload, "rewrite_instruction", "") or ""),
        }
        input_hash = _json_hash(input_snapshot)
        event_id = uuid.UUID(str(event["id"]))
        idempotency_key = (
            getattr(payload, "idempotency_key", None)
            or hashlib.sha256(f"{event_id}:story-script:{input_hash}".encode()).hexdigest()
        )
        attempt, created = self._repository.begin_generation_attempt(
            project_id=project_id,
            business_object_type="story_event_script",
            business_object_id=event_id,
            idempotency_key=idempotency_key,
            provider=self._provider_name,
            model=self._director.model,
            request=input_snapshot,
        )
        if not created:
            return attempt

        attempt_id = str(attempt["id"])
        try:
            script, prompt_id = self._generate_script_from_event(
                project_id=project_id,
                event_id=event_id,
                input_snapshot=input_snapshot,
            )
            score, critic_prompt_id = self._score_candidate(
                project_id=project_id,
                strategy=StoryStrategy.COMBINED,
                candidate=script,
                input_snapshot=input_snapshot,
                parent_prompt_id=prompt_id,
            )
            stored = self._repository.save_story_candidate(
                project_id=project_id,
                brief_id=brief_id,
                strategy=StoryStrategy.COMBINED,
                candidate=script,
                scorecard=score,
                subject_ids=tuple(item.id for item in subjects),
                subject_revision_ids=tuple(item.revision_id for item in subjects),
                candidate_prompt_id=prompt_id,
                critic_prompt_id=critic_prompt_id,
                source_event_candidate_id=event_id,
            )
        except GatewayError as exc:
            status = "submission_unknown" if exc.submission_unknown else "failed"
            self._repository.finish_generation_attempt(
                attempt_id,
                status=status,
                error={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
            )
            raise
        except Exception as exc:
            self._repository.finish_generation_attempt(
                attempt_id,
                status="failed",
                error={"code": "internal", "message": str(exc)},
            )
            raise
        self._repository.finish_generation_attempt(
            attempt_id,
            status="succeeded",
            response={"revisionId": str(stored["id"]), "sourceEventCandidateId": str(event_id)},
        )
        return {"id": attempt_id, "status": "succeeded", "story": stored}

    def run_story_strategies(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        brief_id, brief = self._repository.get_current_brief(project_id)
        subjects = self._repository.list_subjects(project_id)
        validate_story_inputs(brief, tuple(item.draft for item in subjects))
        subject_snapshot = [
            {
                "subjectId": str(item.id),
                "revisionId": str(item.revision_id),
                **item.draft.model_dump(mode="json", by_alias=True),
            }
            for item in subjects
        ]
        input_snapshot = {
            "briefId": str(brief_id),
            "brief": brief.model_dump(mode="json", by_alias=True),
            "subjects": subject_snapshot,
            "creativeDirection": str(getattr(payload, "rewrite_instruction", "") or ""),
        }
        input_hash = _json_hash(input_snapshot)
        idempotency_key = (
            payload.idempotency_key
            or hashlib.sha256(f"{project_id}:story-strategies:{input_hash}".encode()).hexdigest()
        )
        attempt, created = self._repository.begin_generation_attempt(
            project_id=project_id,
            business_object_type="project_story_strategy",
            business_object_id=project_id,
            idempotency_key=idempotency_key,
            provider=self._provider_name,
            model=self._director.model,
            request=input_snapshot,
        )
        if not created:
            return attempt

        attempt_id = str(attempt["id"])
        candidates: list[dict[str, Any]] = []
        try:
            for strategy in (
                StoryStrategy.RELATIONSHIP,
                StoryStrategy.PROBLEM_SOLVING,
                StoryStrategy.TWIST_HOOK,
            ):
                candidate, candidate_prompt_id = self._generate_candidate(
                    project_id=project_id,
                    strategy=strategy,
                    input_snapshot=input_snapshot,
                )
                score, critic_prompt_id = self._score_candidate(
                    project_id=project_id,
                    strategy=strategy,
                    candidate=candidate,
                    input_snapshot=input_snapshot,
                    parent_prompt_id=candidate_prompt_id,
                )
                stored = self._repository.save_story_candidate(
                    project_id=project_id,
                    brief_id=brief_id,
                    strategy=strategy,
                    candidate=candidate,
                    scorecard=score,
                    subject_ids=tuple(item.id for item in subjects),
                    subject_revision_ids=tuple(item.revision_id for item in subjects),
                    candidate_prompt_id=candidate_prompt_id,
                    critic_prompt_id=critic_prompt_id,
                )
                candidates.append(stored)
        except GatewayError as exc:
            status = "submission_unknown" if exc.submission_unknown else "failed"
            self._repository.finish_generation_attempt(
                attempt_id,
                status=status,
                error={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
            )
            raise
        except Exception as exc:
            self._repository.finish_generation_attempt(
                attempt_id,
                status="failed",
                error={"code": "internal", "message": str(exc)},
            )
            raise
        self._repository.finish_generation_attempt(
            attempt_id,
            status="succeeded",
            response={"candidateIds": [str(item["id"]) for item in candidates]},
        )
        return {"id": attempt_id, "status": "succeeded", "candidates": candidates}

    def _generate_event_candidate(
        self,
        *,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        strategy: StoryStrategy,
        input_snapshot: dict[str, Any],
    ) -> tuple[StoryEventCandidateOutput, uuid.UUID]:
        system_prompt = (
            "你是一人一猫治愈短片的事件策划。这里只输出一个可供人选择的事件方向，"
            "不要扩写文学化完整剧情。事件必须分别说明儿童主动行动、猫咪参与、小变化和"
            "温暖收尾，并判断目标时长内是否可拍。固定儿童、固定猫咪和固定画风只作为"
            "不可修改的IP约束；禁止对白，禁止把猫咪生成人形肢体。8至15秒只能使用一个场景，"
            "更长视频只有在叙事必要时才能换场，每次换场必须说明目的。"
        )
        user_prompt = (
            f"事件策略：{strategy.value}\n"
            f"输入约束：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_event_candidate",
                nodeId=uuid.uuid5(project_id, f"story-event-planner:{strategy.value}"),
                businessObjectType="recipe_story_event_batch",
                businessObjectId=recipe_instance_id,
                templateName=f"story.event.{strategy.value}.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "StoryEventCandidateOutput",
                    "schema": StoryEventCandidateOutput.model_json_schema(),
                },
                inputSnapshot=input_snapshot,
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryEventCandidateOutput.model_json_schema(),
                output_name="StoryEventCandidateOutput",
            )
            candidate = StoryEventCandidateOutput.model_validate(result.payload)
            validate_story_event_candidate(
                candidate,
                target_duration_seconds=int(input_snapshot["brief"]["targetDurationSeconds"]),
            )
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=candidate.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return candidate, prompt_id

    def _score_event_candidate(
        self,
        *,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        strategy: StoryStrategy,
        candidate: StoryEventCandidateOutput,
        input_snapshot: dict[str, Any],
        parent_prompt_id: uuid.UUID,
    ) -> tuple[StoryScorecard, uuid.UUID]:
        candidate_document = candidate.model_dump(mode="json", by_alias=True)
        system_prompt = (
            "你是短片事件方案评审。按开头吸引力、因果完整性、儿童与猫咪必要性、"
            "情绪弧线、可视化、时长适配、连续性和安全性逐项给出0到10分。"
            "对无法在目标时长完成、无必要换场或猫咪行为违规的方案必须明确警告。"
        )
        user_prompt = (
            f"原始输入：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}\n"
            f"事件方案：{json.dumps(candidate_document, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_event_critic",
                nodeId=uuid.uuid5(project_id, f"story-event-critic:{strategy.value}"),
                businessObjectType="recipe_story_event_batch",
                businessObjectId=recipe_instance_id,
                parentRunId=parent_prompt_id,
                templateName="story.event.critic.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "StoryEventScorecard",
                    "schema": StoryScorecard.model_json_schema(),
                },
                inputSnapshot={**input_snapshot, "candidate": candidate_document},
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryScorecard.model_json_schema(),
                output_name="StoryEventScorecard",
            )
            score = StoryScorecard.model_validate(result.payload)
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=score.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return score, prompt_id

    def _generate_script_from_event(
        self,
        *,
        project_id: uuid.UUID,
        event_id: uuid.UUID,
        input_snapshot: dict[str, Any],
    ) -> tuple[StoryCandidateOutput, uuid.UUID]:
        system_prompt = (
            "你是一人一猫治愈短片的剧情编剧。把已由人工选择的事件方案扩写为一个完整、"
            "可编辑、可分镜的剧情脚本，不得改变事件方向。脚本必须形成开始、发展、小变化、"
            "温暖收尾的因果链，明确儿童动作和猫咪反应；每个场景提供稳定sceneKey、叙事目的、"
            "地点、室内外、时间天气、关键装饰、道具和必要换场原因。保持固定IP和无对白约束。"
        )
        user_prompt = json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_script_expansion",
                nodeId=uuid.uuid5(project_id, "story-script-expander"),
                businessObjectType="story_event",
                businessObjectId=event_id,
                templateName="story.script.from_event.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "StoryScriptOutput",
                    "schema": StoryCandidateOutput.model_json_schema(),
                },
                inputSnapshot=input_snapshot,
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryCandidateOutput.model_json_schema(),
                output_name="StoryScriptOutput",
            )
            script = StoryCandidateOutput.model_validate(result.payload)
            validate_story_scene_plan(
                script,
                target_duration_seconds=int(input_snapshot["brief"]["targetDurationSeconds"]),
            )
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=script.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return script, prompt_id

    def _generate_candidate(
        self,
        *,
        project_id: uuid.UUID,
        strategy: StoryStrategy,
        input_snapshot: dict[str, Any],
    ) -> tuple[StoryCandidateOutput, uuid.UUID]:
        template_name = f"story.{strategy.value}.v1"
        system_prompt = (
            "你是AIGC短剧故事策划。只规划可视化、因果连续且可按目标时长制作的故事，"
            "所有已给定叙事主体都必须承担不可替代的戏剧功能。每个场景必须给出稳定且唯一的"
            "sceneKey，并完整填写地点、室内外、时间天气、关键装饰、道具和换场原因。"
            "首场景的换场原因可以为空；之后每次换场必须有明确叙事目的。"
        )
        user_prompt = (
            f"策略：{strategy.value}\n"
            f"输入快照：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_candidate",
                nodeId=uuid.uuid5(project_id, f"story-planner:{strategy.value}"),
                businessObjectType="project_story_strategy",
                businessObjectId=project_id,
                templateName=template_name,
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "CanvasStoryCandidateOutput",
                    "schema": StoryCandidateOutput.model_json_schema(),
                },
                inputSnapshot=input_snapshot,
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryCandidateOutput.model_json_schema(),
                output_name="CanvasStoryCandidateOutput",
            )
            candidate = StoryCandidateOutput.model_validate(result.payload)
            validate_story_scene_plan(
                candidate,
                target_duration_seconds=int(input_snapshot["brief"]["targetDurationSeconds"]),
            )
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=candidate.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return candidate, prompt_id

    def _score_candidate(
        self,
        *,
        project_id: uuid.UUID,
        strategy: StoryStrategy,
        candidate: StoryCandidateOutput,
        input_snapshot: dict[str, Any],
        parent_prompt_id: uuid.UUID,
    ) -> tuple[StoryScorecard, uuid.UUID]:
        candidate_document = candidate.model_dump(mode="json", by_alias=True)
        system_prompt = (
            "你是独立短剧评审。按开头钩子、因果完整性、主体必要性、情绪曲线、"
            "可视化、时长适配、连续性与安全性逐项给出0到10分，不得替策划隐瞒风险。"
        )
        user_prompt = (
            f"原始输入：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}\n"
            f"候选故事：{json.dumps(candidate_document, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_critic",
                nodeId=uuid.uuid5(project_id, f"story-critic:{strategy.value}"),
                businessObjectType="project_story_strategy",
                businessObjectId=project_id,
                parentRunId=parent_prompt_id,
                templateName="story.critic.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "CanvasStoryCriticOutput",
                    "schema": StoryScorecard.model_json_schema(),
                },
                inputSnapshot={**input_snapshot, "candidate": candidate_document},
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryScorecard.model_json_schema(),
                output_name="CanvasStoryCriticOutput",
            )
            score = StoryScorecard.model_validate(result.payload)
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=score.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return score, prompt_id

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.approve_story_revision(revision_id)

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        exact_durations: tuple[int, ...] | None = None,
        healing_recipe: bool = False,
        idempotency_key: str | None = None,
        creation_mode: str = "from_story",
        reference_asset_ids: tuple[uuid.UUID, ...] = (),
        instruction: str | None = None,
    ) -> dict[str, Any]:
        context = self._repository.get_storyboard_context(project_id)
        if context["existing"] is not None:
            return context["existing"]
        input_snapshot = {
            "brief": context["brief"],
            "story": context["story"],
            "subjects": context["subjects"],
            "creationMode": creation_mode,
            "referenceAssetIds": [str(item) for item in reference_asset_ids],
            "instruction": instruction,
        }
        input_hash = _json_hash(input_snapshot)
        attempt, created = self._repository.begin_generation_attempt(
            project_id=project_id,
            business_object_type="storyboard",
            business_object_id=uuid.UUID(str(context["storyId"])),
            idempotency_key=(
                idempotency_key
                or hashlib.sha256(f"{project_id}:storyboard:{input_hash}".encode()).hexdigest()
            ),
            provider=self._provider_name,
            model=self._director.model,
            request=input_snapshot,
        )
        if not created:
            return attempt

        total_seconds = int(context["brief"]["targetDurationSeconds"])
        scene_count = len(context["story"]["scenes"])
        if exact_durations is not None:
            if sum(exact_durations) != total_seconds or any(
                not 8 <= duration <= 15 for duration in exact_durations
            ):
                raise ValueError("配方分镜时长必须精确覆盖总时长且每镜为8至15秒")
            minimum_beats = maximum_beats = len(exact_durations)
        else:
            minimum_beats = math.ceil(total_seconds / 15)
            maximum_beats = total_seconds // 8
        system_prompt = (
            "你是AIGC短剧分镜导演。把已批准故事拆成可独立编辑和生成的 Shot Beat；"
            "每个 Beat 只能包含一个连续动作意图，必须明确动作、机位、对白和所属场景。"
        )
        if healing_recipe:
            system_prompt += (
                "当前为固定儿童与固定猫咪的原创水彩治愈短片；每镜必须包含开始、小变化、"
                "温暖收尾三个时间节拍，只写动作、微表情、运镜与声音变化，dialogue必须为空。"
            )
        if creation_mode == "from_characters":
            system_prompt += (
                "用户从画布明确选择了角色素材。必须以已批准故事为叙事边界，"
                "将所选角色素材只作为身份与关系约束，不得用普通参考替换 Canon 身份。"
            )
        user_prompt = (
            f"必须输出 {minimum_beats} 至 {maximum_beats} 个 Beat，"
            f"覆盖全部 {scene_count} 个场景。\n"
            f"用户补充要求：{instruction or '无'}\n"
            f"输入快照：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="storyboard_director",
                nodeId=uuid.uuid5(project_id, "storyboard-director"),
                businessObjectType="story_revision",
                businessObjectId=uuid.UUID(str(context["storyId"])),
                templateName="storyboard.director.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "CanvasStoryboardPlanOutput",
                    "schema": StoryboardPlanOutput.model_json_schema(),
                },
                inputSnapshot=input_snapshot,
            ),
        )
        attempt_id = str(attempt["id"])
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryboardPlanOutput.model_json_schema(),
                output_name="CanvasStoryboardPlanOutput",
            )
            plan = StoryboardPlanOutput.model_validate(result.payload)
            if not minimum_beats <= len(plan.beats) <= maximum_beats:
                raise ValueError("分镜 Beat 数量无法适配目标总时长与 8–15 秒供应商范围")
            scene_orders = {beat.scene_order for beat in plan.beats}
            if scene_orders != set(range(1, scene_count + 1)):
                raise ValueError("分镜计划必须覆盖批准故事中的全部场景")
            if healing_recipe and any(beat.dialogue.strip() for beat in plan.beats):
                raise ValueError("治愈短片配方不允许生成对白")
            if exact_durations is None:
                weights = tuple(beat.duration_weight for beat in plan.beats)
                durations = allocate_bounded_durations(
                    total_seconds,
                    weights,
                    minimum_seconds=8,
                    maximum_seconds=15,
                )
            else:
                durations = exact_durations
        except GatewayError as exc:
            status = "submission_unknown" if exc.submission_unknown else "failed"
            self._repository.complete_prompt_run(
                prompt_id,
                status=status,
                error={"code": exc.code, "message": str(exc)},
            )
            self._repository.finish_generation_attempt(
                attempt_id,
                status=status,
                error={"code": exc.code, "message": str(exc)},
            )
            raise
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            self._repository.finish_generation_attempt(
                attempt_id,
                status="failed",
                error={"code": "invalid_storyboard", "message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=plan.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        storyboard = self._repository.save_storyboard_plan(
            project_id,
            story_id=uuid.UUID(str(context["storyId"])),
            plan=plan,
            durations=durations,
            prompt_id=prompt_id,
        )
        self._repository.finish_generation_attempt(
            attempt_id,
            status="succeeded",
            response={"beatIds": [beat["id"] for beat in storyboard["beats"]]},
        )
        return storyboard

    def update_shot_beat(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.update_shot_beat(
            beat_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.save_manual_storyboard(
            project_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def compile_storyboard_prompts(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.compile_storyboard_prompts(project_id, payload)

    def create_generation_attempt(self, payload: Any) -> dict[str, Any]:
        return self._repository.create_generation_attempt(payload)

    def retry_generation_attempt(self, attempt_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.retry_generation_attempt(attempt_id, payload)

    def review_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.review_asset(asset_id, payload)

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_prompt_run(prompt_id)

    def get_canvas(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_canvas(project_id)

    def list_canvas_templates(self) -> list[dict[str, Any]]:
        return self._repository.list_canvas_templates()

    def instantiate_template(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.instantiate_template(project_id, payload)

    def create_canvas_node(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.create_canvas_node(project_id, payload)

    def create_canvas_edge(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.create_canvas_edge(project_id, payload)

    def delete_canvas_edge(self, edge_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.delete_canvas_edge(edge_id)

    def create_generation_batch(self, payload: Any) -> dict[str, Any]:
        resolved = payload.model_copy(
            update={
                "provider": payload.provider or self._provider_name,
                "model": payload.model or self._image_model,
            }
        )
        return self._repository.create_generation_batch(resolved)

    def create_video_edit_recipe(self, payload: Any) -> dict[str, Any]:
        return self._repository.create_video_edit_recipe(payload)

    def update_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.update_video_edit_recipe(
            recipe_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def replace_video_edit_annotations(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.replace_video_edit_annotations(
            recipe_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def compile_video_edit_recipe(self, recipe_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.compile_video_edit_recipe(recipe_id, self._video_edit_capability)

    def submit_video_edit_recipe(self, recipe_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.submit_video_edit_recipe(
            recipe_id,
            payload,
            image_provider=self._provider_name,
            image_model=self._image_model,
        )

    def events(
        self, project_id: uuid.UUID, *, after_sequence: int = 0
    ) -> tuple[dict[str, Any], ...]:
        return self._repository.events(project_id, after_sequence=after_sequence)

    def save_canvas_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.save_canvas_layout(
            project_id,
            expected_version=expected_version,
            payload=payload,
        )

    def archive_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
        reason: str | None,
    ) -> dict[str, Any]:
        return self._repository.archive_canvas_node(
            project_id,
            node_id,
            expected_version=expected_version,
            reason=reason,
        )

    def restore_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        return self._repository.restore_canvas_node(
            project_id,
            node_id,
            expected_version=expected_version,
        )


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
