from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from cat_video_generator.visual_policy import (
    VisualControl,
    VisualInputMode,
    VisualInputReasonCode,
    VisualInputValidationError,
    select_visual_input,
    validate_visual_input_asset_ids,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = PROJECT_ROOT / "content" / "schemas"
EXAMPLE_ROOT = PROJECT_ROOT / "content" / "examples"
PROVIDER_CONFIG = PROJECT_ROOT / "config" / "providers.example.yaml"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMAS = {
    path.name: load_json(path)
    for path in SCHEMA_ROOT.glob("*.json")
}
SCHEMA_REGISTRY = Registry().with_resources(
    (
        schema["$id"],
        Resource.from_contents(schema),
    )
    for schema in SCHEMAS.values()
)


def validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        SCHEMAS[schema_name],
        registry=SCHEMA_REGISTRY,
    )


def low_risk_control() -> VisualControl:
    return VisualControl(
        requires_exact_opening=False,
        requires_exact_ending=False,
        complex_subject_interaction=False,
        critical_prop_state=False,
    )


def test_all_daily_life_pack_examples_match_episode_contract() -> None:
    contract = validator("daily-life-pack.schema.json")
    examples = sorted(EXAMPLE_ROOT.glob("daily-life-pack.*.example.json"))

    assert examples
    for path in examples:
        contract.validate(load_json(path))


def test_all_render_plan_examples_match_visual_input_contract() -> None:
    contract = validator("render-plan.schema.json")
    examples = sorted(EXAMPLE_ROOT.glob("render-plan.*.example.json"))

    assert len(examples) == 3
    for path in examples:
        contract.validate(load_json(path))


def test_low_risk_content_uses_direct_references() -> None:
    decision = select_visual_input(low_risk_control())

    assert decision.mode is VisualInputMode.DIRECT_REFERENCES
    assert decision.reason_codes == (VisualInputReasonCode.LOW_RISK_DIRECT,)


@pytest.mark.parametrize(
    "field_name,reason_code",
    [
        ("requires_exact_opening", VisualInputReasonCode.EXACT_OPENING),
        (
            "complex_subject_interaction",
            VisualInputReasonCode.COMPLEX_SUBJECT_INTERACTION,
        ),
        ("critical_prop_state", VisualInputReasonCode.CRITICAL_PROP_STATE),
    ],
)
def test_non_ending_visual_risk_uses_generated_first_frame(
    field_name: str,
    reason_code: VisualInputReasonCode,
) -> None:
    values = {
        "requires_exact_opening": False,
        "requires_exact_ending": False,
        "complex_subject_interaction": False,
        "critical_prop_state": False,
    }
    values[field_name] = True

    decision = select_visual_input(VisualControl(**values))

    assert decision.mode is VisualInputMode.GENERATED_FIRST_FRAME
    assert reason_code in decision.reason_codes


def test_exact_ending_has_priority_over_other_visual_risks() -> None:
    decision = select_visual_input(
        VisualControl(
            requires_exact_opening=True,
            requires_exact_ending=True,
            complex_subject_interaction=True,
            critical_prop_state=True,
        )
    )

    assert decision.mode is VisualInputMode.GENERATED_FIRST_LAST_FRAMES
    assert VisualInputReasonCode.EXACT_ENDING in decision.reason_codes


def test_identity_drift_retry_upgrades_mode_without_changing_plan_revision() -> None:
    decision = select_visual_input(
        low_risk_control(),
        retry_after_identity_or_composition_drift=True,
    )
    retry_plan = load_json(
        EXAMPLE_ROOT / "render-plan.single-pass-audio.example.json"
    )
    retry_plan["renderRevision"] += 1
    original_plan_revision = retry_plan["planRevision"]
    retry_plan["visualInputMode"] = decision.mode.value
    retry_plan["sceneKeyframeAssetIds"] = [
        "asset-keyframe-identity-drift-retry-first"
    ]
    retry_plan["visualInputReasonCodes"] = [
        reason.value for reason in decision.reason_codes
    ]

    validator("render-plan.schema.json").validate(retry_plan)
    assert decision.mode is VisualInputMode.GENERATED_FIRST_FRAME
    assert retry_plan["planRevision"] == original_plan_revision


