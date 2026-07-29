from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "src" / "cat_video_generator"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_domain_has_no_framework_or_io_dependencies() -> None:
    forbidden = ("sqlalchemy", "typer", "fastapi", "volcenginesdk", "httpx")
    for path in (PACKAGE / "domain").glob("*.py"):
        imports = _imports(path)
        assert not any(item.startswith(forbidden) for item in imports), (
            f"{path} imports {imports}"
        )


def test_interfaces_do_not_import_models_or_ark_gateway() -> None:
    forbidden = (
        "sqlalchemy",
        "cat_video_generator.infrastructure.db.models",
        "cat_video_generator.infrastructure.ark.gateway",
    )
    for path in (PACKAGE / "interfaces").glob("*.py"):
        imports = _imports(path)
        assert not any(item.startswith(forbidden) for item in imports), (
            f"{path} imports {imports}"
        )


def test_non_migration_modules_stay_below_hard_size_limit() -> None:
    oversized = {
        path: len(path.read_text(encoding="utf-8").splitlines())
        for path in PACKAGE.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > 600
    }
    assert oversized == {}


def test_modules_and_public_application_methods_have_chinese_docs() -> None:
    chinese = re.compile(r"[\u4e00-\u9fff]")
    for path in PACKAGE.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert chinese.search(ast.get_docstring(tree) or ""), path
        if path.parent.name != "application":
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Service"):
                continue
            for method in node.body:
                if isinstance(
                    method, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and not method.name.startswith("_"):
                    assert chinese.search(ast.get_docstring(method) or ""), (
                        path,
                        node.name,
                        method.name,
                    )


def test_legacy_version_dispatch_and_schema_pile_are_gone() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PACKAGE.rglob("*.py")
        if "maintenance.py" not in path.name and "archive_import.py" not in path.name
    )
    assert re.search(r"DailyLifePackV[1-5]|EpisodeSpecV[1-5]", source) is None
    assert not (ROOT / "content" / "schemas").exists() or not any(
        (ROOT / "content" / "schemas").iterdir()
    )
    for name in ("planning_v3.py", "planning_v4.py", "planning_v5.py"):
        assert not (PACKAGE / name).exists()
