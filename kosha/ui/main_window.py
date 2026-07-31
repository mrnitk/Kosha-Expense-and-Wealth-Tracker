"""Main application window: expenses, net worth, import, and security actions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget,
)

from .. import format as fmt
from .. import importer
from ..db import Database
from ..parsers import REGISTRY
from . import theme
from .categorization_view import CategorizationView
from .dashboard_view import DashboardView
from .recurring_view import RecurringView
from .rules_view import RulesView
from .wealth_view import WealthView

_IMPORT_FILTER = "Statements (*.xls *.xlsx *.csv *.pdf);;All files (*)"
_IMPORT_SUFFIXES = {".xls", ".xlsx", ".csv", ".pdf"}


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self._db = db
        self.setWindowTitle("Kosha — Expense Tracker")
        self.resize(1040, 660)
        self.setAcceptDrops(True)          # drag-and-drop statement import

        self._dashboard = DashboardView(db)
        self._view = CategorizationView(db)
        self._rules = RulesView(db)
        self._recurring = RecurringView(db)
        self._wealth = WealthView(db)
        # Rebuilding the dashboard's Plotly page is ~1s, so cross-tab edits only
        # flag it dirty; it re-renders when the user actually opens it (below).
        self._view.changed.connect(self._update_status)
        self._view.changed.connect(self._dashboard.mark_dirty)
        self._view.changed.connect(self._rules.refresh)
        # Editing a rule re-resolves history, so refresh the other views.
        self._rules.changed.connect(self._dashboard.mark_dirty)
        self._rules.changed.connect(self._view.refresh)
        self._rules.changed.connect(self._update_status)
        # A per-transaction edit/delete changes totals everywhere.
        self._dashboard.changed.connect(self._view.refresh)
        self._dashboard.changed.connect(self._recurring.refresh)
        self._dashboard.changed.connect(self._update_status)
        self._wealth.changed.connect(self._update_status)

        # Two top-level sections, each with its own sub-tabs: what you spend, and
        # what you own.
        self._expense_tabs = QTabWidget()
        self._expense_tabs.addTab(self._dashboard, "Dashboard")
        self._expense_tabs.addTab(self._view, "Categorize")
        self._expense_tabs.addTab(self._rules, "Rules")
        self._expense_tabs.addTab(self._recurring, "Recurring")
        self._expense_tabs.currentChanged.connect(self._on_expense_tab_changed)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._expense_tabs, "Expenses")
        self._tabs.addTab(self._wealth, "Net worth")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self._tabs)

        self._build_menu()
        self._update_status()
        self._setup_auto_lock()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        theme.force_light_titlebar(self)   # keep the title bar light under OS dark mode

    # --- auto-lock -----------------------------------------------------------

    def _setup_auto_lock(self) -> None:
        """Lock the vault after a period of no interaction.

        The timer is reset by any key/mouse activity in the window (installed as
        an application-wide event filter), so it only fires when the app has
        genuinely been left alone.
        """
        from .. import wealth
        minutes = _int_or(wealth.get_setting(self._db, "auto_lock_minutes", "5"), 5)
        self._auto_lock_minutes = minutes
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timeout)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._restart_idle_timer()

    def _restart_idle_timer(self) -> None:
        if getattr(self, "_auto_lock_minutes", 0) > 0:
            self._idle_timer.start(self._auto_lock_minutes * 60 * 1000)
        else:
            self._idle_timer.stop()

    def eventFilter(self, obj, event):    # noqa: N802 (Qt signature)
        if event.type() in (
            QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.KeyPress, QEvent.Wheel
        ):
            self._restart_idle_timer()
        return super().eventFilter(obj, event)

    def _on_idle_timeout(self) -> None:
        """Idle limit reached — lock up and close."""
        QMessageBox.information(
            self, "Kosha locked itself",
            f"There was no activity for {self._auto_lock_minutes} minutes, so Kosha "
            "locked the vault and will now close.\n\n"
            "Your data stays saved and encrypted — nothing is lost. To carry on, "
            "start Kosha again and enter your master password.\n\n"
            "(Security ▸ Change master password lets you change it; the idle limit "
            "is 5 minutes by default.)")
        self.close()          # closeEvent drops the key from memory

    def _on_tab_changed(self, _index: int) -> None:
        """Top-level section changed (Expenses / Net worth)."""
        current = self._tabs.currentWidget()
        if current is self._wealth:
            self._wealth.refresh()
        elif current is self._expense_tabs:
            self._on_expense_tab_changed(self._expense_tabs.currentIndex())
        self._update_status()          # the status line is per-section

    def _on_expense_tab_changed(self, _index: int) -> None:
        """Refresh an expense sub-tab's data when it's brought to the front."""
        current = self._expense_tabs.currentWidget()
        if current is self._dashboard:
            self._dashboard.refresh_if_dirty()   # ~1s Plotly rebuild — only when shown
        elif current is self._recurring:
            self._recurring.refresh()

    # --- menus ---------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        import_action = QAction("&Import statements…", self)
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self._import_dialog)
        file_menu.addAction(import_action)

        template_import_action = QAction("Import from &template…", self)
        template_import_action.triggered.connect(self._import_template)
        file_menu.addAction(template_import_action)

        template_dl_action = QAction("Download blank &template…", self)
        template_dl_action.triggered.connect(self._download_template)
        file_menu.addAction(template_dl_action)

        file_menu.addSeparator()
        nw_import = QAction("Import &net-worth data…", self)
        nw_import.triggered.connect(self._import_wealth_template)
        file_menu.addAction(nw_import)

        nw_template = QAction("Download &net-worth template…", self)
        nw_template.triggered.connect(self._download_wealth_template)
        file_menu.addAction(nw_template)

        clear_action = QAction("&Clear all data…", self)
        clear_action.triggered.connect(self._clear_data_dialog)
        file_menu.addAction(clear_action)

        file_menu.addSeparator()
        backup_action = QAction("&Backup vault…", self)
        backup_action.triggered.connect(self._backup_vault)
        file_menu.addAction(backup_action)

        restore_action = QAction("&Restore from backup…", self)
        restore_action.triggered.connect(self._restore_vault)
        file_menu.addAction(restore_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # --- View: privacy mask ---
        view_menu = self.menuBar().addMenu("&View")
        self._mask_action = QAction("&Hide amounts", self, checkable=True)
        self._mask_action.setShortcut(QKeySequence("Ctrl+H"))
        self._mask_action.setToolTip("Mask every amount on screen (shoulder-surfing protection)")
        self._mask_action.toggled.connect(self._on_toggle_mask)
        view_menu.addAction(self._mask_action)

        # --- Security ---
        security_menu = self.menuBar().addMenu("&Security")
        change_pw = QAction("Change &master password…", self)
        change_pw.triggered.connect(self._change_password)
        security_menu.addAction(change_pw)
        security_menu.addSeparator()
        lock_now = QAction("&Lock now", self)
        lock_now.setShortcut(QKeySequence("Ctrl+L"))
        lock_now.triggered.connect(self._lock_now)
        security_menu.addAction(lock_now)

    # --- import --------------------------------------------------------------

    def _import_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select statements", "", _IMPORT_FILTER)
        if paths:
            self._import_paths(paths)

    def _import_template(self) -> None:
        """Import a filled standard template (any bank; Source column per row)."""
        from .. import template_import
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a filled template", "",
            "Template (*.xlsx *.xls *.csv);;All files (*)")
        if not path:
            return
        try:
            result = importer.import_template(self._db, Path(path))
        except template_import.TemplateError as exc:
            QMessageBox.warning(self, "Not a valid template", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self._refresh_after_import()
        box = QMessageBox.warning if result.has_problems else QMessageBox.information
        box(self, "Import complete", result.summary())

    def _download_template(self) -> None:
        """Save a blank standard template for the user to fill in."""
        from .. import template_import
        path, _ = QFileDialog.getSaveFileName(
            self, "Save blank template", "Kosha_import_template.xlsx",
            "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            template_import.write_template(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save template", str(exc))
            return
        QMessageBox.information(
            self, "Template saved",
            f"Saved to:\n{path}\n\nFill in the 'Transactions' sheet, then use "
            "File ▸ Import from template.")

    # --- net-worth template --------------------------------------------------

    def _import_wealth_template(self) -> None:
        """Load assets/liabilities and their whole snapshot history from Excel."""
        from .. import wealth_template
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a net-worth workbook", "",
            "Workbook (*.xlsx *.xlsm *.xls *.csv);;All files (*)")
        if not path:
            return
        try:
            result = wealth_template.import_wealth_template(self._db, Path(path))
        except wealth_template.WealthTemplateError as exc:
            QMessageBox.warning(self, "Not a net-worth workbook", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self._wealth.refresh()
        self._update_status()
        box = QMessageBox.warning if result.has_problems else QMessageBox.information
        box(self, "Net-worth import complete", result.summary())

    def _download_wealth_template(self) -> None:
        """Save a net-worth template — pre-filled with whatever is already recorded."""
        from .. import wealth_template
        path, _ = QFileDialog.getSaveFileName(
            self, "Save net-worth template", "Kosha_networth_template.xlsx",
            "Excel workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            wealth_template.write_wealth_template(path, self._db)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save template", str(exc))
            return
        QMessageBox.information(
            self, "Template saved",
            f"Saved to:\n{path}\n\nFill in one row per holding and one column per "
            "date (e.g. Jun'26), then use File ▸ Import net-worth data.")

    def _refresh_after_import(self) -> None:
        self._view.refresh()
        self._rules.refresh()
        self._recurring.refresh()
        self._dashboard.reset_filter_bounds()
        self._dashboard.refresh()
        self._update_status()

    # --- privacy / security --------------------------------------------------

    def _on_toggle_mask(self, on: bool) -> None:
        """Mask or reveal every amount in the app (one central switch)."""
        fmt.set_masked(on)
        self._refresh_all_views()
        self.statusBar().showMessage(
            "Amounts hidden — press Ctrl+H to reveal" if on else "Amounts visible", 4000)

    def _refresh_all_views(self) -> None:
        """Re-render every view (used after a mask toggle)."""
        self._view.refresh()
        self._rules.refresh()
        self._recurring.refresh()
        self._wealth.refresh()
        self._dashboard.refresh()

    def _change_password(self) -> None:
        """Re-key the vault with a new master password."""
        from .password_dialog import ChangePasswordDialog
        dlg = ChangePasswordDialog(self._db, self)
        dlg.exec()
        if dlg.changed:
            QMessageBox.information(
                self, "Password changed",
                "Your master password has been changed.\n\n"
                "Older backups still open with the OLD password — take a fresh "
                "backup now (File ▸ Backup vault) so you have one that matches.")

    def _lock_now(self) -> None:
        """Drop the key from memory and close — reopen to unlock."""
        if QMessageBox.question(
            self, "Lock Kosha",
            "Lock the vault now?\n\n"
            "Kosha will close and the encryption key is dropped from memory. "
            "Your data stays saved — nothing is lost.\n\n"
            "To get back in, start Kosha again and enter your master password.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self.close()          # closeEvent locks the database

    # --- backup / restore ----------------------------------------------------

    def _backup_vault(self) -> None:
        """Save an encrypted backup (the DB + salt) to a zip the user chooses."""
        from datetime import date as _date
        from .. import backup
        default = f"kosha-backup-{_date.today().isoformat()}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "Save encrypted backup", default,
                                              "Backup archive (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            backup.create_backup(path, self._db.db_path, self._db.salt_path)
        except Exception as exc:
            QMessageBox.critical(self, "Backup failed", str(exc))
            return
        QMessageBox.information(
            self, "Backup saved",
            f"Encrypted backup saved to:\n{path}\n\nIt opens only with this vault's "
            "master password. Keep it somewhere safe.")

    def _restore_vault(self) -> None:
        """Replace the current vault with a backup, then close (reopen to unlock)."""
        from .. import backup
        path, _ = QFileDialog.getOpenFileName(self, "Select a backup to restore", "",
                                              "Backup archive (*.zip);;All files (*)")
        if not path:
            return
        if not backup.is_valid_backup(path):
            QMessageBox.warning(self, "Not a valid backup",
                                "That file isn't a Kosha backup (needs kosha.db and kosha.salt).")
            return
        if QMessageBox.warning(
            self, "Restore backup",
            "This REPLACES your current vault with the backup and cannot be undone.\n\n"
            "Kosha will close afterwards — reopen it and unlock with the backup's "
            "master password.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            self._db.lock()   # close the connection before overwriting the files
            backup.restore_backup(path, self._db.db_path, self._db.salt_path)
        except Exception as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))
            return
        QMessageBox.information(self, "Restored",
                                "Vault restored. Kosha will now close — reopen it to unlock.")
        self.close()

    def _import_paths(self, paths: list[str]) -> None:
        try:
            result = importer.import_paths(self._db, paths)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        self._view.refresh()
        self._rules.refresh()
        self._dashboard.reset_filter_bounds()
        self._dashboard.refresh()
        self._update_status()

        box = QMessageBox.warning if (result.unrecognized or result.failed) else QMessageBox.information
        box(self, "Import complete", result.summary())

    def _clear_data_dialog(self) -> None:
        """Pick what to wipe — expenses, net worth, or everything. Irreversible."""
        from .clear_data_dialog import ClearDataDialog, describe
        dlg = ClearDataDialog(self._db, self)
        dlg.exec()
        if dlg.cleared is None:
            return
        self._view.refresh()
        self._rules.refresh()
        self._recurring.refresh()
        self._wealth.refresh()
        self._dashboard.reset_filter_bounds()
        self._dashboard.refresh()
        self._update_status()
        QMessageBox.information(self, "Data cleared", describe(dlg.cleared))

    # --- drag and drop -------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if self._dropped_statements(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = self._dropped_statements(event)
        if paths:
            event.acceptProposedAction()
            self._import_paths(paths)

    @staticmethod
    def _dropped_statements(event) -> list[str]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        out = []
        for url in md.urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                if p.suffix.lower() in _IMPORT_SUFFIXES:
                    out.append(str(p))
        return out

    # --- status --------------------------------------------------------------

    def _update_status(self) -> None:
        """Show a summary for whichever section is on screen."""
        from .. import wealth
        from ..format import format_inr
        if getattr(self, "_tabs", None) is not None and \
                self._tabs.currentWidget() is getattr(self, "_wealth", None):
            assets = len(wealth.list_assets(self._db, active_only=True))
            liabs = len(wealth.list_liabilities(self._db, active_only=True))
            snapshots = len(wealth.snapshot_dates(self._db))
            point = wealth.current_networth(self._db)
            summary = (f"{assets} assets · {liabs} liabilities · {snapshots} snapshots")
            if point.as_of:
                summary += f" · net worth ₹{format_inr(point.net_worth)} as of {point.as_of}"
            self.statusBar().showMessage(summary)
            return
        n = self._db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0]
        rules = self._db.connection.execute("SELECT count(*) FROM category_rules").fetchone()[0]
        supported = ", ".join(sorted(REGISTRY))
        self.statusBar().showMessage(f"{n} transactions · {rules} rules · parsers: {supported}")

    def closeEvent(self, event) -> None:
        # Stop watching for activity before going away, so a closed window never
        # keeps filtering application events.
        if getattr(self, "_idle_timer", None) is not None:
            self._idle_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._db.lock()          # drop the encryption key from memory
        super().closeEvent(event)


def _int_or(value, default: int) -> int:
    """Parse ``value`` as an int, falling back to ``default``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
