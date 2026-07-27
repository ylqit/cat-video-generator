from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import RuntimeSettings
from ..models import (
    DailySlot,
    EpisodeVariant,
    GenerationJob,
    MediaAsset,
    ReferenceAsset,
)
from ..render_plan import VisualReferences, compile_keyframe_prompt
from ..state import transition_slot
from ..visual_policy import VisualInputMode
from .errors import OrchestrationError
from .jobs import ArkJobExecutor, file_sha256


@dataclass(frozen=True, slots=True)
class PreparedVisualReferences:
    paths: tuple[Path, ...]
    plan: VisualReferences


def keyframe_context_fingerprint(
    episode: dict[str, Any],
    *,
    frame_role: str,
) -> str:
    if frame_role not in {"first", "last"}:
        raise OrchestrationError("frame_role must be first or last.")
    beat = episode["beats"][0] if frame_role == "first" else episode["beats"][-1]
    context = episode["contextReads"]
    payload = {
        "frameRole": frame_role,
        "locationId": context["locationId"],
        "weather": context["weather"],
        "wardrobeVersionIds": context["wardrobeVersionIds"],
        "propIds": context["propIds"],
        "visualAction": beat["visualAction"],
        "requiredSubjectVersionIds": episode["requiredSubjectVersionIds"],
        "styleVersionId": episode["styleVersionId"],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class VisualAssetService:
    """Own approved Canon selection and the conditional keyframe workflow."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: RuntimeSettings,
        jobs: ArkJobExecutor,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._jobs = jobs

    def load_approved_references(
        self,
        session: Session,
        variant: EpisodeVariant,
    ) -> PreparedVisualReferences:
        episode = variant.episode_spec_json
        person_version, cat_version = episode["requiredSubjectVersionIds"]
        requested = {
            ("person", person_version),
            ("cat", cat_version),
            ("style", episode["styleVersionId"]),
        }
        assets = session.execute(
            select(ReferenceAsset).where(ReferenceAsset.status == "approved")
        ).scalars().all()
        by_role_version = {
            (asset.role, asset.version_id): asset for asset in assets
        }
        missing = sorted(
            f"{role}:{version}"
            for role, version in requested
            if (role, version) not in by_role_version
        )
        if missing:
            raise OrchestrationError(
                "Approved Canon references are missing: " + ", ".join(missing)
            )
        person = by_role_version[("person", person_version)]
        cat = by_role_version[("cat", cat_version)]
        style = by_role_version[("style", episode["styleVersionId"])]
        return PreparedVisualReferences(
            paths=(
                Path(person.storage_path),
                Path(cat.storage_path),
                Path(style.storage_path),
            ),
            plan=VisualReferences(
                person_asset_id=person.asset_id,
                cat_asset_id=cat.asset_id,
                style_asset_ids=(style.asset_id,),
            ),
        )

    def ensure_keyframes(
        self,
        *,
        slot_id: uuid.UUID,
        variant_id: uuid.UUID,
        episode: dict[str, Any],
        render_revision: int,
        reference_paths: tuple[Path, ...],
        mode: VisualInputMode,
        resume_only: bool,
    ) -> bool:
        required_roles = (
            ("first", "last")
            if mode is VisualInputMode.GENERATED_FIRST_LAST_FRAMES
            else ("first",)
        )
        for clip_index, frame_role in enumerate(required_roles):
            with self._session_factory.begin() as session:
                approved = self.approved_keyframes(
                    session,
                    variant_id,
                    episode,
                    render_revision,
                )
                if any(
                    asset.asset_kind == f"scene_keyframe_{frame_role}"
                    for asset in approved
                ):
                    continue
                existing = session.execute(
                    select(MediaAsset)
                    .join(
                        GenerationJob,
                        MediaAsset.generation_job_id == GenerationJob.id,
                    )
                    .where(
                        MediaAsset.episode_variant_id == variant_id,
                        MediaAsset.asset_kind
                        == f"scene_keyframe_{frame_role}",
                        MediaAsset.qc_status == "passed",
                        GenerationJob.render_revision == render_revision,
                    )
                ).scalars().all()
                if existing:
                    continue
                slot = session.get_one(DailySlot, slot_id)
                if slot.status == "planned":
                    transition_slot(slot, "keyframe_generating")

            prompt = compile_keyframe_prompt(episode, frame_role=frame_role)
            request_snapshot = {
                **self._settings.request_profile_snapshot(),
                "model": self._settings.ark_image_model,
                "frameRole": frame_role,
                "size": "2K",
                "outputFormat": "png",
                "watermark": False,
                "inputAssetSha256": [
                    file_sha256(path) for path in reference_paths
                ],
                "prompt": prompt,
            }
            self._jobs.ensure_keyframe_asset(
                slot_id=slot_id,
                variant_id=variant_id,
                episode_id=episode["episodeId"],
                render_revision=render_revision,
                frame_role=frame_role,
                clip_index=clip_index,
                prompt=prompt,
                reference_paths=reference_paths,
                request_snapshot=request_snapshot,
                resume_only=resume_only,
            )

        with self._session_factory() as session:
            assets = self.approved_keyframes(
                session,
                variant_id,
                episode,
                render_revision,
            )
            return len(self.ordered_keyframes(assets, mode)) == len(
                required_roles
            )

    def approved_keyframes(
        self,
        session: Session,
        variant_id: uuid.UUID,
        episode: dict[str, Any],
        render_revision: int,
    ) -> list[MediaAsset]:
        current_variant = session.get_one(EpisodeVariant, variant_id)
        rows = session.execute(
            select(
                MediaAsset,
                EpisodeVariant.episode_spec_json,
                GenerationJob.render_revision,
            )
            .join(
                GenerationJob,
                MediaAsset.generation_job_id == GenerationJob.id,
            )
            .join(
                EpisodeVariant,
                MediaAsset.episode_variant_id == EpisodeVariant.id,
            )
            .where(
                EpisodeVariant.episode_id == current_variant.episode_id,
                MediaAsset.asset_kind.in_(
                    ("scene_keyframe_first", "scene_keyframe_last")
                ),
                MediaAsset.qc_status == "passed",
                MediaAsset.review_status == "approved",
            )
            .order_by(
                MediaAsset.created_at.desc(),
            )
        ).all()
        selected: dict[str, MediaAsset] = {}
        for asset, candidate_episode, candidate_render_revision in rows:
            frame_role = (
                "first"
                if asset.asset_kind == "scene_keyframe_first"
                else "last"
            )
            if (
                asset.episode_variant_id == variant_id
                and candidate_render_revision > render_revision
            ):
                continue
            if keyframe_context_fingerprint(
                candidate_episode,
                frame_role=frame_role,
            ) != keyframe_context_fingerprint(
                episode,
                frame_role=frame_role,
            ):
                continue
            selected.setdefault(asset.asset_kind, asset)
        return list(selected.values())

    @staticmethod
    def ordered_keyframes(
        assets: list[MediaAsset],
        mode: VisualInputMode,
    ) -> list[MediaAsset]:
        if mode is VisualInputMode.DIRECT_REFERENCES:
            return []
        by_kind: dict[str, MediaAsset] = {}
        for asset in assets:
            by_kind.setdefault(asset.asset_kind, asset)
        kinds = ["scene_keyframe_first"]
        if mode is VisualInputMode.GENERATED_FIRST_LAST_FRAMES:
            kinds.append("scene_keyframe_last")
        return [by_kind[kind] for kind in kinds if kind in by_kind]
