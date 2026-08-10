"""规划、身份、输入与叙事连续性的少量确定性规则。

这里只阻断引用错误、固定身份改写和跨时段道具键冲突。镜头复杂度、Prompt长度与
动作难度只产生诊断，实际画面质量由视觉审核和人工内容审核决定。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable

from .contracts import (
    DailyProductionPlan,
    DayBrief,
    EpisodePlan,
    RecentContentSummary,
    SlotBrief,
)
from .rendering import build_render_plan
from .visual_profiles import DEFAULT_SERIES_VISUAL_PROFILE, SeriesVisualProfile


class IssueLevel(StrEnum):
    HARD = "hard"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class GateIssue:
    gate: str
    code: str
    message: str
    level: IssueLevel


def validate_plan_gate(
    plan: DailyProductionPlan,
    *,
    expected_date: date | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> tuple[GateIssue, ...]:
    issues: list[GateIssue] = []
    if expected_date is not None and plan.content_date != expected_date:
        issues.append(_hard("plan", "content_date_mismatch", "内容日期与规划目标不一致"))
    for episode in plan.episodes:
        issues.extend(_identity_issues(episode, series_profile))
    if len({item.script.story_pattern for item in plan.episodes}) == 1:
        issues.append(
            _warning(
                "planning", "repeated_story_pattern", "早中晚使用了相同关系弧，建议增加信息组织差异"
            )
        )
    return tuple(issues)


def validate_episode_against_brief(
    episode: EpisodePlan,
    *,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> tuple[GateIssue, ...]:
    issues = list(_identity_issues(episode, series_profile))
    if episode.slot is not slot_brief.slot:
        issues.append(_hard("plan", "slot_mismatch", "时段导演返回了错误的slot"))
    if episode.script.activity_focus is not slot_brief.resolved_activity_focus:
        issues.append(_hard("plan", "activity_focus_mismatch", "时段导演改写了固定活动焦点"))
    minimum, maximum = slot_brief.duration_intent.resolved_band.range
    if not minimum <= episode.duration_seconds <= maximum:
        issues.append(_hard("plan", "duration_band_mismatch", "精确时长超出总导演解析档位"))
    try:
        build_render_plan(episode)
    except ValueError as exc:
        issues.append(_hard("rendering", "invalid_render_sections", str(exc)))

    handoff_keys = {
        item.entity_key
        for item in day_brief.handoffs
        if episode.slot in {item.from_slot, item.to_slot}
    }
    prop_keys = {item.entity_key for item in episode.script.critical_props}
    missing = handoff_keys - prop_keys
    if missing:
        issues.append(
            _warning(
                "continuity",
                "handoff_not_visible_in_slot",
                "本时段未显式展示交接元素：" + ", ".join(sorted(missing)),
            )
        )
    if len(episode.script.critical_props) > 4:
        issues.append(
            _warning("rendering", "many_critical_props", "关键道具较多，可能降低画面聚焦度")
        )
    return tuple(issues)


def validate_input_gate(
    episode: EpisodePlan,
    available_reference_roles: Iterable[str],
) -> tuple[GateIssue, ...]:
    del episode
    available = set(available_reference_roles)
    required = {"person", "cat", "style"}
    return tuple(
        _hard("input", "missing_reference", f"缺少{role}参考素材")
        for role in sorted(required - available)
    )


def validate_episode_cooldown(
    episode: EpisodePlan,
    recent_summaries: Iterable[RecentContentSummary],
) -> tuple[GateIssue, ...]:
    recent = tuple(recent_summaries)
    event_keys = {key for item in recent for key in item.event_keys}
    location_keys = {key for item in recent for key in item.location_keys}
    element_keys = {key for item in recent for key in item.element_keys}
    current_elements = {item.entity_key for item in episode.script.critical_props}
    issues: list[GateIssue] = []
    if episode.script.event_key in event_keys:
        issues.append(_warning("planning", "recent_event_repeat", "事件仍在近期冷却期"))
    if episode.script.location_key in location_keys:
        issues.append(_warning("planning", "recent_location_repeat", "地点仍在近期冷却期"))
    if current_elements and current_elements.issubset(element_keys):
        issues.append(_warning("planning", "recent_element_repeat", "关键元素组合仍在近期冷却期"))
    return tuple(issues)


def hard_failures(issues: Iterable[GateIssue]) -> tuple[GateIssue, ...]:
    return tuple(item for item in issues if item.level is IssueLevel.HARD)


def _identity_issues(
    episode: EpisodePlan,
    profile: SeriesVisualProfile,
) -> tuple[GateIssue, ...]:
    text = _episode_text(episode)
    protected = text.replace("少年宫", "").replace("马尾松", "")
    if any(item.casefold() in protected for item in profile.forbidden_identity_rewrites):
        return (
            _hard(
                "identity",
                "gendered_identity_rewrite",
                f"{episode.slot.value}改写了中性儿童身份或固定发长",
            ),
        )
    return ()


def _episode_text(episode: EpisodePlan) -> str:
    script = episode.script
    return " ".join(
        (
            script.episode_question,
            script.main_event,
            script.scene,
            script.appearance.description,
            script.appearance.change_reason or "",
            script.relationship_arc.lead_activity,
            script.relationship_arc.secondary_activity,
            script.relationship_arc.convergence,
            script.ending.result,
            *(item.action for item in script.actions),
            *(item.visible_result for item in script.actions),
            *(item.framing for item in script.shots),
            *(item.direction for item in script.shots),
            *(item.name for item in script.critical_props),
        )
    ).casefold()


def _hard(gate: str, code: str, message: str) -> GateIssue:
    return GateIssue(gate=gate, code=code, message=message, level=IssueLevel.HARD)


def _warning(gate: str, code: str, message: str) -> GateIssue:
    return GateIssue(gate=gate, code=code, message=message, level=IssueLevel.WARNING)
