from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from cat_video_generator.application.video_editing import VideoEditingService, _replace_clip
from cat_video_generator.domain.rendering import (
    ClipOrigin,
    VideoSequenceClip,
    VideoSequencePlan,
)
from cat_video_generator.infrastructure.media.storage import LocalAssetStore


def clip(
    *,
    order: int,
    source_asset_id: uuid.UUID,
    source_start_ms: int,
    source_end_ms: int,
    timeline_start_ms: int,
    timeline_end_ms: int,
) -> VideoSequenceClip:
    return VideoSequenceClip(
        order=order,
        source_asset_id=source_asset_id,
        source_start_ms=source_start_ms,
        source_end_ms=source_end_ms,
        timeline_start_ms=timeline_start_ms,
        timeline_end_ms=timeline_end_ms,
        origin=ClipOrigin.ORIGINAL,
    )


def test_range_replacement_builds_non_destructive_three_clip_revision() -> None:
    source_id = uuid.uuid4()
    replacement_id = uuid.uuid4()
    step_id = uuid.uuid4()
    plan = VideoSequencePlan(
        duration_ms=12_000,
        clips=[
            clip(
                order=1,
                source_asset_id=source_id,
                source_start_ms=0,
                source_end_ms=12_000,
                timeline_start_ms=0,
                timeline_end_ms=12_000,
            )
        ],
    )

    edited = _replace_clip(
        plan,
        start_ms=3000,
        end_ms=7000,
        replacement_asset_id=replacement_id,
        replacement_step_id=step_id,
    )

    assert edited.duration_ms == plan.duration_ms
    assert plan.clips[0].source_asset_id == source_id
    assert [item.order for item in edited.clips] == [1, 2, 3]
    assert [item.timeline_start_ms for item in edited.clips] == [0, 3000, 7000]
    assert [item.timeline_end_ms for item in edited.clips] == [3000, 7000, 12_000]
    assert edited.clips[1].origin is ClipOrigin.GENERATED
    assert edited.clips[1].replacement_step_id == step_id


def test_range_edit_rejects_selection_crossing_source_clip_boundary() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    plan = VideoSequencePlan(
        duration_ms=12_000,
        clips=[
            clip(
                order=1,
                source_asset_id=first_id,
                source_start_ms=0,
                source_end_ms=6000,
                timeline_start_ms=0,
                timeline_end_ms=6000,
            ),
            clip(
                order=2,
                source_asset_id=second_id,
                source_start_ms=0,
                source_end_ms=6000,
                timeline_start_ms=6000,
                timeline_end_ms=12_000,
            ),
        ],
    )

    with pytest.raises(ValueError, match="不得跨越多个来源Clip"):
        VideoEditingService._single_source_clip(plan, 5500, 6500)


def test_ffmpeg_range_replacement_keeps_original_audio_and_total_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "work"
    assets = tmp_path / "assets"
    output = tmp_path / "output"
    base = tmp_path / "base.mp4"
    replacement = tmp_path / "replacement.mp4"
    base.write_bytes(b"base")
    replacement.write_bytes(b"replacement")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        values = [str(item) for item in command]
        commands.append(values)
        Path(values[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(values, 0, "", "")

    monkeypatch.setattr(
        "cat_video_generator.infrastructure.media.storage.subprocess.run",
        fake_run,
    )
    store = LocalAssetStore(
        work_root=work,
        asset_root=assets,
        delivery_root=output,
        ffmpeg_path=Path("ffmpeg"),
    )

    landed = store.render_range_replacement(
        base_path=base,
        replacement_path=replacement,
        replacement_duration_ms=3000,
        start_ms=2000,
        end_ms=5000,
    )

    assert landed.path.is_file()
    command = commands[0]
    assert command[command.index("-map") + 1] == "[vout]"
    audio_map = command.index("-map", command.index("-map") + 1)
    assert command[audio_map + 1] == "0:a?"
    assert command[command.index("-c:a") + 1] == "copy"
    assert "libx264" in command
    assert "trim=start=0:end=2.000" in command[command.index("-filter_complex") + 1]
    assert "trim=start=5.000" in command[command.index("-filter_complex") + 1]
