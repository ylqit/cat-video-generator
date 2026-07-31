from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from cat_video_generator.application.event_seeds import EventSeedCatalog
from cat_video_generator.application.ports import StoredAsset, StoredEpisode
from cat_video_generator.application.visual_preparation import VisualPreparationService
from cat_video_generator.domain.continuity import (
    ActionTransition,
    EntityLifecycle,
    RenderRiskLevel,
    SceneAnchor,
    ShotBoundaryState,
    TopologyChange,
    TrackedEntity,
    VisibleWorldPlan,
    assess_visible_world,
    validate_visible_world,
)
from cat_video_generator.domain.provider_normalization import (
    normalize_episode_draft_payload,
)
from cat_video_generator.domain.rules import hard_failures, validate_plan_gate
from cat_video_generator.domain.visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
)
from cat_video_generator.domain.workflow import EpisodeStatus


def test_visible_world_rejects_unsupported_non_throwing_object() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="basket",
                display_name="竹篮",
                entity_type="container",
                appearance_signature="一个浅棕圆形无盖竹篮",
                lifecycle=EntityLifecycle.ENTER,
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["basket"],
                state_entity_id="basket",
                lifecycle_event=EntityLifecycle.ENTER,
                change_reason="人物把竹篮带入画面",
            )
        ],
    )
    assert validate_visible_world(plan) == ("basket动作结束后缺少合法支撑",)


def test_visible_world_rejects_high_risk_change_across_cut() -> None:
    plan = VisibleWorldPlan(
            scene_anchors=[
                SceneAnchor(
                    anchor_id="table",
                    display_name="木桌",
                    anchor_type="table",
                ),
                SceneAnchor(
                    anchor_id="shelf",
                    display_name="木架",
                    anchor_type="shelf",
                ),
            ],
            tracked_entities=[
                TrackedEntity(
                    entity_id="person",
                    display_name="中性儿童",
                    entity_type="person",
                    appearance_signature="同一短发中性儿童",
                    initial_anchor_id="table",
                ),
                TrackedEntity(
                    entity_id="teapot",
                    display_name="白色茶壶",
                    entity_type="prop",
                    appearance_signature="一把白色圆肚陶瓷茶壶",
                    initial_anchor_id="table",
                )
            ],
            action_transitions=[
                ActionTransition(
                    action_order=1,
                    shot_order=1,
                    actor_id="person",
                    target_entity_ids=["teapot"],
                    before_anchor_id="table",
                    after_anchor_id="shelf",
                    support_before="table",
                    support_after="shelf",
                    continuous_shot=False,
                )
            ],
        )
    report = assess_visible_world(plan)
    assert report.contradictions == ()
    assert report.render_risk_level is RenderRiskLevel.HIGH
    assert "跨镜头物理变化" in "；".join(report.render_risk_reasons)


def test_visible_world_counts_one_coherent_transfer_as_one_change() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            ),
            SceneAnchor(
                anchor_id="shelf",
                display_name="木架",
                anchor_type="shelf",
            ),
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="box",
                display_name="蓝色小盒",
                entity_type="container",
                appearance_signature="唯一一个蓝色方形木盒",
                initial_anchor_id="table",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["box"],
                before_anchor_id="table",
                support_before="table",
                support_after="person",
            ),
            ActionTransition(
                action_order=2,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["box"],
                before_anchor_id="table",
                after_anchor_id="shelf",
                support_before="person",
                support_after="shelf",
            ),
        ],
    )
    assert validate_visible_world(plan) == ()


def test_visible_world_applies_state_to_one_explicit_entity() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="seat",
                display_name="木椅",
                anchor_type="seat",
            ),
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            ),
            SceneAnchor(
                anchor_id="shelf",
                display_name="木架",
                anchor_type="shelf",
            ),
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="seat",
            ),
            TrackedEntity(
                entity_id="box",
                display_name="蓝色小盒",
                entity_type="container",
                appearance_signature="唯一蓝色方形木盒",
                initial_anchor_id="table",
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person", "box"],
                state_entity_id="box",
                before_anchor_id="table",
                after_anchor_id="shelf",
                support_before="table",
                support_after="shelf",
            )
        ],
    )
    assert validate_visible_world(plan) == ()


def test_visible_world_allows_declared_anchor_as_action_context_target() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="chair",
                display_name="木椅",
                anchor_type="seat",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="chair",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person", "chair"],
                state_entity_id="person",
                before_anchor_id="chair",
                after_anchor_id="chair",
                support_before="chair",
                support_after="chair",
            )
        ],
    )
    assert validate_visible_world(plan) == ()


