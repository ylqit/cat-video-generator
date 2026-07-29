"""旧工作流归档与八表 Schema 的一次性重建工具。

该模块只用于经人工确认的维护窗口。它会先冻结并校验 V5 归档，再原位重建
``cat_video``；普通规划、生成、查询路径不会导入本模块。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, inspect, text

from .archive_import import import_archive
from .session import ALEMBIC_HEAD

ARCHIVE_FORMAT = "cat-video-v5-readonly-archive-v1"
LEGACY_TABLES = {
    "alembic_version",
    "continuity_events",
    "daily_life_packs",
    "daily_slots",
    "delivery_items",
    "delivery_packages",
    "episode_variants",
    "generation_jobs",
    "identity_reference_items",
    "identity_reference_sets",
    "media_assets",
    "reference_assets",
    "resolution_comparison_items",
    "resolution_comparisons",
    "review_decisions",
    "slot_retry_events",
}
ACTIVE_LEGACY_JOB_STATUSES = {
    "submitting",
    "queued",
    "running",
    "submission_unknown",
}


class MaintenanceSafetyError(RuntimeError):
    """归档不完整、目标不精确或仍有活动任务时拒绝破坏性操作。"""


class CompactSchemaMaintenance:
    """在单一受控边界中完成归档、重建、导入和旧文件清理。"""

    def __init__(
        self,
        *,
        engine: Engine,
        schema: str,
        workspace_root: Path,
    ) -> None:
        self._engine = engine
        self._schema = schema
        self._workspace_root = workspace_root.resolve()

    def export_v5_archive(self, archive_root: Path) -> dict[str, Any]:
        """导出并校验 V5、Canon、Prompt 与媒体哈希，不修改数据库。"""

        with self._engine.connect() as connection:
            self._verify_legacy_schema(connection)
            self._verify_no_active_jobs(connection)
            bundle = self._read_legacy_bundle(connection)
        self._verify_preserved_files(bundle)
        archive_dir = archive_root.resolve() / datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        archive_path = archive_dir / "archive.json"
        archive_dir.mkdir(parents=True, exist_ok=False)
        payload = json.dumps(
            bundle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        part_path = archive_path.with_suffix(".json.part")
        part_path.write_text(payload, encoding="utf-8")
        with part_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        part_path.replace(archive_path)
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        (archive_dir / "archive.sha256").write_text(
            f"{digest}  archive.json\n",
            encoding="ascii",
        )
        return {
            "archivePath": str(archive_path),
            "archiveSha256": digest,
            "v5RunCount": len(bundle["legacy"]["productionRuns"]),
            "promptCount": len(bundle["legacy"]["jobs"]),
            "v5MediaCount": len(bundle["legacy"]["mediaAssets"]),
            "canonCount": len(bundle["legacy"]["canonAssets"]),
            "legacyMediaDeleteCount": len(bundle["cleanup"]["assetFiles"]),
        }

    def rebuild_from_archive(
        self,
        archive_path: Path,
        *,
        confirm_schema: str,
        confirm_purge_versions: str,
    ) -> dict[str, Any]:
        """原位重建 Schema，并把 V5 与 Canon 导入只读归档记录。"""

        if self._schema != "cat_video" or confirm_schema != self._schema:
            raise MaintenanceSafetyError("必须精确确认 cat_video Schema")
        if confirm_purge_versions != "V1-V4":
            raise MaintenanceSafetyError("必须精确确认永久清理 V1-V4")
        bundle = self._load_and_verify_archive(archive_path.resolve())
        quoted = self._engine.dialect.identifier_preparer.quote_schema(self._schema)
        # DROP、迁移和归档导入共享一个数据库事务；任一步失败都会回滚旧 Schema，
        # 不留下“表已删但归档尚未导入”的中间状态。
        with self._engine.begin() as connection:
            self._verify_no_active_jobs(connection)
            connection.execute(text(f"DROP SCHEMA {quoted} CASCADE"))
            connection.execute(text(f"CREATE SCHEMA {quoted}"))
            alembic = Config(str(self._workspace_root / "alembic.ini"))
            alembic.attributes["connection"] = connection
            alembic.attributes["schema"] = self._schema
            command.upgrade(alembic, "head")
            import_archive(
                connection,
                bundle,
                archive_format=ARCHIVE_FORMAT,
            )
        removed = self._remove_legacy_files(bundle)
        return {
            "schema": self._schema,
            "alembicRevision": ALEMBIC_HEAD,
            "archivedRuns": len(bundle["legacy"]["productionRuns"]),
            "archivedMedia": len(bundle["legacy"]["mediaAssets"]),
            "canonAssets": len(bundle["legacy"]["canonAssets"]),
            "removedLegacyFiles": removed,
            "archivePath": str(archive_path.resolve()),
        }

    def validate_rebuild_rollback(
        self,
        archive_path: Path,
    ) -> dict[str, Any]:
        """在真实 PostgreSQL 事务中演练重建，并在验收后强制回滚。"""

        bundle = self._load_and_verify_archive(archive_path.resolve())
        quoted = self._engine.dialect.identifier_preparer.quote_schema(self._schema)
        connection = self._engine.connect()
        transaction = connection.begin()
        try:
            self._verify_no_active_jobs(connection)
            connection.execute(text(f"DROP SCHEMA {quoted} CASCADE"))
            connection.execute(text(f"CREATE SCHEMA {quoted}"))
            alembic = Config(str(self._workspace_root / "alembic.ini"))
            alembic.attributes["connection"] = connection
            alembic.attributes["schema"] = self._schema
            command.upgrade(alembic, "head")
            import_archive(
                connection,
                bundle,
                archive_format=ARCHIVE_FORMAT,
            )
            counts = {
                table: connection.execute(
                    text(f"SELECT count(*) FROM {quoted}.{table}")
                ).scalar_one()
                for table in (
                    "production_runs",
                    "episodes",
                    "workflow_steps",
                    "prompt_records",
                    "assets",
                    "reviews",
                )
            }
        finally:
            # 这是一次真实 DDL/DML 演练，但不允许留下任何变化。
            transaction.rollback()
            connection.close()
        return {
            "rolledBack": True,
            "alembicRevision": ALEMBIC_HEAD,
            "countsDuringValidation": counts,
        }

    def _verify_legacy_schema(self, connection: Connection) -> None:
        tables = set(inspect(connection).get_table_names(schema=self._schema))
        if tables != LEGACY_TABLES:
            raise MaintenanceSafetyError(
                "旧 Schema 对象与已审计清单不一致，拒绝自动接管："
                f" missing={sorted(LEGACY_TABLES - tables)},"
                f" unexpected={sorted(tables - LEGACY_TABLES)}"
            )

    def _verify_no_active_jobs(self, connection: Connection) -> None:
        tables = set(inspect(connection).get_table_names(schema=self._schema))
        if "generation_jobs" not in tables:
            return
        statuses = connection.execute(
            text(
                f"SELECT DISTINCT status FROM {self._schema}.generation_jobs "
                "WHERE status = ANY(:statuses)"
            ),
            {"statuses": list(ACTIVE_LEGACY_JOB_STATUSES)},
        ).scalars()
        active = sorted(statuses)
        if active:
            raise MaintenanceSafetyError(f"仍有活动或提交未知的 Ark 任务：{active}")

    def _read_legacy_bundle(self, connection: Connection) -> dict[str, Any]:
        legacy = {
            "productionRuns": self._rows(
                connection,
                """
                SELECT * FROM cat_video.daily_life_packs
                WHERE source_json->>'schemaVersion' = '5'
                ORDER BY date, plan_revision
                """,
            ),
            "slots": self._rows(
                connection,
                """
                SELECT ds.* FROM cat_video.daily_slots ds
                JOIN cat_video.daily_life_packs p
                  ON p.id = ds.daily_life_pack_id
                WHERE p.source_json->>'schemaVersion' = '5'
                ORDER BY ds.sort_order
                """,
            ),
            "variants": self._rows(
                connection,
                """
                SELECT ev.* FROM cat_video.episode_variants ev
                JOIN cat_video.daily_slots ds ON ds.id = ev.daily_slot_id
                JOIN cat_video.daily_life_packs p
                  ON p.id = ds.daily_life_pack_id
                WHERE p.source_json->>'schemaVersion' = '5'
                ORDER BY ds.sort_order, ev.created_at
                """,
            ),
            "jobs": self._rows(
                connection,
                """
                SELECT gj.* FROM cat_video.generation_jobs gj
                JOIN cat_video.episode_variants ev
                  ON ev.id = gj.episode_variant_id
                JOIN cat_video.daily_slots ds ON ds.id = ev.daily_slot_id
                JOIN cat_video.daily_life_packs p
                  ON p.id = ds.daily_life_pack_id
                WHERE p.source_json->>'schemaVersion' = '5'
                ORDER BY gj.created_at
                """,
                sanitize=True,
            ),
            "mediaAssets": self._rows(
                connection,
                """
                SELECT ma.* FROM cat_video.media_assets ma
                JOIN cat_video.episode_variants ev
                  ON ev.id = ma.episode_variant_id
                JOIN cat_video.daily_slots ds ON ds.id = ev.daily_slot_id
                JOIN cat_video.daily_life_packs p
                  ON p.id = ds.daily_life_pack_id
                WHERE p.source_json->>'schemaVersion' = '5'
                ORDER BY ma.created_at
                """,
            ),
            "reviews": self._rows(
                connection,
                """
                SELECT rd.* FROM cat_video.review_decisions rd
                JOIN cat_video.media_assets ma ON ma.id = rd.media_asset_id
                JOIN cat_video.episode_variants ev
                  ON ev.id = ma.episode_variant_id
                JOIN cat_video.daily_slots ds ON ds.id = ev.daily_slot_id
                JOIN cat_video.daily_life_packs p
                  ON p.id = ds.daily_life_pack_id
                WHERE p.source_json->>'schemaVersion' = '5'
                ORDER BY rd.created_at
                """,
                sanitize=True,
            ),
            "canonAssets": self._rows(
                connection,
                "SELECT * FROM cat_video.reference_assets ORDER BY created_at",
            ),
            "identityReferenceSets": self._rows(
                connection,
                "SELECT * FROM cat_video.identity_reference_sets ORDER BY created_at",
            ),
            "identityReferenceItems": self._rows(
                connection,
                "SELECT * FROM cat_video.identity_reference_items ORDER BY created_at",
            ),
        }
        preserved = {
            str(row["storage_path"])
            for group in ("mediaAssets", "canonAssets")
            for row in legacy[group]
        }
        local_files = self._v5_local_files()
        all_generated = {
            str(path.resolve())
            for path in (self._workspace_root / "var" / "assets" / "generated").glob(
                "sha256/*/*"
            )
            if path.is_file()
        }
        return {
            "format": ARCHIVE_FORMAT,
            "createdAt": datetime.now(timezone.utc),
            "source": {
                "database": "vedio-appdb",
                "schema": self._schema,
                "alembicRevision": "0007_resolution_comparisons",
            },
            "legacy": legacy,
            "localFiles": local_files,
            "cleanup": {
                "assetFiles": sorted(all_generated - preserved),
                "directories": [
                    str((self._workspace_root / "var" / "comparisons").resolve()),
                    str((self._workspace_root / "var" / "work").resolve()),
                    str(
                        (
                            self._workspace_root / "var" / "plans" / "2026-07-29"
                        ).resolve()
                    ),
                    str(
                        (
                            self._workspace_root / "var" / "plans" / "2026-07-30"
                        ).resolve()
                    ),
                    str(
                        (
                            self._workspace_root
                            / "var"
                            / "prompts"
                            / "life-2026-07-24-seaside-travel"
                        ).resolve()
                    ),
                    str(
                        (
                            self._workspace_root
                            / "var"
                            / "prompts"
                            / "life-2026-07-29-seaside-travel"
                        ).resolve()
                    ),
                ],
            },
        }

    def _rows(
        self,
        connection: Connection,
        query: str,
        *,
        sanitize: bool = False,
    ) -> list[dict[str, Any]]:
        rows = [dict(row) for row in connection.execute(text(query)).mappings()]
        return [_sanitize(row) for row in rows] if sanitize else rows

    def _v5_local_files(self) -> list[dict[str, Any]]:
        roots = (
            self._workspace_root / "var" / "plans" / "2026-07-31",
            self._workspace_root / "var" / "prompts" / "life-2026-07-31-daily-v5",
        )
        return [
            {
                "path": str(path.resolve()),
                "sha256": _sha256(path),
                "byteSize": path.stat().st_size,
            }
            for root in roots
            if root.is_dir()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]

    def _verify_preserved_files(self, bundle: dict[str, Any]) -> None:
        records = [
            {
                "path": row["storage_path"],
                "sha256": row["sha256"],
            }
            for group in ("mediaAssets", "canonAssets")
            for row in bundle["legacy"][group]
        ] + bundle["localFiles"]
        failures = []
        for record in records:
            path = Path(str(record["path"]))
            if not path.is_file():
                failures.append(f"文件不存在：{path}")
            elif _sha256(path) != record["sha256"]:
                failures.append(f"哈希不一致：{path}")
        if failures:
            raise MaintenanceSafetyError("; ".join(failures))

    def _load_and_verify_archive(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise MaintenanceSafetyError(f"归档不存在：{path}")
        checksum_path = path.with_name("archive.sha256")
        if not checksum_path.is_file():
            raise MaintenanceSafetyError("归档缺少 archive.sha256")
        expected = checksum_path.read_text(encoding="ascii").split()[0]
        if _sha256(path) != expected:
            raise MaintenanceSafetyError("归档文件 SHA-256 校验失败")
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if bundle.get("format") != ARCHIVE_FORMAT:
            raise MaintenanceSafetyError("不支持的归档格式")
        self._verify_preserved_files(bundle)
        return bundle

    def _remove_legacy_files(self, bundle: dict[str, Any]) -> int:
        targets = [Path(path).resolve() for path in bundle["cleanup"]["assetFiles"]]
        directories = [
            Path(path).resolve() for path in bundle["cleanup"]["directories"]
        ]
        allowed = (self._workspace_root / "var").resolve()
        for path in (*targets, *directories):
            if path == allowed or allowed not in path.parents:
                raise MaintenanceSafetyError(f"清理目标越出工作区 var 目录：{path}")
        removed = 0
        for path in targets:
            if path.is_file():
                path.unlink()
                removed += 1
        for path in directories:
            if path.is_dir():
                shutil.rmtree(path)
        legacy_marker = self._workspace_root / "var" / "morning-v4-structure.json"
        if legacy_marker.is_file():
            legacy_marker.unlink()
            removed += 1
        return removed


def _sanitize(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            child_key: _sanitize(child, key=child_key)
            for child_key, child in value.items()
            if not _secret_key(child_key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, key=key) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str) and key not in {"prompt", "videoPrompt"}:
        lowered = value.lower()
        if lowered.startswith(("http://", "https://")):
            return "<redacted-url>"
        if lowered.startswith("data:"):
            return "<redacted-base64>"
    return value


def _secret_key(key: str) -> bool:
    compact = key.lower().replace("_", "")
    return (
        compact in {"apikey", "password", "authorization", "connectionstring"}
        or "base64" in compact
        or "signedurl" in compact
        or "downloadurl" in compact
        or "videourl" in compact
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: Any) -> str:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"不支持归档序列化的类型：{type(value).__name__}")
