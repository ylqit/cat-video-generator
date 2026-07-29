from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).parents[1]
CURRENT_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "docs" / "architecture" / "ADR-001-explicit-workflow.md",
    ROOT / "docs" / "workflows" / "complete-production.md",
    ROOT / "docs" / "workflows" / "windows-runbook.md",
    ROOT / "docs" / "providers" / "volcengine-multimodal.md",
    ROOT / "docs" / "http-api.md",
)


def test_current_markdown_relative_links_exist() -> None:
    missing: list[str] = []
    for document in CURRENT_DOCS:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#")):
                continue
            path = (document.parent / unquote(target)).resolve()
            if not path.exists():
                missing.append(f"{document}: {target}")
    assert missing == []


def test_examples_and_docs_contain_no_real_credentials() -> None:
    files = (ROOT / ".env.example", *CURRENT_DOCS)
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "123456" not in text
    assert re.search(r"ARK_API_KEY=\S+", text) is None
    assert "CAT_VIDEO_DB_PASSWORD=\n" in text


def test_env_and_runtime_outputs_are_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore
    assert "var/" in ignore
    assert "output/" in ignore
    assert "docs" not in ignore
    assert "docs/设计脚本教程/" in ignore
