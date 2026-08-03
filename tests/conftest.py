"""核心收敛后测试共用的最小、完整业务对象。"""

from __future__ import annotations

from datetime import date

import pytest

from cat_video_generator.domain.continuity import (
    DominantView,
    EntityKind,
    EntityLifecycle,
    EntityState,
    Placement,
    SceneAnchor,
    SceneContinuity,
    TrackedEntity,
)
from cat_video_generator.domain.contracts import (
    ActionStage,
    AppearancePlan,
    CameraMove,
    DailyProductionPlan,
    DayBrief,
    EpisodeEnding,
    EpisodePlan,
    EpisodeScript,
    ShotPlan,
    Slot,
    SlotBrief,
)


def state(
    *,
    kind: str = "anchor",
    target: str | None = "ground",
    present: bool = True,
) -> EntityState:
    return EntityState(
        present=present,
        placement=Placement(kind=kind, target_id=target),
    )


def episode_for(slot: Slot) -> EpisodePlan:
    continuity = SceneContinuity(
        anchors=[
            SceneAnchor(id="ground", name="木地板", type="ground"),
            SceneAnchor(id="table", name="矮木桌面", type="table"),
        ],
        entities=[
            TrackedEntity(
                id="person",
                name="中性儿童",
                kind=EntityKind.PERSON,
                entity_key="person",
                start_state=state(),
                end_state=state(),
                lifecycle=EntityLifecycle.PERSIST,
                form_key="neutral-child",
            ),
            TrackedEntity(
                id="cat",
                name="灰白猫",
                kind=EntityKind.CAT,
                entity_key="cat",
                start_state=state(),
                end_state=state(),
                lifecycle=EntityLifecycle.PERSIST,
                form_key="gray-white-cat",
            ),
            TrackedEntity(
                id="pinwheel",
                name="纸风车",
                kind=EntityKind.PROP,
                entity_key="pinwheel",
                start_state=state(kind="anchor", target="table"),
                end_state=state(kind="held_by", target="person"),
                lifecycle=EntityLifecycle.PERSIST,
                form_key="blue-pinwheel",
                change_reason="人物从桌面拿起同一只纸风车",
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
        ),
        actions=[
            ActionStage(
                order=1,
                actor_id="cat",
                action="灰白猫先注意到桌面纸风车被微风吹动",
                visible_result="猫咪抬头看向桌面",
            ),
            ActionStage(
                order=2,
                actor_id="person",
                action="人物伸手拿起同一只纸风车并轻轻转动",
                visible_result="纸风车持续由人物手掌支撑",
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
        ending=EpisodeEnding(
            result="人物继续转动纸风车，猫咪伸鼻靠近感受微风",
            visual_critical=False,
            key_entity_ids=["pinwheel", "cat"],
        ),
        duration_seconds=9,
        continuity=continuity,
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
