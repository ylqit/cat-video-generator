"""显式执行 V5 归档与 ``cat_video`` 八表原位重建。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    load_local_env,
)
from cat_video_generator.infrastructure.db.maintenance import (
    CompactSchemaMaintenance,
)
from cat_video_generator.infrastructure.db.session import (
    create_database_engine,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--validate-rollback", action="store_true")
    parser.add_argument("--confirm-schema")
    parser.add_argument("--confirm-purge-versions")
    args = parser.parse_args()

    load_local_env()
    settings = DatabaseSettings.from_env()
    settings.validate_for(DatabaseOperation.MIGRATION)
    workspace = Path.cwd().resolve()
    engine = create_database_engine(settings, DatabaseOperation.MIGRATION)
    maintenance = CompactSchemaMaintenance(
        engine=engine,
        schema=settings.schema,
        workspace_root=workspace,
    )
    try:
        archive = args.archive
        if archive is None:
            result = maintenance.export_v5_archive(
                workspace / "var" / "archive"
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            archive = Path(result["archivePath"])
        if args.validate_rollback:
            result = maintenance.validate_rebuild_rollback(archive)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif not args.export_only:
            result = maintenance.rebuild_from_archive(
                archive,
                confirm_schema=args.confirm_schema or "",
                confirm_purge_versions=args.confirm_purge_versions or "",
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
