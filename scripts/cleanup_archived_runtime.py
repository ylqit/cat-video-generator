"""在已验证归档SHA后清理旧Run，仅保留最新批准Canon。

这是一次性破坏性维护脚本。它不会删除任何本地媒体文件；删除范围被固定为
``vedio-appdb.cat_video``，且所有记录数必须仍与归档Manifest一致。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import inspect, text

from cat_video_generator.config import DatabaseOperation, DatabaseSettings, load_local_env
from cat_video_generator.infrastructure.db.session import create_database_engine


class CleanupError(RuntimeError):
    """目标、归档或数据库快照不满足精确清理条件。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--confirm-sha", required=True)
    args = parser.parse_args()
    archive_path = args.archive.resolve()
    manifest_path = archive_path.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_sha = _sha256(archive_path)
    if actual_sha != args.confirm_sha or actual_sha != manifest["archiveSha256"]:
        raise CleanupError("归档SHA确认值不一致")

    workspace = Path(__file__).resolve().parents[1]
    load_local_env(workspace / ".env")
    settings = DatabaseSettings.from_env()
    if (
        settings.database != "vedio-appdb"
        or settings.schema != "cat_video"
        or not settings.allow_insecure_runtime
    ):
        raise CleanupError("只允许显式授权的vedio-appdb.cat_video维护窗口")
    engine = create_database_engine(settings, DatabaseOperation.MIGRATION)
    try:
        with engine.begin() as connection:
            tables = set(inspect(connection).get_table_names(schema=settings.schema))
            expected_tables = set(manifest["recordCounts"])
            if not expected_tables.issubset(tables):
                raise CleanupError("当前Schema表集合与归档不一致")
            revision = connection.execute(
                text(f"SELECT version_num FROM {settings.schema}.alembic_version")
            ).scalar_one()
            if revision != "0005_episode_prompt_overrides":
                raise CleanupError(f"清理前数据库必须位于0005，实际为{revision}")
            current = {
                table: int(
                    connection.execute(
                        text(f"SELECT count(*) FROM {settings.schema}.{table}")
                    ).scalar_one()
                )
                for table in manifest["recordCounts"]
            }
            if current != manifest["recordCounts"]:
                raise CleanupError(
                    "归档后数据库记录数已变化，拒绝清理："
                    f"expected={manifest['recordCounts']}, actual={current}"
                )
            active = int(
                connection.execute(
                    text(
                        f"SELECT count(*) FROM {settings.schema}.workflow_steps "
                        "WHERE status IN ('submitting','submission_unknown','queued','running')"
                    )
                ).scalar_one()
            )
            if active:
                raise CleanupError("仍有活动或提交未知步骤，拒绝清理")

            preserved = tuple(manifest["preservedCanonAssetIds"])
            connection.execute(text(f"DELETE FROM {settings.schema}.delivery_items"))
            connection.execute(text(f"DELETE FROM {settings.schema}.delivery_packages"))
            connection.execute(text(f"DELETE FROM {settings.schema}.reviews"))
            connection.execute(text(f"DELETE FROM {settings.schema}.prompt_records"))
            connection.execute(text(f"DELETE FROM {settings.schema}.workflow_steps"))
            connection.execute(text(f"DELETE FROM {settings.schema}.episodes"))
            connection.execute(text(f"DELETE FROM {settings.schema}.production_runs"))
            connection.execute(
                text(
                    f"DELETE FROM {settings.schema}.assets "
                    "WHERE scope <> 'canon' OR id::text <> ALL(:preserved)"
                ),
                {"preserved": list(preserved)},
            )
            remaining = int(
                connection.execute(
                    text(f"SELECT count(*) FROM {settings.schema}.assets")
                ).scalar_one()
            )
            if remaining != len(preserved):
                raise CleanupError("Canon保留数量与归档Manifest不一致，事务将回滚")
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "archiveSha256": actual_sha,
                "clearedRecordCounts": manifest["recordCounts"],
                "preservedCanonCount": len(manifest["preservedCanonAssetIds"]),
                "localMediaDeleted": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
