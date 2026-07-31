"""Tests for the net-worth module: assets, liabilities, snapshots, analytics."""

from __future__ import annotations

from datetime import date

import pytest

from kosha import crypto, wealth
from kosha.db import Database

FAST = crypto.Argon2Params(time_cost=1, memory_cost=8192, parallelism=1)
PW = "correct horse battery"


@pytest.fixture()
def db(tmp_path):
    d = Database(db_file=tmp_path / "kosha.db", salt_file=tmp_path / "kosha.salt")
    d.create(PW, params=FAST)
    yield d
    d.lock()


@pytest.fixture()
def seeded(db):
    """A small portfolio modelled on the user's spreadsheet."""
    cash = wealth.add_asset(db, "HDFC-Cash", "Bank", "Cash", "High", "Me", invested=0)
    fd = wealth.add_asset(db, "SBI-FD/RD", "Bank", "Debt", "High", "Me", invested=500000)
    mf = wealth.add_asset(db, "Mine (Non Tax saving)", "Mutual funds", "Equity", "Medium",
                          "Me", invested=600000)
    mom = wealth.add_asset(db, "Mom-HDFC-Cash", "Bank", "Cash", "High", "Mom", invested=0)
    nps = wealth.add_asset(db, "NPS", "NPS", "Hybrid", "Lowest", "Me", invested=150000)
    wealth.record_snapshot(db, date(2025, 12, 1),
                           {cash: 81000, fd: 500000, mf: 1114000, mom: 250000, nps: 181000})
    wealth.record_snapshot(db, date(2026, 6, 1),
                           {cash: 92000, fd: 500000, mf: 789000, mom: 547000, nps: 180000})
    return db, {"cash": cash, "fd": fd, "mf": mf, "mom": mom, "nps": nps}


# --- assets ------------------------------------------------------------------

def test_add_and_list_assets(db):
    wealth.add_asset(db, "HDFC-Cash", "Bank", "Cash", "High")
    assets = wealth.list_assets(db)
    assert len(assets) == 1
    a = assets[0]
    assert a.name == "HDFC-Cash" and a.owner == "Me" and a.is_active
    assert a.counts_toward_networth is True


def test_asset_name_required(db):
    with pytest.raises(ValueError):
        wealth.add_asset(db, "   ", "Bank", "Cash", "High")


def test_update_and_retire_asset(db):
    aid = wealth.add_asset(db, "Old FD", "Bank", "Debt", "High")
    wealth.update_asset(db, aid, name="New FD", invested=1000, is_active=False)
    a = wealth.list_assets(db)[0]
    assert a.name == "New FD" and a.invested == 1000 and not a.is_active
    assert wealth.list_assets(db, active_only=True) == []      # retired, history kept


def test_delete_asset_removes_history(db):
    aid = wealth.add_asset(db, "Temp", "Bank", "Cash", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {aid: 500})
    wealth.delete_asset(db, aid)
    assert wealth.list_assets(db) == []
    assert wealth.snapshot_dates(db) == []


# --- snapshots ---------------------------------------------------------------

def test_snapshot_upsert_replaces_same_date(db):
    aid = wealth.add_asset(db, "Cash", "Bank", "Cash", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {aid: 100})
    wealth.record_snapshot(db, date(2026, 1, 1), {aid: 250})   # same date -> replace
    assert wealth.snapshot_dates(db) == ["2026-01-01"]
    assert wealth.latest_values(db)[0][aid] == 250


def test_latest_values_carries_forward(seeded):
    db, ids = seeded
    aid = wealth.add_asset(db, "Gold", "Other", "Equity", "Medium")
    wealth.record_snapshot(db, date(2025, 12, 1), {aid: 50000})
    # Not updated in June, so its December value carries forward.
    assets, _ = wealth.latest_values(db, date(2026, 6, 1))
    assert assets[aid] == 50000


