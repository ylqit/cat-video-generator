"""供应商Episode输出只做不改变剧情意图的机械归一化。"""

from cat_video_generator.domain.normalization import normalize_episode_payload


def test_episode_sound_list_is_joined_without_mutating_provider_payload() -> None:
    payload = {
        "title": "池塘边的浮标信号",
        "sound_design": ["微风和水声", "浮标入水轻响", "猫咪轻叫"],
    }

    normalized, warnings = normalize_episode_payload(payload)

    assert normalized["sound_design"] == "微风和水声；浮标入水轻响；猫咪轻叫"
    assert warnings == ("sound_design由列表合并为字符串",)
    assert isinstance(payload["sound_design"], list)


def test_episode_scalar_sound_is_left_unchanged() -> None:
    payload = {"sound_design": "微风、水声和鱼竿轻响同步"}

    normalized, warnings = normalize_episode_payload(payload)

    assert normalized == payload
    assert warnings == ()
