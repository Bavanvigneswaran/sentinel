# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Sentinel agent.

Driven by build/build.py, which passes the output name through
`SENTINEL_BUILD_NAME` — PyInstaller has no supported way to pass arguments to a
spec, and the alternative (rendering a spec per build) would mean the file
under version control is not the file that runs.

**PyInstaller does not cross-compile.** This spec produces a binary for the
machine executing it and nothing else; there is no target selector here because
there could not be a working one. How Windows and Linux binaries actually get
built is written down in docs/PACKAGING.md.
"""

import os

block_cipher = None

name = os.environ.get("SENTINEL_BUILD_NAME", "sentinel-agent")

a = Analysis(
    ["entrypoint.py"],
    pathex=[],
    binaries=[],
    datas=[],
    # psutil, websockets, httpx and pydantic all have PyInstaller hooks in
    # pyinstaller-hooks-contrib or ship their own, so nothing is listed here.
    # If an import goes missing at runtime the fix belongs here, not in a
    # try/except in the agent.
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The agent is a headless console program. Excluding the GUI and scientific
    # stacks keeps a ~12MB binary from becoming a ~90MB one if any of them ever
    # sneak in transitively.
    excludes=[
        "tkinter",
        "test",
        "unittest",
        "pydoc_data",
        "numpy",
        "matplotlib",
        "PIL",
        "IPython",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is off deliberately. A packed executable is a strong heuristic
    # signal to antivirus engines and to SmartScreen, and this binary is
    # already going to be unsigned — see docs/PACKAGING.md. Saving 4MB is not
    # worth making a monitoring agent look more like malware.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,  # signing is build/signing.py's job, after the build
    entitlements_file=None,
)