def test_latest_values_respects_as_of(seeded):
    db, ids = seeded
    assets, _ = wealth.latest_values(db, date(2025, 12, 31))
    assert assets[ids["cash"]] == 81000        # December value, not June's
    assets_now, _ = wealth.latest_values(db)
    assert assets_now[ids["cash"]] == 92000


def test_delete_snapshot(seeded):
    db, _ = seeded
    wealth.delete_snapshot(db, date(2026, 6, 1))
    assert wealth.snapshot_dates(db) == ["2025-12-01"]


# --- net worth ---------------------------------------------------------------

def test_networth_series_and_growth(seeded):
    db, _ = seeded
    series = wealth.networth_series(db)
    assert [p.as_of for p in series] == ["2025-12-01", "2026-06-01"]
    dec, jun = series
    assert dec.assets == 2126000 and dec.liabilities == 0
    assert dec.net_worth == 2126000 and dec.growth_pct is None      # first point
    assert jun.assets == 2108000
    expected = (jun.net_worth - dec.net_worth) / dec.net_worth * 100
    assert round(jun.growth_pct, 6) == round(expected, 6)


def test_networth_subtracts_liabilities(seeded):
    db, _ = seeded
    lid = wealth.add_liability(db, "Car loan - HDFC", "Car loan", principal=800000,
                               interest_rate=9.5, emi_amount=16000)
    wealth.record_snapshot(db, date(2026, 6, 1), liability_values={lid: 700000})
    jun = wealth.networth_series(db)[-1]
    assert jun.liabilities == 700000
    assert jun.net_worth == jun.assets - 700000


def test_excluded_asset_not_counted(seeded):
    db, _ = seeded
    before = wealth.current_networth(db).assets
    info = wealth.add_asset(db, "Reference only", "Other", "Cash", "High",
                            counts_toward_networth=False)
    wealth.record_snapshot(db, date(2026, 6, 1), {info: 999999})
    assert wealth.current_networth(db).assets == before


def test_current_networth_empty_db(db):
    point = wealth.current_networth(db)
    assert point.net_worth == 0 and point.growth_pct is None


def test_total_growth_pct(seeded):
    db, _ = seeded
    series = wealth.networth_series(db)
    expected = (series[-1].net_worth - series[0].net_worth) / series[0].net_worth * 100
    assert round(wealth.total_growth_pct(db), 6) == round(expected, 6)


# --- retired holdings --------------------------------------------------------

def test_retiring_records_a_zero_and_keeps_the_past(seeded):
    """Closing a holding drops it from that date on, without rewriting history."""
    db, ids = seeded
    before = wealth.networth_series(db)
    assert before[0].assets == 2126000 and before[-1].assets == 2108000

    wealth.retire_asset(db, ids["cash"], as_of=date(2026, 12, 1))   # account closed
    series = wealth.networth_series(db)
    # The two earlier snapshots are exactly as they were.
    assert series[0].assets == before[0].assets
    assert series[1].assets == before[-1].assets
    # From the closing date it contributes nothing.
    assert series[-1].as_of == "2026-12-01"
    assert series[-1].assets == before[-1].assets - 92000
    assert wealth.current_networth(db).assets == series[-1].assets


def test_retired_asset_excluded_from_allocation(seeded):
    db, ids = seeded
    wealth.retire_asset(db, ids["mom"], as_of=date(2026, 12, 1))    # Mom's closed
    owners = {b for b, _amt, _pct in wealth.allocation(db, "owner")}
    assert "Mom" not in owners
    assert round(sum(p for _b, _a, p in wealth.allocation(db, "owner")), 6) == 100.0


