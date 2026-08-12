from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from cat_video_generator.application.canon import CanonRepairService
from cat_video_generator.application.ports import LandedAsset, StoredAsset
from cat_video_generator.application.shot_queue import _merge_generation_references
from cat_video_generator.domain.contracts import ReferenceBinding


def _binding(asset_id: uuid.UUID, *, role: str = "identity") -> ReferenceBinding:
    return ReferenceBinding(
        assetId=asset_id,
        usage="generation_reference",
        role=role,
        applyTo="both",
    )


def test_reference_precedence_is_custom_then_scene_then_project_with_deduplication() -> None:
    custom_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    project_id = uuid.uuid4()

    merged = _merge_generation_references(
        custom=(_binding(custom_id), _binding(project_id)),
        scene_look_asset_id=scene_id,
        project_defaults=(_binding(project_id),),
        inherit_project_references=True,
        use_scene_look=True,
    )

    assert [item.asset_id for item in merged] == [custom_id, project_id, scene_id]
    assert merged[-1].role.value == "scene"


def test_reference_inheritance_and_scene_look_can_be_disabled() -> None:
    merged = _merge_generation_references(
        custom=(_binding(uuid.uuid4()),),
        scene_look_asset_id=uuid.uuid4(),
        project_defaults=(_binding(uuid.uuid4()),),
        inherit_project_references=False,
        use_scene_look=False,
    )

    assert len(merged) == 1


def test_canon_manifest_declares_all_runtime_assets() -> None:
    manifest_path = Path("风格定稿/Canon-v1/manifest.json")
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))["assets"]

    assert len(entries) == 11
    assert {item["semanticKey"] for item in entries} == {
        "person:headshot",
        "person:fullbody",
        "person:front",
        "person:side",
        "person:back",
        "cat:front",
        "cat:side",
        "cat:back",
        "style:line_texture",
        "style:outdoor",
        "style:indoor",
    }
    assert sum(bool(item["recommendedDefault"]) for item in entries) == 5


def test_canon_repair_preserves_identity_and_requires_matching_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"canon")
    digest = "7a5356659c5b128bf2a1cfa958aca12b66573ecc89e0089dd5a74018541868aa"
    asset = StoredAsset(
        id=uuid.uuid4(),
        project_id=None,
        scene_id=None,
        shot_card_id=None,
        step_id=None,
        role="canon_reference",
        media_type="image",
        scope="canon",
        status="approved",
        path=tmp_path / "missing.png",
        sha256=digest,
        metadata={},
        semantic_key="cat:front",
    )

    class Repository:
        repaired: tuple[uuid.UUID, LandedAsset] | None = None

        def list_assets(self) -> tuple[StoredAsset, ...]:
            return (asset,)

        def repair_canon_asset(
            self, asset_id: uuid.UUID, landed: LandedAsset
        ) -> StoredAsset:
            self.repaired = (asset_id, landed)
            return asset

    class Store:
        def import_local(self, path: Path) -> LandedAsset:
            return LandedAsset(path=path, sha256=digest, byte_size=path.stat().st_size)

    repository = Repository()
    service = CanonRepairService(repository=repository, asset_store=Store())

    repaired = service.repair_entries(
        source_root=tmp_path,
        entries=(
            {
                "semanticKey": "cat:front",
                "file": "source.png",
                "sha256": digest,
                "recommendedDefault": True,
            },
        ),
    )

    assert repaired == (asset,)
    assert repository.repaired is not None
    assert repository.repaired[0] == asset.id

    with pytest.raises(ValueError, match="hash"):
        service.repair_entries(
            source_root=tmp_path,
            entries=(
                {
                    "semanticKey": "cat:front",
                    "file": "source.png",
                    "sha256": "0" * 64,
                    "recommendedDefault": True,
                },
            ),
        )
