"""Reading the agent build manifest that CI published.

The whole design question this answers is *what the download page shows when
no build exists for the visitor's operating system*, and the answer follows the
posture the rest of the project already takes to unconfigured integrations —
unset SMTP, VAPID, FCM and ANTHROPIC_API_KEY all degrade visibly rather than
pretending. So: nothing here raises when the manifest is absent, unreadable, or
missing a platform. It returns a catalogue that says why, and the page renders
that instead of a link that 404s.

The manifest is written by `agent/build/build.py` (see `agent_manifest.py`
there for the schema) on a machine this server never sees, so it is validated,
not trusted.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

#: Platforms the download page knows how to talk about. An entry naming
#: anything else is dropped rather than rendered as an unlabelled button —
#: "android" is here because Phase 10b's collector is a real agent, even though
#: it is built by Gradle and never by build.py.
KNOWN_OS = frozenset({"macos", "linux", "windows", "android"})
KNOWN_ARCH = frozenset({"x64", "arm64", "universal"})

_REQUIRED_FIELDS = ("os", "arch", "version", "filename", "size_bytes", "sha256", "signed")


@dataclass(frozen=True, slots=True)
class AgentBuild:
    os: str
    arch: str
    version: str
    filename: str
    size_bytes: int
    sha256: str
    signed: bool
    signing: str
    built_at: datetime | None


@dataclass(frozen=True, slots=True)
class DownloadCatalog:
    """What is on offer, and — when the answer is "nothing" — why.

    `unavailable_reason` is the load-bearing field. A catalogue with no builds
    and no reason would be indistinguishable from a catalogue that failed to
    load, and the page would have nothing honest to say.
    """

    configured: bool
    builds: tuple[AgentBuild, ...]
    generated_at: datetime | None = None
    unavailable_reason: str | None = None


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_build(raw: Any) -> AgentBuild | None:
    """One manifest entry, or None if it cannot be trusted.

    Every check is inside the try. This file is written by CI on a machine the
    server never sees, so "validated, not trusted" has to mean *no* input shape
    reaches an exception: `{"os": []}` makes `raw["os"] not in KNOWN_OS` raise
    TypeError on an unhashable key, which would 500 the catalogue rather than
    degrade it — the one thing this module exists to avoid.

    A filename is checked here rather than at serve time as well, because this
    is the only place a name enters the system: anything with a path separator
    or a parent reference is dropped outright, so the allow-list the file route
    consults can never contain one.
    """
    if not isinstance(raw, dict) or any(f not in raw for f in _REQUIRED_FIELDS):
        return None

    try:
        os_name, arch = raw["os"], raw["arch"]
        if not isinstance(os_name, str) or not isinstance(arch, str):
            return None
        if os_name not in KNOWN_OS or arch not in KNOWN_ARCH:
            return None

        filename = raw["filename"]
        if (
            not isinstance(filename, str)
            or not filename
            or filename != Path(filename).name
            # A backslash is not a path separator on POSIX, so Path().name
            # leaves `..\..\x` intact — harmless (it cannot escape the dist
            # directory) but it has no business being offered as a build.
            or "\\" in filename
            or filename.startswith(".")
        ):
            return None

        return AgentBuild(
            os=os_name,
            arch=arch,
            version=str(raw["version"]),
            filename=filename,
            size_bytes=int(raw["size_bytes"]),
            sha256=str(raw["sha256"]),
            signed=bool(raw["signed"]),
            signing=str(raw.get("signing", "")),
            built_at=_parse_ts(raw.get("built_at")),
        )
    except (TypeError, ValueError):
        return None


def dist_dir() -> Path | None:
    configured = get_settings().agent_dist_dir
    return Path(configured).expanduser() if configured else None


def _read_catalog() -> DownloadCatalog:
    """Synchronous body of load_catalog(); see there for why it is threaded."""
    directory = dist_dir()
    if directory is None:
        return DownloadCatalog(
            configured=False,
            builds=(),
            unavailable_reason=(
                "No agent builds have been published on this server "
                "(AGENT_DIST_DIR is not set)."
            ),
        )

    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.exists():
        # The path goes to the log, not to the client. Every reason string here
        # is rendered verbatim on a page, and naming an absolute server path
        # hands any signed-in user a piece of the deployment's filesystem
        # layout for no benefit — they cannot act on it, and the operator reads
        # the log.
        logger.warning("no agent build manifest at %s", manifest_path)
        return DownloadCatalog(
            configured=True,
            builds=(),
            unavailable_reason=(
                "No agent builds have been published on this server yet."
            ),
        )

    try:
        raw = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.exception("agent build manifest at %s could not be read", manifest_path)
        return DownloadCatalog(
            configured=True,
            builds=(),
            unavailable_reason="The build manifest on this server is unreadable.",
        )

    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        logger.error(
            "agent build manifest schema_version is %r, expected %r",
            raw.get("schema_version") if isinstance(raw, dict) else None,
            SCHEMA_VERSION,
        )
        return DownloadCatalog(
            configured=True,
            builds=(),
            unavailable_reason=(
                "The build manifest on this server was written by an "
                "incompatible version of the build tooling."
            ),
        )

    # Not `entries or []`: a `builds` of 5 would make the comprehension raise
    # "'int' object is not iterable" — same class of crash as an unhashable os.
    entries = raw.get("builds")
    if not isinstance(entries, list):
        if entries is not None:
            logger.error("agent build manifest 'builds' is not a list")
        entries = []

    parsed = [b for b in (_parse_build(e) for e in entries) if b is not None]
    dropped = len(entries) - len(parsed)
    if dropped:
        logger.warning("dropped %d unusable entries from the agent build manifest", dropped)

    return DownloadCatalog(
        configured=True,
        builds=tuple(sorted(parsed, key=lambda b: (b.os, b.arch))),
        generated_at=_parse_ts(raw.get("generated_at")),
        unavailable_reason=None if parsed else "The build manifest lists no usable builds.",
    )


async def load_catalog() -> DownloadCatalog:
    """The catalogue, read fresh.

    Threaded because it is filesystem I/O and CLAUDE.md's "nothing blocking on
    the event loop" applies to a small read as much as to WeasyPrint's layout
    pass. Not cached: CI republishes this file while the server is running, and
    a stale in-process cache would keep offering yesterday's build until a
    restart.
    """
    return await asyncio.to_thread(_read_catalog)


def resolve_artifact(catalog: DownloadCatalog, filename: str) -> Path | None:
    """The on-disk path for a build, or None.

    `filename` is user input, so it is never joined into a path until it has
    matched an entry in the catalogue by exact string equality — an allow-list,
    not a sanitiser. The containment check afterwards is a second line of
    defence in case a symlink inside the dist directory points outward.
    """
    directory = dist_dir()
    if directory is None:
        return None
    if not any(build.filename == filename for build in catalog.builds):
        return None

    candidate = (directory / filename).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError:
        logger.error("build %s resolves outside %s; refusing to serve", filename, directory)
        return None

    return candidate if candidate.is_file() else None


def download_url(build: AgentBuild) -> str:
    """Where the browser should fetch this build from.

    An external base URL wins when configured: streaming multi-megabyte files
    out of the app process is fine for a handful of installs and wrong at any
    scale, so a real deployment points this at a release host.
    """
    base = get_settings().agent_download_base_url
    if base:
        return f"{base.rstrip('/')}/{build.filename}"
    return f"/downloads/agent/{build.filename}"