def test_retired_liability_stops_counting(db):
    cash = wealth.add_asset(db, "Cash", "Bank", "Cash", "High")
    lid = wealth.add_liability(db, "Old loan", "Personal loan", emi_amount=5000)
    wealth.record_snapshot(db, date(2026, 1, 1), {cash: 50000}, {lid: 100000})
    assert wealth.current_networth(db).liabilities == 100000
    wealth.retire_liability(db, lid, as_of=date(2026, 6, 1))    # paid off
    wealth.record_snapshot(db, date(2026, 6, 1), {cash: 60000})  # a later snapshot
    latest = wealth.networth_series(db)[-1]
    assert latest.liabilities == 0                              # no longer owed
    assert latest.net_worth == 60000
    assert wealth.monthly_obligations(db) == 0


def test_reactivating_a_holding_needs_a_fresh_value(seeded):
    """Reopening a closed account starts from its recorded 0 until you update it."""
    db, ids = seeded
    wealth.retire_asset(db, ids["cash"], as_of=date(2026, 12, 1))
    without = wealth.current_networth(db).assets
    wealth.update_asset(db, ids["cash"], is_active=True)         # reopened
    assert wealth.current_networth(db).assets == without         # still 0 until updated
    wealth.record_snapshot(db, date(2027, 1, 1), {ids["cash"]: 92000})
    assert wealth.current_networth(db).assets == without + 92000


# --- closing a holding (untick Active) ---------------------------------------

def test_retiring_drops_net_worth_until_you_bank_the_money(db):
    """Closing an FD lowers net worth; recording the proceeds brings it back.

    Kosha deliberately doesn't move the money for you — you close the holding and
    then enter the cash wherever it landed. The two steps net out to the interest.
    """
    cash = wealth.add_asset(db, "HDFC-Cash", "Bank", "Cash", "High")
    fd = wealth.add_asset(db, "SBI-FD", "Bank", "Debt", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {cash: 100000, fd: 500000})
    assert wealth.current_networth(db).net_worth == 600000

    wealth.retire_asset(db, fd, as_of=date(2026, 6, 1))    # matured / closed
    assert wealth.current_networth(db).net_worth == 100000     # drops, as intended

    # Now the user records where the 5,15,000 went.
    wealth.record_snapshot(db, date(2026, 6, 1), {cash: 615000})
    assert wealth.current_networth(db).net_worth == 615000     # +15,000 real interest


def test_retired_holding_keeps_its_history(db):
    fd = wealth.add_asset(db, "FD", "Bank", "Debt", "High")
    cash = wealth.add_asset(db, "Cash", "Bank", "Cash", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {fd: 500000, cash: 10000})
    wealth.retire_asset(db, fd, as_of=date(2026, 6, 1))
    wealth.record_snapshot(db, date(2026, 6, 1), {cash: 510000})

    series = wealth.networth_series(db)
    assert series[0].assets == 510000            # January had the FD
    assert series[1].assets == 510000            # June has the cash instead
    _dates, rows = wealth.snapshot_matrix(db)
    by_name = {n: (attrs, vals) for n, attrs, vals in rows}
    assert by_name["FD"][1] == [500000, 0]       # January intact, closed at 0 in June
    assert by_name["FD"][0][-1] == "retired"


def test_retiring_one_holding_does_not_erase_another_from_earlier_dates(db):
    """Regression: retiring a holding wrongly removed it from dates before closure.

    Closing the FD in March must not make the still-held ELSS vanish from the
    March snapshot just because the ELSS is closed later in June.
    """
    cash = wealth.add_asset(db, "HDFC-Cash", "Bank", "Cash", "High")
    fd = wealth.add_asset(db, "SBI-FD", "Bank", "Debt", "High")
    mf = wealth.add_asset(db, "Mine (ELSS)", "Mutual funds", "Equity", "Low")
    wealth.record_snapshot(db, date(2026, 1, 1), {cash: 100000, fd: 500000, mf: 800000})

    wealth.retire_asset(db, fd, as_of=date(2026, 3, 1))   # FD closed in March
    wealth.record_snapshot(db, date(2026, 3, 1), {cash: 615000})
    wealth.retire_asset(db, mf, as_of=date(2026, 6, 1))   # ELSS sold in June
    wealth.record_snapshot(db, date(2026, 6, 1), {cash: 1465000})

    series = {p.as_of: p.net_worth for p in wealth.networth_series(db)}
    # March: cash 6,15,000 + ELSS still held at 8,00,000 (the FD is gone).
    assert series["2026-03-01"] == 1415000
    assert series["2026-06-01"] == 1465000


