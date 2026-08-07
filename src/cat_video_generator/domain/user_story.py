"""用户完整剧情的识别与结构化（hybrid扩写模式的输入层）。

用户在创作台直接给出"主题 + 剧本1/2/3"的完整剧情时，导演链路应切换为
改编而非创作：日导演只做结构化摘要，时段导演把用户文本逐拍映射为
EpisodeScript 契约。本模块只负责从自由文本中识别出这种结构。
"""

from __future__ import annotations

import re

from .contract_base import StrictModel

_THEME_RE = re.compile(r"^\s*主题[一二三123]?\s*[：:]\s*(?P<theme>.+?)\s*$", re.MULTILINE)
# 标记行允许同行带小标题（"剧本1：出发前准备"），小标题并入该段原文。
_SCRIPT_RE = re.compile(
    r"^\s*剧本\s*(?P<num>[123一二三])\s*[：:.、]?\s*(?P<title>[^\n]*)$",
    re.MULTILINE,
)
_NUM_ORDER = {"1": 0, "2": 1, "3": 2, "一": 0, "二": 1, "三": 2}


class UserStory(StrictModel):
    """识别出的用户完整剧情：一个主题加按早中晚排序的三段原文。"""

    theme: str
    episodes: tuple[str, str, str]


def parse_user_story(planning_context: str) -> UserStory | None:
    """识别"主题+剧本1/2/3"完整输入；不足三个剧本段落时返回None。"""

    markers = list(_SCRIPT_RE.finditer(planning_context))
    if len(markers) < 3:
        return None
    sections: dict[int, str] = {}
    for index, marker in enumerate(markers[:3]):
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(planning_context)
        body = planning_context[start:end].strip()
        title = marker.group("title").strip()
        text = f"{title}\n{body}".strip() if title else body
        if text:
            sections[_NUM_ORDER[marker.group("num")]] = text
    if sorted(sections) != [0, 1, 2]:
        return None
    theme_match = _THEME_RE.search(planning_context)
    theme = (
        theme_match.group("theme").strip()
        if theme_match
        else planning_context.strip().splitlines()[0].strip()
    )
    return UserStory(
        theme=theme or "用户提供的完整剧情",
        episodes=(sections[0], sections[1], sections[2]),
    )
