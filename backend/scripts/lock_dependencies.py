"""Pin the dependency closure declared in pyproject.toml.

Writes `requirements.txt` (runtime) and `requirements-dev.txt` (the dev extra's
additions on top of it). Run it with `make deps-lock`.

Why this exists rather than `pip freeze`
----------------------------------------
A vulnerability scanner cannot do anything with `fastapi>=0.115`; it needs a
version to match a CVE against. So the scanners need a pinned file, and the
obvious way to produce one is `pip freeze`.

`pip freeze` is wrong here. It reports what is *installed*, and a development
venv accumulates packages that stopped being dependencies — this one still has
`anthropic` in it from before Phase 17 removed the hosted-API calls. Freezing
would declare a dependency on a client the product deliberately does not use,
which is a worse lie in a supply-chain manifest than in ordinary code: it is
the file an auditor reads to learn what this thing links against.

So the closure is walked from pyproject.toml's declared roots outward instead,
resolving each requirement's own `Requires-Dist` metadata from the installed
distribution. Anything not reachable from a declared root does not appear, no
matter what is sitting in site-packages.

The versions still come from what is installed, because that is the only
resolution that has actually been tested. A package reachable from a root but
missing from the venv is reported on stderr and is a failure, not a warning:
a manifest with a hole in it scans clean for the part it omits.
"""

from __future__ import annotations

import sys
import tomllib
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement

BACKEND_DIR = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND_DIR / "pyproject.toml"

RUNTIME_HEADER = """\
# Pinned resolution of the runtime dependency closure declared in pyproject.toml.
#
# pyproject.toml stays the source of truth for which packages this project
# depends on and which *ranges* are acceptable. This file is the exact set of
# versions those ranges resolved to, and exists for two consumers that cannot
# read a range:
#
#   * Vulnerability scanners. Trivy, OWASP Dependency-Check and GitHub's
#     Dependency Review all need a concrete version to match against a CVE.
#     Given only `fastapi>=0.115` they report nothing at all — so before this
#     file existed, a dependency scan of the largest attack surface in the
#     product came back empty and looked like a clean result.
#   * CI. A range means two runs a week apart can install different code, and a
#     test that fails only on the later one is nobody's bug.
#
# DO NOT regenerate this with `pip freeze`. The development venv accumulates
# packages that are no longer dependencies — `anthropic` is still installed
# there from before Phase 17 removed the hosted-API calls, and freezing it back
# in would put a client this product deliberately does not use into the
# supply chain it declares. Use the closure walk instead:
#
#   make deps-lock
#
# which resolves the graph from pyproject.toml's declared roots outward and
# writes both this file and requirements-dev.txt.
#
# Regenerate after any change to pyproject.toml's [project.dependencies].
"""

DEV_HEADER = """\
# Pinned resolution of pyproject.toml's [project.optional-dependencies].dev,
# minus everything already pinned in requirements.txt. Install both:
#
#   pip install -r requirements.txt -r requirements-dev.txt
#
# Regenerate with `make deps-lock`; see requirements.txt for why not pip freeze.

-r requirements.txt
"""


def _closure(roots: list[str], missing: list[str]) -> dict[str, str]:
    """Every installed distribution reachable from `roots`, mapped to its version."""
    seen: set[str] = set()
    found: dict[str, str] = {}

    def visit(spec: str, inherited_extras: frozenset[str]) -> None:
        req = Requirement(spec)
        # A dependency guarded by `; extra == "foo"` is only ours if the parent
        # requirement asked for that extra. Evaluating with extra="" alone
        # would drop e.g. uvicorn[standard]'s uvloop and websockets.
        if req.marker is not None:
            contexts = [{"extra": ""}] + [{"extra": e} for e in inherited_extras]
            if not any(req.marker.evaluate(ctx) for ctx in contexts):
                return

        key = req.name.lower().replace("_", "-")
        if key in seen:
            return
        seen.add(key)

        try:
            dist = distribution(req.name)
        except PackageNotFoundError:
            missing.append(req.name)
            return

        found[dist.metadata["Name"]] = dist.version
        for child in dist.requires or []:
            visit(child, frozenset(req.extras))

    for root in roots:
        visit(root, frozenset())
    return found


def _render(pins: dict[str, str]) -> str:
    return "".join(f"{name}=={pins[name]}\n" for name in sorted(pins, key=str.lower))


def main() -> int:
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    extras = project.get("optional-dependencies", {})

    missing: list[str] = []
    runtime = _closure(list(project.get("dependencies", [])), missing)
    combined = _closure(list(project.get("dependencies", [])) + list(extras["dev"]), missing)

    if missing:
        print(
            "refusing to write an incomplete manifest; not installed: "
            + ", ".join(sorted(set(missing)))
            + "\nrun `make install` first",
            file=sys.stderr,
        )
        return 1

    dev_only = {name: v for name, v in combined.items() if name not in runtime}

    (BACKEND_DIR / "requirements.txt").write_text(RUNTIME_HEADER + "\n" + _render(runtime))
    (BACKEND_DIR / "requirements-dev.txt").write_text(DEV_HEADER + "\n" + _render(dev_only))

    print(f"requirements.txt: {len(runtime)} pinned")
    print(f"requirements-dev.txt: {len(dev_only)} pinned (plus the runtime set)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
