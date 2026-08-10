"""新内核的依赖方向与已删除运行路径。"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "cat_video_generator"


def _python_files(path: Path):
    return tuple(item for item in path.rglob("*.py") if "__pycache__" not in item.parts)


def test_domain_does_not_import_framework_or_infrastructure() -> None:
    forbidden = {"sqlalchemy", "fastapi", "typer", "cat_video_generator.infrastructure"}
    violations: list[str] = []
    for path in _python_files(PACKAGE / "domain"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(any(name.startswith(prefix) for prefix in forbidden) for name in names):
                violations.append(str(path.relative_to(ROOT)))
    assert not violations


def test_removed_runtime_symbols_do_not_return() -> None:
    forbidden = (
        "SceneContinuity",
        "TrackedEntity",
        "storyboard_reference",
        "image:storyboard",
        "storyboard_review",
        "strict_first_last",
        "video:shot:",
        "shot_tail",
        "multi_clip",
        "per_shot",
    )
    offenders: dict[str, list[str]] = {}
    for root in (PACKAGE, ROOT / "web" / "src"):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".vue"}:
                continue
            text = path.read_text(encoding="utf-8")
            hits = [symbol for symbol in forbidden if symbol in text]
            if hits:
                offenders[str(path.relative_to(ROOT))] = hits
    assert not offenders


def test_obsolete_modules_and_empty_layer_directories_are_absent() -> None:
    absent = (
        PACKAGE / "domain" / "continuity.py",
        PACKAGE / "infrastructure" / "media" / "finalizer.py",
        PACKAGE / "application" / "multi_clip_finalization.py",
        PACKAGE / "generation",
        PACKAGE / "commands",
    )
    assert all(not path.exists() for path in absent)


def test_application_does_not_import_web_frameworks() -> None:
    offenders: list[str] = []
    for path in _python_files(PACKAGE / "application"):
        text = path.read_text(encoding="utf-8")
        if "import fastapi" in text or "import typer" in text or "from fastapi" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders
