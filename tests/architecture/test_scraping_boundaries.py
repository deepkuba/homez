import ast
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "homefinder"
PORTALS = {"gratka", "morizon", "otodom", "olx"}


def imports(path: Path) -> set[str]:
    package = ".".join(("homefinder", *path.relative_to(ROOT).parts[:-1]))
    result = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            result.add(module)
            result.update(f"{module}.{alias.name}" for alias in node.names)
    return result


def check_tree(package: str, forbidden: set[str]) -> None:
    directory = ROOT / package
    assert (directory / "__init__.py").exists(), f"missing boundary: {package}"
    pending = list(directory.rglob("*.py"))
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        for dependency in imports(path):
            assert not any(
                dependency == name or dependency.startswith(name + ".")
                for name in forbidden
            ), f"{path.relative_to(ROOT)} imports {dependency}"
            if dependency.startswith("homefinder."):
                target = ROOT.joinpath(*dependency.split(".")[1:])
                for module in (target.with_suffix(".py"), target / "__init__.py"):
                    if module.is_file():
                        pending.append(module)


def test_candidate_benchmark_has_no_network_dependency() -> None:
    check_tree(
        "benchmark",
        {
            "http",
            "httpx",
            "httpx2",
            "requests",
            "urllib",
            "socket",
            "subprocess",
            "homefinder.sources",
            "homefinder.scraper",
            "homefinder.catalog",
            "homefinder.scrape_queue",
            "homefinder.workflow",
        },
    )


def test_portal_parsers_do_not_import_each_other() -> None:
    for portal in PORTALS:
        check_tree(
            f"parsers/{portal}",
            {f"homefinder.parsers.{other}" for other in PORTALS - {portal}},
        )


def test_workers_do_not_import_catalog_orm() -> None:
    check_tree("scraper", {"homefinder.catalog", "sqlalchemy", "psycopg"})


def test_network_guard_follows_indirect_imports(tmp_path, monkeypatch) -> None:
    import sys

    import pytest

    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    (tmp_path / "benchmark").mkdir()
    (tmp_path / "benchmark" / "__init__.py").write_text(
        "from homefinder import bridge\n"
    )
    (tmp_path / "bridge.py").write_text("import socket\n")
    with pytest.raises(AssertionError, match="socket"):
        check_tree("benchmark", {"socket"})
