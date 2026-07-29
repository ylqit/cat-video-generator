from __future__ import annotations

from datetime import date

import pytest

from cat_video_generator.domain.contracts import (
    ActionStage,
    AppearancePlan,
    CameraMove,
    CriticalRelation,
    DailyProductionPlan,
    EpisodePlan,
    ShotPlan,
    Slot,
    VideoInputMode,
)


def _episode(
    slot: Slot,
    *,
    title: str,
    appearance: AppearancePlan,
    input_mode: VideoInputMode,
) -> EpisodePlan:
    return EpisodePlan(
        slot=slot,
        title=title,
        main_event=f"{title}中女孩和灰白猫完成一个清晰生活事件",
        scene=f"{title}对应的单一自然生活场景，空间边界清楚且可拍摄",
        cast=["person", "cat"],
        appearance=appearance,
        actions=[
            ActionStage(
                order=1,
                action="女孩和灰白猫进入画面并注意到当前环境中的变化",
                visible_result="观众明确理解人物、猫咪和本段活动目标",
            ),
            ActionStage(
                order=2,
                action="女孩继续主要活动，灰白猫以一次自然反应回应她",
                visible_result="角色关系和事件状态产生可见变化",
            ),
            ActionStage(
                order=3,
                action="两者带着刚刚形成的结果继续自然移动离开当前构图",
                visible_result="事件获得主动收束而不是原地静止互看",
            ),
        ],
        shots=[
            ShotPlan(
                order=1,
                action_orders=[1],
                framing="中景",
                camera_move=CameraMove.FOLLOW,
                direction="平稳跟随女孩和灰白猫进入当前生活场景",
            ),
            ShotPlan(
                order=2,
                action_orders=[2, 3],
                framing="中近景",
                camera_move=CameraMove.PULL,
                direction="展示角色回应以及主动离开构图的结果",
            ),
        ],
        ending="女孩继续迈步，灰白猫自然跟上，环境仍有轻微连续响应",
        duration_seconds=10,
        video_input_mode=input_mode,
        critical_relations=[
            CriticalRelation(
                subject="person-and-cat",
                relation="count",
                initial_state="画面开始为一人一猫",
                final_state="画面结束仍为同一人一猫",
            )
        ],
    )


@pytest.fixture
def daily_plan() -> DailyProductionPlan:
    morning = AppearancePlan(description="轻便上衣、短裤和适合户外行走的鞋，不携带背包")
    noon = AppearancePlan(
        description="延续轻便上衣和短裤，因日照增加草编帽，不携带背包",
        changes_from_previous=["增加草编遮阳帽"],
        change_reason="中午日照增强且活动位于户外",
    )
    evening = AppearancePlan(
        description="延续轻便上衣和短裤，摘下草编帽并增加薄外套",
        changes_from_previous=["摘下草编帽", "增加薄外套"],
        change_reason="傍晚转入有风场景且日照减弱",
    )
    return DailyProductionPlan(
        content_date=date(2026, 7, 31),
        theme="城市里三个有回应的轻生活瞬间",
        day_context="同一天的普通城市生活，天气由晴朗逐步转为傍晚微风",
        episodes=[
            _episode(
                Slot.MORNING,
                title="晨间橱窗",
                appearance=morning,
                input_mode=VideoInputMode.MULTIMODAL_REFERENCE,
            ),
            _episode(
                Slot.NOON,
                title="午间树影",
                appearance=noon,
                input_mode=VideoInputMode.STRICT_FIRST_FRAME,
            ),
            _episode(
                Slot.EVENING,
                title="晚风灯光",
                appearance=evening,
                input_mode=VideoInputMode.STRICT_FIRST_LAST,
            ),
        ],
    )
