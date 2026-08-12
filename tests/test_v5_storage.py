from __future__ import annotations

from pathlib import Path

import pytest

from cat_video_generator.infrastructure.db import models
from cat_video_generator.infrastructure.db.repositories import (
    _resolve_storage_key,
    _storage_key_for,
)
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD


def test_v5_database_models_expose_creation_flow_columns() -> None:
    assert ALEMBIC_HEAD == "0016_v5_creation_flow"
    assert hasattr(models.ProductionRun, "default_reference_bindings_json")
    assert hasattr(models.Scene, "story_mode")
    assert hasattr(models.Scene, "target_shot_count")
    assert hasattr(models.Scene, "look_plan_json")
    assert hasattr(models.Scene, "selected_look_asset_id")
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
