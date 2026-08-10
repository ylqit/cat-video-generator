"""供应商结构化输出的无损归一化回归。"""

from cat_video_generator.domain.normalization import normalize_day_brief_payload


def _brief(slot: str, role: str) -> dict[str, str]:
    return {"slot": slot, "narrative_role": role}


def test_day_brief_removes_only_identical_duplicate_slot() -> None:
    noon = _brief("noon", "推进变化")
    payload = {
        "slot_briefs": [
            _brief("morning", "建立开场"),
            noon,
            dict(noon),
            _brief("evening", "形成回报"),
        ]
    }

    normalized, warnings = normalize_day_brief_payload(payload)

    assert [item["slot"] for item in normalized["slot_briefs"]] == [
        "morning",
        "noon",
        "evening",
    ]
    assert warnings == ("slot_briefs去除完全相同的重复时段：noon",)
    assert len(payload["slot_briefs"]) == 4


def test_day_brief_keeps_conflicting_duplicate_for_contract_rejection() -> None:
    payload = {
        "slot_briefs": [
            _brief("morning", "建立开场"),
            _brief("noon", "推进变化A"),
            _brief("noon", "推进变化B"),
            _brief("evening", "形成回报"),
        ]
    }

    normalized, warnings = normalize_day_brief_payload(payload)

    assert normalized == payload
    assert warnings == ()
