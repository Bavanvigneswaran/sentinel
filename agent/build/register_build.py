"""Add an already-built artifact to the download manifest.

    python build/register_build.py --os android --arch arm64 \
        --version 0.1.0 --file ../mobile/android/app/build/outputs/apk/release/app-release.apk

`build.py` handles the three PyInstaller targets itself. This exists for the
one artifact PyInstaller will never produce: Phase 10b's Android collector,
which Gradle builds. Rather than special-casing Android in the download page,
it lands in the same manifest with the same fields and the page treats it like
any other platform.

`--signed` is not inferred. An APK is *always* signed with something — the
question the manifest asks is whether it was signed with a key anyone can
trust, and only the person running this knows that.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

BUILD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BUILD_DIR))

import agent_manifest as manifest_mod  # noqa: E402

DIST_DIR = BUILD_DIR.parent / "dist"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--os", required=True, choices=["macos", "linux", "windows", "android"])
    parser.add_argument("--arch", required=True, choices=["x64", "arm64", "universal"])
    parser.add_argument("--version", required=True)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--name", help="filename to publish it under (default: as built)")
    parser.add_argument("--dist", type=Path, default=DIST_DIR)
    parser.add_argument(
        "--signed",
        action="store_true",
        help="the artifact is signed with a key a user can trust",
    )
    parser.add_argument(
        "--signing",
        default="",
        help="how it was signed, or why it was not — shown on the download page",
    )
    args = parser.parse_args(argv)

    if not args.file.is_file():
        print(f"No such file: {args.file}", file=sys.stderr)
        return 1
    if args.signed and not args.signing:
        print("--signed requires --signing describing the key used.", file=sys.stderr)
        return 1

    published_name = args.name or args.file.name
    if published_name != Path(published_name).name or published_name.startswith("."):
        # The same string becomes the manifest's `filename`, which the backend
        # drops outright if it is not a plain name — so without this the
        # artifact would be copied somewhere odd and then silently never
        # appear on the download page. Fail here, where it is explicable.
        print(
            f"--name must be a plain filename, got {published_name!r}", file=sys.stderr
        )
        return 1

    args.dist.mkdir(parents=True, exist_ok=True)
    published = args.dist / published_name
    if args.file.resolve() != published.resolve():
        shutil.copy2(args.file, published)

    entry = manifest_mod.build_entry(
        published,
        version=args.version,
        os_name=args.os,
        arch=args.arch,
        signed=args.signed,
        signing=args.signing or "unsigned: no signing information was recorded",
    )
    manifest_path = args.dist / manifest_mod.MANIFEST_FILENAME
    manifest_mod.write(
        manifest_path, manifest_mod.merge(manifest_mod.load(manifest_path), entry)
    )

    print(f"Registered {entry.filename} as {args.os}/{args.arch} v{args.version}")
    print(f"SHA-256:  {entry.sha256}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
