"""四道硬门及非阻断警告。

规则只验证生产能否安全继续，不承担导演评分，也不把普通视觉偏差升级成
复杂的数值门槛。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable, Mapping

from .continuity import assess_visible_world
from .contracts import (
    DailyProductionPlan,
    DayBrief,
    EpisodePlan,
    GenerationStrategy,
    RecentContentSummary,
    SlotBrief,
    VideoInputMode,
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
    """一项可定位的硬失败或警告。"""

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
    for episode in plan.episodes:
        searchable = _episode_script_text(episode)
        identity_rewrites = tuple(
            term.lower()
            for term in series_profile.forbidden_identity_rewrites
        )
        if _contains_identity_rewrite(searchable, identity_rewrites):
            issues.append(
                GateIssue(
                    gate="identity",
                    code="gendered_identity_rewrite",
                    message=(
                        f"{episode.slot.value}脚本把中性儿童改写为性别化身份或"
                        "改变了固定发长。"
                    ),
                    level=IssueLevel.HARD,
                )
            )
        count_issue = _fixed_cast_count_issue(episode, searchable)
        if count_issue is not None:
            issues.append(count_issue)
    return tuple(issues)


def validate_episode_against_brief(
    episode: EpisodePlan,
    *,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> tuple[GateIssue, ...]:
    """检查时段导演没有越过总导演边界或省略共享元素状态。"""

    issues: list[GateIssue] = []
    count_issue = _fixed_cast_count_issue(
        episode,
        _episode_script_text(episode),
    )
    if count_issue is not None:
        issues.append(count_issue)
    identity_rewrites = tuple(
        term.lower() for term in series_profile.forbidden_identity_rewrites
    )
    if _contains_identity_rewrite(_episode_script_text(episode), identity_rewrites):
        issues.append(
            GateIssue(
                gate="identity",
                code="gendered_identity_rewrite",
                message=(
                    f"{episode.slot.value}脚本把中性儿童改写为性别化身份或"
                    "改变了固定发长。"
                ),
                level=IssueLevel.HARD,
            )
        )
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
    assert episode.visible_world is not None
    world_report = assess_visible_world(episode.visible_world)
    for message in world_report.contradictions:
        issues.append(
            GateIssue(
                gate="continuity",
                code="visible_world_contradiction",
                message=message,
                level=IssueLevel.HARD,
            )
        )
    for message in world_report.render_risk_reasons:
        issues.append(
            GateIssue(
                gate="render",
                code="visible_world_render_risk",
                message=message,
                level=IssueLevel.WARNING,
            )
        )
    action_orders = {item.order for item in episode.actions}
    transition_orders = {
        item.action_order for item in episode.visible_world.action_transitions
    }
    if transition_orders != action_orders:
        issues.append(
            GateIssue(
                gate="continuity",
                code="missing_action_transition",
                message="VisibleWorldPlan必须为每个动作阶段提供一条状态转换。",
                level=IssueLevel.HARD,
            )
        )
    for transition in episode.visible_world.action_transitions:
        if not transition.no_state_change and transition.state_entity_id is None:
            issues.append(
                GateIssue(
                    gate="continuity",
                    code="missing_state_entity",
                    message=(
                        f"动作{transition.action_order}必须显式声明stateEntityId，"
                        "纯观察动作则声明noStateChange"
                    ),
                    level=IssueLevel.HARD,
                )
            )
    shot_orders = {item.order for item in episode.shots}
    if any(
        item.shot_order not in shot_orders
        for item in episode.visible_world.action_transitions
    ):
        issues.append(
            GateIssue(
                gate="continuity",
                code="unknown_transition_shot",
                message="VisibleWorldPlan动作引用了不存在的镜头。",
                level=IssueLevel.HARD,
            )
        )
    return tuple(issues)


def _episode_script_text(episode: EpisodePlan) -> str:
    world_text: tuple[str, ...] = ()
    if episode.visible_world is not None:
        world_text = tuple(
            value
            for item in episode.visible_world.tracked_entities
            for value in (item.display_name, item.appearance_signature)
        )
    return " ".join(
        (
            episode.main_event,
            episode.scene,
            episode.appearance.description,
            episode.ending,
            *(stage.action for stage in episode.actions),
            *(stage.visible_result for stage in episode.actions),
            *(shot.framing for shot in episode.shots),
            *(shot.direction for shot in episode.shots),
            *world_text,
        )
    ).lower()


def _contains_identity_rewrite(
    text: str,
    forbidden_terms: tuple[str, ...],
) -> bool:
    """在执行文本中查找身份改写，同时保护与人物无关的固定复合词。"""

    protected_terms = ("少年宫", "马尾松")
    scrubbed = text
    for protected in protected_terms:
        scrubbed = scrubbed.replace(protected, "")
    return any(term in scrubbed for term in forbidden_terms)


def _fixed_cast_count_issue(
    episode: EpisodePlan,
    searchable: str,
) -> GateIssue | None:
    extra_person_terms = (
        "两人一猫",
        "两个人一只猫",
        "两名儿童",
        "两个儿童",
        "第二个儿童",
        "第二名儿童",
    )
    if not any(term in searchable for term in extra_person_terms):
        return None
    return GateIssue(
        gate="identity",
        code="fixed_cast_count_mismatch",
        message=f"{episode.slot.value}脚本把固定一人一猫改写成了额外人物。",
        level=IssueLevel.HARD,
    )


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

    身份、画风和普通元素优先走多模态参考。包含、交接或边界关系本身不再
    自动升级首尾帧；只有导演显式声明端点构图需要精确锚定时才使用严格模式。
    """

    if episode.video_input_mode is VideoInputMode.STRICT_FIRST_LAST:
        return VideoInputMode.STRICT_FIRST_LAST
    if episode.video_input_mode is VideoInputMode.STRICT_FIRST_FRAME:
        return VideoInputMode.STRICT_FIRST_FRAME
    return VideoInputMode.MULTIMODAL_REFERENCE


