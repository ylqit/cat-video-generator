"""核心收敛后测试共用的最小、完整业务对象。"""

from __future__ import annotations

from datetime import date

import pytest

from cat_video_generator.domain.continuity import (
    DominantView,
    EntityState,
    EntityTransition,
    VisibleEntity,
    VisibleWorld,
    WorldAnchor,
)
from cat_video_generator.domain.contracts import (
    ActionStage,
    AppearancePlan,
    CameraMove,
    DailyProductionPlan,
    DayBrief,
    EpisodePlan,
    EpisodeScript,
    ShotPlan,
    Slot,
    SlotBrief,
)
from cat_video_generator.domain.rendering import VideoInputMode


def state(
    *,
    anchor: str | None = None,
    support: str | None = None,
    container: str | None = None,
    active: bool = True,
    appearance: str = "外观保持稳定",
) -> EntityState:
    return EntityState(
        anchor_id=anchor,
        support_id=support,
        container_id=container,
        active=active,
        appearance_signature=appearance,
    )


def episode_for(
    slot: Slot,
    *,
    mode: VideoInputMode = VideoInputMode.MULTIMODAL_REFERENCE,
) -> EpisodePlan:
    prop_before = state(anchor="table", appearance="一只浅蓝色小纸风车")
    prop_after = state(support="person", appearance="同一只浅蓝色小纸风车")
    world = VisibleWorld(
        anchors=[
            WorldAnchor(id="ground", name="木地板", type="ground"),
            WorldAnchor(id="table", name="矮桌桌面", type="table"),
        ],
        entities=[
            VisibleEntity(
                id="person",
                name="中性儿童",
                type="person",
                semantic_key="person:front",
                initial_state=state(anchor="ground", appearance="短发中性儿童"),
            ),
            VisibleEntity(
                id="cat",
                name="灰白猫",
                type="cat",
                semantic_key="cat:front",
                initial_state=state(anchor="ground", appearance="固定灰白斑纹"),
            ),
            VisibleEntity(
                id="pinwheel",
                name="纸风车",
                type="prop",
                semantic_key="element:pinwheel",
                initial_state=prop_before,
            ),
        ],
    )
    script = EpisodeScript(
        title=f"{slot.value}纸风车",
        event_key=f"{slot.value}-pinwheel",
        location_key="sunroom",
        main_event="人物发现风吹动纸风车并拿起来观察",
        scene="明亮的室内阳台，木地板和一张稳定矮桌",
        style_context="indoor",
        appearance=AppearancePlan(
            description="宽松浅色上衣与深色短裤，不携带背包",
            continuity="continue",
        ),
        actions=[
            ActionStage(
                order=1,
                actor_id="cat",
                action="灰白猫先注意到桌面纸风车被微风吹动",
                visible_result="猫咪抬头看向桌面",
                transitions=[],
            ),
            ActionStage(
                order=2,
                actor_id="person",
                action="人物伸手拿起同一只纸风车并轻轻转动",
                visible_result="纸风车持续由人物手掌支撑",
                transitions=[
                    EntityTransition(
                        entity_id="pinwheel",
                        before=prop_before,
                        after=prop_after,
                        reason="人物从桌面拿起纸风车",
                    )
                ],
            ),
        ],
        shots=[
            ShotPlan(
                order=1,
                action_orders=[1, 2],
                framing="中景",
                camera_move=CameraMove.PUSH,
                dominant_view=DominantView.FRONT,
                direction="从猫咪视线缓慢推向人物手中的纸风车",
            )
        ],
        ending="人物继续转动纸风车，猫咪伸鼻靠近感受微风",
        duration_seconds=9,
        video_input_mode=mode,
        visible_world=world,
    )
    return EpisodePlan(slot=slot, script=script)


@pytest.fixture
def daily_plan() -> DailyProductionPlan:
    slots = [
        SlotBrief(
            slot=slot,
            narrative_purpose=f"观察{slot.value}生活中的微小变化",
            scene_direction="同一天中符合时段光线的生活空间",
            event_direction="围绕一个清晰生活事件展开动作",
            appearance_intent="服饰与场景自然一致",
        )
        for slot in Slot
    ]
    return DailyProductionPlan(
        day_brief=DayBrief(
            content_date=date(2026, 8, 2),
            theme="风吹动的小发现",
            day_context="中性儿童和灰白猫在普通生活空间观察风带来的变化",
            slots=slots,
        ),
        episodes=[episode_for(slot) for slot in Slot],
    )
