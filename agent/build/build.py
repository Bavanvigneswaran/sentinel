"""Build one platform's agent binary, sign it if configured, and record it.

    python build/build.py            # build for THIS machine
    python build/build.py --check    # say what would be built, build nothing

PyInstaller does not cross-compile, and this script does not pretend otherwise:
there is no `--target`, because a Makefile target that silently only ever works
on the machine it was written on is worse than no target at all. The real
answer for the other two platforms is the CI matrix in
`.github/workflows/agent-build.yml`; docs/PACKAGING.md explains the decision
and the manual fallback.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path

BUILD_DIR = Path(__file__).resolve().parent
AGENT_DIR = BUILD_DIR.parent
sys.path.insert(0, str(BUILD_DIR))
sys.path.insert(0, str(AGENT_DIR))

import agent_manifest as manifest_mod  # noqa: E402
import signing  # noqa: E402
from sentinel_agent import __version__  # noqa: E402

DIST_DIR = AGENT_DIR / "dist"
# Not ./build — that is this directory, and PyInstaller would scribble its
# intermediates over the spec and these scripts.
WORK_DIR = AGENT_DIR / ".pyinstaller"
SPEC = BUILD_DIR / "sentinel-agent.spec"


def describe_target() -> tuple[str, str, str]:
    return manifest_mod.os_key(), manifest_mod.arch_key(), __version__


def run_pyinstaller(name: str) -> Path:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError as exc:  # pragma: no cover - environment problem
        raise SystemExit(
            "PyInstaller is not installed. `pip install -e '.[build]'` in agent/."
        ) from exc

    os.environ["SENTINEL_BUILD_NAME"] = name
    pyi.run(
        [
            str(SPEC),
            "--noconfirm",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(WORK_DIR),
            "--log-level",
            "WARN",
        ]
    )

    produced = DIST_DIR / name
    if not produced.exists():  # pragma: no cover - PyInstaller would have raised
        raise SystemExit(f"PyInstaller reported success but {produced} is missing")
    return produced


def smoke_test(binary: Path) -> str:
    """Run the thing we just built.

    A spec file that looks right and a binary that starts are different claims.
    `--version` exercises the bootloader, the frozen stdlib and argparse;
    `sample` would additionally exercise psutil but takes a second per reading,
    so the caller does that separately.
    """
    import subprocess  # noqa: S404, PLC0415

    result = subprocess.run(  # noqa: S603
        [str(binary), "--version"], capture_output=True, text=True, check=False, timeout=120
    )
    if result.returncode != 0:
        raise SystemExit(
            f"the built binary does not run: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="report the target and exit without building"
    )
    parser.add_argument(
        "--clean", action="store_true", help="remove dist/ and the PyInstaller workdir first"
    )
    args = parser.parse_args(argv)

    os_name, arch, version = describe_target()
    name = manifest_mod.artifact_name(version, os_name, arch)

    print(f"Host:     {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Target:   {os_name}/{arch}  —  PyInstaller does not cross-compile")
    print(f"Artifact: {name}")
    if args.check:
        print("\n--check: nothing built.")
        return 0

    if args.clean:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        shutil.rmtree(DIST_DIR, ignore_errors=True)

    binary = run_pyinstaller(name)
    print(f"\nBuilt {binary} ({binary.stat().st_size / 1_048_576:.1f} MB)")
    print(f"Runs:  {smoke_test(binary)}")

    result = signing.sign(binary, os_name)
    print(f"Signing: {result.detail}")

    entry = manifest_mod.build_entry(
        binary,
        version=version,
        os_name=os_name,
        arch=arch,
        signed=result.signed,
        signing=result.detail,
    )
    manifest_path = DIST_DIR / manifest_mod.MANIFEST_FILENAME
    manifest_mod.write(
        manifest_path, manifest_mod.merge(manifest_mod.load(manifest_path), entry)
    )

    print(f"SHA-256: {entry.sha256}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