def test_visible_world_treats_persist_as_no_lifecycle_change() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="seat",
                display_name="木椅",
                anchor_type="seat",
            ),
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            ),
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="seat",
                initial_contact_id="目光看向画纸",
            ),
            TrackedEntity(
                entity_id="paper",
                display_name="画纸",
                entity_type="prop",
                appearance_signature="桌面上的唯一白色画纸",
                initial_anchor_id="table",
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person", "paper"],
                state_entity_id="person",
                before_anchor_id="seat",
                after_anchor_id="seat",
                support_before="seat",
                support_after="seat",
                contact_before="目光看向画纸",
                contact_after="手停在画纸上方",
                lifecycle_event=EntityLifecycle.PERSIST,
            ),
            ActionTransition(
                action_order=2,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person", "paper"],
                state_entity_id="person",
                before_anchor_id="seat",
                after_anchor_id="seat",
                support_before="seat",
                support_after="seat",
                contact_before="手停在画纸上方",
                contact_after="手按住画纸边角",
                lifecycle_event=EntityLifecycle.PERSIST,
            ),
        ],
    )
    assert validate_visible_world(plan) == ()


def test_visible_world_does_not_charge_normal_actor_support_to_prop_budget() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="path",
                display_name="连续步道",
                anchor_type="ground",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="path",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=order,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person"],
                before_anchor_id="path",
                after_anchor_id="path",
                support_after="path",
            )
            for order in (1, 2, 3)
        ],
    )
    assert validate_visible_world(plan) == ()


def test_visible_world_requires_declared_seat_before_sitting() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="ground",
                display_name="露台地面",
                anchor_type="ground",
            ),
            SceneAnchor(
                anchor_id="stone",
                display_name="普通石墩",
                anchor_type="other",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="ground",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person"],
                before_anchor_id="ground",
                after_anchor_id="stone",
                support_before="ground",
                support_after="stone",
                topology_change=TopologyChange.SIT,
                seat_anchor_id="stone",
            )
        ],
    )
    assert validate_visible_world(plan) == ("人物坐下或站起必须引用seat类型的可坐表面",)


def test_visible_world_allows_seat_context_without_topology_change() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="seat",
                display_name="木椅",
                anchor_type="seat",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="seat",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person"],
                state_entity_id="person",
                before_anchor_id="seat",
                after_anchor_id="seat",
                support_before="seat",
                support_after="seat",
                topology_change=TopologyChange.NONE,
                seat_anchor_id="seat",
            )
        ],
    )
    assert validate_visible_world(plan) == ()


def test_visible_world_rejects_conflicting_position_at_cut() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="terrace",
                display_name="露台地面",
                anchor_type="ground",
            ),
            SceneAnchor(
                anchor_id="shelf",
                display_name="露台木架",
                anchor_type="shelf",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="terrace",
            ),
            TrackedEntity(
                entity_id="teabag",
                display_name="白色茶袋",
                entity_type="container",
                appearance_signature="一个白色方形布茶袋",
                initial_anchor_id="terrace",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["teabag"],
                before_anchor_id="terrace",
                after_anchor_id="shelf",
                support_before="terrace",
                support_after="shelf",
            )
        ],
        shot_boundary_states=[
            ShotBoundaryState(
                after_shot_order=1,
                next_shot_order=2,
                visible_entity_ids=["teabag"],
                entity_anchor_ids={"teabag": "terrace"},
            )
        ],
    )
    assert "切镜继承的teabag位置'terrace'" in validate_visible_world(plan)[0]


def test_anchor_only_target_becomes_contradiction_instead_of_key_error() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="ground",
                display_name="庭院地面",
                anchor_type="ground",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="ground",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["ground"],
            )
        ],
    )
    assert validate_visible_world(plan) == (
        "动作1必须声明stateEntityId或noStateChange",
    )


def test_empty_transition_requires_explicit_no_state_change() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="ground",
                display_name="窗边地面",
                anchor_type="ground",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="ground",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
            )
        ],
    )
    assert "必须声明stateEntityId或noStateChange" in validate_visible_world(plan)[0]


def test_no_state_change_rejects_hidden_state_mutation() -> None:
    with pytest.raises(ValueError, match="noStateChange"):
        ActionTransition(
            action_order=1,
            shot_order=1,
            actor_id="person",
            no_state_change=True,
            after_anchor_id="table",
        )


