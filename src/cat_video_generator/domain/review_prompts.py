"""关键帧与视频抽帧审核 Prompt。

审核上下文可以读取完整可见世界状态，但不会被回灌到 Seedance 执行 Prompt，
从而把“如何生成”和“如何验收”保持为两套独立、可审计的语义。
"""

from __future__ import annotations

from .contracts import EpisodePlan
from .prompts import (
    describe_actor,
    describe_boundaries,
    describe_relations,
    describe_world_frame,
)
from .visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    DEFAULT_STYLE_PROFILE,
    SeriesVisualProfile,
    StyleProfile,
)


def compile_keyframe_review_prompt(
    episode: EpisodePlan,
    *,
    target: str,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
    style_profile: StyleProfile = DEFAULT_STYLE_PROFILE,
) -> str:
    """生成关键帧身份、二维画风、世界状态和空间拓扑审核指令。"""

    if target not in {"first_frame", "last_frame"}:
        raise ValueError("关键帧审核target必须是first_frame或last_frame")
    final = target == "last_frame"
    return "\n".join(
        (
            "你是关键帧语义审核器。依据下列业务状态审核输入图片，不创作图片。"
            "只返回给定JSON Schema，不输出解释或Markdown。",
            f"身份：{series_profile.person_identity}；{series_profile.person_hair}；"
            f"{series_profile.person_body}；{series_profile.cat_identity}。",
            f"画风：必须符合{style_profile.prompt_positive()}；"
            f"不得出现{style_profile.prompt_negative()}。",
            f"本集场景：{episode.scene}。本集外观：{episode.appearance.description}。",
            f"目标帧状态：{describe_world_frame(episode, final=final)}。",
            f"关键关系：{describe_relations(episode, final=final)}。",
            "sceneTopologyOk只判断已声明的家具、承重面、座位、容器和边界是否存在且"
            "关系合理。未声明座位、无支撑悬空、物体穿透、容器类型改变或持久实体"
            "缺失都必须判为false。evidence写出图片中实际观察，不得只复述要求。",
        )
    )


def compile_video_diagnostic_prompt(episode: EpisodePlan) -> str:
    """生成抽帧时序诊断指令，不把审核阈值塞回 Seedance 执行 Prompt。"""

    actions = "；".join(
        f"{item.order}.{describe_actor(item.actor_id)}{item.action}"
        for item in episode.actions
    )
    assert episode.visible_world is not None
    entities = "；".join(
        f"{item.entity_id}={item.display_name}，外观{item.appearance_signature}"
        for item in episode.visible_world.tracked_entities
    )
    return "\n".join(
        (
            "你是生活流短视频的抽帧时序诊断器。输入图片按视频时间顺序排列。"
            "只返回给定JSON Schema，不创作画面，不把单帧遮挡误判为角色消失。",
            f"本集角色与实体：{entities}。",
            f"计划动作顺序：{actions}。结尾应为：{episode.ending}。",
            f"镜头边界继承：{describe_boundaries(episode)}。",
            "identityOk检查人物和灰白猫在可见帧中是否仍可辨识为同一主体；"
            "styleOk检查是否保持二维彩铅/蜡笔绘本而非3D渲染；"
            "worldContinuityOk检查持久物体、服装、支撑、包含和座位是否无原因消失、"
            "复制、穿透或变形；narrativeOrderOk检查动作与结果的先后是否可读。"
            "evidence必须包含帧序号和实际观察，不能只复述要求。",
        )
    )
