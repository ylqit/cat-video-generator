"""规划、身份、输入和连续性的少量确定性硬门。

规则只阻断明确矛盾，不用动作数量预测生成难度。渲染效果在关键帧和成片审核中判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable

from .continuity import EntityKind, validate_continuity
from .contracts import (
    DailyProductionPlan,
    DayBrief,
    EpisodePlan,
    RecentContentSummary,
    SlotBrief,
)
from .visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    SeriesVisualProfile,
)


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
    """检查全天日期、事件差异和中性主体描述。"""

    issues: list[GateIssue] = []
    if expected_date is not None and plan.content_date != expected_date:
        issues.append(_hard("plan", "content_date_mismatch", "内容日期与规划目标不一致"))
    if len({item.script.title for item in plan.episodes}) != 3:
        issues.append(_hard("plan", "duplicate_title", "早中晚标题必须互不重复"))
    if len({item.script.event_key for item in plan.episodes}) != 3:
        issues.append(_hard("plan", "duplicate_event", "早中晚不能重复同一主事件"))
    for episode in plan.episodes:
        issues.extend(_identity_issues(episode, series_profile))
    return tuple(issues)


def validate_episode_against_brief(
    episode: EpisodePlan,
    *,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> tuple[GateIssue, ...]:
    """检查时段边界、共享语义键和可见世界终态。"""

    issues = list(_identity_issues(episode, series_profile))
    if episode.slot is not slot_brief.slot:
        issues.append(_hard("plan", "slot_mismatch", "时段导演返回了错误的slot"))
    allowed_shared = {
        item.entity_key for item in day_brief.shared_elements if episode.slot in item.slots
    }
    used_shared = {
        item.entity_key
        for item in episode.script.continuity.entities
        if item.entity_key in {shared.entity_key for shared in day_brief.shared_elements}
    }
    if not used_shared.issubset(allowed_shared):
        issues.append(
            _hard("continuity", "undeclared_shared_element", "脚本使用了未授权的共享元素")
        )
    # 这里只检查关键实体起终态引用；中间姿态与动作由故事板语义审核判断。
    result = validate_continuity(episode.script.continuity)
    issues.extend(_hard("continuity", item.code, item.message) for item in result.issues)
    if len(episode.script.ending.key_entity_ids) > 4:
        issues.append(
            _warning(
                "rendering",
                "many_ending_entities",
                "结尾关键实体较多，可能降低画面聚焦度，但不阻断生成",
            )
        )
    return tuple(issues)


def validate_input_gate(
    episode: EpisodePlan,
    available_reference_roles: Iterable[str],
) -> tuple[GateIssue, ...]:
    """固定要求人物、猫咪和画风；剧情实体不会自动升级为参考资产。"""

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
    """按结构化键精确比较，不用中文子串猜测相似度。"""

    recent = tuple(recent_summaries)
    event_keys = {key for item in recent for key in item.event_keys}
    location_keys = {key for item in recent for key in item.location_keys}
    element_keys = {key for item in recent for key in item.element_keys}
    current_elements = {
        item.entity_key
        for item in episode.script.continuity.entities
        if item.kind is EntityKind.PROP
    }
    issues: list[GateIssue] = []
    if episode.script.event_key in event_keys:
        issues.append(_warning("planning", "recent_event_repeat", "事件仍在近期冷却期"))
    if episode.script.location_key in location_keys:
        issues.append(_warning("planning", "recent_location_repeat", "地点仍在近期冷却期"))
    if current_elements and current_elements.issubset(element_keys):
        issues.append(_warning("planning", "recent_element_repeat", "关键元素组合仍在冷却期"))
    return tuple(issues)


def hard_failures(issues: Iterable[GateIssue]) -> tuple[GateIssue, ...]:
    return tuple(item for item in issues if item.level is IssueLevel.HARD)


def _identity_issues(
    episode: EpisodePlan,
    profile: SeriesVisualProfile,
) -> tuple[GateIssue, ...]:
    text = _episode_text(episode)
    scrubbed = text.replace("少年宫", "").replace("马尾松", "")
    forbidden = tuple(item.casefold() for item in profile.forbidden_identity_rewrites)
    if any(item in scrubbed for item in forbidden):
        return (
            _hard(
                "identity",
                "gendered_identity_rewrite",
                f"{episode.slot.value}把中性儿童改写为性别化身份或改变固定发长",
            ),
        )
    return ()


def _episode_text(episode: EpisodePlan) -> str:
    script = episode.script
    return " ".join(
        (
            script.main_event,
            script.scene,
            script.appearance.description,
            script.ending.result,
            *(item.action for item in script.actions),
            *(item.visible_result for item in script.actions),
            *(item.framing for item in script.shots),
            *(item.direction for item in script.shots),
            *(item.name for item in script.continuity.entities),
            *(item.form_key for item in script.continuity.entities),
        )
    ).casefold()


def _hard(gate: str, code: str, message: str) -> GateIssue:
    return GateIssue(gate=gate, code=code, message=message, level=IssueLevel.HARD)


def _warning(gate: str, code: str, message: str) -> GateIssue:
    return GateIssue(gate=gate, code=code, message=message, level=IssueLevel.WARNING)