def test_retired_without_recording_stops_carrying_forward(db):
    """Simply unticking Active must not propagate a stale value into later dates."""
    cash = wealth.add_asset(db, "Cash", "Bank", "Cash", "High")
    old = wealth.add_asset(db, "Old account", "Bank", "Cash", "High")
    wealth.record_snapshot(db, date(2026, 1, 1), {cash: 100000, old: 50000})
    wealth.retire_asset(db, old, as_of=date(2026, 6, 1))   # closed -> recorded as 0
    wealth.record_snapshot(db, date(2026, 6, 1), {cash: 120000})
    series = {p.as_of: p.assets for p in wealth.networth_series(db)}
    assert series["2026-01-01"] == 150000       # it was genuinely held in January
    assert series["2026-06-01"] == 120000       # but doesn't linger afterwards


def test_closed_holding_stays_out_of_allocation(db):
    cash = wealth.add_asset(db, "Cash", "Bank", "Cash", "High")
    mf = wealth.add_asset(db, "MF", "Mutual funds", "Equity", "Medium")
    wealth.record_snapshot(db, date(2026, 1, 1), {cash: 100000, mf: 500000})
    wealth.retire_asset(db, mf, as_of=date(2026, 6, 1))
    wealth.record_snapshot(db, date(2026, 6, 1), {cash: 600000})
    buckets = {b for b, _amt, _pct in wealth.allocation(db, "asset_type")}
    assert buckets == {"Cash"}                      # the equity holding is gone


def test_closed_holdings_sort_to_the_bottom(db):
    """Retired rows sink below the active ones in every list."""
    wealth.add_asset(db, "AAA Closed", "Bank", "Cash", "High", is_active=False)
    wealth.add_asset(db, "ZZZ Active", "Bank", "Cash", "High")
    names = [a.name for a in wealth.list_assets(db)]
    assert names == ["ZZZ Active", "AAA Closed"]        # despite alphabetical order

    wealth.add_liability(db, "AAA Paid off", "Car loan", is_active=False)
    wealth.add_liability(db, "ZZZ Running", "Home loan")
    assert [l.name for l in wealth.list_liabilities(db)] == ["ZZZ Running", "AAA Paid off"]


# --- allocations -------------------------------------------------------------

def test_allocation_by_liquidity_sums_to_100(seeded):
    db, _ = seeded
    rows = wealth.allocation(db, "liquidity")
    assert round(sum(pct for _b, _a, pct in rows), 6) == 100.0
    buckets = {b: amt for b, amt, _p in rows}
    assert buckets["High"] == 92000 + 500000 + 547000
    assert buckets["Lowest"] == 180000


def test_allocation_by_owner(seeded):
    db, _ = seeded
    rows = {b: (amt, pct) for b, amt, pct in wealth.allocation(db, "owner")}
    assert rows["Mom"][0] == 547000
    assert round(rows["Me"][1] + rows["Mom"][1], 6) == 100.0


def test_allocation_by_type_and_category(seeded):
    db, _ = seeded
    by_type = {b: amt for b, amt, _p in wealth.allocation(db, "asset_type")}
    assert by_type["Equity"] == 789000 and by_type["Hybrid"] == 180000
    by_cat = {b: amt for b, amt, _p in wealth.allocation(db, "category")}
    assert by_cat["Mutual funds"] == 789000


def test_allocation_rejects_unknown_dimension(db):
    with pytest.raises(ValueError):
        wealth.allocation(db, "nonsense")


