from __future__ import annotations

import hashlib
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = PROJECT_ROOT / "content" / "schemas"

_SLOT_ORDER = {"morning": 1, "noon": 2, "evening": 3}


class ContentValidationError(ValueError):
    """Raised when a content pack violates its schema or cross-object rules."""


class ContentConflictError(RuntimeError):
    """Raised when a business revision already exists with different content."""


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentValidationError(f"Cannot read JSON content from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContentValidationError("The content document must be a JSON object.")
    return value


def canonical_content_hash(value: dict[str, Any]) -> str:
    normalized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def validate_daily_life_pack(value: dict[str, Any]) -> None:
    _validate_schema(value, "daily-life-pack.schema.json")
    _validate_pack_relationships(value)


def validate_render_plan(value: dict[str, Any]) -> None:
    _validate_schema(value, "render-plan.schema.json")
    identity = value["identityReferences"]
    all_asset_ids = [
        identity["personAssetId"],
        identity["catAssetId"],
        *value["styleReferenceAssetIds"],
        *value["sceneKeyframeAssetIds"],
    ]
    if len(all_asset_ids) != len(set(all_asset_ids)):
        raise ContentValidationError(
            "RenderPlan visual assets must be unique across identity, style, "
            "and scene-keyframe roles."
        )
    if (
        value["visualInputMode"] == "direct_references"
        and len(all_asset_ids) > 9
    ):
        raise ContentValidationError(
            "A direct-reference RenderPlan can send at most 9 images."
        )


def validate_delivery_manifest(value: dict[str, Any]) -> None:
    _validate_schema(value, "delivery-manifest.schema.json")


def _validate_schema(value: dict[str, Any], schema_name: str) -> None:
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in SCHEMA_ROOT.glob("*.json")
    }
    registry = Registry().with_resources(
        (
            schema["$id"],
            Resource.from_contents(schema),
        )
        for schema in schemas.values()
    )
    validator = Draft202012Validator(
        schemas[schema_name],
        registry=registry,
        format_checker=FormatChecker(),
    )
    try:
        validator.validate(value)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ContentValidationError(f"{location}: {exc.message}") from exc


def _validate_pack_relationships(value: dict[str, Any]) -> None:
    life_pack_id = value["lifePackId"]
    day_context = value["dayContext"]
    cluster = day_context["locationCluster"]
    allowed_locations = set(cluster["allowedLocationIds"])
    available_props = set(day_context["availableProps"])
    primary_episodes = value["slots"]
    fallback_episodes = {
        episode["episodeId"]: episode for episode in value["fallbackEpisodes"]
    }
    primary_by_id = {
        episode["episodeId"]: episode for episode in primary_episodes.values()
    }
    referenced_fallbacks: set[str] = set()

    for slot, episode in primary_episodes.items():
        _validate_episode_context(
            episode,
            expected_life_pack_id=life_pack_id,
            expected_slot=slot,
            cluster_id=cluster["id"],
            allowed_locations=allowed_locations,
            weather=day_context["weather"],
            wardrobe=day_context["wardrobeVersionIds"],
            available_props=available_props,
        )
        dependencies = episode["dependsOnEpisodeIds"]
        if episode["clipKind"] == "continuation":
            if episode["continuityMode"] != "follows_previous" or not dependencies:
                raise ContentValidationError(
                    f"{episode['episodeId']}: continuation requires "
                    "follows_previous and at least one dependency."
                )
            fallback_id = episode["fallbackEpisodeId"]
            if not fallback_id or fallback_id not in fallback_episodes:
                raise ContentValidationError(
                    f"{episode['episodeId']}: continuation requires a declared fallback."
                )
            fallback = fallback_episodes[fallback_id]
            referenced_fallbacks.add(fallback_id)
            _validate_episode_context(
                fallback,
                expected_life_pack_id=life_pack_id,
                expected_slot=slot,
                cluster_id=cluster["id"],
                allowed_locations=allowed_locations,
                weather=day_context["weather"],
                wardrobe=day_context["wardrobeVersionIds"],
                available_props=available_props,
            )
            if fallback["dependsOnEpisodeIds"]:
                raise ContentValidationError(
                    f"{fallback_id}: fallback cannot depend on another Episode."
                )
        elif dependencies or episode["continuityMode"] != "shared_context":
            raise ContentValidationError(
                f"{episode['episodeId']}: non-continuation content must use "
                "shared_context without dependencies."
            )

        for dependency_id in dependencies:
            dependency = primary_by_id.get(dependency_id)
            if dependency is None:
                raise ContentValidationError(
                    f"{episode['episodeId']}: unknown dependency {dependency_id!r}."
                )
            if _SLOT_ORDER[dependency["slot"]] >= _SLOT_ORDER[slot]:
                raise ContentValidationError(
                    f"{episode['episodeId']}: dependencies must use an earlier slot."
                )

    unreferenced = set(fallback_episodes) - referenced_fallbacks
    if unreferenced:
        raise ContentValidationError(
            "Unreferenced fallbackEpisodes: " + ", ".join(sorted(unreferenced))
        )


def _validate_episode_context(
    episode: dict[str, Any],
    *,
    expected_life_pack_id: str,
    expected_slot: str,
    cluster_id: str,
    allowed_locations: set[str],
    weather: str,
    wardrobe: dict[str, str],
    available_props: set[str],
) -> None:
    episode_id = episode["episodeId"]
    context = episode["contextReads"]
    if episode["lifePackId"] != expected_life_pack_id:
        raise ContentValidationError(
            f"{episode_id}: lifePackId does not match the containing pack."
        )
    if episode["slot"] != expected_slot:
        raise ContentValidationError(
            f"{episode_id}: slot does not match its containing slot."
        )
    if context["locationClusterId"] != cluster_id:
        raise ContentValidationError(
            f"{episode_id}: locationClusterId does not match dayContext."
        )
    if context["locationId"] not in allowed_locations:
        raise ContentValidationError(
            f"{episode_id}: locationId is outside dayContext.locationCluster."
        )
    if context["weather"] != weather:
        raise ContentValidationError(
            f"{episode_id}: weather does not match dayContext."
        )
    if context["wardrobeVersionIds"] != wardrobe:
        raise ContentValidationError(
            f"{episode_id}: wardrobe does not match dayContext."
        )
    unexpected_props = set(context["propIds"]) - available_props
    if unexpected_props:
        raise ContentValidationError(
            f"{episode_id}: unavailable props: {', '.join(sorted(unexpected_props))}."
        )
    beats = episode["beats"]
    if [beat["order"] for beat in beats] != list(range(1, len(beats) + 1)):
        raise ContentValidationError(
            f"{episode_id}: beat orders must be contiguous from 1."
        )
    if beats[0]["startMs"] != 0:
        raise ContentValidationError(
            f"{episode_id}: the first beat must start at 0ms."
        )
    if any(beat["startMs"] >= beat["endMs"] for beat in beats):
        raise ContentValidationError(
            f"{episode_id}: every beat must have positive duration."
        )
    for previous, current in pairwise(beats):
        if previous["endMs"] != current["startMs"]:
            raise ContentValidationError(
                f"{episode_id}: beats must form a contiguous semantic timeline."
            )
    if beats[-1]["endMs"] != episode["durationMs"]:
        raise ContentValidationError(
            f"{episode_id}: the final beat must end at durationMs."
        )
