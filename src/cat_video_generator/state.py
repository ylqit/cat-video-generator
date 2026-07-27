from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class Stateful(Protocol):
    status: str


class StateTransitionError(RuntimeError):
    """Raised when a persisted object is moved across an invalid state edge."""


_PACK_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "candidate": frozenset({"approved"}),
    "approved": frozenset({"frozen"}),
    "frozen": frozenset({"rendering"}),
    "rendering": frozenset({"ready", "failed"}),
    "ready": frozenset({"delivered"}),
    "delivered": frozenset(),
    "failed": frozenset(),
}

_SLOT_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "planned": frozenset({"keyframe_generating", "video_generating", "failed"}),
    "keyframe_generating": frozenset({"keyframe_review", "failed"}),
    "keyframe_review": frozenset({"planned", "video_generating", "failed"}),
    "video_generating": frozenset({"media_qc", "failed"}),
    "media_qc": frozenset({"content_review", "failed"}),
    "content_review": frozenset({"planned", "ready", "failed"}),
    "ready": frozenset(),
    "failed": frozenset(),
}

_VARIANT_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "planned": frozenset({"active", "rejected", "failed"}),
    "active": frozenset({"ready", "rejected", "failed"}),
    "ready": frozenset(),
    "rejected": frozenset(),
    "failed": frozenset(),
}

_JOB_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "submitting": frozenset(
        {"submission_unknown", "queued", "running", "succeeded", "failed"}
    ),
    "submission_unknown": frozenset(
        {"queued", "running", "succeeded", "failed", "expired", "cancelled"}
    ),
    "queued": frozenset({"running", "succeeded", "failed", "expired", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "expired", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "expired": frozenset(),
    "cancelled": frozenset(),
}


def transition_pack(record: Stateful, target: str) -> None:
    _transition(record, target, _PACK_TRANSITIONS, "LifePack")


def transition_slot(record: Stateful, target: str) -> None:
    _transition(record, target, _SLOT_TRANSITIONS, "Slot")


def transition_variant(record: Stateful, target: str) -> None:
    _transition(record, target, _VARIANT_TRANSITIONS, "Variant")


def transition_job(record: Stateful, target: str) -> None:
    _transition(record, target, _JOB_TRANSITIONS, "GenerationJob")


def _transition(
    record: Stateful,
    target: str,
    transitions: Mapping[str, frozenset[str]],
    label: str,
) -> None:
    current = record.status
    allowed = transitions.get(current)
    if allowed is None or target not in allowed:
        raise StateTransitionError(
            f"{label} cannot transition from {current!r} to {target!r}."
        )
    record.status = target