# --- liabilities -------------------------------------------------------------

def test_liability_crud_and_emi(db):
    lid = wealth.add_liability(db, "Car loan", "Car loan", principal=800000,
                               emi_amount=16000, interest_rate=9.5,
                               start_date=date(2026, 7, 1))
    liab = wealth.list_liabilities(db)[0]
    assert liab.name == "Car loan" and liab.emi_amount == 16000
    assert liab.start_date == "2026-07-01"
    assert wealth.monthly_obligations(db) == 16000
    wealth.update_liability(db, lid, is_active=False)
    assert wealth.monthly_obligations(db) == 0       # inactive loans don't count
    wealth.delete_liability(db, lid)
    assert wealth.list_liabilities(db) == []


def test_debt_to_asset(seeded):
    db, _ = seeded
    assert wealth.debt_to_asset(db) == 0.0
    lid = wealth.add_liability(db, "Car loan", "Car loan", emi_amount=16000)
    wealth.record_snapshot(db, date(2026, 6, 1), liability_values={lid: 527000})
    ratio = wealth.debt_to_asset(db)
    assert round(ratio, 2) == round(527000 / 2108000 * 100, 2)


def test_liability_outstanding_declines(db):
    lid = wealth.add_liability(db, "Car loan", "Car loan", principal=800000)
    wealth.record_snapshot(db, date(2026, 7, 1), liability_values={lid: 800000})
    wealth.record_snapshot(db, date(2026, 12, 1), liability_values={lid: 720000})
    series = wealth.networth_series(db)
    assert [p.liabilities for p in series] == [800000, 720000]
    assert [p.net_worth for p in series] == [-800000, -720000]


# --- loan amortization -------------------------------------------------------

def test_months_between():
    assert wealth.months_between(date(2026, 1, 1), date(2026, 7, 1)) == 6
    assert wealth.months_between(date(2026, 1, 15), date(2026, 7, 10)) == 5   # partial
    assert wealth.months_between(date(2026, 7, 1), date(2026, 1, 1)) == 0     # before
    assert wealth.months_between("2026-01-01", "2027-01-01") == 12            # ISO ok


def test_estimate_outstanding_declines_over_time():
    p, rate, emi, start = 800000, 9.5, 16000, date(2026, 7, 1)
    at_start = wealth.estimate_outstanding(p, rate, emi, start, date(2026, 7, 1))
    after_1y = wealth.estimate_outstanding(p, rate, emi, start, date(2027, 7, 1))
    after_3y = wealth.estimate_outstanding(p, rate, emi, start, date(2029, 7, 1))
    assert at_start == 800000                      # nothing paid yet
    assert after_1y < at_start and after_3y < after_1y
    # A year of EMIs pays some principal plus interest, so the drop is under 12*EMI.
    assert 0 < (at_start - after_1y) < 12 * emi


def test_estimate_outstanding_zero_interest_is_linear():
    balance = wealth.estimate_outstanding(120000, 0, 10000, date(2026, 1, 1), date(2026, 7, 1))
    assert balance == 60000                        # 6 EMIs x 10,000 off 1,20,000


def test_estimate_outstanding_never_negative():
    balance = wealth.estimate_outstanding(50000, 0, 10000, date(2026, 1, 1), date(2030, 1, 1))
    assert balance == 0


def test_estimate_outstanding_needs_enough_info():
    assert wealth.estimate_outstanding(0, 9.5, 16000, date(2026, 1, 1), date(2027, 1, 1)) is None
    assert wealth.estimate_outstanding(800000, 9.5, 0, date(2026, 1, 1), date(2027, 1, 1)) is None
    assert wealth.estimate_outstanding(800000, 9.5, 16000, None, date(2027, 1, 1)) is None


