"""Frozen entry point.

PyInstaller needs a script, not a console-script entry point. This exists so
the spec has something to analyse and so `multiprocessing`-style re-execution
of the bootloader has a single obvious main.
"""

from sentinel_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
