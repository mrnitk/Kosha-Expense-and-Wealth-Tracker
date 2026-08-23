# Packaging Kosha for macOS

Kosha ships on macOS as **Kosha.app** inside a drag-to-install `.dmg`, built with
PyInstaller. As on Windows this is a onedir-style bundle, never onefile:
QtWebEngine ships a separate helper application (`QtWebEngineProcess.app`) nested
in the Qt frameworks, and it only works reliably when it stays unpacked on disk.

For the Windows build see [PACKAGING.md](PACKAGING.md).

## 0. Prerequisites

- macOS 11 or later, Xcode command line tools (`xcode-select --install`) — the
  build needs `iconutil`, `codesign`, and `hdiutil`.
- Python 3.14 (same as the Windows build; `sqlcipher3-wheels` ships a macOS
  wheel for it).

A macOS venv, separate from the Windows `.venv` in the same checkout:

```bash
python3 -m venv .venv-mac
.venv-mac/bin/python -m pip install -r requirements.txt
```

## 1. Build everything

```bash
./installer/macos/build_mac.sh
```

That one script builds the app, runs the frozen self-test, ad-hoc signs the
bundle, and produces the disk image:

- `dist/mac/Kosha.app`
- `installer/macos/Output/Kosha-<version>-<arch>.dmg`

The build targets **the architecture of the Mac it runs on** — an Apple Silicon
Mac produces an arm64 build, an Intel Mac an x86_64 one. To ship both, build on
both (or on one machine under Rosetta with an x86_64 Python).

### Running the steps by hand

```bash
.venv-mac/bin/python installer/macos/make_icns.py      # only if the icon is missing
.venv-mac/bin/python -m PyInstaller --noconfirm --clean \
    --distpath dist/mac --workpath build/mac kosha-mac.spec
```

`--distpath dist/mac --workpath build/mac` keeps macOS output from colliding with
a Windows build of the same checkout.

### What the spec handles ([kosha-mac.spec](kosha-mac.spec))

It mirrors the Windows spec's bundling decisions (`schema.sql` as a data file;
`collect_all` for `sqlcipher3`, `plotly`, `argon2`, `openpyxl`, `xlrd`; explicit
QtWebEngine hidden imports) and adds the macOS `BUNDLE` step, which writes the
`Info.plist`: bundle id `com.kosha.kosha`, the version read from
`kosha/__init__.py`, the finance category, and `NSRequiresAquaSystemAppearance`
so the native title bar stays light like the rest of the UI.

## 2. Verify the build

`build_mac.sh` does this for you; to re-run it alone:

```bash
KOSHA_SELFTEST=1 KOSHA_SELFTEST_OUT=/tmp/kosha_selftest.txt \
    dist/mac/Kosha.app/Contents/MacOS/Kosha
cat /tmp/kosha_selftest.txt        # expect: SELFTEST OK
```

`SELFTEST OK` means the frozen app can load native SQLCipher and create an
encrypted DB, read the bundled `schema.sql`, inline Plotly's JS, **render a page
through QtWebEngine** (which proves the helper process launches), and round-trip
an `.xlsx` template.

## 3. Signing and Gatekeeper

The build is **ad-hoc signed** (`codesign --sign -`), which is enough to launch
on the machine that built it but not enough for distribution. A `.dmg` downloaded
by someone else is quarantined, and macOS will report the app as damaged or
untrusted.

Options, in increasing order of polish:

1. **Tell users to bypass it once** — right-click Kosha.app → Open → Open, or:
   ```bash
   xattr -dr com.apple.quarantine /Applications/Kosha.app
   ```
2. **Sign with a Developer ID** (paid Apple Developer account), then notarize:
   ```bash
   codesign --force --deep --options runtime --timestamp \
       --sign "Developer ID Application: NAME (TEAMID)" dist/mac/Kosha.app
   xcrun notarytool submit installer/macos/Output/Kosha-<v>-<arch>.dmg \
       --apple-id <id> --team-id <TEAMID> --password <app-specific-password> --wait
   xcrun stapler staple installer/macos/Output/Kosha-<v>-<arch>.dmg
   ```
   Notarization requires the hardened runtime (`--options runtime`), which in turn
   needs entitlements for PyInstaller's bundled interpreter — wire
   `entitlements_file` in [kosha-mac.spec](kosha-mac.spec) to a plist granting
   `com.apple.security.cs.allow-unsigned-executable-memory` and
   `com.apple.security.cs.disable-library-validation`.

## Notes

- Launchpad and Spotlight index every `.app` anywhere on disk, so a bundle left
  in `dist/` appears as a second "Kosha" beside the one in `/Applications` — and
  launches whatever build happens to be sitting there. The build script therefore
  deletes `dist/mac/Kosha.app` once the disk image is built (the image contains
  it, and the self-test has already exercised it). Run with `KEEP_APP=1` to keep
  the loose bundle for debugging, and expect the duplicate entry while it exists.
  `dist/` and `build/` also carry a `.metadata_never_index` marker, but recent
  macOS indexes a freshly written `.app` regardless, so do not rely on it alone.
- The vault (`kosha.db`, `kosha.salt`) lives in
  `~/Library/Application Support/Kosha`, independent of where the app is
  installed, so it survives upgrades and reinstalls. Deleting the app never
  deletes the vault — remove that folder by hand to erase your data.
- `KOSHA_DATA_DIR` overrides the vault location (used by the tests).
- Nothing in the build or the app makes network calls.
