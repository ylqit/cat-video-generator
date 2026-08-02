"""核心Schema和去冗余架构门。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from cat_video_generator.infrastructure.db.models import (
    Asset,
    Episode,
    ProductionRun,
    PromptRecord,
    WorkflowStep,
)
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "cat_video_generator"


def test_core_columns_do_not_duplicate_json_facts() -> None:
    assert {column.name for column in ProductionRun.__table__.c} >= {
        "id",
        "content_date",
        "planning_json",
        "status",
    }
    run_columns = {column.name for column in ProductionRun.__table__.c}
    episode_columns = {column.name for column in Episode.__table__.c}
    step_columns = {column.name for column in WorkflowStep.__table__.c}
    prompt_columns = {column.name for column in PromptRecord.__table__.c}
    asset_columns = {column.name for column in Asset.__table__.c}
    assert "theme" not in run_columns
    assert "plan_json" not in run_columns
    assert "title" not in episode_columns
    assert "video_input_mode" not in episode_columns
    assert "operation_key" in step_columns
    assert "input_snapshot_json" in step_columns
    assert "request_summary_json" not in step_columns
    assert "char_count" not in prompt_columns
    assert "utf8_bytes" not in prompt_columns
    assert "semantic_key" in asset_columns
    assert "pipeline_settings_json" in run_columns
    assert ALEMBIC_HEAD == "0007_pipeline_settings"


def test_deleted_runtime_modules_and_commands_are_absent() -> None:
    removed = (
        "application/resolution_comparison.py",
        "application/multi_clip_finalization.py",
        "application/video_landing.py",
        "domain/media_contracts.py",
        "infrastructure/db/archive_import.py",
    )
    assert all(not (SOURCE / item).exists() for item in removed)
    cli = (SOURCE / "interfaces" / "cli.py").read_text(encoding="utf-8")
    assert "compare-resolution" not in cli
    assert "allow-multi-clip" not in cli
    assert "allow-unverified-keyframes" not in cli


def test_domain_does_not_import_framework_or_io_boundaries() -> None:
    forbidden = {"sqlalchemy", "typer", "fastapi", "volcenginesdkarkruntime"}
    violations: list[str] = []
    for path in (SOURCE / "domain").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [item.name for item in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                if any(name.split(".", 1)[0] in forbidden for name in names):
                    violations.append(f"{path.name}:{node.lineno}")
    assert violations == []


def test_application_services_use_capability_stores() -> None:
    files = [
        SOURCE / "application" / name
        for name in (
            "planning.py",
            "production.py",
            "video_execution.py",
            "visual_preparation.py",
            "queries.py",
        )
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "WorkflowRepository" not in text
    assert "PlanningStore" in text
    assert "ProductionStore" in text
    assert "QueryStore" in text


def test_env_example_has_no_agent_plan_or_accidental_analysis() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ARK_ACCESS_MODE" not in text
    assert "technical_auto" not in text
    assert "Update Todos" not in text


def test_active_documentation_describes_only_core_runtime() -> None:
    active = (
        ROOT / "README.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "architecture" / "ADR-001-explicit-workflow.md",
        ROOT / "docs" / "http-api.md",
        ROOT / "docs" / "workflows" / "complete-production.md",
        ROOT / "docs" / "workflows" / "windows-runbook.md",
        ROOT / "docs" / "providers" / "volcengine-multimodal.md",
    )
    forbidden = (
        "multi_clip",
        "technical_auto",
        "allow-unverified",
        "allow-multi",
        "compare-resolution",
        "ARK_ACCESS_MODE",
    )
    for path in active:
        text = path.read_text(encoding="utf-8")
        assert all(term not in text for term in forbidden), path
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            resolved = (path.parent / target.replace("%20", " ")).resolve()
            assert resolved.exists(), f"{path}: 缺失链接 {target}"
