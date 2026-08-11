"""极简混合导演契约的共用业务对象。"""

from __future__ import annotations

from datetime import date

import pytest

from cat_video_generator.domain.contracts import (
    ActivityFocus,
    DailyProductionPlan,
    EpisodePlan,
    EpisodeScript,
    Handoff,
    HardConstraint,
    OutlineEpisode,
    OutlineEpisodes,
    ProjectOutlineV3,
    ShotDirection,
    Slot,
    StoryInputMode,
    StoryProjectInput,
)


def _shot_directions(duration: int) -> list[ShotDirection]:
    shots = [
        ShotDirection(
            order=1,
            direction=(
                "中景平视固定镜头，人物位于画面左侧并始终用右手握住线轴，灰白猫在右前方以四足姿态追逐地面风筝影子；"
                "风筝线从人物右手一侧直接向天空延伸，猫咪不接触线，镜头在双方减速站稳时结束。"
            ),
        ),
        ShotDirection(
            order=2,
            direction=(
                "中近景缓慢推近，猫咪先抬头提示风筝方向变化，人物沿猫咪视线轻收线轴恢复飞行；"
                "猫咪回到人物脚边，线轴仍在人物右手，风筝稳定升空后形成清楚稳定切点。"
            ),
        ),
    ]
    if duration > 30:
        shots.append(
            ShotDirection(
                order=3,
                direction=(
                    "全景缓慢拉远，猫咪四足贴在人物身侧观看天空，人物保持握住同一线轴；"
                    "同一只风筝在高处稳定飞行，人猫关系线汇合并以持续风声和飘带声收束。"
                ),
            )
        )
    return shots


def episode_for(
    slot: Slot,
    *,
    duration: int = 12,
    focus: ActivityFocus = ActivityFocus.CAT_LEAD,
) -> EpisodePlan:
    indoor = slot is Slot.MORNING
    script = EpisodeScript(
        title=f"{slot.value}风筝生活片段",
        event_key=f"{slot.value}_kite_event",
        location_key="home_worktable" if indoor else "spring_hillside",
        visual_context="indoor" if indoor else "outdoor",
        activity_focus=focus,
        duration_seconds=duration,
        appearance=(
            "同一个中性短发儿童，穿米白短袖、藏青短裤和防滑便鞋；人物完整服装在本集镜头间保持一致。"
        ),
        story_text=(
            "春日生活空间里，灰白猫先追逐同一只风筝的彩色飘带或地面影子，把观众注意力带向风筝变化；"
            "人物始终握住线轴并进行制作、放线或收线。猫咪观察到新的动静后回到人物脚边，人物顺着它的提示调整风筝，"
            "最后同一只风筝恢复稳定，人猫在同一画面里形成可见回报。"
        ),
        relationship_arc=(
            "灰白猫以自然四足追逐和观察推动新信息，人物承担线轴等工具操作；猫咪回到人物身侧提示变化，人物回应后让同一只风筝稳定飞行。"
        ),
        shots=_shot_directions(duration),
        hard_constraints=[
            HardConstraint(
                shot_orders=[shot.order for shot in _shot_directions(duration)],
                text=(
                    "风筝线一端始终连接人物右手中的同一个线轴，另一端连接同一只风筝；线从人物一侧直接向天空延伸，"
                    "不得经过、缠绕或连接猫咪身体。"
                ),
            )
        ],
        sound_design="春风、草叶、脚步和风筝纸张轻响同步，结尾保留猫咪轻叫与飘带声，无对白、旁白或歌词。",
        ending="猫咪回到人物身侧，同一只风筝保持完整并稳定飞行，人物回应猫咪后形成温暖回报。",
    )
    return EpisodePlan(slot=slot, script=script)


@pytest.fixture
def daily_plan() -> DailyProductionPlan:
    durations = {Slot.MORNING: 12, Slot.NOON: 22, Slot.EVENING: 12}
    project_input = StoryProjectInput(
        theme="春日放风筝的一天",
        input_mode=StoryInputMode.THEME_EXPAND,
    )
    return DailyProductionPlan(
        content_date=date(2026, 8, 10),
        project_input=project_input,
        outline=ProjectOutlineV3(
            content_date=date(2026, 8, 10),
            theme=project_input.theme,
            day_arc=(
                "上午在家完成风筝并建立猫咪对飘带的兴趣，中午到山坡让猫咪追影并提示线况变化，"
                "傍晚人物收线、猫咪围着线轴活动，最后一起带着同一只风筝离开。"
            ),
            episodes=OutlineEpisodes(
                morning=OutlineEpisode(
                    scene="春日家中的小木桌",
                    direction="制作风筝，猫咪追彩带并引出轻微笑点。",
                ),
                noon=OutlineEpisode(
                    scene="开满野花的春日山坡",
                    direction="放飞风筝，猫咪先发现异常并与人物共同恢复飞行。",
                ),
                evening=OutlineEpisode(
                    scene="夕阳下的山坡小径",
                    direction="收好风筝并分享点心，人猫一起迎着夕阳离开。",
                ),
            ),
            handoffs=[
                Handoff(
                    name="同一只彩色风筝",
                    from_slot=Slot.MORNING,
                    to_slot=Slot.NOON,
                    continuity="上午制作完成的风筝被带到春日山坡放飞。",
                ),
                Handoff(
                    name="同一只彩色风筝",
                    from_slot=Slot.NOON,
                    to_slot=Slot.EVENING,
                    continuity="中午稳定飞行的风筝在傍晚由人物收线带走。",
                ),
            ],
        ),
        episodes=[episode_for(slot, duration=durations[slot]) for slot in Slot],
    )