@pytest.mark.parametrize(
    "event",
    [
        EntityLifecycle.ENTER,
        EntityLifecycle.EXIT,
        EntityLifecycle.CONSUME,
        EntityLifecycle.TRANSFORM,
    ],
)
def test_lifecycle_change_requires_reason(event: EntityLifecycle) -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="item",
                display_name="唯一物体",
                entity_type="prop",
                appearance_signature="唯一浅色圆形物体",
                initial_anchor_id=None if event is EntityLifecycle.ENTER else "table",
                lifecycle=(
                    EntityLifecycle.ENTER
                    if event is EntityLifecycle.ENTER
                    else EntityLifecycle.PERSIST
                ),
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["item"],
                state_entity_id="item",
                lifecycle_event=event,
                resulting_appearance_signature=(
                    "唯一蓝色圆形物体"
                    if event is EntityLifecycle.TRANSFORM
                    else None
                ),
            )
        ],
    )
    assert any("生命周期变化缺少changeReason" in issue for issue in validate_visible_world(plan))


def test_transform_requires_resulting_appearance_signature() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="dough",
                display_name="面团",
                entity_type="food",
                appearance_signature="唯一白色圆面团",
                initial_anchor_id="table",
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["dough"],
                state_entity_id="dough",
                lifecycle_event=EntityLifecycle.TRANSFORM,
                change_reason="人物把面团压成薄饼",
            )
        ],
    )
    assert any("transform缺少结果外观签名" in issue for issue in validate_visible_world(plan))


def test_initial_relation_cannot_reference_entity_waiting_to_enter() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="tray",
                display_name="托盘",
                entity_type="container",
                appearance_signature="唯一木托盘",
                lifecycle=EntityLifecycle.ENTER,
            ),
            TrackedEntity(
                entity_id="cup",
                display_name="白杯",
                entity_type="prop",
                appearance_signature="唯一白色陶瓷杯",
                initial_anchor_id="table",
                initial_support_id="tray",
            ),
        ],
    )
    assert validate_visible_world(plan) == ("cup初始支撑tray尚未进入场景",)


def test_container_exit_cannot_leave_active_contents_behind() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="box",
                display_name="盒子",
                entity_type="container",
                appearance_signature="唯一蓝色方盒",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="ball",
                display_name="小球",
                entity_type="prop",
                appearance_signature="唯一黄色圆球",
                initial_anchor_id="table",
                initial_container_id="box",
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["box"],
                state_entity_id="box",
                before_anchor_id="table",
                support_before="table",
                lifecycle_event=EntityLifecycle.EXIT,
                change_reason="人物把盒子带离画面",
            )
        ],
    )
    assert "box离场后ball仍位于其中" in validate_visible_world(plan)


def test_transition_shot_order_cannot_move_backwards() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="ground",
                display_name="室内地面",
                anchor_type="ground",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="ground",
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=2,
                actor_id="person",
                no_state_change=True,
            ),
            ActionTransition(
                action_order=2,
                shot_order=1,
                actor_id="person",
                no_state_change=True,
            ),
        ],
    )
    assert any(
        "shotOrder不能随动作推进而倒退" in issue
        for issue in validate_visible_world(plan)
    )


def test_inactive_entity_and_dangling_support_are_rejected() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="tray",
                display_name="木托盘",
                entity_type="container",
                appearance_signature="唯一方形木托盘",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="cup",
                display_name="白杯",
                entity_type="prop",
                appearance_signature="唯一白色陶瓷杯",
                initial_anchor_id="table",
                initial_support_id="tray",
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["tray"],
                state_entity_id="tray",
                before_anchor_id="table",
                support_before="table",
                lifecycle_event=EntityLifecycle.EXIT,
                change_reason="人物把木托盘搬出画面",
            ),
            ActionTransition(
                action_order=2,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["tray"],
                state_entity_id="tray",
                lifecycle_event=EntityLifecycle.PERSIST,
            ),
        ],
    )
    issues = validate_visible_world(plan)
    assert "tray离场后cup仍由其支撑" in issues
    assert "目标实体tray已离场或尚未进入场景" in issues


def test_missing_before_state_is_not_backfilled() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            ),
            SceneAnchor(
                anchor_id="shelf",
                display_name="木架",
                anchor_type="shelf",
            ),
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="box",
                display_name="蓝盒",
                entity_type="container",
                appearance_signature="唯一蓝色方盒",
                initial_anchor_id="table",
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["box"],
                state_entity_id="box",
                after_anchor_id="shelf",
                support_after="shelf",
            )
        ],
    )
    issues = validate_visible_world(plan)
    assert any("动作前位置状态缺失" in item for item in issues)
    assert any("动作前支撑状态缺失" in item for item in issues)


