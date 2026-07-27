from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VisualInputMode(StrEnum):
    DIRECT_REFERENCES = "direct_references"
    GENERATED_FIRST_FRAME = "generated_first_frame"
    GENERATED_FIRST_LAST_FRAMES = "generated_first_last_frames"


class VisualInputReasonCode(StrEnum):
    LOW_RISK_DIRECT = "low_risk_direct"
    EXACT_OPENING = "exact_opening"
    EXACT_ENDING = "exact_ending"
    COMPLEX_SUBJECT_INTERACTION = "complex_subject_interaction"
    CRITICAL_PROP_STATE = "critical_prop_state"
    IDENTITY_DRIFT_RETRY = "identity_drift_retry"


@dataclass(frozen=True, slots=True)
class VisualControl:
    requires_exact_opening: bool
    requires_exact_ending: bool
    complex_subject_interaction: bool
    critical_prop_state: bool


@dataclass(frozen=True, slots=True)
class VisualInputDecision:
    mode: VisualInputMode
    reason_codes: tuple[VisualInputReasonCode, ...]


class VisualInputValidationError(ValueError):
    """Raised when visual input assets cannot be assigned unambiguous roles."""


def validate_visual_input_asset_ids(
    *,
    person_asset_id: str,
    cat_asset_id: str,
    style_reference_asset_ids: tuple[str, ...],
    scene_keyframe_asset_ids: tuple[str, ...],
) -> None:
    """Enforce uniqueness across asset-role arrays, which JSON Schema cannot express."""
    asset_ids = (
        person_asset_id,
        cat_asset_id,
        *style_reference_asset_ids,
        *scene_keyframe_asset_ids,
    )
    if len(asset_ids) != len(set(asset_ids)):
        raise VisualInputValidationError(
            "Each visual input asset must have exactly one role."
        )


def select_visual_input(
    control: VisualControl,
    *,
    retry_after_identity_or_composition_drift: bool = False,
) -> VisualInputDecision:
    """Select the cheapest visual input mode that satisfies explicit controls."""
    reasons: list[VisualInputReasonCode] = []
    if control.requires_exact_ending:
        reasons.append(VisualInputReasonCode.EXACT_ENDING)
    if control.requires_exact_opening:
        reasons.append(VisualInputReasonCode.EXACT_OPENING)
    if control.complex_subject_interaction:
        reasons.append(VisualInputReasonCode.COMPLEX_SUBJECT_INTERACTION)
    if control.critical_prop_state:
        reasons.append(VisualInputReasonCode.CRITICAL_PROP_STATE)
    if retry_after_identity_or_composition_drift:
        reasons.append(VisualInputReasonCode.IDENTITY_DRIFT_RETRY)

    if control.requires_exact_ending:
        mode = VisualInputMode.GENERATED_FIRST_LAST_FRAMES
    elif retry_after_identity_or_composition_drift or any(
        (
            control.requires_exact_opening,
            control.complex_subject_interaction,
            control.critical_prop_state,
        )
    ):
        mode = VisualInputMode.GENERATED_FIRST_FRAME
    else:
        mode = VisualInputMode.DIRECT_REFERENCES
        reasons.append(VisualInputReasonCode.LOW_RISK_DIRECT)

    return VisualInputDecision(mode=mode, reason_codes=tuple(reasons))
