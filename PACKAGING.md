# Packaging Kosha for Windows

> For the macOS build (`Kosha.app` in a `.dmg`) see
> [PACKAGING-macos.md](PACKAGING-macos.md).

Kosha ships as a **onedir** PyInstaller bundle wrapped in an Inno Setup
installer. onedir (not onefile) is deliberate: QtWebEngine's `QtWebEngineProcess.exe`
plus its resource tree are far more reliable unpacked than re-extracted to a temp
directory on every launch.

## 1. Build the app bundle

```powershell
# from the repo root, inside the venv
python -m PyInstaller --noconfirm --clean kosha.spec
```

Output: `dist\Kosha\` (contains `Kosha.exe` + all dependencies, ~670 MB — the
bulk is QtWebEngine/Chromium).

### What the spec handles ([kosha.spec](kosha.spec))

| Risk | How it's bundled |
|------|------------------|
| `kosha/schema.sql` (loaded via `importlib.resources`) | explicit `datas` entry |
| SQLCipher native extension + DLL | `collect_all("sqlcipher3")` |
| Plotly's inlined `plotly.min.js` | `collect_all("plotly")` |
| Argon2 CFFI binding | `collect_all("argon2")` |
| PySide6 QtWebEngine | PySide6 hooks + explicit hidden imports |

## 2. Verify the build

The entry point ([run_kosha.py](run_kosha.py)) has a built-in self-test that
exercises every bundled-dependency risk without opening the (blocking) unlock
dialog:

```powershell
$env:KOSHA_SELFTEST = "1"
$env:KOSHA_SELFTEST_OUT = "$env:TEMP\kosha_selftest.txt"
.\dist\Kosha\Kosha.exe
Get-Content $env:TEMP\kosha_selftest.txt   # expect: SELFTEST OK
```

`SELFTEST OK` means the frozen app can: load native SQLCipher and create an
encrypted DB, read the bundled `schema.sql`, inline Plotly's JS, and start
QtWebEngine.

## 3. Build the installer

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php).

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\kosha.iss
```

Output: `installer\Output\KoshaSetup-<version>.exe`.

The installer ([installer/kosha.iss](installer/kosha.iss)):
- installs per-user by default (no admin required),
- creates Start Menu (and optional desktop) shortcuts,
- on uninstall removes program files **but preserves** the encrypted vault in
  `%APPDATA%\Kosha` — uninstalling must never destroy the user's data.

## Notes

- The vault (`kosha.db`, `kosha.salt`) always lives in `%APPDATA%\Kosha`,
  independent of the install location, so it survives upgrades and reinstalls.
- Nothing in the build or the app makes network calls.
