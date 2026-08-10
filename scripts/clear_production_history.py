"""为0012极简导演契约生成清理清单，并显式删除全部非Canon历史。

默认只输出诊断Manifest。只有传入与当前Run数量匹配的``--confirm``口令时，
才在单一数据库事务中删除业务记录；事务提交后再删除Manifest已记录且位于媒体
根目录内的文件。同时只保留每个正式semantic_key最新的已批准Canon版本；旧版、
拒绝版和legacy Canon会进入同一份清单并在同一事务中删除。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from cat_video_generator.config import (
    DatabaseOperation,
    DatabaseSettings,
    RuntimeSettings,
    load_local_env,
)
from cat_video_generator.infrastructure.db.models import (
    Asset,
    DeliveryItem,
    DeliveryPackage,
    Episode,
    ProductionRun,
    PromptRecord,
    Review,
    WorkflowStep,
)
from cat_video_generator.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
)

CURRENT_CANON_KEYS = frozenset(
    {
        "person:headshot",
        "person:fullbody",
        "person:front",
        "person:side",
        "person:back",
        "cat:front",
        "cat:side",
        "cat:back",
        "style:line_texture",
        "style:indoor",
        "style:outdoor",
    }
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm",
        help="真正删除时必须传入清单打印的完整确认口令",
    )
    args = parser.parse_args()

    load_local_env()
    database = DatabaseSettings.from_env()
    runtime = RuntimeSettings.from_env()
    engine = create_database_engine(database, DatabaseOperation.RUNTIME)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            manifest = _manifest(session, runtime)
        path = _write_manifest(manifest)
        print(f"诊断清单：{path}")
        expected = _confirmation(manifest)
        print(f"确认口令：{expected}")
        if args.confirm is None:
            print("当前为预览模式，数据库和媒体均未修改。")
            return 0
        if args.confirm != expected:
            raise SystemExit(f"确认口令不匹配，期望 {expected}")
        if manifest["missingCurrentCanonKeys"]:
            raise RuntimeError(
                "缺少当前生产Canon，拒绝清理：" + ", ".join(manifest["missingCurrentCanonKeys"])
            )
        unsafe_canon = [
            item
            for item in manifest["retainedCanonAssets"]
            if not item["pathPermitted"] or not item["exists"] or not item["sha256Matches"]
        ]
        if unsafe_canon:
            raise RuntimeError("保留Canon的路径或SHA-256校验失败，拒绝清理")

        with sessions.begin() as session:
            current_count = session.scalar(select(func.count()).select_from(ProductionRun))
            if current_count != manifest["counts"]["runs"]:
                raise RuntimeError("生成清单后Run数量发生变化，拒绝清理")
            # 旧Schema的delivery_items同时引用Episode和Asset，但这两个外键没有
            # ON DELETE CASCADE。必须先按依赖拓扑显式删除交付明细与包，再删除Run；
            # 全部仍在同一事务中，任何一步失败都会完整回滚。
            session.execute(delete(DeliveryItem))
            session.execute(delete(DeliveryPackage))
            session.execute(delete(ProductionRun))
            obsolete_ids = [uuid.UUID(item["id"]) for item in manifest["obsoleteCanonAssets"]]
            if obsolete_ids:
                session.execute(delete(Asset).where(Asset.id.in_(obsolete_ids)))

        removed = _delete_recorded_media(manifest, runtime)
        print(
            f"已删除 {manifest['counts']['runs']} 个Run、"
            f"{manifest['counts']['obsoleteCanonAssets']} 个旧Canon记录及 "
            f"{removed} 个历史媒体文件。"
        )
        return 0
    finally:
        engine.dispose()


def _manifest(session, runtime: RuntimeSettings) -> dict[str, Any]:
    retained_canon, obsolete_canon = _partition_canon_assets(session)
    protected_media_paths = {
        str(Path(item.local_path).expanduser().resolve()) for item in retained_canon
    }
    counts = {
        "runs": session.scalar(select(func.count()).select_from(ProductionRun)),
        "episodes": session.scalar(select(func.count()).select_from(Episode)),
        "steps": session.scalar(select(func.count()).select_from(WorkflowStep)),
        "prompts": session.scalar(select(func.count()).select_from(PromptRecord)),
        "assets": session.scalar(
            select(func.count()).select_from(Asset).where(Asset.production_run_id.is_not(None))
        ),
        "reviews": session.scalar(select(func.count()).select_from(Review)),
        "deliveryPackages": session.scalar(select(func.count()).select_from(DeliveryPackage)),
        "deliveryItems": session.scalar(select(func.count()).select_from(DeliveryItem)),
        "canonAssets": session.scalar(
            select(func.count()).select_from(Asset).where(Asset.scope == "canon")
        ),
        "obsoleteCanonAssets": len(obsolete_canon),
    }
    run_rows = session.execute(
        select(ProductionRun.id, ProductionRun.content_date, ProductionRun.status).order_by(
            ProductionRun.created_at
        )
    ).all()
    asset_rows = list(
        session.execute(
            select(Asset.id, Asset.local_path, Asset.sha256, Asset.role)
            .where(Asset.production_run_id.is_not(None))
            .order_by(Asset.created_at)
        ).all()
    )
    asset_rows.extend((item.id, item.local_path, item.sha256, item.role) for item in obsolete_canon)
    package_rows = session.execute(
        select(
            DeliveryPackage.id, DeliveryPackage.local_path, DeliveryPackage.manifest_sha256
        ).order_by(DeliveryPackage.created_at)
    ).all()
    roots = tuple(
        path.expanduser().resolve()
        for path in (runtime.work_root, runtime.asset_root, runtime.delivery_root)
    )
    retained = [
        _asset_manifest_item(item, roots=roots)
        for item in sorted(retained_canon, key=lambda value: str(value.semantic_key))
    ]
    assets = []
    for asset_id, raw_path, stored_sha, role in asset_rows:
        path = Path(raw_path).expanduser().resolve()
        permitted = any(path.is_relative_to(root) for root in roots)
        exists = path.is_file()
        actual_sha = _sha256(path) if exists and permitted else None
        assets.append(
            {
                "id": str(asset_id),
                "role": role,
                "path": str(path),
                "pathPermitted": permitted,
                "exists": exists,
                "storedSha256": stored_sha,
                "actualSha256": actual_sha,
                "sha256Matches": actual_sha == stored_sha if actual_sha else None,
            }
        )
    return {
        "createdAt": datetime.now(UTC).isoformat(),
        "database": database_safe_name(session),
        "schema": DatabaseSettings.from_env().schema,
        "counts": counts,
        "runs": [
            {"id": str(run_id), "contentDate": value.isoformat(), "status": status}
            for run_id, value, status in run_rows
        ],
        "assets": assets,
        "retainedCanonAssets": retained,
        "missingCurrentCanonKeys": sorted(
            CURRENT_CANON_KEYS - {str(item.semantic_key) for item in retained_canon}
        ),
        "protectedMediaPaths": sorted(protected_media_paths),
        "obsoleteCanonAssets": [
            {
                "id": str(item.id),
                "semanticKey": item.semantic_key,
                "status": item.status,
                "path": str(item.local_path),
                "sha256": item.sha256,
            }
            for item in obsolete_canon
        ],
        "deliveryPackages": [
            {
                "id": str(package_id),
                "path": str(Path(raw_path).expanduser().resolve()),
                "manifestSha256": manifest_sha,
            }
            for package_id, raw_path, manifest_sha in package_rows
        ],
    }


def _partition_canon_assets(session) -> tuple[list[Any], list[Any]]:
    """把Canon分成每个正式语义键的最新批准版本与全部旧版本。"""

    rows = list(
        session.execute(
            select(Asset)
            .where(Asset.scope == "canon")
            .order_by(Asset.semantic_key, Asset.created_at.desc(), Asset.id.desc())
        ).scalars()
    )
    kept: set[str] = set()
    retained: list[Any] = []
    obsolete: list[Any] = []
    for row in rows:
        key = str(row.semantic_key or "")
        approved = row.status in {"approved", "ready"}
        current = approved and key in CURRENT_CANON_KEYS and key not in kept
        if current:
            kept.add(key)
            retained.append(row)
        else:
            obsolete.append(row)
    return retained, obsolete


def _asset_manifest_item(item: Any, *, roots: tuple[Path, ...]) -> dict[str, Any]:
    """生成不含秘密的资产校验项；保留Canon与待删除资产共用同一判定。"""

    path = Path(item.local_path).expanduser().resolve()
    permitted = any(path.is_relative_to(root) for root in roots)
    exists = path.is_file()
    actual_sha = _sha256(path) if exists and permitted else None
    return {
        "id": str(item.id),
        "semanticKey": item.semantic_key,
        "role": item.role,
        "status": item.status,
        "path": str(path),
        "pathPermitted": permitted,
        "exists": exists,
        "storedSha256": item.sha256,
        "actualSha256": actual_sha,
        "sha256Matches": actual_sha == item.sha256 if actual_sha else None,
    }


def _confirmation(manifest: dict[str, Any]) -> str:
    return (
        f"DELETE-{manifest['counts']['runs']}-RUNS-AND-"
        f"{manifest['counts']['obsoleteCanonAssets']}-OLD-CANON"
    )


def database_safe_name(session) -> str:
    """只记录数据库名，不把主机、用户名或密码写入清单。"""

    return str(session.execute(select(func.current_database())).scalar_one())


def _write_manifest(manifest: dict[str, Any]) -> Path:
    root = Path("var/diagnostics").resolve()
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    body = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path = root / f"minimal-contract-cleanup-{timestamp}-{digest[:12]}.json"
    path.write_text(body + "\n", encoding="utf-8")
    return path


def _delete_recorded_media(manifest: dict[str, Any], runtime: RuntimeSettings) -> int:
    roots = tuple(
        path.expanduser().resolve()
        for path in (runtime.work_root, runtime.asset_root, runtime.delivery_root)
    )
    protected = {str(Path(item).resolve()) for item in manifest["protectedMediaPaths"]}
    removed = 0
    for item in manifest["assets"]:
        path = Path(item["path"]).resolve()
        if str(path) in protected:
            continue
        if not any(path.is_relative_to(root) for root in roots):
            continue
        if path.is_file():
            path.unlink()
            removed += 1
    for item in manifest["deliveryPackages"]:
        directory = Path(item["path"]).resolve()
        if not directory.is_relative_to(runtime.delivery_root.expanduser().resolve()):
            continue
        if not directory.is_dir():
            continue
        # 交付目录只删除清单中已确认位于delivery_root下的普通文件和空目录，
        # 不跟随符号链接，也不使用宽泛递归删除。
        paths = sorted(directory.rglob("*"), key=lambda value: len(value.parts), reverse=True)
        for path in paths:
            if path.is_symlink():
                path.unlink()
            elif path.is_file():
                path.unlink()
                removed += 1
            elif path.is_dir():
                path.rmdir()
        directory.rmdir()
    return removed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
