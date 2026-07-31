"""Tests for the v6 batch: source/account column, monthly stats, rule editing,
and Month-Year axis labels."""

from __future__ import annotations

import pytest

from kosha import analytics, categorization as cat, charts, crypto
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    _seed(d)
    yield d
    d.lock()


def _seed(db: Database) -> None:
    con = db.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'HDFC Bank','bank','hdfc_bank')")
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (2,'HDFC Card','credit_card','hdfc_card')")
    rows = [
        ("2026-04-05", "SWIGGY", 300.0, "debit", 1),
        ("2026-05-05", "SWIGGY", 500.0, "debit", 2),      # on the card
        ("2026-04-01", "EMPLOYER", 60000.0, "credit", 1),
        ("2026-05-01", "EMPLOYER", 60000.0, "credit", 1),
        ("2026-04-25", "ZERODHA", 5000.0, "debit", 1),
    ]
    for i, (d, kw, amt, direction, acc) in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (i, d, f"DESC {kw}", amt, direction, acc, kw, f"h{i}"),
        )
    con.commit()
    cat.add_rule(db, "ZERODHA", "Savings", "Investments")


# --- source / account --------------------------------------------------------

def test_view_exposes_account_source(db):
    row = db.connection.execute(
        "SELECT account_name, account_type FROM v_transactions_resolved WHERE id=2"
    ).fetchone()
    assert row == ("HDFC Card", "credit_card")


def test_transactions_include_source_and_subcategory(db):
    rows = analytics.transactions(db, analytics.Filter())
    # columns: date, desc, amount, direction, txn_type, source, keyword, category, sub, tag
    by_desc = {r[1]: r for r in rows}
    z = by_desc["DESC ZERODHA"]
    assert z[5] == "HDFC Bank"          # source
    assert z[6] == "ZERODHA"            # keyword
    assert z[7] == "Savings"            # category
    assert z[8] == "Investments"        # sub-category


def test_list_accounts(db):
    assert analytics.list_accounts(db) == [(1, "HDFC Bank"), (2, "HDFC Card")]


def test_filter_by_source(db):
    card = next(a for a in analytics.list_accounts(db) if a[1] == "HDFC Card")
    rows = analytics.transactions(db, analytics.Filter(account_ids=(card[0],)))
    assert len(rows) == 1 and rows[0][1] == "DESC SWIGGY" and rows[0][5] == "HDFC Card"


# --- monthly stats -----------------------------------------------------------

def test_monthly_stats(db):
    stats = analytics.monthly_stats(db, analytics.Filter())
    inc = stats["Income"]
    assert inc["avg"] == 60000.0 and inc["min"] == 60000.0 and inc["max"] == 60000.0
    exp = stats["Expense"]                         # Apr=300, May=500
    assert exp["min"] == 300.0 and exp["max"] == 500.0 and exp["avg"] == 400.0
    sav = stats["Savings"]                         # Apr=5000, May=0
    assert sav["max"] == 5000.0 and sav["min"] == 0.0 and sav["total"] == 5000.0


# --- Month-Year axis labels --------------------------------------------------

@pytest.mark.parametrize("period,pretty", [
    ("2026-07", "Jul-2026"),
    ("2026-01", "Jan-2026"),
    ("2026-Q2", "2026-Q2"),      # quarter unchanged
    ("2026", "2026"),            # year unchanged
])
def test_prettify_period(period, pretty):
    assert charts.prettify_period(period) == pretty


def test_snapshot_labels_are_unique_per_date():
    """Regression: two snapshots in one month collapsed to one label, and a bar
    chart sums duplicate categories — silently doubling that month's bar."""
    # Distinct months read cleanly, no day clutter.
    assert charts.snapshot_labels(["2025-12-01", "2026-06-01", "2026-07-31"]) == \
        ["Dec-2025", "Jun-2026", "Jul-2026"]
    # Same month -> disambiguated by day, so every snapshot is its own bar.
    labels = charts.snapshot_labels(["2026-06-01", "2026-07-15", "2026-07-31"])
    assert labels == ["Jun-2026", "Jul-2026 (15)", "Jul-2026 (31)"]
    assert len(labels) == len(set(labels))


