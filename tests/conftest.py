"""新叙事渲染内核的共用业务对象。"""

from __future__ import annotations

from datetime import date

import pytest

from cat_video_generator.domain.contracts import (
    ActionStage,
    ActivityFocus,
    AppearancePlan,
    CameraMove,
    CriticalProp,
    DailyProductionPlan,
    DayBrief,
    DurationBand,
    DurationIntent,
    DurationMode,
    EpisodeEnding,
    EpisodePlan,
    EpisodeScript,
    Handoff,
    RelationshipArc,
    ShotPlan,
    Slot,
    SlotBrief,
    StoryPatternId,
)


def _duration_band(seconds: int) -> DurationBand:
    if seconds <= 15:
        return DurationBand.SHORT
    if seconds <= 30:
        return DurationBand.MEDIUM
    return DurationBand.LONG


def slot_brief(
    slot: Slot, *, duration: int, focus: ActivityFocus = ActivityFocus.CAT_LEAD
) -> SlotBrief:
    band = _duration_band(duration)
    return SlotBrief(
        slot=slot,
        narrative_role=f"推进{slot.value}时段的风筝生活片段并承接全天关系",
        scene_direction="同一天春日环境中符合时段光线的自然生活空间",
        event_direction="猫咪先推动可见变化，人物完成工具活动并在结尾回应",
        appearance_intent="中性儿童服饰随地点自然变化，猫咪斑纹与体型保持稳定",
        resolved_activity_focus=focus,
        relationship_direction="猫咪主活动与人物副活动最终汇合为一个温暖回报",
        duration_intent=DurationIntent(
            requested_mode=DurationMode.ADAPTIVE,
            resolved_band=band,
            resolution_reason="总导演根据时段叙事容量解析自适应时长档位",
        ),
    )