def test_monthly_interest_and_required_emi():
    # 10,00,000 at 8% -> 6,666.67 a month in interest alone.
    assert round(wealth.monthly_interest(1000000, 8), 2) == 6666.67
    # Clearing it over 66 months needs a far larger EMI than 3,000.
    emi = wealth.required_emi(1000000, 8, 66)
    assert 18000 < emi < 20000
    assert wealth.required_emi(120000, 0, 12) == 10000        # zero-interest case
    assert wealth.required_emi(0, 8, 12) is None


def test_emi_shortfall_flags_a_loan_that_never_repays():
    # The user's figures: 10,00,000 @ 8% with a 3,000 EMI.
    shortfall = wealth.emi_shortfall(1000000, 8, 3000)
    assert shortfall is not None and round(shortfall, 2) == 3666.67
    # A realistic EMI clears the interest, so there's no shortfall.
    assert wealth.emi_shortfall(1000000, 8, 18900) is None
    assert wealth.emi_shortfall(1000000, 0, 3000) is None     # no interest, no growth


def test_estimate_grows_when_emi_cannot_cover_interest():
    """An EMI below the interest makes the balance rise — the maths must show it."""
    start = date(2026, 1, 1)
    after_2y = wealth.estimate_outstanding(1000000, 8, 3000, start, date(2028, 1, 1))
    assert after_2y > 1000000                    # debt grew, as it really would


def test_record_estimated_outstanding(db):
    """The computed balance is stored as a snapshot, so net worth reflects it."""
    lid = wealth.add_liability(db, "Car loan", "Car loan", principal=1000000,
                               interest_rate=8.0, emi_amount=50000,
                               start_date=date(2026, 1, 1))
    recorded = wealth.record_estimated_outstanding(db, lid, as_of=date(2026, 7, 1))
    assert recorded is not None and 0 < recorded < 1000000
    stored = db.connection.execute(
        "SELECT outstanding FROM liability_valuations WHERE liability_id=?", (lid,)
    ).fetchone()[0]
    assert stored == recorded
    point = wealth.current_networth(db)
    assert point.liabilities == recorded and point.net_worth == -recorded


def test_record_estimated_outstanding_needs_terms(db):
    bare = wealth.add_liability(db, "Unknown", "Other")       # no principal/EMI/date
    assert wealth.record_estimated_outstanding(db, bare) is None
    assert wealth.snapshot_dates(db) == []                    # nothing written


def test_recording_estimate_twice_replaces_it(db):
    lid = wealth.add_liability(db, "Car loan", "Car loan", principal=1000000,
                               interest_rate=8.0, emi_amount=50000,
                               start_date=date(2026, 1, 1))
    wealth.record_estimated_outstanding(db, lid, as_of=date(2026, 7, 1))
    wealth.update_liability(db, lid, emi_amount=60000)         # terms changed
    second = wealth.record_estimated_outstanding(db, lid, as_of=date(2026, 7, 1))
    rows = db.connection.execute(
        "SELECT COUNT(*), MAX(outstanding) FROM liability_valuations WHERE liability_id=?",
        (lid,)).fetchone()
    assert rows[0] == 1 and rows[1] == second                  # replaced, not doubled


def test_estimate_outstanding_for_stored_liability(db):
    lid = wealth.add_liability(db, "Car loan", "Car loan", principal=800000,
                               interest_rate=9.5, emi_amount=16000,
                               start_date=date(2026, 7, 1))
    estimate = wealth.estimate_outstanding_for(db, lid, date(2027, 7, 1))
    assert estimate is not None and 0 < estimate < 800000
    # A liability without the numbers can't be estimated.
    bare = wealth.add_liability(db, "Unknown loan", "Other")
    assert wealth.estimate_outstanding_for(db, bare, date(2027, 7, 1)) is None


# --- gains, matrix, insurance ------------------------------------------------

# --- invested moves too (SIPs, stocks) ---------------------------------------

