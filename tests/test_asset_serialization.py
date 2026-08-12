from __future__ import annotations

import uuid
from pathlib import Path

from cat_video_generator.application.ports import StoredAsset
from cat_video_generator.infrastructure.db.repositories import _json_asset
from cat_video_generator.interfaces.api import _asset_json


def test_asset_dtos_expose_stored_shot_card_id_as_shot_id() -> None:
    shot_card_id = uuid.uuid4()
    asset = StoredAsset(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        scene_id=uuid.uuid4(),
        shot_card_id=shot_card_id,
        step_id=None,
        role="shot_anchor",
        media_type="image",
        scope="shot",
        status="approved",
        path=Path("missing.png"),
        sha256="0" * 64,
        metadata={},
    )

    assert _json_asset(asset)["shotId"] == str(shot_card_id)
    assert _asset_json(asset)["shotId"] == str(shot_card_id)