def episode_for(
    slot: Slot,
    *,
    duration: int = 12,
    focus: ActivityFocus = ActivityFocus.CAT_LEAD,
) -> EpisodePlan:
    action_count = 4 if duration > 15 else 3
    actions = [
        ActionStage(
            order=1,
            actor_id="cat",
            action="灰白猫先追逐轻轻摆动的同一条彩色飘带并停在风筝旁观察",
            visible_result="猫咪把观众注意力带到尚未完成的同一只风筝",
        ),
        ActionStage(
            order=2,
            actor_id="person",
            action="中性儿童稳定扶住风筝骨架并把同一条彩色飘带系牢",
            visible_result="风筝与彩带连接完成且始终由人物或桌面支撑",
        ),
        ActionStage(
            order=3,
            actor_id="cat",
            action="灰白猫用前爪轻拍飘带后回到人物手边，保持自然四足姿态",
            visible_result="猫咪的探索线与人物制作线在同一只风筝旁汇合",
        ),
    ]
    if action_count == 4:
        actions.append(
            ActionStage(
                order=4,
                actor_id="person",
                action="人物顺着猫咪视线调整线轴，让风筝重新稳定升向天空",
                visible_result="同一只风筝恢复飞行，猫咪回到人物身侧抬头观看",
            )
        )

    if duration <= 15:
        shots = [
            ShotPlan(
                order=1,
                action_orders=[1, 2],
                framing="中景建立人猫与风筝的相对位置",
                camera_move=CameraMove.FIXED,
                direction="固定机位观察猫咪追飘带与人物制作风筝的并行活动",
            ),
            ShotPlan(
                order=2,
                action_orders=[3],
                framing="近景呈现猫咪回到人物手边的关系汇合",
                camera_move=CameraMove.PUSH,
                direction="缓慢推近猫咪轻拍飘带后蹭向人物手边的可见回报",
            ),
        ]
    elif duration <= 30:
        shots = [
            ShotPlan(
                order=1,
                action_orders=[1, 2],
                framing="中景建立山坡、人猫和同一只风筝",
                camera_move=CameraMove.FOLLOW,
                direction="平稳跟随猫咪追逐风筝影子并带出人物持续放线",
            ),
            ShotPlan(
                order=2,
                action_orders=[3, 4],
                framing="中近景呈现猫咪提示与人物恢复风筝飞行",
                camera_move=CameraMove.PUSH,
                direction="缓慢推近猫咪回到人物身侧以及风筝恢复升空的回报",
            ),
        ]
    else:
        shots = [
            ShotPlan(
                order=1,
                action_orders=[1],
                framing="全景建立开阔山坡和猫咪主活动",
                camera_move=CameraMove.FOLLOW,
                direction="平稳跟随猫咪追逐风筝影子并建立空间方向",
            ),
            ShotPlan(
                order=2,
                action_orders=[2],
                framing="中景表现人物持续稳定操作线轴",
                camera_move=CameraMove.FIXED,
                direction="固定机位保留人物、猫咪与风筝线的连续空间关系",
            ),
            ShotPlan(
                order=3,
                action_orders=[3, 4],
                framing="近景至中景兑现人猫汇合与风筝恢复",
                camera_move=CameraMove.PULL,
                direction="缓慢拉远呈现猫咪回到人物身侧和风筝重新升空",
            ),
        ]

    script = EpisodeScript(
        title=f"{slot.value}风筝生活片段",
        event_key=f"{slot.value}_kite_event",
        location_key="home_worktable" if slot is Slot.MORNING else "spring_hillside",
        story_pattern=(
            StoryPatternId.SETUP_MISHAP_RECOVERY
            if slot is Slot.NOON
            else StoryPatternId.PARALLEL_CONVERGENCE
        ),
        episode_question="猫咪的发现会怎样改变人物正在进行的风筝活动",
        main_event="猫咪推动观众注意力，人物完成同一项风筝活动并在结尾回应猫咪",
        scene="春日自然光下的生活空间，人物、灰白猫与同一只彩色风筝关系清楚",
        style_context="indoor" if slot is Slot.MORNING else "outdoor",
        appearance=AppearancePlan(
            description="同一个中性短发儿童，穿轻便春装与防滑鞋，不携带无关配饰",
            change_reason="从室内制作转到户外活动，服饰按地点与温度自然调整"
            if slot is not Slot.MORNING
            else None,
        ),
        activity_focus=focus,
        relationship_arc=RelationshipArc(
            lead_activity="灰白猫追逐、观察或轻拍风筝飘带，持续推动新的可见信息",
            secondary_activity="人物稳定制作、放线或收线，只承担需要手和工具的活动",
            convergence="猫咪回到人物身侧提示变化，人物回应并让同一只风筝形成回报",
        ),
        actions=actions,
        shots=shots,
        ending=EpisodeEnding(result="猫咪回到人物身侧，同一只风筝保持完整并形成温暖回报"),
        sound_design="春风、草叶、脚步和风筝纸张轻响同步，结尾保留猫咪轻叫与飘带声，无对白旁白歌词",
        duration_seconds=duration,
        critical_props=[
            CriticalProp(
                entity_key="day_kite",
                name="同一只彩色风筝",
                start="本时段开始时由人物或桌面稳定支撑并保持完整",
                end="本时段结束时仍是同一只风筝且状态与全天交接一致",
            )
        ],
    )
    return EpisodePlan(slot=slot, script=script)


@pytest.fixture
def daily_plan() -> DailyProductionPlan:
    durations = {Slot.MORNING: 12, Slot.NOON: 22, Slot.EVENING: 12}
    return DailyProductionPlan(
        day_brief=DayBrief(
            content_date=date(2026, 8, 10),
            theme="春日放风筝的一天",
            day_objective="让猫咪推动早中晚三个风筝生活片段并与人物持续汇合",
            day_context="上午在家制作，中午到山坡放飞，傍晚收线后一起迎着夕阳回家",
            shared_motif="同一只带彩色飘带的风筝贯穿全天并记录人猫关系变化",
            slot_briefs=[slot_brief(slot, duration=durations[slot]) for slot in Slot],
            handoffs=[
                Handoff(
                    entity_key="day_kite",
                    from_slot=Slot.MORNING,
                    to_slot=Slot.NOON,
                    state="制作完成的同一只风筝被带到春日山坡",
                ),
                Handoff(
                    entity_key="day_kite",
                    from_slot=Slot.NOON,
                    to_slot=Slot.EVENING,
                    state="放飞完成的同一只风筝在傍晚被人物收回",
                ),
            ],
        ),
        episodes=[episode_for(slot, duration=durations[slot]) for slot in Slot],
    )
