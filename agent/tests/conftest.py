"""Make `agent/build/` importable.

The build scripts are not part of the shipped package — they are not in
`[tool.setuptools.packages.find]` and never land in a wheel — but the manifest
logic in them is a contract the backend also depends on, so it is tested rather
than trusted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
