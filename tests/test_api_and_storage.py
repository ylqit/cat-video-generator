from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from cat_video_generator.application.ports import StoredAsset
from cat_video_generator.infrastructure.media.storage import LocalAssetStore
from cat_video_generator.interfaces.api import create_app


class FakeQueries:
    def __init__(self, asset: StoredAsset) -> None:
        self._asset = asset

    def health(self):
        return {"connected": True}

    def list_runs(self, **_):
        return []

    def run_graph(self, _):
        raise LookupError("missing")

    def episode(self, _):
        raise LookupError("missing")

    def step(self, _):
        raise LookupError("missing")

    def prompt(self, _):
        raise LookupError("missing")

    def asset(self, _):
        return self._asset


def _asset(path: Path) -> StoredAsset:
    payload = path.read_bytes()
    return StoredAsset(
        id=uuid.uuid4(),
        run_id=None,
        episode_id=None,
        step_id=None,
        role="video",
        media_type="video",
        scope="episode",
        status="ready",
        path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
        metadata={},
    )


def test_api_serves_only_files_inside_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    media = root / "video.mp4"
    media.write_bytes(b"video")
    client = TestClient(
        create_app(FakeQueries(_asset(media)), allowed_media_roots=(root,))
    )
    response = client.get(f"/api/v1/assets/{uuid.uuid4()}/content")
    assert response.status_code == 200
    assert response.content == b"video"


def test_api_rejects_asset_path_outside_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "assets"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")
    client = TestClient(
        create_app(FakeQueries(_asset(outside)), allowed_media_roots=(root,))
    )
    response = client.get(f"/api/v1/assets/{uuid.uuid4()}/content")
    assert response.status_code == 403


def test_canon_crop_is_deterministic_and_keeps_source_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (300, 200), (20, 40, 60)).save(source)
    store = LocalAssetStore(
        work_root=tmp_path / "work",
        asset_root=tmp_path / "assets",
        delivery_root=tmp_path / "output",
    )

    first = store.crop_local(source, box=(0, 0, 100, 200))
    second = store.crop_local(source, box=(0, 0, 100, 200))

    assert first.sha256 == second.sha256
    assert first.path == second.path
    assert Image.open(source).size == (300, 200)
    assert Image.open(first.path).size == (100, 200)
