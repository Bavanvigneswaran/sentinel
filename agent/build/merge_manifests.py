"""Combine the per-runner manifests a CI matrix produced into one file.

    python build/merge_manifests.py out/manifest.json artifacts/*/manifest.json

Its own script rather than a `jq` line in the workflow because
`agent_manifest.merge_manifests()` is tested, and a silent drop here is
invisible until a user on that platform finds nothing to download.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_manifest as manifest_mod  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    out = Path(argv[0])
    inputs = [Path(p) for p in argv[1:]]
    manifests = [json.loads(p.read_text()) for p in inputs]

    merged = manifest_mod.merge_manifests(manifests)
    manifest_mod.write(out, merged)

    print(f"Merged {len(inputs)} manifest(s) into {out}:")
    for build in merged["builds"]:
        flag = "signed" if build["signed"] else "UNSIGNED"
        print(f"  {build['os']}/{build['arch']:<6} {build['filename']}  [{flag}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
