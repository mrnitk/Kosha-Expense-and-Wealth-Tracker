"""Headless (offscreen) tests for the Phase-3 UI widgets."""

from __future__ import annotations

import pytest

from kosha import categorization as cat
from kosha import crypto
from kosha.db import Database
from kosha.ui.categorization_view import CategorizationView
from kosha.ui.main_window import MainWindow
from kosha.ui.rules_view import RulesView
from kosha.ui.unlock import UnlockDialog

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"

pytestmark = pytest.mark.usefixtures("qapp")


def _make_db(tmp_path) -> Database:
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    seed = [
        (1, "SWIGGY LIMITED", 300.0, "debit"),
        (2, "SWIGGY LIMITED", 200.0, "debit"),
        (3, "ZERODHA", 5000.0, "debit"),
        (4, "ACME EMPLOYER", 90000.0, "credit"),
    ]
    for tid, kw, amt, direction in seed:
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (tid, "2026-04-01", f"D {kw}", amt, direction, kw, f"h{tid}"),
        )
    con.commit()
    return d


def test_view_lists_uncategorized_ranked(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    # All unreviewed keywords, ranked by amount: ACME (90000) > ZERODHA > SWIGGY.
    assert view._table.rowCount() == 3
    assert view._table.item(0, 0).text() == "ACME EMPLOYER"
    db.lock()


def test_view_assign_creates_rule_and_drops_row(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    before = view._table.rowCount()

    view.assign("SWIGGY LIMITED", "Expense", "Food")

    assert view._table.rowCount() == before - 1
    rules = {r.keyword: r for r in cat.list_rules(db)}
    assert rules["SWIGGY LIMITED"].sub_category == "Food"
    # Totals panel shows the fixed categories.
    totals = [view._totals.item(r, 0).text() for r in range(view._totals.rowCount())]
    assert "Expense" in totals and "Income" in totals
    db.lock()


def test_bulk_assign_multiple_keywords(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    view.assign(["SWIGGY LIMITED", "ZERODHA"], "Expense", "Misc")
    keywords = {r.keyword for r in cat.list_rules(db) if r.sub_category == "Misc"}
    assert keywords == {"SWIGGY LIMITED", "ZERODHA"}
    db.lock()


def test_selected_keywords_tracks_selection(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    view._table.selectRow(0)
    assert view.selected_keywords() == ["ACME EMPLOYER"]
    db.lock()


def test_keyword_detail_shows_transactions(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    # Select the SWIGGY row (has 2 transactions) and check the drill-in panel.
    for r in range(view._table.rowCount()):
        if view._table.item(r, 0).text() == "SWIGGY LIMITED":
            view._table.selectRow(r)
            break
    assert view._detail.rowCount() == 2
    db.lock()


def test_filter_box_narrows_keywords(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    assert view._table.rowCount() == 3
    view._filter_text.setText("swig")          # case-insensitive substring
    assert view._table.rowCount() == 1
    assert view._table.item(0, 0).text() == "SWIGGY LIMITED"
    view._filter_text.clear()
    assert view._table.rowCount() == 3
    db.lock()


def test_filter_by_category_dropdown(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    idx = view._filter_category.findData("Income")
    view._filter_category.setCurrentIndex(idx)
    # Only ACME EMPLOYER (a credit) defaults to Income.
    assert view._table.rowCount() == 1
    assert view._table.item(0, 0).text() == "ACME EMPLOYER"
    db.lock()


def test_assign_with_exclude_flag(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    view.assign("SWIGGY LIMITED", "Expense", "Food", excluded=True)
    rule = {r.keyword: r for r in cat.list_rules(db)}["SWIGGY LIMITED"]
    assert rule.excluded is True
    db.lock()


def test_category_column_renamed(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    assert view._table.horizontalHeaderItem(4).text() == "Category"
    db.lock()


def test_sub_category_totals_and_data_summary_present(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    view.refresh()
    subs = [
        (view._sub_totals.item(r, 0).text(), view._sub_totals.item(r, 1).text())
        for r in range(view._sub_totals.rowCount())
    ]
    assert ("Expense", "Food") in subs
    assert "2026-04-01" in view._data_summary.text()
    db.lock()


def test_review_row_assign_is_direction_scoped(tmp_path):
    db = _make_db(tmp_path)
    # Make SWIGGY bidirectional: add a refund (credit) alongside the debits.
    db.connection.execute(
        "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash) "
        "VALUES (99,'2026-04-02','REFUND SWIGGY',150,'credit',1,'SWIGGY LIMITED','h99')")
    db.connection.commit()

    view = CategorizationView(db)
    # Two SWIGGY slices now exist (debit + credit); select the credit one.
    target = None
    for r in range(view._table.rowCount()):
        ks = view._keywords[r]
        if ks.keyword == "SWIGGY LIMITED" and ks.direction == "credit":
            target = r
            break
    assert target is not None
    view._table.selectRow(target)
    view._category.setCurrentIndex(view._category.findData("Income"))
    view._sub_category.setText("Refund")
    view._on_assign()

    rules = {(r.keyword, r.direction): r for r in cat.list_rules(db)}
    assert rules[("SWIGGY LIMITED", "credit")].sub_category == "Refund"
    # The debit slice is untouched and still pending review.
    pending = {(k.keyword, k.direction) for k in cat.unreviewed_keywords(db)}
    assert ("SWIGGY LIMITED", "debit") in pending
    assert ("SWIGGY LIMITED", "credit") not in pending
    db.lock()


def test_rules_view_edits_a_mapping(tmp_path):
    db = _make_db(tmp_path)
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    view = RulesView(db)
    # Find and select the SWIGGY rule row.
    target = next(i for i, r in enumerate(view._rules) if r.keyword == "SWIGGY LIMITED")
    view._table.selectRow(target)
    assert view._sub_category.text() == "Food"
    view._sub_category.setText("Dining")
    view._on_save()
    rule = {r.keyword: r for r in cat.list_rules(db)}["SWIGGY LIMITED"]
    assert rule.sub_category == "Dining"
    db.lock()


def test_rules_view_filter_by_column(tmp_path):
    db = _make_db(tmp_path)
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    cat.add_rule(db, "ZERODHA", "Savings", "Investments")
    view = RulesView(db)
    # Filter by Sub-category = "Food" should match only the SWIGGY rule.
    view._filter_col.setCurrentIndex(view._filter_col.findData("Sub-category"))
    view._filter.setText("Food")
    kws = {view._table.item(r, 0).text() for r in range(view._table.rowCount())}
    assert kws == {"SWIGGY LIMITED"}
    # Same text under Keyword column matches nothing.
    view._filter_col.setCurrentIndex(view._filter_col.findData("Keyword"))
    assert view._table.rowCount() == 0
    db.lock()


def test_rules_view_deletes_a_mapping(tmp_path):
    db = _make_db(tmp_path)
    cat.add_rule(db, "SWIGGY LIMITED", "Expense", "Food")
    view = RulesView(db)
    target = next(i for i, r in enumerate(view._rules) if r.keyword == "SWIGGY LIMITED")
    view._table.selectRow(target)
    # _on_delete pops a modal confirm; exercise the engine + refresh path instead.
    cat.delete_rule(db, view._rules[target].id)
    view.refresh()
    assert "SWIGGY LIMITED" not in {r.keyword for r in cat.list_rules(db)}
    assert view._table.rowCount() == 0
    db.lock()


def test_main_window_has_rules_tab(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    titles = [win._tabs.tabText(i) for i in range(win._tabs.count())]
    assert titles == ["Expenses", "Net worth"]          # two top-level sections
    sub = [win._expense_tabs.tabText(i) for i in range(win._expense_tabs.count())]
    assert sub == ["Dashboard", "Categorize", "Rules", "Recurring"]
    nw = [win._wealth._tabs.tabText(i) for i in range(win._wealth._tabs.count())]
    assert nw == ["Holdings", "Update values", "Net worth"]
    win.close()


def test_dashboard_has_source_and_subcategory_columns(tmp_path):
    db = _make_db(tmp_path)
    from kosha.ui.dashboard_view import DashboardView
    dash = DashboardView(db)
    headers = [dash._table.horizontalHeaderItem(c).text() for c in range(dash._table.columnCount())]
    assert "Source" in headers and "Sub-category" in headers
    db.lock()


def test_dashboard_defaults_to_last_six_months(tmp_path):
    from datetime import date
    from kosha.ui.dashboard_view import DashboardView, _months_back
    db = _make_db(tmp_path)
    dash = DashboardView(db)
    flt = dash.current_filter()
    assert flt.end == date.today()
    assert flt.start == _months_back(date.today(), 6)
    db.lock()


def test_ignore_button_excludes_keyword(tmp_path):
    db = _make_db(tmp_path)
    view = CategorizationView(db)
    target = next(i for i, ks in enumerate(view._keywords) if ks.keyword == "SWIGGY LIMITED")
    view._table.selectRow(target)
    view._on_ignore()
    from kosha import categorization as cat
    assert "SWIGGY LIMITED" not in {k.keyword for k in cat.unreviewed_keywords(db)}
    db.lock()


def test_fit_columns_avoids_resizetocontents(tmp_path):
    # ResizeToContents on a visible table re-measures every column on each
    # setItem (O(n^2)) and froze the app; fit_columns must use Interactive.
    from PySide6.QtWidgets import QHeaderView, QTableWidget
    from kosha.ui.uihelp import fit_columns
    t = QTableWidget(0, 4)
    fit_columns(t, stretch_col=1)
    hh = t.horizontalHeader()
    assert hh.sectionResizeMode(1) == QHeaderView.Stretch
    for c in (0, 2, 3):
        assert hh.sectionResizeMode(c) == QHeaderView.Interactive


def test_rules_view_handles_many_rules_fast(tmp_path):
    # Regression for the 27s hang: populating a large rules table must be quick.
    import time
    from kosha.ui.rules_view import RulesView
    db = _make_db(tmp_path)
    for i in range(300):
        cat.add_rule(db, f"MERCHANT{i}", "Expense", "Misc")
    view = RulesView(db)
    start = time.perf_counter()
    view.refresh()
    elapsed = time.perf_counter() - start
    assert view._table.rowCount() == 300
    assert elapsed < 5.0, f"rules refresh too slow: {elapsed:.1f}s"
    db.lock()


def test_dashboard_lazy_refresh(tmp_path):
    from kosha.ui.dashboard_view import DashboardView
    db = _make_db(tmp_path)
    dash = DashboardView(db)
    assert dash._dirty is False
    dash.mark_dirty()
    assert dash._dirty is True
    dash.refresh_if_dirty()
    assert dash._dirty is False          # rebuilt, flag cleared
    db.lock()


def test_categorize_marks_dashboard_dirty_not_rebuilt(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    win._expense_tabs.setCurrentWidget(win._view)   # leave the dashboard sub-tab
    win._dashboard.refresh()                        # start clean
    assert win._dashboard._dirty is False
    # Assigning on the Categorize tab should only flag the dashboard, not rebuild.
    win._view.assign("SWIGGY LIMITED", "Expense", "Food")
    assert win._dashboard._dirty is True
    # Returning to the dashboard sub-tab clears it (rebuilds once).
    win._expense_tabs.setCurrentWidget(win._dashboard)
    assert win._dashboard._dirty is False
    win.close()


def test_main_window_has_recurring_tab_and_backup(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    titles = [win._tabs.tabText(i) for i in range(win._tabs.count())]
    assert titles == ["Expenses", "Net worth"]          # two top-level sections
    sub = [win._expense_tabs.tabText(i) for i in range(win._expense_tabs.count())]
    assert sub == ["Dashboard", "Categorize", "Rules", "Recurring"]
    nw = [win._wealth._tabs.tabText(i) for i in range(win._wealth._tabs.count())]
    assert nw == ["Holdings", "Update values", "Net worth"]
    file_menu = next(m for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))
                     if "File" in m.title())
    labels = [a.text() for a in file_menu.actions()]
    assert any("Backup" in t for t in labels) and any("Restore" in t for t in labels)
    win.close()


def test_dashboard_edit_deletes_and_refreshes(tmp_path, monkeypatch):
    # Regression: a per-txn delete must refresh the table. The bug was
    # `dlg.exec() == dlg.Accepted` crashing on instance enum access in PySide6,
    # so refresh() never ran and the deleted row stayed on screen.
    from kosha.ui.dashboard_view import DashboardView
    from kosha import categorization as cat
    import kosha.ui.transaction_editor as te
    db = _make_db(tmp_path)
    dash = DashboardView(db)
    before = dash._table.rowCount()
    assert before >= 1
    target_id = dash._table.item(0, 0).data(256)     # Qt.UserRole

    class _FakeEditor:
        def __init__(self, db, ids, parent=None):
            self._db, self._ids = db, ids
            self.changed = False
        def exec(self):
            cat.delete_transactions(self._db, self._ids)   # what Delete does
            self.changed = True
            return 1
    monkeypatch.setattr(te, "TransactionEditor", _FakeEditor)

    dash._table.selectRow(0)
    dash._on_edit_txn()
    assert dash._table.rowCount() == before - 1
    ids_now = [dash._table.item(r, 0).data(256) for r in range(dash._table.rowCount())]
    assert target_id not in ids_now
    db.lock()


def test_dashboard_has_search_box(tmp_path):
    from kosha.ui.dashboard_view import DashboardView
    db = _make_db(tmp_path)
    dash = DashboardView(db)
    dash._search.setText("swiggy")
    assert dash.current_filter().search == "swiggy"
    db.lock()


def test_asset_dialog_save_creates_asset(tmp_path):
    """Regression: Save did nothing because add_asset() rejected is_active."""
    from kosha import wealth
    from kosha.ui.wealth_dialogs import AssetDialog
    db = _make_db(tmp_path)
    dlg = AssetDialog(db)
    dlg._name.setText("HDFC-Cash")
    dlg._category.setCurrentText("Bank")
    dlg._asset_type.setCurrentText("Cash")
    dlg._liquidity.setCurrentText("High")
    dlg._owner.setCurrentText("Mom")
    dlg._invested.setValue(1000)
    dlg._on_save()
    assert dlg.saved is True
    assets = wealth.list_assets(db)
    assert len(assets) == 1
    a = assets[0]
    assert (a.name, a.owner, a.invested, a.is_active) == ("HDFC-Cash", "Mom", 1000, True)
    db.lock()


def test_asset_dialog_can_create_inactive(tmp_path):
    from kosha import wealth
    from kosha.ui.wealth_dialogs import AssetDialog
    db = _make_db(tmp_path)
    dlg = AssetDialog(db)
    dlg._name.setText("Closed FD")
    dlg._active.setChecked(False)
    dlg._on_save()
    assert wealth.list_assets(db)[0].is_active is False
    db.lock()


def test_asset_dialog_edits_existing(tmp_path):
    from kosha import wealth
    from kosha.ui.wealth_dialogs import AssetDialog
    db = _make_db(tmp_path)
    aid = wealth.add_asset(db, "Old", "Bank", "Cash", "High")
    asset = wealth.list_assets(db)[0]
    dlg = AssetDialog(db, asset)
    dlg._name.setText("Renamed")
    dlg._on_save()
    assets = wealth.list_assets(db)
    assert len(assets) == 1 and assets[0].name == "Renamed" and assets[0].id == aid
    db.lock()


def test_asset_dialog_requires_name(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from kosha import wealth
    from kosha.ui.wealth_dialogs import AssetDialog
    db = _make_db(tmp_path)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    dlg = AssetDialog(db)
    dlg._on_save()                      # no name entered
    assert dlg.saved is False and wealth.list_assets(db) == []
    db.lock()


def test_liability_dialog_save_creates_liability(tmp_path):
    from kosha import wealth
    from kosha.ui.wealth_dialogs import LiabilityDialog
    db = _make_db(tmp_path)
    dlg = LiabilityDialog(db)
    dlg._name.setText("Car loan - HDFC")
    dlg._kind.setCurrentText("Car loan")
    dlg._principal.setValue(800000)
    dlg._emi.setValue(16000)
    dlg._rate.setValue(9.5)
    dlg._on_save()
    assert dlg.saved is True
    liabs = wealth.list_liabilities(db)
    assert len(liabs) == 1
    assert (liabs[0].name, liabs[0].emi_amount, liabs[0].is_active) == \
           ("Car loan - HDFC", 16000, True)
    assert wealth.monthly_obligations(db) == 16000
    db.lock()


def test_liability_dialog_inactive_is_not_counted(tmp_path):
    from kosha import wealth
    from kosha.ui.wealth_dialogs import LiabilityDialog
    db = _make_db(tmp_path)
    dlg = LiabilityDialog(db)
    dlg._name.setText("Paid-off loan")
    dlg._emi.setValue(5000)
    dlg._active.setChecked(False)
    dlg._on_save()
    assert wealth.list_liabilities(db)[0].is_active is False
    assert wealth.monthly_obligations(db) == 0        # closed loans don't count
    db.lock()


def test_liability_dialog_warns_when_emi_cannot_repay(tmp_path, monkeypatch):
    """The user's 10L @ 8% with a 3,000 EMI can never amortize — warn before saving."""
    from PySide6.QtWidgets import QMessageBox
    from kosha import wealth
    from kosha.ui.wealth_dialogs import LiabilityDialog
    db = _make_db(tmp_path)
    seen = {}

    def _capture(parent, title, text, *a, **k):
        seen["title"], seen["text"] = title, text
        return QMessageBox.Cancel                 # user backs out
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_capture))

    dlg = LiabilityDialog(db)
    dlg._name.setText("Car")
    dlg._principal.setValue(1000000)
    dlg._rate.setValue(8.0)
    dlg._emi.setValue(3000)
    dlg._on_save()
    assert dlg.saved is False and wealth.list_liabilities(db) == []   # not saved
    assert "repay" in seen["title"].lower()
    assert "doesn't cover the monthly interest" in seen["text"]

    # Saying yes saves it anyway (the user may know better than the model).
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg._on_save()
    assert dlg.saved is True and len(wealth.list_liabilities(db)) == 1
    db.lock()


def test_liability_dialog_saves_quietly_with_sane_emi(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from kosha import wealth
    from kosha.ui.wealth_dialogs import LiabilityDialog
    db = _make_db(tmp_path)
    called = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: called.append(1) or QMessageBox.Yes))
    dlg = LiabilityDialog(db)
    dlg._name.setText("Car loan")
    dlg._principal.setValue(1000000)
    dlg._rate.setValue(8.0)
    dlg._emi.setValue(19000)                      # comfortably above interest
    dlg._on_save()
    assert dlg.saved is True and not called       # no warning shown
    assert len(wealth.list_liabilities(db)) == 1
    db.lock()


def test_liability_dialog_records_outstanding_on_save(tmp_path):
    """Saving a loan with full terms computes and stores today's outstanding."""
    from PySide6.QtCore import QDate
    from kosha import wealth
    from kosha.ui.wealth_dialogs import LiabilityDialog
    db = _make_db(tmp_path)
    dlg = LiabilityDialog(db)
    dlg._name.setText("Car")
    dlg._principal.setValue(1000000)
    dlg._rate.setValue(8.0)
    dlg._emi.setValue(50000)
    dlg._start.setDate(QDate(2026, 1, 1))
    assert dlg._record_estimate.isChecked()          # on by default
    dlg._on_save()
    assert dlg.saved and dlg.recorded_outstanding is not None
    # It's a real snapshot, so net worth already reflects the debt.
    point = wealth.current_networth(db)
    assert point.liabilities == dlg.recorded_outstanding
    db.lock()


def test_liability_dialog_can_skip_auto_outstanding(tmp_path):
    from kosha import wealth
    from kosha.ui.wealth_dialogs import LiabilityDialog
    db = _make_db(tmp_path)
    dlg = LiabilityDialog(db)
    dlg._name.setText("Car")
    dlg._principal.setValue(1000000)
    dlg._rate.setValue(8.0)
    dlg._emi.setValue(50000)
    dlg._record_estimate.setChecked(False)
    dlg._on_save()
    assert dlg.saved and dlg.recorded_outstanding is None
    assert wealth.snapshot_dates(db) == []           # nothing recorded
    db.lock()


def test_holdings_shows_estimate_when_no_snapshot(tmp_path):
    """A loan with no recorded outstanding shows an estimate, not a bare 0.00."""
    from datetime import date
    from kosha import wealth
    from kosha.ui.wealth_view import WealthView
    db = _make_db(tmp_path)
    wealth.add_liability(db, "Car loan", "Car loan", principal=800000,
                         interest_rate=9.5, emi_amount=16800,
                         start_date=date(2026, 7, 1))
    view = WealthView(db)
    shown = view._liab_table.item(0, 6).text()
    assert "est." in shown and "0.00" != shown
    db.lock()


def test_holdings_marks_closed_assets(tmp_path):
    """A closed holding must not show a live-looking current value."""
    from datetime import date
    from kosha import wealth
    from kosha.ui.wealth_view import WealthView
    db = _make_db(tmp_path)
    fd = wealth.add_asset(db, "FD", "Bank", "Debt", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {fd: 500000})
    wealth.update_asset(db, fd, is_active=False)
    view = WealthView(db)
    assert view._assets_table.item(0, 6).text() == "closed"   # current value column
    assert view._assets_table.item(0, 8).text() == "closed"   # active column
    db.lock()


def test_insurance_dialog_save(tmp_path):
    from kosha import wealth
    from kosha.ui.wealth_dialogs import InsuranceDialog
    db = _make_db(tmp_path)
    dlg = InsuranceDialog(db)
    dlg._name.setText("Medical - Self")
    dlg._premium.setValue(16000)
    dlg._coverage.setValue(2000000)
    dlg._on_save()
    assert dlg.saved is True
    assert wealth.insurance_summary(db) == (16000, 2000000)
    db.lock()


def test_owner_combo_offers_me_mom_wife(tmp_path):
    from kosha.ui.wealth_dialogs import AssetDialog
    db = _make_db(tmp_path)
    dlg = AssetDialog(db)
    owners = [dlg._owner.itemText(i) for i in range(dlg._owner.count())]
    assert owners[:3] == ["Me", "Mom", "Wife"]
    db.lock()


def test_wealth_view_add_asset_appears_in_table(tmp_path):
    """End-to-end: the Holdings table shows an asset saved from the dialog."""
    from kosha import wealth
    from kosha.ui.wealth_view import WealthView
    db = _make_db(tmp_path)
    view = WealthView(db)
    assert view._assets_table.rowCount() == 0
    wealth.add_asset(db, "SBI-FD/RD", "Bank", "Debt", "High", owner="Wife")
    view.refresh()
    assert view._assets_table.rowCount() == 1
    assert view._assets_table.item(0, 0).text() == "SBI-FD/RD"
    assert view._assets_table.item(0, 4).text() == "Wife"
    db.lock()


def test_main_window_has_template_actions(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    file_menu = next(m for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))
                     if "File" in m.title())
    labels = [a.text() for a in file_menu.actions()]
    assert any("Import from" in t and "template" in t.lower() for t in labels)
    assert any("Download" in t and "template" in t.lower() for t in labels)
    win.close()


def test_clear_data_dialog_offers_networth_scopes(tmp_path):
    from datetime import date
    from kosha import wealth
    from kosha.ui.clear_data_dialog import ClearDataDialog, describe
    db = _make_db(tmp_path)
    aid = wealth.add_asset(db, "FD", "Bank", "Debt", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {aid: 500000})

    dlg = ClearDataDialog(db)
    scopes = [scope for scope, _radio in dlg._buttons]
    assert scopes == ["transactions", "expenses", "networth_values", "networth", "all"]
    assert dlg.selected_scope() == "transactions"          # safest default

    # Choosing a net-worth scope clears only that side.
    next(r for s, r in dlg._buttons if s == "networth").setChecked(True)
    assert dlg.selected_scope() == "networth"
    from kosha import importer
    removed = importer.clear_data(db, dlg.selected_scope())
    assert wealth.list_assets(db) == []
    assert db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 4
    assert "assets" in describe(removed)
    db.lock()


def test_main_window_has_clear_action(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    file_menu = next(m for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))
                     if "File" in m.title())
    labels = [a.text() for a in file_menu.actions()]
    assert any("Clear all data" in t for t in labels)
    win.close()


def test_main_window_status_and_menus(tmp_path):
    db = _make_db(tmp_path)
    win = MainWindow(db)
    assert "4 transactions" in win.statusBar().currentMessage()
    menus = [m.title() for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))]
    assert any("File" in t for t in menus)
    assert any("Security" in t for t in menus)     # change password / lock
    # The View menu holds the privacy mask only — the theme options stayed removed
    # (the app is light-only).
    view = next(m for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))
                if "View" in m.title())
    labels = [a.text() for a in view.actions()]
    assert any("Hide amounts" in t for t in labels)
    assert not any("Theme" in t for t in labels)
    win.close()                      # triggers db.lock via closeEvent
    assert not db.is_unlocked


def test_privacy_mask_toggle_masks_amounts(tmp_path):
    from kosha import format as fmt
    db = _make_db(tmp_path)
    win = MainWindow(db)
    try:
        win._mask_action.setChecked(True)          # Ctrl+H equivalent
        assert fmt.is_masked()
        # The drill-down table shows the mask instead of figures.
        amounts = {win._dashboard._table.item(r, 2).text()
                   for r in range(win._dashboard._table.rowCount())}
        assert amounts and amounts <= {fmt.MASK}
        win._mask_action.setChecked(False)
        assert not fmt.is_masked()
    finally:
        fmt.set_masked(False)
        win.close()


#: Long enough and varied enough to pass the policy without the weak-password
#: confirmation (which would open a modal no test can click).
STRONG_PW = "correct horse battery staple"


def test_unlock_dialog_creates_then_unlocks(tmp_path):
    db = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")

    # First run: create flow (FAST params keep key derivation quick).
    create = UnlockDialog(db, params=FAST)
    assert create._creating is True
    create._pw.setText(STRONG_PW)
    create._confirm.setText(STRONG_PW)
    create._on_accept()
    assert db.exists and db.is_unlocked
    db.lock()

    # Second run: unlock flow with wrong then right password.
    unlock = UnlockDialog(db)
    assert unlock._creating is False
    unlock._pw.setText("wrongpass")
    unlock._on_accept()
    assert not db.is_unlocked                 # rejected, dialog stays open
    unlock._pw.setText(STRONG_PW)
    unlock._on_accept()
    assert db.is_unlocked
    db.lock()


def test_unlock_dialog_rejects_short_password(tmp_path):
    db = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    dlg = UnlockDialog(db, params=FAST)
    dlg._pw.setText("short")
    dlg._confirm.setText("short")
    dlg._on_accept()
    assert not db.exists                      # policy blocked it, no vault created
    assert "at least" in dlg._error.text()


def test_unlock_dialog_backoff_after_repeated_failures(tmp_path, monkeypatch):
    """Wrong passwords eventually force a wait, and the OK button disables."""
    from PySide6.QtWidgets import QDialogButtonBox
    db = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    db.create(STRONG_PW, params=FAST)
    db.lock()

    dlg = UnlockDialog(db)
    for _ in range(4):                        # 3 free attempts, 4th trips the delay
        dlg._pw.setText("wrongpass")
        dlg._on_accept()
    assert not db.is_unlocked
    assert dlg._tracker.seconds_remaining() > 0
    assert not dlg._buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert "Try again in" in dlg._error.text()


# --- auto-lock idle detection -------------------------------------------------
#
# The idle timer used to be driven by an application-wide event filter. That
# routed every QObject's events through Python and segfaulted on macOS (PySide
# re-enters the filter while building a wrapper for Cocoa's own objects), so
# activity is now detected by filtering only Kosha's own windows.

def _key_press():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier, "a")


def _watch_activity(win):
    seen = []
    win._idle_watcher.activity.connect(lambda: seen.append(1))
    return seen


def test_idle_watcher_is_not_an_application_wide_filter(tmp_path):
    """Events for objects Kosha does not own must never reach the watcher."""
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    db = _make_db(tmp_path)
    win = MainWindow(db)
    try:
        seen = _watch_activity(win)
        # A bare QObject stands in for the platform-owned objects (native menu
        # bar, window animations) that crashed the old app-wide filter.
        QApplication.sendEvent(QObject(), _key_press())
        assert seen == []
    finally:
        win.close()


def test_idle_watcher_sees_input_to_the_window(tmp_path):
    """Real user input still restarts the auto-lock countdown."""
    from PySide6.QtWidgets import QApplication

    db = _make_db(tmp_path)
    win = MainWindow(db)
    try:
        win.show()
        handle = win.windowHandle()
        assert handle is not None, "window never got a QWindow to watch"

        seen = _watch_activity(win)
        QApplication.sendEvent(handle, _key_press())
        assert seen, "key press did not reset the idle timer"

        # Re-watching must not double-count: Qt drops the earlier registration.
        seen.clear()
        win._idle_watcher.watch(handle)
        QApplication.sendEvent(handle, _key_press())
        assert len(seen) == 1
    finally:
        win.close()


def test_idle_watcher_picks_up_dialogs(tmp_path):
    """Typing in a dialog counts as activity, so a long edit can't trip the lock.

    Kosha's dialogs are parented to whichever view opened them, not to the main
    window, so the scan has to walk the whole widget tree.
    """
    from PySide6.QtWidgets import QApplication, QDialog

    db = _make_db(tmp_path)
    win = MainWindow(db)
    try:
        win.show()
        dlg = QDialog(win._wealth)      # as deep as a real asset/liability editor
        dlg.show()
        seen = _watch_activity(win)
        win._idle_watcher._scan()       # normally the periodic scan does this
        QApplication.sendEvent(dlg.windowHandle(), _key_press())
        assert seen, "input to a dialog did not count as activity"
    finally:
        dlg.close()
        win.close()


def test_idle_watcher_ignores_other_windows(tmp_path):
    """One window's input must not hold another window's vault open."""
    from PySide6.QtWidgets import QApplication

    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir(); dir_b.mkdir()
    win_a, win_b = MainWindow(_make_db(dir_a)), MainWindow(_make_db(dir_b))
    try:
        win_a.show(); win_b.show()
        seen_a = _watch_activity(win_a)
        win_a._idle_watcher._scan()
        QApplication.sendEvent(win_b.windowHandle(), _key_press())
        assert seen_a == [], "a window watched one it does not own"
    finally:
        win_a.close(); win_b.close()


def test_idle_watcher_stops_after_close(tmp_path):
    """A closed window must not keep the vault alive by observing input."""
    from PySide6.QtWidgets import QApplication

    db = _make_db(tmp_path)
    win = MainWindow(db)
    win.show()
    handle = win.windowHandle()
    seen = _watch_activity(win)
    win.close()
    QApplication.sendEvent(handle, _key_press())
    assert seen == []