def validate_generation_strategy(episode: EpisodePlan) -> tuple[GateIssue, ...]:
    """验证分段只用于两个天然硬切镜头，不能成为自动失败重试。"""

    if episode.generation_strategy is GenerationStrategy.SINGLE_PASS:
        return ()
    issues: list[GateIssue] = []
    if len(episode.shots) != 2 or len(episode.segments) != 2:
        issues.append(
            GateIssue(
                gate="render",
                code="invalid_multi_clip_shape",
                message="multi_clip必须由两个天然硬切镜头和两个片段组成。",
                level=IssueLevel.HARD,
            )
        )
        return tuple(issues)
    if any(item.duration_seconds < 4 for item in episode.segments):
        issues.append(
            GateIssue(
                gate="render",
                code="segment_too_short",
                message="multi_clip每段至少4秒。",
                level=IssueLevel.HARD,
            )
        )
    assert episode.visible_world is not None
    if episode.segments[1].requires_tail_link:
        boundaries = {
            (item.after_shot_order, item.next_shot_order)
            for item in episode.visible_world.shot_boundary_states
        }
        if (1, 2) not in boundaries:
            issues.append(
                GateIssue(
                    gate="render",
                    code="missing_segment_boundary_state",
                    message="连续空间第二段必须声明镜头1到镜头2的状态继承",
                    level=IssueLevel.HARD,
                )
            )
    action_to_shot = {
        item.action_order: item.shot_order
        for item in episode.visible_world.action_transitions
    }
    for transition in episode.visible_world.action_transitions:
        if (
            transition.continuous_shot
            and transition.action_order in action_to_shot
            and any(
                transition.action_order in segment.action_orders
                and segment.shot_order != action_to_shot[transition.action_order]
                for segment in episode.segments
            )
        ):
            issues.append(
                GateIssue(
                    gate="render",
                    code="physical_change_crosses_cut",
                    message="高风险物理变化不能跨multi_clip切点。",
                    level=IssueLevel.HARD,
                )
            )
    return tuple(issues)


def validate_recent_cooldown(
    plan: DailyProductionPlan,
    recent_summaries: Iterable[RecentContentSummary],
) -> tuple[GateIssue, ...]:
    """按结构化键检查全天冷却，不再用中文子串猜测。"""

    return tuple(
        issue
        for episode in plan.episodes
        for issue in validate_episode_cooldown(episode, recent_summaries)
    )


def validate_episode_cooldown(
    episode: EpisodePlan,
    recent_summaries: Iterable[RecentContentSummary],
) -> tuple[GateIssue, ...]:
    """在每个时段导演返回后立即检查事件、地点和关键元素冷却。"""

    recent = tuple(recent_summaries)
    event_keys = {key for item in recent for key in item.event_keys}
    location_keys = {key for item in recent for key in item.location_keys}
    element_keys = {
        key for item in recent for key in item.element_semantic_keys
    }
    issues: list[GateIssue] = []
    if episode.event_key and episode.event_key in event_keys:
        issues.append(
            GateIssue(
                gate="planning",
                code="recent_event_repeat",
                message=f"{episode.slot.value}事件键仍在近期冷却期。",
                level=IssueLevel.HARD,
            )
        )
    if episode.location_key and episode.location_key in location_keys:
        issues.append(
            GateIssue(
                gate="planning",
                code="recent_location_repeat",
                message=f"{episode.slot.value}地点键仍在近期冷却期。",
                level=IssueLevel.HARD,
            )
        )
    current_elements = set(episode.reference_semantic_keys)
    if current_elements and current_elements.issubset(element_keys):
        issues.append(
            GateIssue(
                gate="planning",
                code="recent_prop_set_repeat",
                message=f"{episode.slot.value}关键元素组合仍在冷却期。",
                level=IssueLevel.HARD,
            )
        )
    return tuple(issues)


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