def test_invested_is_dated_per_snapshot(db):
    """A SIP's cost basis rises over time, so gains must use the basis of that date."""
    sip = wealth.add_asset(db, "Mine (SIP)", "Mutual funds", "Equity", "Medium")
    wealth.record_snapshot(db, date(2023, 12, 1), {sip: 568500},
                           invested_values={sip: 500000})
    wealth.record_snapshot(db, date(2026, 6, 1), {sip: 789000},
                           invested_values={sip: 700000})

    dec = {n: (inv, cur, gain) for n, inv, cur, gain in
           wealth.invested_vs_current(db, date(2023, 12, 1))}
    jun = {n: (inv, cur, gain) for n, inv, cur, gain in
           wealth.invested_vs_current(db, date(2026, 6, 1))}
    assert dec["Mine (SIP)"] == (500000, 568500, 68500)
    assert jun["Mine (SIP)"] == (700000, 789000, 89000)   # basis grew with the SIP


def test_invested_carries_forward_until_updated(db):
    sip = wealth.add_asset(db, "SIP", "Mutual funds", "Equity", "Medium")
    wealth.record_snapshot(db, date(2026, 1, 1), {sip: 100000}, invested_values={sip: 90000})
    wealth.record_snapshot(db, date(2026, 6, 1), {sip: 130000})   # value only
    assert wealth.latest_invested(db)[sip] == 90000               # last known basis


def test_invested_falls_back_to_static_field(db):
    """An asset with a one-off purchase still shows a cost basis."""
    fd = wealth.add_asset(db, "FD", "Bank", "Debt", "High", invested=500000)
    wealth.record_snapshot(db, date(2026, 6, 1), {fd: 540000})
    rows = {n: (inv, cur, gain) for n, inv, cur, gain in wealth.invested_vs_current(db)}
    assert rows["FD"] == (500000, 540000, 40000)


def test_dated_invested_beats_the_static_field(db):
    fd = wealth.add_asset(db, "SIP", "Mutual funds", "Equity", "Medium", invested=100)
    wealth.record_snapshot(db, date(2026, 6, 1), {fd: 200000}, invested_values={fd: 150000})
    assert wealth.latest_invested(db)[fd] == 150000        # the dated figure wins


def test_gain_series_tracks_both_over_time(db):
    sip = wealth.add_asset(db, "SIP", "Mutual funds", "Equity", "Medium")
    wealth.record_snapshot(db, date(2025, 12, 1), {sip: 110000}, invested_values={sip: 100000})
    wealth.record_snapshot(db, date(2026, 6, 1), {sip: 180000}, invested_values={sip: 150000})
    series = wealth.gain_series(db)
    assert [(d, inv, val, gain) for d, inv, val, gain in series] == [
        ("2025-12-01", 100000, 110000, 10000),
        ("2026-06-01", 150000, 180000, 30000),
    ]


def test_recording_invested_alone_is_allowed(db):
    sip = wealth.add_asset(db, "SIP", "Mutual funds", "Equity", "Medium")
    wealth.record_snapshot(db, date(2026, 6, 1), invested_values={sip: 50000})
    assert wealth.latest_invested(db)[sip] == 50000


def test_updating_value_keeps_existing_invested(db):
    sip = wealth.add_asset(db, "SIP", "Mutual funds", "Equity", "Medium")
    wealth.record_snapshot(db, date(2026, 6, 1), {sip: 100000}, invested_values={sip: 90000})
    wealth.record_snapshot(db, date(2026, 6, 1), {sip: 120000})   # same date, value only
    assert wealth.latest_values(db)[0][sip] == 120000
    assert wealth.latest_invested(db)[sip] == 90000               # basis not wiped


def test_invested_vs_current(seeded):
    db, _ = seeded
    rows = {name: (inv, cur, gain) for name, inv, cur, gain in wealth.invested_vs_current(db)}
    inv, cur, gain = rows["Mine (Non Tax saving)"]
    assert inv == 600000 and cur == 789000 and gain == 189000


