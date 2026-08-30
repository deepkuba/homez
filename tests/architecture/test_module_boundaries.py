import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "homefinder"
FORBIDDEN_IMPORTS = {
    "domain": {"application", "catalog", "sources", "web"},
    "sources": {"application", "catalog", "web"},
    "catalog": {"application", "sources", "web"},
    "application": {"web"},
}


def test_internal_modules_follow_dependency_boundaries() -> None:
    violations: list[str] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(SOURCE_ROOT)
        owner = relative.parts[0]
        forbidden = FORBIDDEN_IMPORTS.get(owner, set())
        if not forbidden:
            continue

        for imported in _homefinder_imports(path):
            parts = imported.split(".")
            imported_owner = parts[1] if len(parts) > 1 else ""
            if imported_owner in forbidden:
                violations.append(f"{relative}: {owner} imports {imported}")

    assert violations == [], "Architecture boundary violations:\n" + "\n".join(
        violations
    )


def _homefinder_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(
                alias.name
                for alias in node.names
                if alias.name.startswith("homefinder")
            )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("homefinder")
        ):
            imported.add(node.module)
    return imported
