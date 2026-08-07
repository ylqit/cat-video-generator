"""导演候选的前置归一化：只改写意图明确但表述违规的矛盾。

连续性规则本身（persist不能带finalFormKey、transform必须换形态）保证账本
无歧义，必须保留；但"声明了不同finalFormKey却写persist"这类矛盾，模型的
意图是明确的，程序可以无歧义地修正，不值得花一次付费LLM修复——修复还会
顾此失彼。归一化只处理这类可机械恢复的矛盾，并把每次改写记为warning供
审计；person/cat非persist、姿态词form_key、位置图矛盾等不可恢复矛盾仍
交给契约硬校验与修复循环。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_DEFAULT_SOUND_DESIGN = "环境声与动作声自然同步，无对白、旁白或歌词。"


def normalize_episode_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """返回（归一化后的payload, 归一化警告）；不修改入参对象。"""

    warnings: list[str] = []
    normalized = deepcopy(payload)

    sound = normalized.get("sound_design")
    if isinstance(sound, list):
        joined = "、".join(
            text for item in sound if (text := str(item).strip())
        )
        normalized["sound_design"] = joined or _DEFAULT_SOUND_DESIGN
        warnings.append("sound_design由列表合并为一句话字符串")

    continuity = normalized.get("continuity")
    entities = continuity.get("entities") if isinstance(continuity, dict) else None
    if isinstance(entities, list):
        seen_keys: set[str] = set()
        seen_names: set[str] = set()
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("id", "?"))
            # 模型常把entity_key当类别词复用（钓竿/凳子/饵盒都写fishing_gear）。
            # 键在账本内必须唯一：重复时改用该实体自己的id（id本身有唯一约束）。
            entity_key = entity.get("entity_key")
            if isinstance(entity_key, str) and entity_key in seen_keys:
                replacement = str(entity.get("id") or f"{entity_key}_2")
                if replacement in seen_keys:
                    replacement = f"{entity_key}_{len(seen_keys) + 1}"
                entity["entity_key"] = replacement
                warnings.append(
                    f"实体{entity_id}的entityKey {entity_key} 与前面实体重复，"
                    f"已修正为 {replacement}"
                )
                entity_key = replacement
            if isinstance(entity_key, str):
                seen_keys.add(entity_key)
            name = entity.get("name")
            if isinstance(name, str) and name in seen_names:
                replacement = f"{name}2"
                while replacement in seen_names:
                    replacement = f"{replacement}x"
                entity["name"] = replacement
                warnings.append(f"实体{entity_id}的名称{name}重复，已修正为{replacement}")
                name = replacement
            if isinstance(name, str):
                seen_names.add(name)
            lifecycle = entity.get("lifecycle", "persist")
            form_key = entity.get("form_key")
            final_form_key = entity.get("final_form_key")
            if lifecycle == "persist" and final_form_key is not None:
                if final_form_key == form_key:
                    entity.pop("final_form_key")
                    warnings.append(
                        f"实体{entity_id}的finalFormKey与formKey相同，已删除冗余声明"
                    )
                else:
                    entity["lifecycle"] = "transform"
                    warnings.append(
                        f"实体{entity_id}声明了不同的finalFormKey，"
                        "lifecycle已从persist修正为transform"
                    )
            elif lifecycle == "transform" and (
                final_form_key is None or final_form_key == form_key
            ):
                entity["lifecycle"] = "persist"
                entity.pop("final_form_key", None)
                warnings.append(
                    f"实体{entity_id}未提供不同的finalFormKey，"
                    "lifecycle已从transform降级为persist"
                )
            elif (
                lifecycle in {"enter", "exit", "consume"}
                and final_form_key is not None
            ):
                # enter/exit/consume无法表达形态变化（transform要求起终都在场），
                # 保守丢弃finalFormKey并保留生命周期，形态细节由change_reason承载。
                entity.pop("final_form_key")
                warnings.append(
                    f"实体{entity_id}是{lifecycle}实体却声明了finalFormKey，"
                    "已删除该声明"
                )
    return normalized, tuple(warnings)
