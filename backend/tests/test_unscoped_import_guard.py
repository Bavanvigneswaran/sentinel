"""The owner-role session bypasses RLS. Keep its blast radius small and stated.

This is the one real footgun in the two-engine design: any handler that reaches
for the unscoped session silently loses tenant isolation, and nothing at runtime
would complain.

Widening ALLOWED is a deliberate act — each entry must carry the reason that
module cannot use a tenant-scoped session.
"""

import ast
import pathlib

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

#: Everything that hands out a connection on the owner role. `UnscopedSession`
#: is the FastAPI annotation wrapping get_unscoped_session — using it is the
#: same act, so the guard has to watch the alias too.
UNSCOPED_NAMES = {
    "get_unscoped_session",
    "UnscopedSession",
    "AdminSessionLocal",
    "admin_engine",
}

ALLOWED: dict[str, str] = {
    "app.db": "defines the engines and the dependency",
    "app.api.deps": "defines the UnscopedSession annotation",
    "app.api.routes.auth": (
        "signup, login and refresh must read rows before any user identity "
        "exists, so there is no tenant to scope by"
    ),
    "app.api.routes.devices": (
        "POST /enroll is authenticated by the one-time code itself; the agent "
        "has no user identity until the code tells us who owns it"
    ),
    "app.ingest.ws": (
        "agents authenticate as a device, not a user, so there is no JWT to "
        "derive a tenant GUC from. The user_id written on every row comes from "
        "the agent token's own record, and the composite FK to "
        "devices (id, user_id) rejects any row that disagrees with the "
        "device's real owner"
    ),
    "app.alerts.evaluator": (
        "the evaluator runs as a periodic background task with no request and "
        "no JWT to derive a tenant GUC from; the one unscoped query enumerates "
        "which users currently have an enabled alert rule, and every "
        "subsequent read or write in the sweep uses a session scoped to that "
        "user via scope_to_user(), no different from a request's own session"
    ),
}


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(APP_DIR.parent).with_suffix("")
    return ".".join(p for p in rel.parts if p != "__init__")


def _modules_using_unscoped() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in APP_DIR.rglob("*.py"):
        module = _module_name(path)
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, ast.Name):
                names = [node.id]
            if any(n in UNSCOPED_NAMES for n in names):
                found.setdefault(module, []).append(node.lineno)
    return found


def test_only_approved_modules_use_the_unscoped_session():
    offenders = {m: lines for m, lines in _modules_using_unscoped().items() if m not in ALLOWED}
    assert offenders == {}, (
        "these modules reach past row-level security without a stated reason: "
        f"{offenders}. Either scope them with get_db() + scope_to_user(), or add "
        "an entry to ALLOWED explaining why they cannot be."
    )


def test_the_allowlist_has_no_stale_entries():
    """An exemption that is no longer used should be removed, not left to rot
    and quietly authorise a future change."""
    used = set(_modules_using_unscoped())
    stale = sorted(set(ALLOWED) - used)
    assert stale == [], f"ALLOWED lists modules that no longer use it: {stale}"


def test_every_exemption_states_a_reason():
    for module, reason in ALLOWED.items():
        assert len(reason) > 20, f"{module} needs a real justification, not {reason!r}"
