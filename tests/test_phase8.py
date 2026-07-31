"""Tests for Phase 8: Transfer category, exclude flag, INR format, clear_data,
sub-category filter, data summary, and the v3->v4 migration."""

from __future__ import annotations

from datetime import date

import pytest

from kosha import analytics, categorization as cat, crypto, importer
from kosha.db import Database
from kosha.format import format_inr, format_inr_short, group_indian

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
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    rows = [
        ("2026-04-05", "SWIGGY", 300.0, "debit"),
        ("2026-04-20", "SWIGGY", 200.0, "debit"),
        ("2026-04-25", "HDFC CREDIT CARD", 15000.0, "debit"),   # card bill payment
        ("2026-04-01", "EMPLOYER", 60000.0, "credit"),
        ("2026-05-15", "AMAZON", 1000.0, "debit"),
    ]
    for i, (d, kw, amt, direction) in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,txn_type,merchant_keyword,dedup_hash)"
            " VALUES (?,?,?,?,?,1,?,?,?)",
            (i, d, f"DESC {kw}", amt, direction, "UPI", kw, f"h{i}"),
        )
    con.commit()


def _effective(db, txn_id):
    return db.connection.execute(
        "SELECT effective_category, effective_sub_category, effective_excluded "
        "FROM v_transactions_resolved WHERE id=?", (txn_id,),
    ).fetchone()


# --- Indian number format ----------------------------------------------------

@pytest.mark.parametrize("digits,grouped", [
    ("300000", "3,00,000"),
    ("12345678", "1,23,45,678"),
    ("100", "100"),
    ("1000", "1,000"),
    ("999", "999"),
])
def test_group_indian(digits, grouped):
    assert group_indian(digits) == grouped


def test_format_inr():
    assert format_inr(300000) == "3,00,000.00"
    assert format_inr(-12345.5) == "-12,345.50"
    assert format_inr(1000, decimals=0) == "1,000"


def test_format_inr_short():
    assert format_inr_short(300000) == "₹3.0L"
    assert format_inr_short(12_000_000) == "₹1.2Cr"
    assert format_inr_short(12300) == "₹12.3K"
    assert format_inr_short(850) == "₹850"


# --- Transfer category -------------------------------------------------------

def test_transfer_is_a_category():
    assert cat.TRANSFER in cat.CATEGORIES
    assert cat.normalize_category("credit card payment") == "Transfer"
    assert cat.normalize_category("Credit Card Payment / Transfer") == "Transfer"


def test_transfer_excluded_from_income_expense_savings(db):
    cat.add_rule(db, "HDFC CREDIT CARD", "Transfer")
    rows = analytics.income_expense_savings(db, analytics.Filter(), "month")
    apr = next(r for r in rows if r[0] == "2026-04")
    _, income, expense, savings, _rate = apr
    assert income == 60000.0
    assert expense == 500.0          # SWIGGY only; the 15000 card payment is Transfer
    assert savings == 0.0


def test_transfer_not_a_top_merchant(db):
    cat.add_rule(db, "HDFC CREDIT CARD", "Transfer")
    names = [r[0] for r in analytics.top_merchants(db, analytics.Filter())]
    assert "HDFC CREDIT CARD" not in names


def test_transfer_keyword_leaves_review_list(db):
    cat.add_rule(db, "HDFC CREDIT CARD", "Transfer")   # no sub-category
    assert "HDFC CREDIT CARD" not in [k.keyword for k in cat.unreviewed_keywords(db)]


# --- exclude flag ------------------------------------------------------------

def test_exclude_hides_from_analytics(db):
    cat.set_excluded_keywords(db, ["AMAZON"], True)
    assert _effective(db, 5)[2] == 1
    # Default filter hides it everywhere.
    rows = analytics.transactions(db, analytics.Filter())
    assert all("AMAZON" not in r[1] for r in rows)
    subs = analytics.subcategory_totals(db, analytics.Filter(), "Expense")
    assert all("AMAZON" not in str(s) for s, *_ in subs)


def test_include_excluded_reveals(db):
    cat.set_excluded_keywords(db, ["AMAZON"], True)
    rows = analytics.transactions(db, analytics.Filter(include_excluded=True))
    assert any("AMAZON" in r[1] for r in rows)


def test_per_txn_exclude_override(db):
    cat.set_excluded(db, 1, True)
    assert _effective(db, 1)[2] == 1
    assert _effective(db, 2)[2] == 0     # sibling unaffected
    cat.set_excluded(db, 1, None)
    assert _effective(db, 1)[2] == 0     # back to inherit


def test_excluded_keyword_leaves_review_list(db):
    cat.set_excluded_keywords(db, ["AMAZON"], True)
    assert "AMAZON" not in [k.keyword for k in cat.unreviewed_keywords(db)]


def test_assign_many_carries_exclude(db):
    cat.assign_many(db, ["SWIGGY"], "Expense", "Food", excluded=True)
    assert _effective(db, 1)[2] == 1


# --- sub-category filter + summaries -----------------------------------------