def test_visible_world_allows_explicit_consumption() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            )
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="table",
            ),
            TrackedEntity(
                entity_id="snack",
                display_name="一块点心",
                entity_type="food",
                appearance_signature="唯一一块圆形黄色点心",
                initial_anchor_id="table",
                lifecycle=EntityLifecycle.CONSUME,
            )
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["snack"],
                before_anchor_id="table",
                support_before="table",
                lifecycle_event=EntityLifecycle.CONSUME,
                change_reason="人物吃掉这块点心",
            )
        ],
    )
    assert validate_visible_world(plan) == ()


def test_visible_world_allows_three_prop_transfers_and_sit_then_stand() -> None:
    plan = VisibleWorldPlan(
        scene_anchors=[
            SceneAnchor(
                anchor_id="ground",
                display_name="室内地面",
                anchor_type="ground",
            ),
            SceneAnchor(
                anchor_id="seat",
                display_name="木椅",
                anchor_type="seat",
            ),
            SceneAnchor(
                anchor_id="table",
                display_name="木桌",
                anchor_type="table",
            ),
        ],
        tracked_entities=[
            TrackedEntity(
                entity_id="person",
                display_name="中性儿童",
                entity_type="person",
                appearance_signature="同一短发中性儿童",
                initial_anchor_id="ground",
            ),
            TrackedEntity(
                entity_id="cup",
                display_name="白色杯子",
                entity_type="prop",
                appearance_signature="唯一白色圆口陶瓷杯",
                initial_anchor_id="table",
            ),
        ],
        action_transitions=[
            ActionTransition(
                action_order=1,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person"],
                before_anchor_id="ground",
                after_anchor_id="seat",
                support_before="ground",
                support_after="seat",
                topology_change=TopologyChange.SIT,
                seat_anchor_id="seat",
            ),
            ActionTransition(
                action_order=2,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["cup"],
                before_anchor_id="table",
                support_before="table",
                support_after="person",
            ),
            ActionTransition(
                action_order=3,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["cup"],
                before_anchor_id="table",
                after_anchor_id="table",
                support_before="person",
                support_after="table",
            ),
            ActionTransition(
                action_order=4,
                shot_order=1,
                actor_id="person",
                target_entity_ids=["person"],
                before_anchor_id="seat",
                after_anchor_id="ground",
                support_before="seat",
                support_after="ground",
                topology_change=TopologyChange.STAND,
                seat_anchor_id="seat",
            ),
        ],
    )
    report = assess_visible_world(plan)
    assert report.contradictions == ()
    assert report.render_risk_level in {
        RenderRiskLevel.MEDIUM,
        RenderRiskLevel.HIGH,
    }


def test_gendered_identity_rewrite_is_hard_failure(daily_plan) -> None:
    episode = daily_plan.episodes[0].model_copy(
        update={"main_event": "固定女孩扎起马尾后与灰白猫观察窗外风景"}
    )
    plan = daily_plan.model_copy(
        update={"episodes": [episode, *daily_plan.episodes[1:]]}
    )
    failures = hard_failures(validate_plan_gate(plan))
    assert {item.code for item in failures} == {"gendered_identity_rewrite"}


def test_gendered_identity_in_shot_or_entity_is_rejected(daily_plan) -> None:
    base = daily_plan.episodes[0]
    shot = base.shots[0].model_copy(
        update={"direction": "镜头跟随固定女孩和灰白猫靠近窗边"}
    )
    assert base.visible_world is not None
    entities = [
        (
            item.model_copy(update={"display_name": "短发少女"})
            if item.entity_id == "person"
            else item
        )
        for item in base.visible_world.tracked_entities
    ]
    episode = base.model_copy(
        update={
            "shots": [shot, *base.shots[1:]],
            "visible_world": base.visible_world.model_copy(
                update={"tracked_entities": entities}
            ),
        }
    )
    plan = daily_plan.model_copy(
        update={"episodes": [episode, *daily_plan.episodes[1:]]}
    )
    failures = hard_failures(validate_plan_gate(plan))
    assert {item.code for item in failures} == {"gendered_identity_rewrite"}


def test_protected_non_person_phrases_do_not_trigger_identity_gate(daily_plan) -> None:
    episode = daily_plan.episodes[0].model_copy(
        update={"main_event": "中性儿童和灰白猫在少年宫外观察一棵马尾松"}
    )
    plan = daily_plan.model_copy(
        update={"episodes": [episode, *daily_plan.episodes[1:]]}
    )
    failures = hard_failures(validate_plan_gate(plan))
    assert "gendered_identity_rewrite" not in {item.code for item in failures}


