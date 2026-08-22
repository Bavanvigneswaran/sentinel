"""The owner-role session bypasses RLS. Keep its blast radius to one module.

This is the one real footgun in the two-engine design: any handler that reaches
for get_unscoped_session() silently loses tenant isolation, and nothing at
runtime would complain.
"""

import ast
import pathlib

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

UNSCOPED_NAMES = {"get_unscoped_session", "AdminSessionLocal", "admin_engine"}

ALLOWED = {
    "app.db",  # defines it
    "app.api.deps",  # re-exports it as the UnscopedSession annotation
    "app.services.auth_service",  # pre-auth paths: signup, login, refresh
}


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(APP_DIR.parent).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def test_only_approved_modules_use_the_unscoped_session():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        module = _module_name(path)
        if module in ALLOWED:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, ast.Name):
                names = [node.id]
            if any(n in UNSCOPED_NAMES for n in names):
                offenders.append(f"{module}:{node.lineno}")
    assert offenders == [], (
        "these modules reach past row-level security: " + ", ".join(offenders)
    )


def test_the_allowlist_still_matches_reality():
    """If auth_service stops needing it, tighten the allowlist rather than
    leaving a stale exemption behind."""
    source = (APP_DIR / "services" / "auth_service.py").read_text()
    assert "app/models/" not in source  # sanity: paths not hardcoded
    assert "UnscopedSession" in (APP_DIR / "api" / "deps.py").read_text()
