from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

import pytest

from cat_video_generator.application.canon import CanonRepairService
from cat_video_generator.application.ports import (
    LandedAsset,
    StoredAsset,
    StoredProject,
    StoredScene,
    StoredShot,
)
from cat_video_generator.application.shot_queue import (
    ShotProductionService,
    _merge_generation_references,
)
from cat_video_generator.domain.contracts import ReferenceBinding, SceneDraft, ShotCardDraft
from cat_video_generator.domain.workflow import RunStatus, SceneStatus, ShotStatus


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


def test_resolved_video_references_keep_precedence_and_deduplicate_sha(
    tmp_path: Path,
) -> None:
    project_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    custom = _image_asset(tmp_path, "custom", "a" * 64, project_id, scene_id)
    scene_look = _image_asset(tmp_path, "look", "b" * 64, project_id, scene_id)
    duplicate_project = _image_asset(
        tmp_path,
        "project-duplicate",
        "a" * 64,
        project_id,
        scene_id,
    )
    project_style = _image_asset(tmp_path, "style", "c" * 64, project_id, scene_id)
    assets = {item.id: item for item in (custom, scene_look, duplicate_project, project_style)}
    shot = StoredShot(
        id=uuid.uuid4(),
        scene_id=scene_id,
        project_id=project_id,
        order=1,
        draft=ShotCardDraft(
            title="片段",
            direction="1. 中景固定，人和猫准备出门，稳定收尾。\n2. 近景跟随猫咪。",
            referenceBindings=[_binding(custom.id)],
        ),
        status=ShotStatus.READY,
    )
    project = StoredProject(
        id=project_id,
        title="项目",
        content_date=date(2026, 8, 13),
        status=RunStatus.ACTIVE,
        default_reference_bindings=(
            _binding(duplicate_project.id),
            _binding(project_style.id, role="style"),
        ),
    )
    scene = StoredScene(
        id=scene_id,
        project_id=project_id,
        order=1,
        draft=SceneDraft(title="场景", sourceText="人和猫准备出门。"),
        status=SceneStatus.READY,
        selected_look_asset_id=scene_look.id,
    )

    class Repository:
        def get_project(self, requested_id: uuid.UUID) -> StoredProject:
            assert requested_id == project_id
            return project

        def get_scene(self, requested_id: uuid.UUID) -> StoredScene:
            assert requested_id == scene_id
            return scene

        def get_asset(self, asset_id: uuid.UUID) -> StoredAsset:
            return assets[asset_id]

    service = ShotProductionService(
        repository=Repository(),  # type: ignore[arg-type]
        gateway=None,
        asset_store=object(),  # type: ignore[arg-type]
        media_probe=object(),  # type: ignore[arg-type]
        frame_extractor=None,
        provider_name="fake",
        resolution="720p",
    )

    anchor, references, descriptions = service._resolve_video_inputs(shot)

    assert anchor is None
    assert [item.id for item in references] == [custom.id, scene_look.id, project_style.id]
    assert "长期身份" in descriptions[1]


def _image_asset(
    tmp_path: Path,
    name: str,
    digest: str,
    project_id: uuid.UUID,
    scene_id: uuid.UUID,
) -> StoredAsset:
    path = tmp_path / f"{name}.png"
    path.write_bytes(name.encode("utf-8"))
    return StoredAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        scene_id=scene_id,
        shot_card_id=None,
        step_id=None,
        role="reference",
        media_type="image",
        scope="project",
        status="approved",
        path=path,
        sha256=digest,
        metadata={},
        semantic_key=name,
    )


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
        repaired: tuple[tuple[uuid.UUID, LandedAsset], ...] | None = None

        def list_assets(self) -> tuple[StoredAsset, ...]:
            return (asset,)

        def repair_canon_assets(
            self,
            repairs: tuple[tuple[uuid.UUID, LandedAsset], ...],
        ) -> tuple[StoredAsset, ...]:
            self.repaired = repairs
            return (asset,)

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
    assert repository.repaired[0][0] == asset.id

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


def test_canon_repair_does_not_mutate_database_before_all_hashes_validate(
    tmp_path: Path,
) -> None:
    good = tmp_path / "good.png"
    bad = tmp_path / "bad.png"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")
    good_hash = "770e607624d689265ca6c44884d0807d9b054d23c473c106c72be9de08b7376c"
    bad_hash = "2f05d4b689d270cafb02285f35f44866f1f025de1fd47d69cd246df852c7f784"
    assets = tuple(
        StoredAsset(
            id=uuid.uuid4(),
            project_id=None,
            scene_id=None,
            shot_card_id=None,
            step_id=None,
            role="canon_reference",
            media_type="image",
            scope="canon",
            status="approved",
            path=None,
            sha256=digest,
            metadata={},
            semantic_key=key,
        )
        for key, digest in (("cat:front", good_hash), ("style:outdoor", bad_hash))
    )

    class Repository:
        called = False

        def list_assets(self) -> tuple[StoredAsset, ...]:
            return assets

        def repair_canon_assets(
            self,
            repairs: tuple[tuple[uuid.UUID, LandedAsset], ...],
        ) -> tuple[StoredAsset, ...]:
            self.called = True
            return assets

    class Store:
        def import_local(self, path: Path) -> LandedAsset:
            digest = good_hash if path == good else bad_hash
            return LandedAsset(path=path, sha256=digest, byte_size=path.stat().st_size)

    repository = Repository()
    service = CanonRepairService(repository=repository, asset_store=Store())

    with pytest.raises(ValueError, match="style:outdoor"):
        service.repair_entries(
            source_root=tmp_path,
            entries=(
                {
                    "semanticKey": "cat:front",
                    "file": "good.png",
                    "sha256": good_hash,
                    "recommendedDefault": True,
                },
                {
                    "semanticKey": "style:outdoor",
                    "file": "bad.png",
                    "sha256": "0" * 64,
                    "recommendedDefault": True,
                },
            ),
        )

    assert repository.called is False
