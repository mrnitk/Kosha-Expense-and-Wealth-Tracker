"""Add/edit dialogs for net-worth holdings: assets, liabilities, insurance.

Each dialog edits one record and writes it on Accept, so the calling view just
refreshes afterwards. ``record`` None means "add new".
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
)

from .. import wealth
from ..db import Database
from ..format import format_inr

_MAX_AMOUNT = 10_000_000_000.0      # ₹1,000 crore — generous upper bound


def _amount_box(value: float = 0.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0.0, _MAX_AMOUNT)
    box.setDecimals(2)
    box.setGroupSeparatorShown(True)
    box.setValue(value or 0.0)
    return box


def _owner_combo(db: Database, current: str = "Me") -> QComboBox:
    """Owner picker: the common owners first, then any others already in use.

    Editable, so a name that isn't listed can simply be typed.
    """
    combo = QComboBox()
    combo.setEditable(True)
    in_use = ({a.owner for a in wealth.list_assets(db)}
              | {l.owner for l in wealth.list_liabilities(db)}
              | {p.owner for p in wealth.list_insurance(db)})
    for owner in wealth.OWNERS:
        combo.addItem(owner)
    for owner in sorted(o for o in in_use if o and o not in wealth.OWNERS):
        combo.addItem(owner)
    combo.setCurrentText(current or "Me")
    return combo


class AssetDialog(QDialog):
    """Create or edit one asset (a place money sits)."""

    def __init__(self, db: Database, asset: Optional[wealth.Asset] = None, parent=None):
        super().__init__(parent)
        self._db = db
        self._asset = asset
        self.saved = False
        self.retired_now = False        # True when this save closed the holding
        self.setWindowTitle("Edit asset" if asset else "Add asset")
        self.setMinimumWidth(420)
        self._build()

    def _build(self) -> None:
        form = QFormLayout(self)
        a = self._asset

        self._name = QLineEdit(a.name if a else "")
        self._name.setPlaceholderText("e.g. HDFC-Cash, Stocks(Me), Mine (ELSS)")
        form.addRow("Name:", self._name)

        self._category = QComboBox(); self._category.setEditable(True)
        self._category.addItems(wealth.CATEGORIES)
        self._category.setCurrentText(a.category if a else wealth.CATEGORIES[0])
        form.addRow("Category:", self._category)

        self._asset_type = QComboBox(); self._asset_type.addItems(wealth.ASSET_TYPES)
        if a:
            self._asset_type.setCurrentText(a.asset_type)
        form.addRow("Type:", self._asset_type)

        self._liquidity = QComboBox(); self._liquidity.addItems(wealth.LIQUIDITY)
        if a:
            self._liquidity.setCurrentText(a.liquidity)
        form.addRow("Liquidity:", self._liquidity)

        self._owner = _owner_combo(self._db, a.owner if a else "Me")
        form.addRow("Owner:", self._owner)

        self._invested = _amount_box(a.invested if a else 0.0)
        self._invested.setToolTip("What you put in — used to show gains against current value")
        form.addRow("Invested (₹):", self._invested)

        self._counts = QCheckBox("Counts toward net worth")
        self._counts.setChecked(a.counts_toward_networth if a else True)
        form.addRow("", self._counts)

        self._active = QCheckBox("Active (uncheck to retire, keeping history)")
        self._active.setChecked(a.is_active if a else True)
        form.addRow("", self._active)

        self._notes = QPlainTextEdit(a.notes or "" if a else "")
        self._notes.setFixedHeight(56)
        form.addRow("Notes:", self._notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Give this asset a name.")
            return
        active = self._active.isChecked()
        fields = dict(
            name=name,
            category=self._category.currentText().strip() or "Other",
            asset_type=self._asset_type.currentText(),
            liquidity=self._liquidity.currentText(),
            owner=self._owner.currentText().strip() or "Me",
            invested=self._invested.value(),
            counts_toward_networth=self._counts.isChecked(),
            is_active=active,
            notes=self._notes.toPlainText().strip() or None,
        )
        self.retired_now = False
        if self._asset:
            wealth.update_asset(self._db, self._asset.id, **fields)
            # Closing it: date the closure with a 0 so net worth drops from today
            # rather than the old value lingering.
            if self._asset.is_active and not active:
                wealth.retire_asset(self._db, self._asset.id)
                self.retired_now = True
        else:
            wealth.add_asset(self._db, **fields)
        self.saved = True
        self.accept()


class LiabilityDialog(QDialog):
    """Create or edit one liability (loan / EMI / card outstanding)."""

    def __init__(self, db: Database, liability: Optional[wealth.Liability] = None, parent=None):
        super().__init__(parent)
        self._db = db
        self._liability = liability
        self.saved = False
        self.recorded_outstanding = None      # set when the balance is auto-recorded
        self.retired_now = False              # True when this save closed the loan
        self.setWindowTitle("Edit liability" if liability else "Add liability")
        self.setMinimumWidth(420)
        self._build()

    def _build(self) -> None:
        form = QFormLayout(self)
        l = self._liability

        self._name = QLineEdit(l.name if l else "")
        self._name.setPlaceholderText("e.g. Car loan - HDFC")
        form.addRow("Name:", self._name)

        self._kind = QComboBox(); self._kind.setEditable(True)
        self._kind.addItems(wealth.LIABILITY_KINDS)
        self._kind.setCurrentText(l.kind if l else wealth.LIABILITY_KINDS[1])
        form.addRow("Kind:", self._kind)

        self._owner = _owner_combo(self._db, l.owner if l else "Me")
        form.addRow("Owner:", self._owner)

        self._principal = _amount_box(l.principal if l else 0.0)
        self._principal.setToolTip("Original sanctioned amount")
        form.addRow("Principal (₹):", self._principal)

        self._rate = QDoubleSpinBox()
        self._rate.setRange(0.0, 100.0); self._rate.setDecimals(2); self._rate.setSuffix(" %")
        if l and l.interest_rate:
            self._rate.setValue(l.interest_rate)
        form.addRow("Interest rate:", self._rate)

        self._emi = _amount_box(l.emi_amount if l else 0.0)
        self._emi.setToolTip("Monthly instalment — feeds your total monthly obligations")
        form.addRow("EMI (₹/month):", self._emi)

        self._start = QDateEdit(); self._start.setCalendarPopup(True)
        self._start.setDisplayFormat("yyyy-MM-dd")
        self._start.setDate(_qdate(l.start_date) if l and l.start_date else QDate.currentDate())
        form.addRow("Start date:", self._start)

        self._end = QDateEdit(); self._end.setCalendarPopup(True)
        self._end.setDisplayFormat("yyyy-MM-dd")
        self._end.setDate(_qdate(l.end_date) if l and l.end_date else QDate.currentDate().addYears(5))
        form.addRow("End date:", self._end)

        self._active = QCheckBox("Active (uncheck when closed/paid off)")
        self._active.setChecked(l.is_active if l else True)
        form.addRow("", self._active)

        self._record_estimate = QCheckBox("Work out today's outstanding from these figures")
        self._record_estimate.setToolTip(
            "On save, compute the amortized balance for today from the principal, "
            "interest rate, EMI and start date, and record it as today's value. "
            "Uncheck to enter the outstanding yourself on the Update values tab.")
        self._record_estimate.setChecked(True)
        form.addRow("", self._record_estimate)

        self._notes = QPlainTextEdit(l.notes or "" if l else "")
        self._notes.setFixedHeight(56)
        form.addRow("Notes:", self._notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Give this liability a name.")
            return
        if not self._check_emi_repays():
            return
        fields = dict(
            name=name,
            kind=self._kind.currentText().strip() or "Other",
            owner=self._owner.currentText().strip() or "Me",
            principal=self._principal.value(),
            interest_rate=self._rate.value() or None,
            emi_amount=self._emi.value(),
            start_date=self._start.date().toString("yyyy-MM-dd"),
            end_date=self._end.date().toString("yyyy-MM-dd"),
            notes=self._notes.toPlainText().strip() or None,
        )
        active = self._active.isChecked()
        self.retired_now = False
        if self._liability:
            liability_id = self._liability.id
            wealth.update_liability(self._db, liability_id, is_active=active, **fields)
            if self._liability.is_active and not active:
                wealth.retire_liability(self._db, liability_id)   # paid off: 0 owed
                self.retired_now = True
        else:
            liability_id = wealth.add_liability(self._db, is_active=active, **fields)

        self.recorded_outstanding = None
        if self._record_estimate.isChecked() and not self.retired_now:
            self.recorded_outstanding = wealth.record_estimated_outstanding(
                self._db, liability_id)
        self.saved = True
        self.accept()

    def _check_emi_repays(self) -> bool:
        """Warn if the EMI can never clear the loan. Returns False to stop saving."""
        principal = self._principal.value()
        rate = self._rate.value()
        emi = self._emi.value()
        shortfall = wealth.emi_shortfall(principal, rate, emi)
        if shortfall is None:
            return True
        interest = wealth.monthly_interest(principal, rate)
        months = wealth.months_between(
            self._start.date().toString("yyyy-MM-dd"),
            self._end.date().toString("yyyy-MM-dd"))
        needed = wealth.required_emi(principal, rate, months) if months else None
        detail = (
            f"An EMI of ₹{format_inr(emi)} doesn't cover the monthly interest of "
            f"₹{format_inr(interest)} on ₹{format_inr(principal)} at {rate:.2f}%.\n\n"
            f"The balance would grow by about ₹{format_inr(shortfall)} every month "
            "instead of reducing, so this loan can never be repaid.")
        if needed:
            detail += (f"\n\nTo clear it over {months} months you'd need an EMI of "
                       f"about ₹{format_inr(needed)}.")
        detail += "\n\nSave these figures anyway?"
        return QMessageBox.warning(
            self, "EMI won't repay this loan", detail,
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes


class InsuranceDialog(QDialog):
    """Create one insurance policy (informational — never part of net worth)."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self.saved = False
        self.setWindowTitle("Add insurance policy")
        self.setMinimumWidth(420)
        form = QFormLayout(self)

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Medical - Self - HDFC ergo")
        form.addRow("Name:", self._name)

        self._kind = QComboBox(); self._kind.setEditable(True)
        self._kind.addItems(wealth.INSURANCE_KINDS)
        form.addRow("Kind:", self._kind)

        self._owner = _owner_combo(self._db)
        form.addRow("Owner:", self._owner)

        self._premium = _amount_box()
        form.addRow("Premium (₹/year):", self._premium)

        self._coverage = _amount_box()
        form.addRow("Coverage (₹):", self._coverage)

        self._notes = QPlainTextEdit()
        self._notes.setFixedHeight(56)
        form.addRow("Notes:", self._notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Give this policy a name.")
            return
        wealth.add_insurance(
            self._db, name, self._kind.currentText().strip() or None,
            premium_per_year=self._premium.value(), coverage=self._coverage.value(),
            owner=self._owner.currentText().strip() or "Me",
            notes=self._notes.toPlainText().strip() or None)
        self.saved = True
        self.accept()


def _qdate(iso: str) -> QDate:
    y, m, d = (int(p) for p in iso.split("-")[:3])
    return QDate(y, m, d)
