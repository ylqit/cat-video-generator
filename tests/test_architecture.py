from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "cat_video_generator"


def _module_name(path: Path) -> str:
    relative = path.relative_to(PROJECT_ROOT / "src").with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _internal_dependencies() -> dict[str, set[str]]:
    paths = list(PACKAGE_ROOT.rglob("*.py"))
    modules = {_module_name(path): path for path in paths}
    dependencies = {module: set() for module in modules}
    for module, path in modules.items():
        package_parts = module.split(".")[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                parent_parts = package_parts[
                    : len(package_parts) - (node.level - 1)
                ]
                target = ".".join(
                    [*parent_parts, *(node.module or "").split(".")]
                ).rstrip(".")
            else:
                target = node.module or ""
            for candidate in modules:
                if target == candidate or target.startswith(candidate + "."):
                    dependencies[module].add(candidate)
                    break
    return dependencies


def test_internal_modules_have_no_dependency_cycles() -> None:
    dependencies = _internal_dependencies()
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str, path: tuple[str, ...]) -> None:
        if module in visiting:
            cycle = " -> ".join((*path, module))
            raise AssertionError(f"Internal dependency cycle: {cycle}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in dependencies[module]:
            visit(dependency, (*path, module))
        visiting.remove(module)
        visited.add(module)

    for module in dependencies:
        visit(module, ())


def test_domain_rules_do_not_depend_on_cli_database_or_ark_sdk() -> None:
    domain_files = (
        "contracts.py",
        "state.py",
        "visual_policy.py",
        "render_plan.py",
    )
    forbidden = {"typer", "sqlalchemy", "volcenginesdk", "ark_provider", "models"}
    for file_name in domain_files:
        tree = ast.parse(
            (PACKAGE_ROOT / file_name).read_text(encoding="utf-8")
        )
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert imported.isdisjoint(forbidden), file_name


def test_cli_is_composition_only_and_runtime_has_no_mock_provider() -> None:
    cli_source = (PACKAGE_ROOT / "cli.py").read_text(encoding="utf-8")
    package_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PACKAGE_ROOT.rglob("*.py")
    )

    assert "sqlalchemy" not in cli_source
    assert "MockMediaProvider" not in package_source
    assert not (PACKAGE_ROOT / "mock_provider.py").exists()
