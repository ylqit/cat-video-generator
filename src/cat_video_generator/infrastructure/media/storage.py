"""供应商临时URL到本地内容寻址资产的原子落盘。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import date
from pathlib import Path

import httpx
from PIL import Image

from ...application.ports import DeliveryBuild, LandedAsset, StoredAsset
from ...domain.contracts import Slot


class AssetStorageError(RuntimeError):
    """媒体下载或原子落盘失败。"""


class LocalAssetStore:
    """同盘`.part`下载和SHA-256内容寻址存储。"""

    def __init__(
        self,
        *,
        work_root: Path,
        asset_root: Path,
        delivery_root: Path,
        ffmpeg_path: Path | None = None,
        max_bytes: int = 2_000_000_000,
    ) -> None:
        self._work_root = work_root.expanduser().resolve()
        self._asset_root = asset_root.expanduser().resolve()
        self._delivery_root = delivery_root.expanduser().resolve()
        self._ffmpeg_path = None if ffmpeg_path is None else ffmpeg_path.expanduser().resolve()
        self._max_bytes = max_bytes
        if (
            self._work_root.drive
            and self._asset_root.drive
            and self._work_root.drive.lower() != self._asset_root.drive.lower()
        ):
            raise AssetStorageError("工作目录和资产目录必须位于同一磁盘")

    def download(self, url: str, *, suffix: str) -> LandedAsset:
        self._work_root.mkdir(parents=True, exist_ok=True)
        temporary = self._work_root / f".download-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with (
                httpx.Client(
                    follow_redirects=True,
                    timeout=httpx.Timeout(120.0, connect=15.0),
                ) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        output.write(chunk)
                        digest.update(chunk)
                        byte_size += len(chunk)
                        if byte_size > self._max_bytes:
                            raise AssetStorageError("供应商媒体超过大小上限")
                    output.flush()
                    os.fsync(output.fileno())
            if byte_size == 0:
                raise AssetStorageError("供应商返回了空文件")
            sha256 = digest.hexdigest()
            extension = suffix if suffix.startswith(".") else f".{suffix}"
            destination = (
                self._asset_root
                / "generated"
                / "sha256"
                / sha256[:2]
                / f"{sha256}{extension.lower()}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                temporary.unlink()
            else:
                # 只有完整下载并fsync后才原子改名，任何中断都不会留下看似
                # 完整的正式资产。
                os.replace(temporary, destination)
            return LandedAsset(destination, sha256, byte_size)
        except (OSError, httpx.HTTPError) as exc:
            raise AssetStorageError(f"媒体下载失败: {exc}") from exc
        finally:
            if temporary.exists():
                temporary.unlink()

    def import_local(self, path: Path) -> LandedAsset:
        source = path.expanduser().resolve()
        if not source.is_file():
            raise AssetStorageError(f"本地素材不存在: {source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        suffix = source.suffix.lower()
        destination = self._asset_root / "imported" / "sha256" / digest[:2] / f"{digest}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".part")
            shutil.copy2(source, temporary)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                temporary.unlink()
                raise AssetStorageError("本地素材复制后哈希不一致")
            os.replace(temporary, destination)
        return LandedAsset(destination, digest, destination.stat().st_size)

    def crop_local(
        self,
        path: Path,
        *,
        box: tuple[int, int, int, int],
    ) -> LandedAsset:
        """按像素确定性裁剪Canon，不调用生成模型也不改变主体身份。"""

        source = path.expanduser().resolve()
        if not source.is_file():
            raise AssetStorageError(f"本地素材不存在: {source}")
        self._work_root.mkdir(parents=True, exist_ok=True)
        temporary = self._work_root / f".crop-{uuid.uuid4().hex}.png"
        try:
            with Image.open(source) as image:
                width, height = image.size
                left, top, right, bottom = box
                if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                    raise AssetStorageError(f"裁剪框{box}超出素材尺寸{width}x{height}")
                image.crop(box).save(temporary, format="PNG")
            return self.import_local(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def concatenate_videos(self, paths: tuple[Path, ...]) -> LandedAsset:
        """顺序合并Ark续写尾段，不重新编码画面或声音。

        标准Ark接口把reference_video续写结果作为新的尾段返回。这里只负责把同规格的原片与
        尾段封装为一个交付MP4；任何编码不兼容都会显式失败，不会偷偷转码或掩盖断点。
        """

        if self._ffmpeg_path is None:
            raise AssetStorageError("视频续写成片需要配置ffmpeg")
        if len(paths) not in {2, 3}:
            raise AssetStorageError("视频续写只允许合并2或3个连续区段")
        resolved = tuple(path.expanduser().resolve() for path in paths)
        if any(not path.is_file() for path in resolved):
            raise AssetStorageError("视频续写区段文件缺失")
        self._work_root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        manifest = self._work_root / f".concat-{token}.txt"
        output = self._work_root / f".concat-{token}.mp4"
        try:
            lines = []
            for path in resolved:
                value = path.as_posix().replace("'", "'\\''")
                lines.append(f"file '{value}'")
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            subprocess.run(
                [
                    str(self._ffmpeg_path),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(manifest),
                    "-c",
                    "copy",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600,
            )
            return self.import_local(output)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = (
                exc.stderr.strip()[-1000:]
                if isinstance(exc, subprocess.CalledProcessError)
                else str(exc)
            )
            raise AssetStorageError(f"视频续写区段合并失败: {detail}") from exc
        finally:
            manifest.unlink(missing_ok=True)
            output.unlink(missing_ok=True)

    def build_delivery(
        self,
        *,
        content_date: date,
        run_id: uuid.UUID,
        revision: int,
        items: tuple[tuple[Slot, StoredAsset], ...],
    ) -> DeliveryBuild:
        if [slot.sort_order for slot, _ in items] != [1, 2, 3]:
            raise AssetStorageError("交付必须严格包含morning、noon、evening")
        parent = self._delivery_root / content_date.isoformat() / str(run_id)
        destination = parent / f"delivery-r{revision}"
        if destination.exists():
            raise AssetStorageError(f"交付目录已经存在: {destination}")
        building = parent / f".building-{uuid.uuid4().hex}"
        building.mkdir(parents=True, exist_ok=False)
        manifest_items: list[dict[str, str | int]] = []
        try:
            for slot, asset in items:
                filename = f"{slot.sort_order:02d}-{slot.value}.mp4"
                target = building / filename
                shutil.copy2(asset.path, target)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest != asset.sha256:
                    raise AssetStorageError(f"{filename}复制后哈希不一致")
                manifest_items.append(
                    {
                        "slot": slot.value,
                        "sortOrder": slot.sort_order,
                        "filename": filename,
                        "sha256": digest,
                        "assetId": str(asset.id),
                    }
                )
            manifest = {
                "runId": str(run_id),
                "contentDate": content_date.isoformat(),
                "revision": revision,
                "items": manifest_items,
            }
            manifest_path = building / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            parent.mkdir(parents=True, exist_ok=True)
            os.replace(building, destination)
            return DeliveryBuild(
                path=destination,
                manifest_sha256=manifest_hash,
                items=tuple(manifest_items),
            )
        except Exception:
            if building.exists():
                shutil.rmtree(building)
            raise
