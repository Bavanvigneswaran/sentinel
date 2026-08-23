"""The build manifest: the contract between "a binary was produced" and
"the download page offers it".

Pure, and tested (`tests/test_build_manifest.py`), because the naming and the
merge are the two places a release quietly goes wrong: a mislabelled arch hands
an x64 binary to an Apple Silicon Mac, and a careless merge drops the platforms
built on the *other* CI runners.

`schema_version` exists because the backend validates this file rather than
trusting it — see `backend/app/services/download_service.py`. A manifest is
produced by CI and read by a server; those two things get out of step.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

#: `platform.system()` → the key the web download page matches against.
OS_KEYS = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}

#: Every spelling of the two architectures that actually ship, collapsed. A
#: manifest that says "x86_64" while the browser detects "x64" offers nothing.
ARCH_KEYS = {
    "x86_64": "x64",
    "amd64": "x64",
    "x64": "x64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


class ManifestError(Exception):
    pass


def os_key(system: str | None = None) -> str:
    system = system or platform.system()
    try:
        return OS_KEYS[system]
    except KeyError as exc:
        raise ManifestError(f"no build target for platform {system!r}") from exc


def arch_key(machine: str | None = None) -> str:
    machine = (machine or platform.machine()).lower()
    try:
        return ARCH_KEYS[machine]
    except KeyError as exc:
        raise ManifestError(
            f"unrecognised architecture {machine!r}. Add it to ARCH_KEYS rather "
            f"than letting it be published under a name nothing matches."
        ) from exc


def artifact_name(version: str, os_name: str, arch: str) -> str:
    """`sentinel-agent-0.1.0-macos-arm64`, plus `.exe` on Windows.

    The OS and arch are in the filename and not only in the manifest, because
    the file outlives the page that served it — someone will find it in a
    Downloads folder six months later and need to know what it is.
    """
    suffix = ".exe" if os_name == "windows" else ""
    return f"sentinel-agent-{version}-{os_name}-{arch}{suffix}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class BuildEntry:
    os: str
    arch: str
    version: str
    filename: str
    size_bytes: int
    sha256: str
    signed: bool
    signing: str
    built_at: str
    built_on: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "os": self.os,
            "arch": self.arch,
            "version": self.version,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            # False is not an omission. An unsigned binary triggers Gatekeeper
            # and SmartScreen, and the download page has to say so before the
            # user meets the warning rather than after.
            "signed": self.signed,
            "signing": self.signing,
            "built_at": self.built_at,
            "built_on": self.built_on,
        }


def build_entry(
    path: Path,
    *,
    version: str,
    os_name: str,
    arch: str,
    signed: bool,
    signing: str,
    built_on: str | None = None,
) -> BuildEntry:
    return BuildEntry(
        os=os_name,
        arch=arch,
        version=version,
        filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        signed=signed,
        signing=signing,
        built_at=datetime.now(UTC).isoformat(),
        built_on=built_on or f"{platform.system()} {platform.release()}",
    )


def empty_manifest() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "generated_at": None, "builds": []}


def merge(existing: dict[str, Any] | None, entry: BuildEntry) -> dict[str, Any]:
    """Add or replace one (os, arch) build.

    Replace, not append: rebuilding macos/arm64 must not leave the previous
    one in the list where the download page could still offer it. Other
    platforms' entries are untouched, which is what lets three CI runners each
    merge into the same file.
    """
    manifest = dict(existing or empty_manifest())
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(
            f"manifest schema_version {manifest.get('schema_version')!r} is not "
            f"{SCHEMA_VERSION}; refusing to merge into it"
        )

    builds = [
        b
        for b in manifest.get("builds", [])
        if not (b.get("os") == entry.os and b.get("arch") == entry.arch)
    ]
    builds.append(entry.to_dict())
    builds.sort(key=lambda b: (b["os"], b["arch"]))

    manifest["builds"] = builds
    manifest["generated_at"] = datetime.now(UTC).isoformat()
    return manifest


def merge_manifests(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine the single-entry manifests each CI runner produced.

    PyInstaller does not cross-compile, so a release is assembled from four
    machines that never see each other's output. This is the step that makes
    them one file — and the reason it is a function under test rather than a
    `jq` one-liner in a workflow is that a silent drop here is invisible until
    a user on that platform finds nothing to download.
    """
    combined: dict[tuple[str, str], dict[str, Any]] = {}
    for manifest in manifests:
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ManifestError(
                f"refusing to merge a manifest with schema_version "
                f"{manifest.get('schema_version')!r}"
            )
        for build in manifest.get("builds", []):
            key = (build["os"], build["arch"])
            existing = combined.get(key)
            # Latest build wins, so a re-run of one matrix leg supersedes it.
            if existing is None or build.get("built_at", "") >= existing.get("built_at", ""):
                combined[key] = build

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "builds": sorted(combined.values(), key=lambda b: (b["os"], b["arch"])),
    }


def load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path} is not valid JSON: {exc}") from exc


def write(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