def test_networth_trend_does_not_merge_same_month_snapshots():
    from kosha.wealth import NetWorthPoint
    points = [
        NetWorthPoint("2026-07-15", 4950000, 0, 4950000, None),
        NetWorthPoint("2026-07-31", 4961368, 0, 4961368, 0.2),
    ]
    fig = charts.networth_trend(points)
    xs = list(fig.data[0].x)                       # the Assets bar trace
    assert len(xs) == len(set(xs))                 # no duplicate category to sum


def test_income_line_uses_month_year_labels(db):
    rows = analytics.income_expense_savings(db, analytics.Filter(), "month")
    fig = charts.income_expense_line(rows)
    xs = set(fig.data[0].x)
    assert "Apr-2026" in xs and "May-2026" in xs


# --- rule editing ------------------------------------------------------------

def test_edit_rule_changes_sub_category(db):
    rule = next(r for r in cat.list_rules(db) if r.keyword == "ZERODHA")
    cat.edit_rule(db, rule.id, keyword="ZERODHA", category="Savings",
                  sub_category="Mutual Funds", priority=0, excluded=False, direction=None)
    got = db.connection.execute(
        "SELECT effective_sub_category FROM v_transactions_resolved WHERE merchant_keyword='ZERODHA'"
    ).fetchone()
    assert got[0] == "Mutual Funds"


def test_edit_rule_can_exclude(db):
    rule = next(r for r in cat.list_rules(db) if r.keyword == "ZERODHA")
    cat.edit_rule(db, rule.id, keyword="ZERODHA", category="Savings",
                  sub_category="Investments", priority=0, excluded=True, direction=None)
    got = db.connection.execute(
        "SELECT effective_excluded FROM v_transactions_resolved WHERE merchant_keyword='ZERODHA'"
    ).fetchone()
    assert got[0] == 1


def test_edit_rule_rename_keyword_remaps(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food")           # create the rule to rename
    rule = next(r for r in cat.list_rules(db) if r.keyword == "SWIGGY")
    # Rename to EMPLOYER's keyword scope shouldn't be done normally, but renaming
    # to a fresh keyword should stop matching SWIGGY transactions.
    cat.edit_rule(db, rule.id, keyword="SWIGGYY", category="Expense",
                  sub_category="Food", priority=0, excluded=False, direction=None)
    # SWIGGY txns now fall back to direction default (Expense, no sub).
    got = db.connection.execute(
        "SELECT effective_sub_category FROM v_transactions_resolved WHERE merchant_keyword='SWIGGY' LIMIT 1"
    ).fetchone()
    assert got[0] is None


def test_edit_rule_dedupes_colliding_slot(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    cat.add_rule(db, "AMAZON", "Expense", "Shopping")   # AMAZON has no txns but a rule
    amazon = next(r for r in cat.list_rules(db) if r.keyword == "AMAZON")
    # Rename AMAZON's rule onto SWIGGY (a collision) — the old SWIGGY rule is dropped.
    cat.edit_rule(db, amazon.id, keyword="SWIGGY", category="Expense",
                  sub_category="Dining", priority=0, excluded=False, direction=None)
    swiggy_rules = [r for r in cat.list_rules(db) if r.keyword == "SWIGGY"]
    assert len(swiggy_rules) == 1 and swiggy_rules[0].sub_category == "Dining"


def test_edit_rule_rejects_empty_keyword(db):
    rule = next(r for r in cat.list_rules(db) if r.keyword == "ZERODHA")
    with pytest.raises(ValueError):
        cat.edit_rule(db, rule.id, keyword="   ", category="Savings",
                      sub_category="x", priority=0, excluded=False, direction=None)
