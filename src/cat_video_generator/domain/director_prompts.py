"""总导演与时段导演Prompt。

导演上下文允许完整，但不会直接发送给Seedream或Seedance。
"""

from __future__ import annotations

import json
from datetime import date

from .continuity import replay_terminal_state
from .contracts import (
    DayBrief,
    EpisodePlan,
    RecentContentSummary,
    SlotBrief,
)
from .visual_profiles import (
    DEFAULT_SERIES_VISUAL_PROFILE,
    SeriesVisualProfile,
)


def compile_day_director_prompt(
    *,
    target_date: date,
    planning_context: str,
    recent_summaries: tuple[RecentContentSummary, ...] = (),
    event_seeds: tuple[str, ...] = (),
    persona_anchor: str | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> str:
    """生成一次只输出DayBrief的总导演Prompt。"""

    persona = persona_anchor or series_profile.person_identity
    recent = "；".join(item.summary_text for item in recent_summaries[-6:]) or "无"
    seeds = "；".join(event_seeds[:3]) or "无适用事件种子，可原创"
    return "\n".join(
        (
            "你是持续角色生活流短视频的总导演。只输出一个符合给定JSON Schema的"
            "DayBrief，不输出Episode、分镜、解释、Markdown或候选数组。",
            f"内容日期：{target_date.isoformat()}。",
            f"当天输入：{planning_context}。",
            f"近期内容：{recent}。",
            f"适用事件种子：{seeds}。事件种子只是方向，可以选择、改写或不用。",
            "固定主角是同一个人物和同一只灰白猫。你只决定全天主题、共同背景、"
            "真正需要跨时段保持的共享元素，以及早中晚各自的叙事目的、场景方向、"
            "事件方向和外观意图。不要替时段导演写具体动作时间线。",
            f"人物设定为{persona}；全天外观方向与剧情不得暗示或强调性别。",
            "scene_direction要点明该时段场景中的关键家具与道具（桌椅、餐具、"
            "容器等），时段导演会把它们落实为场景清单。",
            "早中晚是同一天的三个生活观察窗口，可以独立，也可以局部承接；不要"
            "强制准备—完成—归家。服装、鞋帽、配饰和随身物品必须服务场景：需要"
            "拖鞋就换拖鞋，不需要背包就不出现；相邻时段有变化时在方向中给出天气、"
            "地点或事件原因。",
            "slots严格按morning、noon、evening排序。所有自然语言字段使用中文；"
            "只有slot和element_id等契约标识保留英文。",
        )
    )


def compile_episode_director_prompt(
    *,
    day_brief: DayBrief,
    slot_brief: SlotBrief,
    previous_state_summaries: tuple[str, ...],
    retry_reason: str | None = None,
    rejected_candidate: dict[str, object] | None = None,
    validation_errors: tuple[str, ...] = (),
    persona_anchor: str | None = None,
    series_profile: SeriesVisualProfile = DEFAULT_SERIES_VISUAL_PROFILE,
) -> str:
    """为一个时段生成一次只输出EpisodePlan的导演Prompt。"""

    persona = persona_anchor or (
        f"{series_profile.person_identity}；{series_profile.person_hair}；"
        f"{series_profile.person_body}"
    )
    previous = "；".join(previous_state_summaries) or "这是当天第一条，无前序状态"
    retry = f"本次是局部重规划，必须修正：{retry_reason}。" if retry_reason else "本次不是重试。"
    repair = ""
    if rejected_candidate is not None:
        candidate_json = json.dumps(
            rejected_candidate,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        repair = (
            "上一次完整候选未通过自洽校验。必须重新输出完整EpisodeDirectorDraft，"
            "不能返回局部补丁，也不能删除DayBrief要求的主事件。"
            f"校验错误：{'；'.join(validation_errors)}。"
            f"被拒绝候选：{candidate_json}"
        )
    brief_json = json.dumps(
        day_brief.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    slot_json = json.dumps(
        slot_brief.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n".join(
        (
            f"你是{slot_brief.slot.value}时段导演。只输出一个符合给定JSON Schema的"
            "EpisodeDirectorDraft，不输出全天方案、解释、Markdown或候选数组。",
            f"不可改写的DayBrief：{brief_json}",
            f"本时段边界：{slot_json}",
            f"已发生状态摘要：{previous}。",
            retry,
            repair,
            "把边界细化为一条8至15秒视频：一个主事件、2至4个连续动作阶段和一个"
            "可见收束。另提供1至3个轻量镜头，每个镜头关联动作阶段且只能使用一种"
            "主要运镜。动作结束后不能用站立互看或静止画面填时长。",
            "必须填写稳定英文slug形式的event_key和location_key，用于与最近已批准"
            "内容做精确冷却比较；它们描述事件类别和地点类别，不写中文句子。",
            "每个动作阶段必须填写真实actor_id。固定出现一个人物和一只灰白猫，"
            "没有配角时所有自然语言字段只能写“一人一猫”，不得误写“两人一猫”。"
            "最多一个剧情确需的配角。人物主要面貌、发型和体型保持同一人；猫保持"
            "同一只灰白猫的脸型、体型和主要灰白斑纹。眼睛服从参考素材整体画风。",
            "共享元素只能引用DayBrief已定义且包含本slot的element_id。每个引用必须"
            "填写element_uses，明确用途、初态和终态。关键数量、承重、包含、边界或"
            "交接关系必须填写critical_relations的initial_state和final_state。",
            "必须直接输出VisibleWorldPlan：scene_anchors先声明桌面、地面、座椅等"
            "承重位置；tracked_entities登记会被操作、承重、包含或跨镜头持续出现的"
            "实例；每个动作阶段必须有一条action_transition，写明actor、目标、动作"
            "前后锚点、支撑、接触、包含和生命周期；切镜时继承仍可见实体与服装层。",
            "每次动作的before状态必须等于上一动作结束状态。state_entity_id必须且"
            "只能指定一名实际发生位置、支撑、接触、包含或生命周期变化的实体；多个"
            "实体分别改变状态时拆成相邻动作。所有关系ID必须已声明。",
            "lifecycle_event=persist表示没有生命周期变化；enter、exit、consume或"
            "transform必须填写change_reason，transform还要填写结果外观签名。坐下"
            "和站起引用同一合法seat锚点并把动作主体列入目标。",
            "纯观察或微反应若不改变建账状态，必须显式no_state_change=true，且不得"
            "同时填写位置、支撑、接触、包含、生命周期或拓扑变化字段。",
            "scene_inventory只作为旧格式兼容字段，新输出保持空数组。不要因为交互"
            "次数较多而删减合理剧情；动作可以丰富，但必须属于同一主事件、能在时长"
            "内看清，并且VisibleWorldPlan的状态链完整自洽。",
            "ShotPlan必须显式填写dominant_view。默认generation_strategy=single_pass；"
            "不要主动选择multi_clip，除非边界明确要求两个天然硬切镜头。",
            f"人物设定为{persona}；服装、发型与剧情不得暗示或强调性别。",
            "appearance落实本时段外观；morning使用continue且不声明相对变化，"
            "noon/evening若增减服饰或随身物品则使用changed并给出情景原因。slot、"
            "cast、required_reference_roles和video_input_mode由本地系统确定。"
            "所有自然语言字段使用中文。",
        )
    )


def summarize_episode_state(episode: EpisodePlan) -> str:
    """提取下一个时段真正需要读取的已发生状态。"""

    element_states = "、".join(
        f"{item.element_id}={item.final_state}" for item in episode.element_uses
    )
    relation_states = "、".join(
        f"{item.subject}:{item.final_state}" for item in episode.critical_relations
    )
    parts = [
        f"{episode.slot.value}结尾={episode.ending}",
        f"外观={episode.appearance.description}",
    ]
    if element_states:
        parts.append(f"共享元素={element_states}")
    if relation_states:
        parts.append(f"关键关系={relation_states}")
    if episode.visible_world is not None:
        terminal = replay_terminal_state(episode.visible_world)
        parts.append(
            "可见世界="
            + "、".join(
                (
                    f"{entity_id}@{state.anchor_id or '离场'}:"
                    f"{'active' if state.active else 'inactive'}"
                )
                for entity_id, state in sorted(terminal.items())
            )
        )
    return "，".join(parts)
