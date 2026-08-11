"""规划、身份、输入与叙事连续性的少量确定性规则。

这里只阻断无法提交给供应商的结构、素材与渲染能力错误。身份措辞、镜头复杂度、
Prompt长度与动作难度只产生诊断，实际画面质量由AI建议和人工内容审核决定。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable

from .contracts import (
    DailyProductionPlan,
    EpisodePlan,
    RecentContentSummary,
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
    return tuple(issues)


def validate_episode_gate(
    episode: EpisodePlan,
    *,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> tuple[GateIssue, ...]:
    """检查单集固有合法性；焦点和时长由应用层对照RunCreativeControls。"""

    issues = list(_identity_issues(episode, series_profile))
    try:
        build_render_plan(episode)
    except ValueError as exc:
        issues.append(_hard("rendering", "invalid_render_sections", str(exc)))

    if len(episode.script.hard_constraints) > 4:
        issues.append(
            _warning("rendering", "many_hard_constraints", "硬约束较多，可能降低画面聚焦度")
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
    issues: list[GateIssue] = []
    if episode.script.event_key in event_keys:
        issues.append(_warning("planning", "recent_event_repeat", "事件仍在近期冷却期"))
    if episode.script.location_key in location_keys:
        issues.append(_warning("planning", "recent_location_repeat", "地点仍在近期冷却期"))
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
            _warning(
                "identity",
                "gendered_identity_rewrite",
                f"{episode.slot.value}可能改写中性儿童身份或固定发长，请人工确认",
            ),
        )
    return ()


def _episode_text(episode: EpisodePlan) -> str:
    script = episode.script
    return " ".join(
        (
            script.story_text,
            script.appearance,
            script.relationship_arc,
            script.ending,
            *(item.direction for item in script.shots),
            *(item.text for item in script.hard_constraints),
        )
    ).casefold()


def _hard(gate: str, code: str, message: str) -> GateIssue:
    return GateIssue(gate=gate, code=code, message=message, level=IssueLevel.HARD)


def _warning(gate: str, code: str, message: str) -> GateIssue:
    return GateIssue(gate=gate, code=code, message=message, level=IssueLevel.WARNING)
