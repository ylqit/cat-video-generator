"""一次性导出当前八表工作流，供核心Schema收敛前留档。

本脚本只读取PostgreSQL并写入本地 ``var/archive``。它不会修改数据库，也不会
删除任何媒体。归档使用只读、可重复读事务，确保八张表来自同一个一致快照。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, text

from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    load_local_env,
)
from cat_video_generator.infrastructure.db.session import create_database_engine

TABLES = (
    "production_runs",
    "episodes",
    "workflow_steps",
    "prompt_records",
    "assets",
    "reviews",
    "delivery_packages",
    "delivery_items",
)
SECRET_KEYS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "signed_url",
    "download_url",
    "temporary_url",
)
URL_KEY_RE = re.compile(r"(^|_)(url|uri)(_|$)", re.IGNORECASE)
BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]{1024,}$")


class ArchiveError(RuntimeError):
    """归档内容不完整或媒体校验失败。"""


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime, Decimal, UUID)):
        return str(value)
    raise TypeError(f"不能序列化归档值：{type(value).__name__}")


def _sanitize(value: Any, *, key: str = "") -> Any:
    """递归移除秘密、临时URL与Base64，同时保留Task ID和Prompt正文。"""

    normalized_key = key.casefold()
    if any(secret in normalized_key for secret in SECRET_KEYS):
        return "[redacted]"
    if URL_KEY_RE.search(normalized_key):
        return "[redacted-url]"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        if value.startswith("data:") or BASE64_RE.fullmatch(value):
            return "[redacted-base64]"
        return value
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_media_path(workspace: Path, stored_path: str) -> Path:
    path = Path(stored_path)
    return path.resolve() if path.is_absolute() else (workspace / path).resolve()


def _read_rows(connection: Any, schema: str, table: str) -> list[dict[str, Any]]:
    preparer = connection.dialect.identifier_preparer
    qualified = f"{preparer.quote_schema(schema)}.{preparer.quote(table)}"
    columns = {item["name"] for item in inspect(connection).get_columns(table, schema=schema)}
    order = "created_at, id" if "created_at" in columns else "id"
    rows = connection.execute(text(f"SELECT * FROM {qualified} ORDER BY {order}"))
    return [_sanitize(dict(row)) for row in rows.mappings()]


def _latest_canon_ids(asset_rows: list[dict[str, Any]]) -> list[str]:
    latest: dict[str, dict[str, Any]] = {}
    for row in asset_rows:
        semantic_key = row.get("semantic_key")
        if (
            row.get("scope") != "canon"
            or row.get("status") != "approved"
            or not isinstance(semantic_key, str)
            or semantic_key.startswith("legacy:")
        ):
            continue
        current = latest.get(semantic_key)
        if current is None or str(row.get("created_at")) > str(current.get("created_at")):
            latest[semantic_key] = row
    return sorted(str(row["id"]) for row in latest.values())


def _verify_media(
    workspace: Path,
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    verification: list[dict[str, Any]] = []
    failures: list[str] = []
    for asset in assets:
        path = _resolve_media_path(workspace, str(asset["local_path"]))
        exists = path.is_file()
        actual_sha = _sha256(path) if exists else None
        expected_sha = str(asset["sha256"])
        matches = bool(exists and actual_sha == expected_sha)
        item = {
            "assetId": str(asset["id"]),
            "path": str(path),
            "exists": exists,
            "expectedSha256": expected_sha,
            "actualSha256": actual_sha,
            "matches": matches,
        }
        verification.append(item)
        if not matches:
            failures.append(f"{asset['id']}:{path}")
    if failures:
        raise ArchiveError("媒体缺失或SHA-256不一致：" + ", ".join(failures))
    return verification


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    load_local_env(workspace / ".env")
    settings = DatabaseSettings.from_env()
    settings.validate_for(DatabaseOperation.READ_ONLY_SMOKE)
    if settings.schema != "cat_video":
        raise ArchiveError("归档只允许读取精确目标Schema cat_video")

    engine = create_database_engine(settings, DatabaseOperation.READ_ONLY_SMOKE)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                tables = set(inspect(connection).get_table_names(schema=settings.schema))
                missing = set(TABLES) - tables
                if missing:
                    raise ArchiveError(f"当前核心Schema缺少表：{sorted(missing)}")
                rows = {
                    table: _read_rows(connection, settings.schema, table)
                    for table in TABLES
                }
                active = [
                    row
                    for row in rows["workflow_steps"]
                    if row.get("status")
                    in {"submitting", "submission_unknown", "queued", "running"}
                ]
                if active:
                    raise ArchiveError(
                        "仍存在活动或提交结果未知的Ark步骤，禁止冻结归档："
                        + ", ".join(str(row["id"]) for row in active)
                    )
                revision = connection.execute(
                    text(f"SELECT version_num FROM {settings.schema}.alembic_version")
                ).scalar_one()
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise
    finally:
        engine.dispose()

    media_verification = _verify_media(workspace, rows["assets"])
    preserved_canon_ids = _latest_canon_ids(rows["assets"])
    created_at = datetime.now(timezone.utc)
    archive_dir = workspace / "var" / "archive" / created_at.strftime("core-%Y%m%dT%H%M%SZ")
    archive_dir.mkdir(parents=True, exist_ok=False)
    archive_path = archive_dir / "archive.json"
    bundle = {
        "format": "cat-video-core-runtime-v1",
        "createdAt": created_at.isoformat(),
        "source": {
            "database": settings.database,
            "schema": settings.schema,
            "alembicRevision": revision,
        },
        "tables": rows,
        "mediaVerification": media_verification,
        "preservedCanonAssetIds": preserved_canon_ids,
    }
    payload = json.dumps(
        bundle,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=_json_default,
    )
    part_path = archive_path.with_suffix(".json.part")
    part_path.write_text(payload, encoding="utf-8")
    part_path.replace(archive_path)
    archive_sha = _sha256(archive_path)

    manifest = {
        "format": bundle["format"],
        "createdAt": bundle["createdAt"],
        "archiveFile": archive_path.name,
        "archiveSha256": archive_sha,
        "recordCounts": {table: len(items) for table, items in rows.items()},
        "mediaCount": len(media_verification),
        "mediaVerified": all(item["matches"] for item in media_verification),
        "preservedCanonAssetIds": preserved_canon_ids,
    }
    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    manifest_sha = _sha256(manifest_path)
    (archive_dir / "manifest.sha256").write_text(
        f"{manifest_sha}  manifest.json\n{archive_sha}  archive.json\n",
        encoding="ascii",
    )
    print(json.dumps({"archiveDir": str(archive_dir), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ArchiveError as exc:
        print(f"archive failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
