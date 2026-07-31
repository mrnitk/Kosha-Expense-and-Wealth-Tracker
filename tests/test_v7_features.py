"""Tests for the v7 batch: keyword extraction quality, guaranteed keyword,
re-derive migration, ignore-keyword, and the 6-month default range."""

from __future__ import annotations

from datetime import date

import pytest

from kosha import categorization as cat, crypto, features
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    d.connection.execute(
        "INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    d.connection.commit()
    yield d
    d.lock()


# --- extraction quality (BBPY-style) -----------------------------------------

@pytest.mark.parametrize("narration,expected", [
    ("BBPY CC PAYMENT DP0160 92120635RK8ND (REF# ST26093008300010341954)", "BBPY CC PAYMENT"),
    ("BBPY CC PAYMENT DP0161 20204811PREBH (REF# ST26122008300010683437)", "BBPY CC PAYMENT"),
    ("LITTLE CHEF SINCE 19", "LITTLE CHEF SINCE 19"),          # short trailing number kept
    ("POS 552260XXXXXX1234 AMAZON RETAIL MUMBAI", "AMAZON RETAIL MUMBAI"),
])
def test_keyword_strips_reference_tail(narration, expected):
    ttype = features.derive_txn_type(narration)
    assert features.derive_merchant_keyword(narration, ttype) == expected


def test_bbpy_rows_collapse_to_one_keyword():
    rows = [
        "BBPY CC PAYMENT DP0160 92120635RK8ND (REF# ST26093008300010341954)",
        "BBPY CC PAYMENT DP0161 20204811PREBH (REF# ST26122008300010683437)",
        "BBPY CC PAYMENT DP0161 51193429EDTWR (REF# ST26153008300010303194)",
    ]
    kws = {features.derive_merchant_keyword(r, features.derive_txn_type(r)) for r in rows}
    assert kws == {"BBPY CC PAYMENT"}


# --- every transaction gets a keyword ----------------------------------------

def test_keyword_is_never_empty():
    # A narration that is all reference/number noise still yields a keyword.
    assert features.derive_merchant_keyword("123456 987654", features.OTHER) == "OTHER"
    assert features.derive_merchant_keyword("999999999999", "UPI") == "UPI"
    # Never None, whatever the input.
    for n in ("", "----", "@@@", "0000ABCD1234"):
        kw = features.derive_merchant_keyword(n, features.OTHER)
        assert isinstance(kw, str) and kw != ""


# --- re-derive migration (v6 -> v7) ------------------------------------------

def test_migration_rederives_keywords(tmp_path):
    db_file, salt_file = tmp_path / "k.db", tmp_path / "k.salt"
    d = Database(db_file=db_file, salt_file=salt_file)
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','hdfc_bank')")
    # Insert a row with a deliberately bad (over-specific) stored keyword.
    con.execute(
        "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,txn_type,merchant_keyword,dedup_hash)"
        " VALUES (1,'2026-04-01','BBPY CC PAYMENT DP0160 92120635RK8ND (REF# ST26093008300010341954)',"
        "500,'debit',1,'OTHER','BBPY CC PAYMENT DP0160 92120635RK8ND','h1')")
    con.execute("PRAGMA user_version = 6")            # pretend it's a v6 DB
    con.commit()
    d.lock()

    d2 = Database(db_file=db_file, salt_file=salt_file)
    d2.unlock(PW)                                     # triggers re-derive
    kw = d2.connection.execute("SELECT merchant_keyword FROM transactions WHERE id=1").fetchone()[0]
    assert kw == "BBPY CC PAYMENT"
    assert d2.connection.execute("PRAGMA user_version").fetchone()[0] == 12
    d2.lock()


# --- ignore keyword ----------------------------------------------------------

def test_ignore_excludes_keyword_slice(db):
    con = db.connection
    for i, (amt, d) in enumerate([(300, "debit"), (200, "debit")], start=1):
        con.execute(
            "INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,merchant_keyword,dedup_hash)"
            " VALUES (?,?,?,?,?,1,?,?)", (i, "2026-04-01", "NOISE", amt, d, "NOISE KW", f"h{i}"))
    con.commit()
    cat.set_excluded_keywords(db, ["NOISE KW"], True, direction="debit")
    # Excluded and gone from the review list.
    assert "NOISE KW" not in [k.keyword for k in cat.unreviewed_keywords(db)]
    exc = con.execute(
        "SELECT effective_excluded FROM v_transactions_resolved WHERE merchant_keyword='NOISE KW' LIMIT 1"
    ).fetchone()[0]
    assert exc == 1
