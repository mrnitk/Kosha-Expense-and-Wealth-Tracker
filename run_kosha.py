"""Frozen-app entry point for Kosha (used by the PyInstaller build).

Running the package's ``__main__`` under PyInstaller breaks relative imports, so
the bundle launches this top-level script instead. Set ``KOSHA_SELFTEST=1`` to
exercise the bundled-dependency risks (native SQLCipher, the schema.sql data
resource, Plotly's inlined JS, and QtWebEngine) and exit — used to verify a
build without blocking on the unlock dialog.
"""

from __future__ import annotations

import os
import sys


def _selftest() -> int:
    """Prove the tricky bundled dependencies survived packaging."""
    from PySide6.QtWidgets import QApplication
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication(sys.argv)

    # 1) native SQLCipher extension + the schema.sql package resource.
    import tempfile
    from kosha import crypto
    from kosha.db import Database
    workdir = tempfile.mkdtemp()
    db = Database(
        db_file=os.path.join(workdir, "k.db"),
        salt_file=os.path.join(workdir, "k.salt"),
    )
    db.create("selftestpw", params=crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1))
    assert db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0
    db.lock()

    # 2) Plotly's inlined JS bundle (package data).
    import plotly.graph_objects as go
    import plotly.io as pio
    html = pio.to_html(go.Figure(go.Bar(x=[1], y=[2])), include_plotlyjs="inline", full_html=True)
    assert "plotly" in html.lower() and len(html) > 100_000, "plotly.js not bundled"

    # 3) QtWebEngine (the dashboard's renderer). Constructing the view isn't
    #    enough — actually load the page, which is what forces the separate
    #    QtWebEngineProcess helper to spawn. That helper lives outside the main
    #    executable (a nested .app on macOS, a .exe on Windows) and is the part
    #    most likely to be missing or unlaunchable in a frozen bundle.
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtWebEngineWidgets import QWebEngineView
    chart_file = os.path.join(workdir, "chart.html")
    with open(chart_file, "w", encoding="utf-8") as fh:
        fh.write(html)
    view = QWebEngineView()
    loop, loaded = QEventLoop(), []
    view.loadFinished.connect(lambda ok: (loaded.append(ok), loop.quit()))
    # setUrl, not setHtml: setHtml data-URL-encodes the page and caps it at ~2 MB,
    # which an inlined-Plotly chart blows past. The dashboard loads from a file
    # for the same reason, so this exercises the real path.
    view.setUrl(QUrl.fromLocalFile(chart_file))
    QTimer.singleShot(60_000, loop.quit)   # don't hang a build forever
    loop.exec()
    assert loaded and loaded[0], "QtWebEngine did not render (helper process missing?)"

    # 4) openpyxl (.xlsx) for the template importer — round-trip a blank template.
    from kosha import template_import
    tmpl = os.path.join(workdir, "t.xlsx")
    template_import.write_template(tmpl)
    records, _problems = template_import.read_template(tmpl)
    assert records == [], "openpyxl/template not bundled"

    print("SELFTEST OK")
    return 0


def _run_selftest() -> int:
    """Run the self-test, recording the outcome to KOSHA_SELFTEST_OUT if set.

    Needed because a windowed (console=False) build has no visible stdout.
    """
    out = os.environ.get("KOSHA_SELFTEST_OUT")
    try:
        _selftest()
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("SELFTEST OK\n")
        return 0
    except Exception:
        import traceback
        tb = traceback.format_exc()
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("SELFTEST FAILED\n" + tb)
        print(tb)
        return 1


def main() -> int:
    if os.environ.get("KOSHA_SELFTEST"):
        return _run_selftest()
    from kosha.app import main as app_main
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
