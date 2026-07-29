"""四道硬门及非阻断警告。

规则只验证生产能否安全继续，不承担导演评分，也不把普通视觉偏差升级成
复杂的数值门槛。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable, Mapping

from .contracts import (
    DayBrief,
    DailyProductionPlan,
    EpisodePlan,
    SlotBrief,
    VideoInputMode,
)


class IssueLevel(StrEnum):
    HARD = "hard"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class GateIssue:
    """一项可定位的硬失败或警告。"""

    gate: str
    code: str
    message: str
    level: IssueLevel


def validate_plan_gate(
    plan: DailyProductionPlan,
    *,
    expected_date: date | None = None,
) -> tuple[GateIssue, ...]:
    """检查全天结构和跨时段外观逻辑。"""

    issues: list[GateIssue] = []
    if expected_date is not None and plan.content_date != expected_date:
        issues.append(
            GateIssue(
                gate="plan",
                code="content_date_mismatch",
                message="导演返回的内容日期与本次规划目标不一致。",
                level=IssueLevel.HARD,
            )
        )
    titles = {episode.title for episode in plan.episodes}
    if len(titles) != 3:
        issues.append(
            GateIssue(
                gate="plan",
                code="duplicate_episode_title",
                message="早中晚标题必须互不重复。",
                level=IssueLevel.HARD,
            )
        )
    events = {episode.main_event for episode in plan.episodes}
    if len(events) != 3:
        issues.append(
            GateIssue(
                gate="plan",
                code="duplicate_main_event",
                message="早中晚不能重复同一个主事件。",
                level=IssueLevel.HARD,
            )
        )
    forbidden_eye_terms = (
        "眼白",
        "虹膜",
        "写实瞳孔",
        "glassy eye",
        "realistic pupil",
    )
    for episode in plan.episodes:
        searchable = " ".join(
            (
                episode.main_event,
                episode.scene,
                episode.ending,
                *(stage.action for stage in episode.actions),
                *(stage.visible_result for stage in episode.actions),
            )
        ).lower()
        if any(term in searchable for term in forbidden_eye_terms):
            issues.append(
                GateIssue(
                    gate="identity",
                    code="forbidden_eye_topology",
                    message=(f"{episode.slot.value}脚本要求了与Canon冲突的眼睛结构。"),
                    level=IssueLevel.HARD,
                )
            )
    return tuple(issues)


def validate_episode_against_brief(
    episode: EpisodePlan,
    *,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
) -> tuple[GateIssue, ...]:
    """检查时段导演没有越过总导演边界或省略共享元素状态。"""

    issues: list[GateIssue] = []
    if episode.slot is not slot_brief.slot:
        issues.append(
            GateIssue(
                gate="plan",
                code="slot_mismatch",
                message="时段导演返回了错误的slot。",
                level=IssueLevel.HARD,
            )
        )
    allowed = {
        item.element_id
        for item in day_brief.shared_elements
        if episode.slot in item.slots
    }
    used = set(episode.shared_element_ids)
    if not used.issubset(allowed):
        issues.append(
            GateIssue(
                gate="continuity",
                code="undeclared_shared_element",
                message="时段导演引用了DayBrief未授权给本时段的共享元素。",
                level=IssueLevel.HARD,
            )
        )
    if used and {item.element_id for item in episode.element_uses} != used:
        issues.append(
            GateIssue(
                gate="continuity",
                code="missing_element_state",
                message="每个共享元素都必须声明本时段的用途、初态和终态。",
                level=IssueLevel.HARD,
            )
        )
    return tuple(issues)


def validate_input_gate(
    episode: EpisodePlan,
    available_reference_roles: Iterable[str],
) -> tuple[GateIssue, ...]:
    """检查收费调用所需的参考素材是否齐全。"""

    available = set(available_reference_roles)
    return tuple(
        GateIssue(
            gate="input",
            code="missing_reference",
            message=f"缺少{role}参考素材。",
            level=IssueLevel.HARD,
        )
        for role in episode.required_reference_roles
        if role not in available
    )


def select_video_input_mode(episode: EpisodePlan) -> VideoInputMode:
    """选择满足本集端点精度需求的最低Seedance输入模式。

    身份、画风和普通元素优先走多模态参考；只有关键包含、交接或空间边界的
    结果状态必须精确时才切到严格首尾帧，避免把帧锚定误当成通用身份方案。
    """

    if episode.video_input_mode is VideoInputMode.STRICT_FIRST_LAST:
        return VideoInputMode.STRICT_FIRST_LAST
    high_risk_relations = {"containment", "handoff", "boundary"}
    if any(
        relation.relation in high_risk_relations
        for relation in episode.critical_relations
    ):
        return VideoInputMode.STRICT_FIRST_LAST
    if episode.video_input_mode is VideoInputMode.STRICT_FIRST_FRAME:
        return VideoInputMode.STRICT_FIRST_FRAME
    return VideoInputMode.MULTIMODAL_REFERENCE


def validate_critical_continuity(
    episode: EpisodePlan,
    observations: Mapping[tuple[str, str], str],
) -> tuple[GateIssue, ...]:
    """只核对Episode显式声明的关键物理关系。

    `observations`来自人工或视觉审核，键为(subject, relation)。普通背景和
    非关键装饰不参与硬门，避免审核体系再次无限膨胀。
    """

    issues: list[GateIssue] = []
    for relation in episode.critical_relations:
        actual = observations.get((relation.subject, relation.relation))
        if actual is None:
            issues.append(
                GateIssue(
                    gate="continuity",
                    code="missing_observation",
                    message=(f"缺少{relation.subject}/{relation.relation}的审核观察。"),
                    level=IssueLevel.HARD,
                )
            )
        elif actual != relation.final_state:
            issues.append(
                GateIssue(
                    gate="continuity",
                    code="critical_relation_mismatch",
                    message=(
                        f"{relation.subject}/{relation.relation}实际为"
                        f"{actual!r}，预期为{relation.final_state!r}。"
                    ),
                    level=IssueLevel.HARD,
                )
            )
    return tuple(issues)


def hard_failures(issues: Iterable[GateIssue]) -> tuple[GateIssue, ...]:
    """提取真正阻断生产的规则结果。"""

    return tuple(issue for issue in issues if issue.level is IssueLevel.HARD)
