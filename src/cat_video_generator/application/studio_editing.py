"""生活故事工作台的人工编辑用例：剧本、项目大纲、原文与流水线开关。

人工编辑是显式意图，因此只重跑契约、用户冻结控制、Prompt可编译性和整盘硬门，
**不重跑冷却校验**（``validate_episode_cooldown`` 是给自动生成路径用的）。
所有结构化校验在写库前完成——读路径对 ``script_json`` 直接
``EpisodeScript.model_validate``，非法JSON会崩所有读路径，绝不允许落库。
尚未镜头化的用户原文通过时段source入口保存；本服务的结构化剧本编辑只面向已经
创建Episode的时段。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..domain.contracts import (
    AcceptedOutcome,
    ActivityFocus,
    ActivityFocusMode,
    CrossSlotReference,
    CrossSlotReferenceTarget,
    DailyProductionPlan,
    DurationMode,
    EpisodePlan,
    EpisodeScript,
    ProjectOutlineV3,
    RunCreativeControls,
    Slot,
    StoryConnection,
    StoryProjectInput,
)
from ..domain.normalization import normalize_episode_payload
from ..domain.pipeline import PipelineSettings
from ..domain.prompts import (
    PromptCompilationError,
    compile_video_prompt_preview,
)
from ..domain.rules import (
    hard_failures,
    validate_episode_gate,
    validate_plan_gate,
)
from ..domain.visual_profiles import SeriesVisualProfile, StyleProfile
from ..domain.workflow import RunStatus
from .ports import StudioStore


class StudioEditingService:
    """校验并落库创作台的人工编辑，不触发任何供应商调用。"""

    def __init__(
        self,
        *,
        repository: StudioStore,
        series_profile: SeriesVisualProfile,
        style_profile: StyleProfile,
        video_resolution: str = "720p",
    ) -> None:
        self._repository = repository
        self._series_profile = series_profile
        self._style_profile = style_profile
        self._video_resolution = video_resolution

    def update_episode_script(
        self,
        episode_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """结构化编辑单集剧本；校验全部通过才落库，失败不触碰任何数据。"""

        detail = self._repository.episode_detail(episode_id)
        status = str(detail["status"])
        if status not in {"planned", "video_pending", "failed"}:
            raise ValueError(
                f"该集状态{status}已进入媒体生产，不能编辑剧本；请先人工拒绝相关资产回到failed"
            )
        normalized, normalizations = normalize_episode_payload(payload)
        script = EpisodeScript.model_validate(normalized)
        run_id = uuid.UUID(str(detail["runId"]))
        slot = Slot(str(detail["slot"]))
        context = self._repository.get_planning_context(run_id)
        raw_project_input = context.get("projectInput")
        if not isinstance(raw_project_input, dict):
            raise ValueError("该项目缺少StoryProjectInput，不能校验剧本编辑")
        project_input = StoryProjectInput.model_validate(raw_project_input)
        raw_outline = context.get("projectOutline")
        project_outline = (
            ProjectOutlineV3.model_validate(raw_outline)
            if isinstance(raw_outline, dict)
            else None
        )
        raw_controls = (context.get("planningMetadata") or {}).get("creativeControls")
        controls = RunCreativeControls.model_validate(raw_controls or {})
        episode = EpisodePlan(slot=slot, script=script)

        gate_issues = validate_episode_gate(
            episode,
            series_profile=self._series_profile,
        )
        blocking_issues = hard_failures(gate_issues)
        errors = [item.message for item in blocking_issues]
        suggestions = [
            item.message for item in gate_issues if item not in blocking_issues
        ]
        errors.extend(_frozen_control_errors(episode, controls))
        try:
            compile_video_prompt_preview(
                episode,
                resolution=self._video_resolution,
                style_profile=self._style_profile,
                series_profile=self._series_profile,
            )
        except PromptCompilationError as exc:
            errors.append(str(exc))

        stored_episodes = self._repository.list_episodes(run_id)
        candidate_episodes = [
            episode if item.plan.slot is slot else item.plan for item in stored_episodes
        ]
        if len(candidate_episodes) == 3:
            candidate_plan = DailyProductionPlan(
                content_date=self._repository.get_run(run_id).content_date,
                project_input=project_input,
                outline=project_outline,
                episodes=candidate_episodes,
            )
            errors.extend(
                item.message
                for item in hard_failures(
                    validate_plan_gate(
                        candidate_plan,
                        expected_date=candidate_plan.content_date,
                        series_profile=self._series_profile,
                    )
                )
            )
        if errors:
            raise ValueError("；".join(errors))

        self._repository.replace_episode_plan(
            run_id=run_id,
            episode=episode,
            project_outline=project_outline,
        )
        return {
            "episodeId": str(episode_id),
            "slot": slot.value,
            "saved": True,
            "promptOverrideStale": bool(
                detail.get("promptOverrideState", {}).get("values")
            ),
            "normalizations": list(normalizations),
            "suggestions": suggestions,
        }

    def update_project_outline(
        self,
        run_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """确认主题扩写得到的项目大纲；已有剧本项目不经过该入口。"""

        stored_run = self._repository.get_run(run_id)
        if self._repository.list_episodes(run_id):
            raise ValueError("已有时段脚本后不能改写项目大纲；请局部重规划对应时段")
        if stored_run.plan is not None:
            raise ValueError("方案已定稿，项目大纲只能经局部重规划调整")
        if stored_run.status not in {
            RunStatus.DRAFT.value,
            RunStatus.PLANNING_REVIEW.value,
            RunStatus.FAILED.value,
        }:
            raise ValueError(f"Run状态{stored_run.status}不允许编辑项目大纲")
        project_outline = ProjectOutlineV3.model_validate(payload)
        if project_outline.content_date != stored_run.content_date:
            raise ValueError("ProjectOutlineV3内容日期不能改写项目固定日期")
        self._repository.update_project_outline(
            run_id=run_id,
            project_outline=project_outline,
        )
        return {
            "runId": str(run_id),
            "saved": True,
            "confirmed": True,
            "episodeDraftsCleared": True,
        }

    def outcome(self, run_id: uuid.UUID, slot: Slot) -> dict[str, Any]:
        """返回已确认结果或由脚本、诊断和全天交接组成的可编辑草稿。"""

        source = self._repository.get_outcome_source(run_id, slot)
        accepted = source.get("acceptedOutcome")
        if isinstance(accepted, dict):
            return {"slot": slot.value, "confirmed": True, **accepted}
        script = EpisodeScript.model_validate(source["script"])
        diagnostic = source.get("diagnostic")
        evidence = diagnostic if isinstance(diagnostic, dict) else {}
        context = self._repository.get_planning_context(run_id)
        raw_outline = context.get("projectOutline")
        project_outline = (
            ProjectOutlineV3.model_validate(raw_outline)
            if isinstance(raw_outline, dict)
            else None
        )
        handoffs = [
            item.continuity
            for item in (() if project_outline is None else project_outline.handoffs)
            if item.from_slot is slot
        ]
        return {
            "slot": slot.value,
            "confirmed": False,
            "summary": str(evidence.get("actualOutcome") or script.ending),
            "carryForward": list(evidence.get("carryForward") or handoffs),
            "doNotCarryForward": list(evidence.get("doNotCarryForward") or []),
            "source": "video_diagnostic" if evidence.get("actualOutcome") else "script_ending",
            "episodeStatus": source["episodeStatus"],
        }

    def confirm_outcome(
        self,
        run_id: uuid.UUID,
        slot: Slot,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """确认实际结果；导演不会隐式读取，关联必须另行确认和启用。"""

        outcome = AcceptedOutcome(
            summary=payload.get("summary", ""),
            carryForward=payload.get("carryForward", []),
            doNotCarryForward=payload.get("doNotCarryForward", []),
            confirmedAt=datetime.now(timezone.utc),
        )
        self._repository.save_accepted_outcome(
            run_id=run_id,
            slot=slot,
            outcome=outcome,
        )
        return {
            "runId": str(run_id),
            "slot": slot.value,
            "confirmed": True,
            **outcome.model_dump(mode="json", by_alias=True),
        }

    def update_story_connection(
        self,
        run_id: uuid.UUID,
        slot: Slot,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """确认一张可选关联卡；保存与是否加载由用户分别决定。"""

        connection = StoryConnection.model_validate(
            {
                **payload,
                "confirmedAt": datetime.now(timezone.utc),
            }
        )
        self._repository.save_story_connection(
            run_id=run_id,
            slot=slot,
            connection=connection,
        )
        return {
            "runId": str(run_id),
            "slot": slot.value,
            "storyConnection": connection.model_dump(mode="json", by_alias=True),
        }

    def update_cross_slot_references(
        self,
        run_id: uuid.UUID,
        slot: Slot,
        payload: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """保存用户挑选的前序媒体及其职责，不自动加入其他匹配素材。"""

        references = tuple(CrossSlotReference.model_validate(item) for item in payload)
        video_reference_count = sum(
            item.apply_to
            in {CrossSlotReferenceTarget.VIDEO, CrossSlotReferenceTarget.BOTH}
            for item in references
        )
        if video_reference_count > 2:
            raise ValueError("视频节点最多选择两项前序参考素材")
        self._repository.save_cross_slot_references(
            run_id=run_id,
            slot=slot,
            references=references,
        )
        return {
            "runId": str(run_id),
            "slot": slot.value,
            "crossSlotReferences": [
                item.model_dump(mode="json", by_alias=True) for item in references
            ],
        }

    def save_shot_note(
        self,
        episode_id: uuid.UUID,
        *,
        asset_id: uuid.UUID,
        start_ms: int,
        end_ms: int,
        note: str,
    ) -> dict[str, Any]:
        """把人工镜头备注写入现有Review证据，不创建媒体或供应商任务。"""

        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError("镜头备注区间必须是有效正时长")
        asset = self._repository.asset_detail(asset_id)
        if asset.episode_id != episode_id or asset.media_type != "video":
            raise ValueError("镜头备注只能关联当前Episode的视频资产")
        if asset.step_id is None:
            raise ValueError("视频资产缺少生产Step，不能保存镜头备注")
        normalized = note.strip()
        if len(normalized) < 2:
            raise ValueError("镜头备注至少需要2个字符")
        review_id = self._repository.record_review(
            step_id=asset.step_id,
            asset_id=asset.id,
            source="human_note",
            decision="pending",
            reason=normalized,
            warnings=[],
            evidence={
                "kind": "shot_note",
                "startMs": start_ms,
                "endMs": end_ms,
            },
        )
        return {
            "episodeId": str(episode_id),
            "assetId": str(asset.id),
            "reviewId": str(review_id),
            "saved": True,
        }

    def update_pipeline_settings(
        self,
        run_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """更新流水线阶段开关；本质是"下一次推进决策"的输入，任何状态可改。"""

        self._repository.get_run(run_id)
        settings = PipelineSettings.model_validate(payload)
        current = self._repository.get_pipeline_settings(run_id)
        if settings.planning_mode is not current.planning_mode:
            raise ValueError("planningMode在Run创建后不可切换，请新建Run选择另一流程")
        self._repository.save_pipeline_settings(run_id=run_id, settings=settings)
        return {
            "runId": str(run_id),
            "pipelineSettings": settings.model_dump(mode="json", by_alias=True),
        }


def _frozen_control_errors(
    episode: EpisodePlan,
    controls: RunCreativeControls,
) -> tuple[str, ...]:
    """只阻断用户已明确固定的焦点与时长；adaptive允许导演自行决定。"""

    slot_control = next(item for item in controls.slot_controls if item.slot is episode.slot)
    requested_focus = controls.requested_focus(episode.slot)
    expected_focus = {
        ActivityFocusMode.CAT_LEAD: ActivityFocus.CAT_LEAD,
        ActivityFocusMode.PERSON_LEAD: ActivityFocus.PERSON_LEAD,
        ActivityFocusMode.BALANCED: ActivityFocus.BALANCED,
    }.get(requested_focus)
    errors: list[str] = []
    if expected_focus is not None and episode.script.activity_focus is not expected_focus:
        errors.append(f"{episode.slot.value}活动焦点与用户固定设置不一致")
    duration_range = {
        DurationMode.SHORT: (8, 15),
        DurationMode.MEDIUM: (16, 30),
        DurationMode.LONG: (31, 45),
    }.get(slot_control.duration_mode)
    if duration_range is not None and not (
        duration_range[0] <= episode.script.duration_seconds <= duration_range[1]
    ):
        errors.append(f"{episode.slot.value}精确时长超出用户固定档位")
    return tuple(errors)