def test_snapshot_matrix_shape(seeded):
    db, _ = seeded
    lid = wealth.add_liability(db, "Car loan", "Car loan")
    wealth.record_snapshot(db, date(2026, 6, 1), liability_values={lid: 700000})
    dates, rows = wealth.snapshot_matrix(db)
    assert dates == ["2025-12-01", "2026-06-01"]
    by_name = {name: (attrs, values) for name, attrs, values in rows}
    attrs, values = by_name["HDFC-Cash"]
    assert values == [81000, 92000]
    # Attribute columns travel with each row: kind, category, type, liquidity, owner, active.
    assert attrs == ["Asset", "Bank", "Cash", "High", "Me", "yes"]
    # Liabilities are negative and only present where recorded.
    liab_attrs, liab_values = by_name["Car loan"]
    assert liab_values == [None, -700000]
    assert liab_attrs[0] == "Liability" and liab_attrs[1] == "Car loan"


def test_snapshot_matrix_marks_retired_holdings(seeded):
    db, ids = seeded
    wealth.update_asset(db, ids["cash"], is_active=False)
    _dates, rows = wealth.snapshot_matrix(db)
    by_name = {name: attrs for name, attrs, _v in rows}
    assert by_name["HDFC-Cash"][-1] == "retired"   # still listed, clearly flagged


def test_insurance_is_informational(seeded):
    db, _ = seeded
    before = wealth.current_networth(db).assets
    wealth.add_insurance(db, "Medical - Self - HDFC ergo", "Medical",
                         premium_per_year=16000, coverage=2000000)
    wealth.add_insurance(db, "LIC", "Life", premium_per_year=17850)
    assert wealth.current_networth(db).assets == before      # never counted
    premium, coverage = wealth.insurance_summary(db)
    assert premium == 33850 and coverage == 2000000
    assert len(wealth.list_insurance(db)) == 2


def test_insurance_delete(db):
    iid = wealth.add_insurance(db, "Temp", "Other")
    wealth.delete_insurance(db, iid)
    assert wealth.list_insurance(db) == []


# --- settings ----------------------------------------------------------------

def test_settings_roundtrip(db):
    assert wealth.get_setting(db, "auto_lock_minutes", "5") == "5"
    wealth.set_setting(db, "auto_lock_minutes", "10")
    assert wealth.get_setting(db, "auto_lock_minutes") == "10"
    wealth.set_setting(db, "auto_lock_minutes", "15")            # upsert
    assert wealth.get_setting(db, "auto_lock_minutes") == "15"


# --- migration ---------------------------------------------------------------

def test_v11_tables_added_to_existing_vault(tmp_path):
    """An existing expense vault gains the wealth tables without losing data."""
    db_file, salt_file = tmp_path / "k.db", tmp_path / "k.salt"
    d = Database(db_file=db_file, salt_file=salt_file)
    d.create(PW, params=FAST)
    con = d.connection
    con.execute("INSERT INTO accounts(id,name,account_type,institution) VALUES (1,'A','bank','x')")
    con.execute("INSERT INTO transactions(id,txn_date,raw_description,amount,direction,account_id,dedup_hash) "
                "VALUES (1,'2026-04-01','D SWIGGY',300,'debit',1,'h1')")
    con.execute("PRAGMA user_version = 10")          # pretend it's a v10 vault
    con.commit()
    d.lock()

    d2 = Database(db_file=db_file, salt_file=salt_file)
    d2.unlock(PW)                                    # triggers migration
    assert d2.connection.execute("PRAGMA user_version").fetchone()[0] == 12
    tables = {r[0] for r in d2.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"assets", "asset_valuations", "liabilities", "liability_valuations",
            "insurance", "app_settings"} <= tables
    # Expense data survived.
    assert d2.connection.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1
    d2.lock()