def test_distinct_sub_categories_dashboard(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    cat.add_rule(db, "AMAZON", "Expense", "Shopping")
    subs = analytics.distinct_sub_categories(db)
    assert "Food" in subs and "Shopping" in subs and "Unassigned" in subs
    only_expense = analytics.distinct_sub_categories(db, "Income")
    assert only_expense == ["Unassigned"]      # EMPLOYER has no sub-category


def test_filter_by_sub_category(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    cat.add_rule(db, "AMAZON", "Expense", "Shopping")
    flt = analytics.Filter(sub_categories=("Food",))
    rows = analytics.transactions(db, flt)
    assert len(rows) == 2 and all("SWIGGY" in r[1] for r in rows)


def test_subcategory_totals_all(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    rows = {(c, s): (t, n) for c, s, t, n in cat.subcategory_totals(db)}
    assert rows[("Expense", "Food")] == (500.0, 2)
    assert ("Income", "Unassigned") in rows


def test_data_summary(db):
    lo, hi, n = cat.data_summary(db)
    assert lo == "2026-04-01" and hi == "2026-05-15" and n == 5


def test_display_label():
    assert cat.display_label("Savings") == "Savings / Investments"
    assert cat.display_label("Transfer") == "Credit Card Payment / Transfer"
    assert cat.display_label("Expense") == "Expense"


# --- clear_data --------------------------------------------------------------

def test_clear_transactions_keeps_rules(db):
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    removed = importer.clear_data(db, "transactions")
    assert removed["transactions"] == 5
    assert db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0
    assert db.connection.execute("SELECT count(*) FROM import_batches").fetchone()[0] == 0
    assert len(cat.list_rules(db)) == 1              # rule survives
    assert db.connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1


def test_clear_expenses_wipes_the_expense_side_only(db):
    from datetime import date as _date
    from kosha import wealth
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    aid = wealth.add_asset(db, "FD", "Bank", "Debt", "High")
    wealth.record_snapshot(db, _date(2026, 1, 1), {aid: 500000})

    importer.clear_data(db, "expenses")
    assert db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0
    assert len(cat.list_rules(db)) == 0
    assert db.connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
    # Net worth untouched.
    assert len(wealth.list_assets(db)) == 1
    assert wealth.current_networth(db).assets == 500000


def test_clear_networth_values_keeps_the_holdings(db):
    from datetime import date as _date
    from kosha import wealth
    aid = wealth.add_asset(db, "FD", "Bank", "Debt", "High")
    lid = wealth.add_liability(db, "Car loan", "Car loan")
    wealth.record_snapshot(db, _date(2026, 1, 1), {aid: 500000}, {lid: 300000})

    removed = importer.clear_data(db, "networth_values")
    assert removed["asset_valuations"] == 1 and removed["liability_valuations"] == 1
    assert wealth.snapshot_dates(db) == []            # history gone
    assert len(wealth.list_assets(db)) == 1           # holdings kept
    assert len(wealth.list_liabilities(db)) == 1
    # Expenses untouched.
    assert db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 5


def test_clear_networth_wipes_holdings_and_snapshots(db):
    from datetime import date as _date
    from kosha import wealth
    aid = wealth.add_asset(db, "FD", "Bank", "Debt", "High")
    wealth.record_snapshot(db, _date(2026, 1, 1), {aid: 500000})
    wealth.add_insurance(db, "Medical", "Medical", premium_per_year=16000)

    importer.clear_data(db, "networth")
    assert wealth.list_assets(db) == [] and wealth.list_liabilities(db) == []
    assert wealth.list_insurance(db) == [] and wealth.snapshot_dates(db) == []
    # Expenses survive.
    assert db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 5


def test_clear_all_wipes_everything(db):
    from datetime import date as _date
    from kosha import wealth
    cat.add_rule(db, "SWIGGY", "Expense", "Food")
    aid = wealth.add_asset(db, "FD", "Bank", "Debt", "High")
    wealth.record_snapshot(db, _date(2026, 1, 1), {aid: 500000})

    importer.clear_data(db, "all")
    assert db.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0
    assert len(cat.list_rules(db)) == 0
    assert db.connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
    assert wealth.list_assets(db) == [] and wealth.snapshot_dates(db) == []


def test_data_counts_reports_both_sides(db):
    from datetime import date as _date
    from kosha import wealth
    aid = wealth.add_asset(db, "FD", "Bank", "Debt", "High")
    wealth.record_snapshot(db, _date(2026, 1, 1), {aid: 500000})
    counts = importer.data_counts(db)
    assert counts["transactions"] == 5 and counts["assets"] == 1
    assert counts["asset_valuations"] == 1


def test_clear_data_rejects_unknown_scope(db):
    with pytest.raises(ValueError):
        importer.clear_data(db, "bogus")


# --- migration v3 -> v4 ------------------------------------------------------

def test_migration_adds_exclude_and_direction_columns(tmp_path):
    """A v3 database gains the exclude + direction columns via migration."""
    db_file, salt_file = tmp_path / "k.db", tmp_path / "k.salt"
    d = Database(db_file=db_file, salt_file=salt_file)
    d.create(PW, params=FAST)
    # Simulate an older DB by faking the version back to 3.
    con = d.connection
    con.execute("PRAGMA user_version = 3")
    con.commit()
    d.lock()

    d2 = Database(db_file=db_file, salt_file=salt_file)
    d2.unlock(PW)                                     # triggers _migrate
    assert d2.connection.execute("PRAGMA user_version").fetchone()[0] == 12
    cols = {r[1] for r in d2.connection.execute("PRAGMA table_info(category_rules)")}
    assert {"excluded", "direction"} <= cols
    tcols = {r[1] for r in d2.connection.execute("PRAGMA table_info(transactions)")}
    assert "excluded_override" in tcols
    # v6 view gains the account 'source' columns.
    vcols = {r[1] for r in d2.connection.execute("PRAGMA table_info(v_transactions_resolved)")}
    assert {"account_name", "account_type"} <= vcols
    d2.lock()