@pytest.mark.parametrize(
    ("mode", "scene_keyframes"),
    [
        ("direct_references", ["unexpected-first-frame"]),
        ("generated_first_frame", []),
        ("generated_first_frame", ["first", "extra"]),
        ("generated_first_last_frames", ["first"]),
        ("generated_first_last_frames", ["first", "last", "extra"]),
    ],
)
def test_visual_input_modes_reject_wrong_scene_keyframe_counts(
    mode: str,
    scene_keyframes: list[str],
) -> None:
    plan = deepcopy(
        load_json(EXAMPLE_ROOT / "render-plan.single-pass-audio.example.json")
    )
    plan["visualInputMode"] = mode
    plan["sceneKeyframeAssetIds"] = scene_keyframes

    with pytest.raises(ValidationError):
        validator("render-plan.schema.json").validate(plan)


def test_direct_references_allow_at_most_nine_provider_images() -> None:
    plan = load_json(
        EXAMPLE_ROOT / "render-plan.single-pass-audio.example.json"
    )
    plan["styleReferenceAssetIds"] = [
        f"asset-style-{index}"
        for index in range(7)
    ]
    validator("render-plan.schema.json").validate(plan)

    plan["styleReferenceAssetIds"].append("asset-style-8")
    with pytest.raises(ValidationError):
        validator("render-plan.schema.json").validate(plan)


def test_visual_input_assets_cannot_be_reused_across_roles() -> None:
    with pytest.raises(VisualInputValidationError):
        validate_visual_input_asset_ids(
            person_asset_id="asset-person-v1",
            cat_asset_id="asset-cat-v1",
            style_reference_asset_ids=("asset-person-v1",),
            scene_keyframe_asset_ids=(),
        )


def test_provider_config_uses_on_demand_execution_without_time_gate() -> None:
    config = yaml.safe_load(PROVIDER_CONFIG.read_text(encoding="utf-8"))

    assert config["content"]["timezone"] == "Asia/Shanghai"
    assert config["execution"] == {
        "triggerMode": "on_demand",
        "targetSelection": "explicit_date_or_earliest_approved",
        "enforceWallClockWindow": False,
        "scheduler": "external_optional",
        "paidGenerationRequiresCliAcknowledgement": True,
    }
    assert config["media"]["accessProfile"] == {
        "mode": "agent_plan",
        "agentPlanTier": "large",
        "providerProfile": "volcengine-agent-plan",
        "baseUrl": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "apiKeyEnv": "ARK_API_KEY",
        "standardAlternative": {
            "mode": "standard",
            "agentPlanTier": None,
            "providerProfile": "volcengine-ark-standard",
            "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
            "imageModel": "<standard-image-model-or-endpoint-id>",
            "videoModel": "<standard-video-model-or-endpoint-id>",
        },
    }
    assert config["media"]["image"]["model"] == "doubao-seedream-5.0-lite"
    assert config["media"]["video"]["model"] == "doubao-seedance-2.0-mini"


def test_env_example_defaults_to_secret_free_agent_plan_large() -> None:
    values = dotenv_values(ENV_EXAMPLE)

    assert values["ARK_ACCESS_MODE"] == "agent_plan"
    assert values["ARK_AGENT_PLAN_TIER"] == "large"
    assert (
        values["ARK_BASE_URL"]
        == "https://ark.cn-beijing.volces.com/api/plan/v3"
    )
    assert values["ARK_IMAGE_MODEL"] == "doubao-seedream-5.0-lite"
    assert values["ARK_VIDEO_MODEL"] == "doubao-seedance-2.0-mini"
    assert values["ARK_API_KEY"] == ""
    assert "API_KEY" not in values
    assert "BASE_URL" not in values
