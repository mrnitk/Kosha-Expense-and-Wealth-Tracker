#!/usr/bin/env bash
# Build Kosha.app and package it as a .dmg. Run from the repo root:
#
#     ./installer/macos/build_mac.sh
#
# Steps: PyInstaller -> frozen self-test -> disk image.
# Output: dist/mac/Kosha.app and installer/macos/Output/Kosha-<version>-<arch>.dmg
set -euo pipefail

cd "$(dirname "$0")/../.."          # repo root

PY="${PY:-.venv-mac/bin/python}"
[ -x "$PY" ] || { echo "no macOS venv at $PY — see PACKAGING-macos.md" >&2; exit 1; }

VERSION="$("$PY" -c 'import re,pathlib; print(re.search(r"__version__ = \"([^\"]+)\"", pathlib.Path("kosha/__init__.py").read_text()).group(1))')"
ARCH="$(uname -m)"
APP="dist/mac/Kosha.app"
OUTDIR="installer/macos/Output"
DMG="$OUTDIR/Kosha-$VERSION-$ARCH.dmg"

# The icon is generated art, not a checked-in binary — build it if missing.
[ -f installer/macos/Kosha.icns ] || "$PY" installer/macos/make_icns.py

# Keep Spotlight out of the build trees where we can. This is not enough on its
# own (recent macOS re-indexes a freshly written .app regardless), so the loose
# bundle is also removed at the end -- see the note there.
mkdir -p dist build
touch dist/.metadata_never_index build/.metadata_never_index

echo "==> Building Kosha $VERSION ($ARCH)"
"$PY" -m PyInstaller --noconfirm --clean \
    --distpath dist/mac --workpath build/mac kosha-mac.spec

echo "==> Self-test (frozen app)"
SELFTEST_OUT="$(mktemp -t kosha_selftest)"
KOSHA_SELFTEST=1 KOSHA_SELFTEST_OUT="$SELFTEST_OUT" \
    "$APP/Contents/MacOS/Kosha" || true
cat "$SELFTEST_OUT"
grep -q "SELFTEST OK" "$SELFTEST_OUT" || { echo "self-test FAILED" >&2; exit 1; }

# An unsigned bundle assembled by PyInstaller can carry stale/absent signatures
# on the nested Qt frameworks; an ad-hoc signature makes it launchable on the
# machine that built it. Distribution to other Macs still needs a Developer ID.
echo "==> Ad-hoc signing"
codesign --force --deep --sign - "$APP" 2>/dev/null || \
    echo "    (codesign unavailable — bundle left unsigned)"

echo "==> Disk image"
mkdir -p "$OUTDIR"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"     # drag-to-install target
rm -f "$DMG"
hdiutil create -volname "Kosha $VERSION" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

# Launchpad and Spotlight index every .app on disk, wherever it lives, so a
# bundle left behind in dist/ shows up as a second "Kosha" beside the installed
# one and launches an out-of-date build. The dmg already contains it, and the
# self-test above has exercised it, so drop it. KEEP_APP=1 keeps it for
# debugging -- at the cost of that duplicate entry.
if [ -z "${KEEP_APP:-}" ]; then
    rm -rf "$APP"
    APP_NOTE="(removed; set KEEP_APP=1 to keep it)"
fi

echo "==> Done"
echo "    app: $APP ${APP_NOTE:-}"
echo "    dmg: $DMG  ($(du -h "$DMG" | cut -f1))"