def test_fixed_cast_count_mismatch_is_hard_failure(daily_plan) -> None:
    episode = daily_plan.episodes[0].model_copy(
        update={"ending": "两人一猫共同发现了窗边的光点"}
    )
    plan = daily_plan.model_copy(
        update={"episodes": [episode, *daily_plan.episodes[1:]]}
    )
    failures = hard_failures(validate_plan_gate(plan))
    assert {item.code for item in failures} == {"fixed_cast_count_mismatch"}


def test_provider_normalizes_only_unambiguous_support_descriptions() -> None:
    payload = {
        "shots": [{"order": 1, "action_orders": [1]}],
        "visible_world": {
            "scene_anchors": [
                {
                    "anchor_id": "floor",
                    "display_name": "窗边地面",
                    "anchor_type": "ground",
                },
                {
                    "anchor_id": "table",
                    "display_name": "矮木桌桌面",
                    "anchor_type": "table",
                },
            ],
            "tracked_entities": [],
            "action_transitions": [
                {
                    "action_order": 1,
                    "support_before": "双脚立于窗边地面",
                    "support_after": "双脚仍立于地面",
                }
            ],
        },
    }
    normalized = normalize_episode_draft_payload(payload)
    transition = normalized["visible_world"]["action_transitions"][0]
    assert transition["support_before"] == "floor"
    assert transition["support_after"] == "floor"


def test_event_seed_selection_is_deterministic_and_optional(tmp_path: Path) -> None:
    root = tmp_path / "events"
    root.mkdir()
    (root / "seeds.yaml").write_text(
        """
events:
  - seed_id: one
    direction: 观察一段移动光影
    slots: [morning]
    weather_tags: []
    context_tags: []
  - seed_id: two
    direction: 循声找到环境来源
    slots: [noon]
    weather_tags: []
    context_tags: []
  - seed_id: rain-only
    direction: 在雨声里观察屋檐水滴
    slots: [evening]
    weather_tags: [雨]
    context_tags: []
""",
        encoding="utf-8",
    )
    catalog = EventSeedCatalog(root)
    kwargs = {
        "series_profile_hash": "a" * 64,
        "content_date": date(2026, 8, 1),
        "planning_revision": 1,
        "planning_context": "普通生活",
    }
    assert catalog.select(**kwargs) == catalog.select(**kwargs)
    assert len(catalog.select(**kwargs)) == 2
    assert {seed.seed_id for seed in catalog.select(**kwargs)} == {"one", "two"}
    rainy = catalog.select(**{**kwargs, "planning_context": "傍晚有雨"})
    assert "rain-only" in {seed.seed_id for seed in rainy}
    assert EventSeedCatalog(tmp_path / "missing").select(**kwargs) == ()


def test_reference_selection_uses_exact_semantic_keys(
    daily_plan,
    tmp_path: Path,
) -> None:
    episode = StoredEpisode(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        plan=daily_plan.episodes[0],
        status=EpisodeStatus.PLANNED,
        selected_video_asset_id=None,
    )

    def asset(role: str, semantic_key: str) -> StoredAsset:
        return StoredAsset(
            id=uuid.uuid4(),
            run_id=None,
            episode_id=None,
            step_id=None,
            role=role,
            media_type="image",
            scope="canon",
            status="approved",
            path=tmp_path / f"{semantic_key.replace(':', '-')}.png",
            sha256=uuid.uuid4().hex * 2,
            metadata={"width": 720, "height": 1280},
            semantic_key=semantic_key,
        )

    older_person = asset("person", "person:front")
    latest_person = asset("person", "person:front")
    expected = (
        latest_person,
        asset("cat", "cat:front"),
        asset("style", "style:line_texture"),
        asset("style", "style:outdoor"),
    )
    wrong = asset("element", "element:unrelated-teapot")

    class Repository:
        def list_assets(self, **_):
            # Repository承诺按created_at/id升序，选择器以最后一项作为最新批准版本。
            return (older_person, *expected, wrong)

    service = VisualPreparationService(
        repository=Repository(),
        media_gateway=object(),
        visual_review_gateway=object(),
        asset_store=object(),
        media_probe=object(),
        provider_name="test",
        keyframe_review_mode="semantic_auto",
        series_profile=DEFAULT_SERIES_VISUAL_PROFILE,
        style_profile=DEFAULT_STYLE_PROFILE,
    )
    selection = service.select_references(episode)
    assert selection.semantic_keys == (
        "person:front",
        "cat:front",
        "style:line_texture",
        "style:outdoor",
    )
    assert wrong not in selection.assets
    assert selection.assets[0].id == latest_person.id
