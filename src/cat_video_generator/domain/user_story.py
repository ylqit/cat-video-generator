"""用户长剧情的本地拆分与预览。

该模块不调用导演，也不猜测缺失剧情。它只识别清晰的“主题＋剧本1/2/3”标题，
把结果交给Web确认后再建立生活故事项目，避免解析失败意外触发收费调用。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from .contract_base import StrictModel
from .contracts import EpisodeSources, SceneRoute, Slot, StoryInputMode, StoryProjectInput

_THEME_MARKERS = frozenset(
    {"主题", "主题一", "主题二", "主题三", "主题1", "主题2", "主题3"}
)
_SCRIPT_MARKERS = {
    "剧本1": Slot.MORNING,
    "剧本一": Slot.MORNING,
    "剧本2": Slot.NOON,
    "剧本二": Slot.NOON,
    "剧本3": Slot.EVENING,
    "剧本三": Slot.EVENING,
}


class UserStory(StrictModel):
    """用户确认后的完整三集原文，按命名时段保存而非依赖元组顺序。"""

    theme: Annotated[str, Field(min_length=2, max_length=160)]
    episode_sources: EpisodeSources

    @model_validator(mode="after")
    def validate_complete_story(self) -> UserStory:
        if self.episode_sources.populated_slots != tuple(Slot):
            raise ValueError("完整用户剧情必须同时包含morning、noon、evening三段原文")
        return self

    def episode_for(self, slot: Slot) -> str:
        value = self.episode_sources.for_slot(slot)
        if value is None:
            raise ValueError(f"完整用户剧情缺少{slot.value}原文")
        return value

    def to_project_input(
        self,
        *,
        scene_route: SceneRoute = SceneRoute.ADAPTIVE,
    ) -> StoryProjectInput:
        return StoryProjectInput(
            theme=self.theme,
            input_mode=StoryInputMode.EPISODE_SCRIPTS,
            scene_route=scene_route,
            episode_sources=self.episode_sources,
        )


class EpisodeSourcesPreview(StrictModel):
    """只用于解析预览的宽松文本，不承担生产契约校验。"""

    morning: str | None = None
    noon: str | None = None
    evening: str | None = None

    def for_slot(self, slot: Slot) -> str | None:
        return {
            Slot.MORNING: self.morning,
            Slot.NOON: self.noon,
            Slot.EVENING: self.evening,
        }[slot]


class UserStoryParsePreview(StrictModel):
    """自由文本拆分预览；Web必须先展示它，不能把解析结果直接送入收费链路。"""

    theme: str
    episode_sources: EpisodeSourcesPreview
    detected_slots: tuple[Slot, ...]
    complete: bool
    issues: tuple[str, ...] = ()

    def confirmed_story(self) -> UserStory:
        if not self.complete or self.issues:
            raise ValueError("三集剧情拆分尚未完整确认")
        return UserStory(
            theme=self.theme,
            episode_sources=EpisodeSources(
                morning=self.episode_sources.morning,
                noon=self.episode_sources.noon,
                evening=self.episode_sources.evening,
            ),
        )


def preview_user_story(planning_context: str) -> UserStoryParsePreview:
    """按中文标题逐行拆分三集文本，不使用模糊正则猜测剧情边界。"""

    lines = planning_context.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    theme: str | None = None
    current_slot: Slot | None = None
    sections: dict[Slot, list[str]] = {}
    issues: list[str] = []
    preamble: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        heading, title = _split_heading(line)
        compact_heading = heading.replace(" ", "") if heading is not None else None
        if compact_heading in _THEME_MARKERS:
            if theme is not None:
                issues.append("检测到多个主题标题，请保留一个主题")
            elif title:
                theme = title
            else:
                issues.append("主题标题后缺少主题内容")
            current_slot = None
            continue
        slot = _SCRIPT_MARKERS.get(compact_heading or "")
        if slot is not None:
            if slot in sections:
                issues.append(f"检测到重复的{slot.value}剧本标题")
            else:
                sections[slot] = []
            current_slot = slot
            if title:
                sections[slot].append(title)
            continue
        if current_slot is None:
            preamble.append(line)
        else:
            sections[current_slot].append(line)

    source_values = {
        slot: "\n".join(sections.get(slot, ())).strip() or None for slot in Slot
    }
    for slot in Slot:
        value = source_values[slot]
        if slot in sections and value is None:
            issues.append(f"{slot.value}剧本标题后缺少正文")
        elif value is not None and len(value) < 4:
            issues.append(f"{slot.value}剧本文本过短，至少需要4个字符")
    detected = tuple(slot for slot in Slot if source_values[slot] is not None)
    missing = tuple(slot.value for slot in Slot if source_values[slot] is None)
    if missing:
        issues.append("缺少剧本：" + "、".join(missing))
    resolved_theme = theme or (preamble[0] if preamble else "用户提供的生活故事")
    if len(resolved_theme) < 2:
        issues.append("主题文本过短，至少需要2个字符")
    elif len(resolved_theme) > 160:
        issues.append("主题文本过长，最多160个字符")
    sources = EpisodeSourcesPreview(
        morning=source_values[Slot.MORNING],
        noon=source_values[Slot.NOON],
        evening=source_values[Slot.EVENING],
    )
    return UserStoryParsePreview(
        theme=resolved_theme,
        episode_sources=sources,
        detected_slots=detected,
        complete=not missing,
        issues=tuple(dict.fromkeys(issues)),
    )


def _split_heading(line: str) -> tuple[str | None, str]:
    """返回标题标记和同一行的小标题；没有明确分隔符时不猜测。"""

    candidate = line.lstrip("#").strip()
    colon_positions = [
        index for mark in ("：", ":") if (index := candidate.find(mark)) >= 0
    ]
    if not colon_positions:
        return None, ""
    position = min(colon_positions)
    return candidate[:position].strip(), candidate[position + 1 :].strip()
