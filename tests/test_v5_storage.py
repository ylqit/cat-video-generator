from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from cat_video_generator.domain.contracts import VisualProfileDraft
from cat_video_generator.infrastructure.db import models
from cat_video_generator.infrastructure.db.repositories import (
    _asset,
    _profile_hash,
    _resolve_storage_key,
    _storage_key_for,
)
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD


def test_v5_database_models_expose_creation_flow_columns() -> None:
    assert ALEMBIC_HEAD == "0017_v5_visual_profile"
    assert hasattr(models.ProductionRun, "default_reference_bindings_json")
    assert hasattr(models.ProductionRun, "current_visual_profile_revision_id")
    assert hasattr(models.VisualProfileRevision, "profile_hash")
    assert hasattr(models.VisualProfileRevision, "reference_snapshot_json")
    assert hasattr(models.Scene, "story_mode")
    assert hasattr(models.Scene, "target_shot_count")
    assert hasattr(models.Scene, "look_plan_json")
    assert hasattr(models.Scene, "selected_look_asset_id")
    assert hasattr(models.Scene, "look_draft_json")
    assert hasattr(models.Scene, "look_draft_revision")
    assert hasattr(models.ShotCard, "inherit_project_references")
    assert hasattr(models.ShotCard, "use_scene_look")
    assert hasattr(models.Asset, "storage_key")
    assert not hasattr(models.Asset, "local_path")


def test_storage_key_round_trip_stays_below_asset_root(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    path = asset_root / "imported" / "sha256" / "ab" / "asset.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"asset")

    key = _storage_key_for(path, asset_root)

    assert key == "imported/sha256/ab/asset.png"
    assert _resolve_storage_key(key, asset_root) == path.resolve()


@pytest.mark.parametrize(
    "key",
    ["../secret.png", "/tmp/secret.png", "C:/secret.png", "legacy:C:/old.png"],
)
def test_storage_key_rejects_absolute_legacy_and_traversal_paths(
    tmp_path: Path, key: str
) -> None:
    with pytest.raises(ValueError, match="storage key"):
        _resolve_storage_key(key, tmp_path / "assets")


def test_storage_key_rejects_landed_asset_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"asset")

    with pytest.raises(ValueError, match="asset root"):
        _storage_key_for(outside, tmp_path / "assets")


def test_legacy_storage_key_is_quarantined_when_loading_asset(tmp_path: Path) -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        production_run_id=None,
        scene_id=None,
        shot_card_id=None,
        producing_step_id=None,
        role="identity",
        media_type="image",
        scope="canon",
        status="approved",
        storage_key="legacy:C:/old-machine/canon.png",
        sha256="0" * 64,
        metadata_json={},
        semantic_key="person:headshot",
    )

    asset = _asset(row, tmp_path / "assets")

    assert asset.path is None
    assert asset.content_ready is False


def test_visual_profile_hash_includes_reference_content_hash() -> None:
    draft = VisualProfileDraft()
    first = _profile_hash(
        draft,
        reference_snapshot=[{"assetId": str(uuid.uuid4()), "sha256": "1" * 64}],
    )
    second = _profile_hash(
        draft,
        reference_snapshot=[{"assetId": str(uuid.uuid4()), "sha256": "2" * 64}],
    )

    assert first != second


def test_visual_profile_hash_uses_the_migration_json_canonicalization() -> None:
    draft = VisualProfileDraft()
    snapshot: list[dict[str, str]] = []
    payload = {
        **draft.model_dump(mode="json", by_alias=True),
        "referenceSnapshot": snapshot,
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    assert _profile_hash(draft, reference_snapshot=snapshot) == expected
