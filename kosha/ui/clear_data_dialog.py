"""Choose what to wipe for a fresh start — expenses, net worth, or everything.

Deleting is irreversible, so the dialog shows exactly what's in the vault and
what each option removes, and asks for a final confirmation.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QMessageBox, QRadioButton, QVBoxLayout,
)

from .. import importer
from ..db import Database

#: (scope, title, what it removes) — order is least to most destructive.
_OPTIONS = [
    ("transactions", "Expense transactions only",
     "Transactions and import history. Keeps your categorization rules, keywords "
     "and accounts, so re-importing statements re-applies them."),
    ("expenses", "All expense data",
     "Transactions, import history, categorization rules, merged keywords and "
     "accounts. Net worth is untouched."),
    ("networth_values", "Net-worth snapshots only",
     "Every dated value. Keeps your assets, liabilities and insurance, so you can "
     "start the history again from scratch."),
    ("networth", "All net-worth data",
     "Assets, liabilities, insurance and every snapshot. Expenses are untouched."),
    ("all", "Everything (full reset)",
     "Both sides: all expense data and all net-worth data. The vault is emptied "
     "but your master password stays the same."),
]

#: Friendly names for the row counts reported afterwards.
_LABELS = {
    "transactions": "transactions", "category_rules": "rules", "accounts": "accounts",
    "assets": "assets", "liabilities": "liabilities", "insurance": "policies",
    "asset_valuations": "asset values", "liability_valuations": "liability values",
}


class ClearDataDialog(QDialog):
    """Pick a scope and confirm. ``cleared`` holds what was removed, or None."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self.cleared = None
        self.setWindowTitle("Clear data")
        self.setMinimumWidth(560)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        counts = importer.data_counts(self._db)
        summary = (f"<b>In this vault:</b> {counts['transactions']} transactions · "
                   f"{counts['category_rules']} rules · {counts['assets']} assets · "
                   f"{counts['liabilities']} liabilities · "
                   f"{counts['asset_valuations'] + counts['liability_valuations']} "
                   "recorded values")
        header = QLabel(summary + "<br><br>Choose what to delete. "
                        "<b>This cannot be undone</b> — take a backup first "
                        "(File ▸ Backup vault) if you might want any of it back.")
        header.setWordWrap(True)
        root.addWidget(header)

        self._buttons: list[tuple[str, QRadioButton]] = []
        for scope, title, detail in _OPTIONS:
            radio = QRadioButton(title)
            root.addWidget(radio)
            note = QLabel(detail)
            note.setWordWrap(True)
            note.setStyleSheet("color: gray; margin-left: 20px; margin-bottom: 6px;")
            root.addWidget(note)
            self._buttons.append((scope, radio))
        self._buttons[0][1].setChecked(True)

        box = QDialogButtonBox(QDialogButtonBox.Cancel)
        delete = box.addButton("Delete", QDialogButtonBox.DestructiveRole)
        delete.clicked.connect(self._on_delete)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def selected_scope(self) -> str:
        for scope, radio in self._buttons:
            if radio.isChecked():
                return scope
        return "transactions"

    def _on_delete(self) -> None:
        scope = self.selected_scope()
        title = next(t for s, t, _d in _OPTIONS if s == scope)
        if QMessageBox.warning(
            self, "Confirm delete",
            f"Delete: {title}?\n\nThis permanently removes the data and cannot be "
            "undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            self.cleared = importer.clear_data(self._db, scope)
        except Exception as exc:
            QMessageBox.critical(self, "Clear failed", str(exc))
            return
        self.accept()


def describe(cleared: dict) -> str:
    """Human summary of what a clear removed."""
    if not cleared:
        return "Nothing to delete — that data was already empty."
    parts = [f"{count} {_LABELS.get(table, table)}" for table, count in cleared.items()]
    return "Removed " + ", ".join(parts) + "."
