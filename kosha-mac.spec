# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Kosha (macOS, .app bundle).

The Windows build ([kosha.spec](kosha.spec)) stays the source of truth for what
has to be bundled; this spec mirrors those choices and adds the macOS-only
BUNDLE step that turns the onedir tree into Kosha.app.

Build from the repo root, inside the macOS venv:

    .venv-mac/bin/python -m PyInstaller --noconfirm --clean \
        --distpath dist/mac --workpath build/mac kosha-mac.spec

The separate dist/work paths keep macOS output from colliding with a Windows
build of the same checkout.

Bundling risks handled explicitly (same as Windows):
  * kosha/schema.sql          - loaded via importlib.resources at runtime
  * sqlcipher3 (+ native .so)  - compiled extension, collected whole
  * plotly package data        - the inlined plotly.min.js
  * PySide6 QtWebEngine        - via PyInstaller's PySide6 hooks + hidden imports

macOS specifics:
  * QtWebEngine ships a helper app (QtWebEngineProcess.app) nested in the
    Qt frameworks; PyInstaller's PySide6 hooks place it, which is why this must
    stay a onedir/BUNDLE build and never a onefile one.
  * The bundle is unsigned. Gatekeeper quarantines unsigned downloads, so the
    README documents the one-time right-click-Open (or xattr) step.
"""

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# Single source of truth for the version: kosha/__init__.py.
_init = Path(SPECPATH) / "kosha" / "__init__.py"
VERSION = re.search(r'__version__ = "([^"]+)"', _init.read_text(encoding="utf-8")).group(1)

ICON = Path(SPECPATH) / "installer" / "macos" / "Kosha.icns"
icon = str(ICON) if ICON.exists() else None

datas = [("kosha/schema.sql", "kosha")]
binaries = []
hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtPrintSupport",
    "PySide6.QtSvg",
    "et_xmlfile",            # openpyxl dependency, imported indirectly
]

# Collect data/binaries/submodules for packages PyInstaller can't fully trace.
# openpyxl/xlrd back the template importer (.xlsx/.xls) and are imported lazily,
# so PyInstaller's static scan can miss them without an explicit collect.
for pkg in ("plotly", "sqlcipher3", "argon2", "openpyxl", "xlrd"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["run_kosha.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "xlwt", "tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Kosha",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app: no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,   # off: Kosha opens no documents from Finder
    target_arch=None,       # build for whatever the host Mac is (arm64 or x86_64)
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Kosha",
)

app = BUNDLE(
    coll,
    name="Kosha.app",
    icon=icon,
    bundle_identifier="com.kosha.kosha",
    version=VERSION,
    info_plist={
        "CFBundleName": "Kosha",
        "CFBundleDisplayName": "Kosha",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSApplicationCategoryType": "public.app-category.finance",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # Kosha always renders light (see kosha/ui/theme.py). This keeps the
        # native title bar light too instead of following macOS dark mode.
        "NSRequiresAquaSystemAppearance": True,
        "NSHumanReadableCopyright": "Kosha — MIT licensed. Fully local; no network access.",
    },
)
