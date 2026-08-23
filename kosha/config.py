"""Filesystem paths for Kosha.

All persistent data lives in a per-user application-data directory so it
survives app updates and reinstalls:

    Windows  %APPDATA%\\Kosha
    macOS    ~/Library/Application Support/Kosha
    other    ~/.kosha

Nothing here reaches the network.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Kosha"

DB_FILENAME = "kosha.db"
SALT_FILENAME = "kosha.salt"


def _default_base() -> Path:
    """The platform's per-user application-data directory for Kosha."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def data_dir() -> Path:
    """Return the per-user data directory, creating it if needed."""
    override = os.environ.get("KOSHA_DATA_DIR")
    base = Path(override) if override else _default_base()
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def salt_path() -> Path:
    return data_dir() / SALT_FILENAME
