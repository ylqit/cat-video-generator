from __future__ import annotations

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
                approved = session.execute(
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
                        MediaAsset.review_status == "approved",
                        GenerationJob.render_revision <= render_revision,
                    )
                    .order_by(
                        GenerationJob.render_revision.desc(),
                        MediaAsset.created_at.desc(),
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if approved is not None:
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
                session, variant_id, render_revision
            )
            return len(self.ordered_keyframes(assets, mode)) == len(
                required_roles
            )

    def approved_keyframes(
        self,
        session: Session,
        variant_id: uuid.UUID,
        render_revision: int,
    ) -> list[MediaAsset]:
        return session.execute(
            select(MediaAsset)
            .join(
                GenerationJob,
                MediaAsset.generation_job_id == GenerationJob.id,
            )
            .where(
                MediaAsset.episode_variant_id == variant_id,
                MediaAsset.asset_kind.in_(
                    ("scene_keyframe_first", "scene_keyframe_last")
                ),
                MediaAsset.qc_status == "passed",
                MediaAsset.review_status == "approved",
                GenerationJob.render_revision <= render_revision,
            )
            .order_by(
                GenerationJob.render_revision.desc(),
                MediaAsset.created_at.desc(),
            )
        ).scalars().all()

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
