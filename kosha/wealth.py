"""Net-worth tracking: assets, liabilities, and dated valuation snapshots.

The model mirrors how the user tracked this in a spreadsheet: holdings are rows
with fixed attributes (category, type, liquidity, owner, invested), and each
"update" records every holding's value on a date — one column per snapshot.

    net worth (on a date) = sum(asset values) - sum(liability outstanding)

Values are stored per (holding, date) so history is preserved and growth between
snapshots is derivable. A holding can be retired (``is_active=0``) without losing
its history. Insurance policies are tracked separately for premium/cover
visibility and never count toward net worth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .db import Database

CATEGORIES = ("Bank", "Stocks", "Mutual funds", "PF", "NPS", "Other")
#: Suggested owners for holdings — the pickers are editable, so others can be typed.
OWNERS = ("Me", "Mom", "Wife")
ASSET_TYPES = ("Cash", "Debt", "Equity", "Hybrid", "Real estate")
LIQUIDITY = ("High", "Medium", "Low", "Lowest")
LIABILITY_KINDS = ("Home loan", "Car loan", "Personal loan", "Credit card", "Other")
INSURANCE_KINDS = ("Life", "Medical", "Term", "Other")

# Allocation dimensions -> the assets column that backs them.
_ALLOCATION_COLUMNS = {
    "liquidity": "liquidity",
    "asset_type": "asset_type",
    "owner": "owner",
    "category": "category",
}


@dataclass(frozen=True)
class Asset:
    id: int
    name: str
    category: str
    asset_type: str
    liquidity: str
    owner: str
    invested: float
    counts_toward_networth: bool
    is_active: bool
    sort_order: int
    notes: Optional[str]


@dataclass(frozen=True)
class Liability:
    id: int
    name: str
    kind: str
    owner: str
    principal: float
    interest_rate: Optional[float]
    emi_amount: float
    start_date: Optional[str]
    end_date: Optional[str]
    is_active: bool
    sort_order: int
    notes: Optional[str]


@dataclass(frozen=True)
class Insurance:
    id: int
    name: str
    kind: Optional[str]
    premium_per_year: float
    coverage: float
    renews_on: Optional[str]
    owner: str
    notes: Optional[str]


@dataclass(frozen=True)
class NetWorthPoint:
    """One snapshot date: totals plus growth against the previous snapshot."""

    as_of: str
    assets: float
    liabilities: float
    net_worth: float
    growth_pct: Optional[float]      # vs previous snapshot; None for the first


def _as_iso(value) -> str:
    """Accept a date or an ISO string and return an ISO string."""
    return value.isoformat() if isinstance(value, date) else str(value)


# --- assets ------------------------------------------------------------------

def add_asset(
    db: Database,
    name: str,
    category: str,
    asset_type: str,
    liquidity: str,
    owner: str = "Me",
    invested: float = 0.0,
    counts_toward_networth: bool = True,
    notes: Optional[str] = None,
    sort_order: int = 0,
    is_active: bool = True,
) -> int:
    """Create an asset (a place money sits). Returns its id."""
    if not name.strip():
        raise ValueError("asset name is required")
    con = db.connection
    cur = con.execute(
        "INSERT INTO assets(name, category, asset_type, liquidity, owner, invested, "
        "counts_toward_networth, is_active, sort_order, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name.strip(), category, asset_type, liquidity, owner.strip() or "Me",
         invested or 0.0, 1 if counts_toward_networth else 0,
         1 if is_active else 0, sort_order, notes),
    )
    con.commit()
    return cur.lastrowid


def update_asset(db: Database, asset_id: int, **fields) -> None:
    """Patch an asset. Only recognised, provided fields change."""
    allowed = ("name", "category", "asset_type", "liquidity", "owner", "invested",
               "counts_toward_networth", "is_active", "sort_order", "notes")
    sets, params = [], []
    for key in allowed:
        if key in fields:
            value = fields[key]
            if key in ("counts_toward_networth", "is_active"):
                value = 1 if value else 0
            sets.append(f"{key}=?")
            params.append(value)
    if not sets:
        return
    params.append(asset_id)
    con = db.connection
    con.execute(f"UPDATE assets SET {', '.join(sets)} WHERE id=?", params)
    con.commit()


def delete_asset(db: Database, asset_id: int) -> None:
    """Remove an asset and its valuation history."""
    con = db.connection
    con.execute("DELETE FROM asset_valuations WHERE asset_id=?", (asset_id,))
    con.execute("DELETE FROM assets WHERE id=?", (asset_id,))
    con.commit()


def list_assets(db: Database, active_only: bool = False) -> list[Asset]:
    sql = ("SELECT id, name, category, asset_type, liquidity, owner, invested, "
           "counts_toward_networth, is_active, sort_order, notes FROM assets")
    if active_only:
        sql += " WHERE is_active=1"
    # Closed holdings sink to the bottom of every list, still visible for history.
    sql += " ORDER BY is_active DESC, sort_order, category, name"
    return [
        Asset(i, n, c, t, l, o, inv or 0.0, bool(cnt), bool(act), so, note)
        for i, n, c, t, l, o, inv, cnt, act, so, note in db.connection.execute(sql).fetchall()
    ]


# --- liabilities -------------------------------------------------------------

def add_liability(
    db: Database,
    name: str,
    kind: str,
    owner: str = "Me",
    principal: float = 0.0,
    interest_rate: Optional[float] = None,
    emi_amount: float = 0.0,
    start_date=None,
    end_date=None,
    notes: Optional[str] = None,
    sort_order: int = 0,
    is_active: bool = True,
) -> int:
    """Create a liability (loan / EMI / card outstanding). Returns its id."""
    if not name.strip():
        raise ValueError("liability name is required")
    con = db.connection
    cur = con.execute(
        "INSERT INTO liabilities(name, kind, owner, principal, interest_rate, emi_amount, "
        "start_date, end_date, is_active, sort_order, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (name.strip(), kind, owner.strip() or "Me", principal or 0.0, interest_rate,
         emi_amount or 0.0, _as_iso(start_date) if start_date else None,
         _as_iso(end_date) if end_date else None,
         1 if is_active else 0, sort_order, notes),
    )
    con.commit()
    return cur.lastrowid


def update_liability(db: Database, liability_id: int, **fields) -> None:
    allowed = ("name", "kind", "owner", "principal", "interest_rate", "emi_amount",
               "start_date", "end_date", "is_active", "sort_order", "notes")
    sets, params = [], []
    for key in allowed:
        if key in fields:
            value = fields[key]
            if key == "is_active":
                value = 1 if value else 0
            elif key in ("start_date", "end_date") and value:
                value = _as_iso(value)
            sets.append(f"{key}=?")
            params.append(value)
    if not sets:
        return
    params.append(liability_id)
    con = db.connection
    con.execute(f"UPDATE liabilities SET {', '.join(sets)} WHERE id=?", params)
    con.commit()


def delete_liability(db: Database, liability_id: int) -> None:
    con = db.connection
    con.execute("DELETE FROM liability_valuations WHERE liability_id=?", (liability_id,))
    con.execute("DELETE FROM liabilities WHERE id=?", (liability_id,))
    con.commit()


def list_liabilities(db: Database, active_only: bool = False) -> list[Liability]:
    sql = ("SELECT id, name, kind, owner, principal, interest_rate, emi_amount, "
           "start_date, end_date, is_active, sort_order, notes FROM liabilities")
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY is_active DESC, sort_order, kind, name"
    return [
        Liability(i, n, k, o, p or 0.0, rate, emi or 0.0, sd, ed, bool(act), so, note)
        for i, n, k, o, p, rate, emi, sd, ed, act, so, note in db.connection.execute(sql).fetchall()
    ]


# --- snapshots ---------------------------------------------------------------

def record_snapshot(
    db: Database,
    as_of,
    asset_values: Optional[dict[int, float]] = None,
    liability_values: Optional[dict[int, float]] = None,
    invested_values: Optional[dict[int, float]] = None,
) -> None:
    """Record values for ``as_of``, replacing any existing values on that date.

    Pass only the holdings you want to set; others keep whatever they had. This
    is the "update" action — one call per portfolio review.

    ``invested_values`` records how much had been put into each asset by that
    date — for a SIP or a stock position that number keeps rising, so it's dated
    alongside the market value rather than held as one static figure.
    """
    iso = _as_iso(as_of)
    invested_values = invested_values or {}
    con = db.connection
    try:
        for asset_id, value in (asset_values or {}).items():
            invested = invested_values.get(asset_id)
            con.execute(
                "INSERT INTO asset_valuations(asset_id, as_of, value, invested) VALUES (?,?,?,?) "
                "ON CONFLICT(asset_id, as_of) DO UPDATE SET value=excluded.value, "
                "invested=COALESCE(excluded.invested, asset_valuations.invested)",
                (asset_id, iso, float(value),
                 float(invested) if invested is not None else None),
            )
        # Invested can also be recorded on its own (e.g. imported separately).
        for asset_id, invested in invested_values.items():
            if asset_values and asset_id in asset_values:
                continue
            con.execute(
                "INSERT INTO asset_valuations(asset_id, as_of, value, invested) VALUES (?,?,0,?) "
                "ON CONFLICT(asset_id, as_of) DO UPDATE SET invested=excluded.invested",
                (asset_id, iso, float(invested)),
            )
        for liability_id, outstanding in (liability_values or {}).items():
            con.execute(
                "INSERT INTO liability_valuations(liability_id, as_of, outstanding) VALUES (?,?,?) "
                "ON CONFLICT(liability_id, as_of) DO UPDATE SET outstanding=excluded.outstanding",
                (liability_id, iso, float(outstanding)),
            )
        con.commit()
    except Exception:
        con.rollback()
        raise


def delete_snapshot(db: Database, as_of) -> None:
    """Remove every value recorded on ``as_of`` (assets and liabilities)."""
    iso = _as_iso(as_of)
    con = db.connection
    con.execute("DELETE FROM asset_valuations WHERE as_of=?", (iso,))
    con.execute("DELETE FROM liability_valuations WHERE as_of=?", (iso,))
    con.commit()


def snapshot_dates(db: Database) -> list[str]:
    """Every date that has any recorded value, oldest first."""
    rows = db.connection.execute(
        "SELECT as_of FROM asset_valuations "
        "UNION SELECT as_of FROM liability_valuations ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


#: Upper bound used when no ``as_of`` is given — later than any real date.
_MAX_DATE = "9999-12-31"

# Latest row per holding on or before a cutoff: pick the value whose date is the
# max date <= cutoff for that holding. Parameterised on the cutoff so the same
# SQL serves "as of today" and "as of some past snapshot".
_LATEST_ASSETS_SQL = """
    SELECT v.asset_id, v.value
    FROM asset_valuations v
    WHERE v.as_of <= ?
      AND v.as_of = (SELECT MAX(x.as_of) FROM asset_valuations x
                     WHERE x.asset_id = v.asset_id AND x.as_of <= ?)
"""
_LATEST_LIABS_SQL = """
    SELECT v.liability_id, v.outstanding
    FROM liability_valuations v
    WHERE v.as_of <= ?
      AND v.as_of = (SELECT MAX(x.as_of) FROM liability_valuations x
                     WHERE x.liability_id = v.liability_id AND x.as_of <= ?)
"""


def latest_values(db: Database, as_of=None) -> tuple[dict[int, float], dict[int, float]]:
    """Most recent value per asset / liability, on or before ``as_of``.

    Returns ``(asset_values, liability_values)`` keyed by id. Used to pre-fill the
    update form (carry forward what hasn't changed) and to value a holding on a
    date it wasn't explicitly updated on.
    """
    cutoff = _as_iso(as_of) if as_of is not None else _MAX_DATE
    con = db.connection
    assets = dict(con.execute(_LATEST_ASSETS_SQL, (cutoff, cutoff)).fetchall())
    liabilities = dict(con.execute(_LATEST_LIABS_SQL, (cutoff, cutoff)).fetchall())
    return assets, liabilities


# --- analytics ---------------------------------------------------------------

def _countable_asset_ids(db: Database) -> set[int]:
    """Assets that count toward net worth (excludes informational holdings)."""
    return {r[0] for r in db.connection.execute(
        "SELECT id FROM assets WHERE counts_toward_networth=1").fetchall()}


def _active_ids(db: Database) -> tuple[set[int], set[int]]:
    """(active asset ids, active liability ids)."""
    con = db.connection
    return (
        {r[0] for r in con.execute("SELECT id FROM assets WHERE is_active=1").fetchall()},
        {r[0] for r in con.execute("SELECT id FROM liabilities WHERE is_active=1").fetchall()},
    )


def explicit_values(db: Database, as_of) -> tuple[dict[int, float], dict[int, float]]:
    """Values recorded *on exactly* ``as_of`` (no carry-forward)."""
    iso = _as_iso(as_of)
    con = db.connection
    return (
        dict(con.execute(
            "SELECT asset_id, value FROM asset_valuations WHERE as_of=?", (iso,)).fetchall()),
        dict(con.execute(
            "SELECT liability_id, outstanding FROM liability_valuations WHERE as_of=?",
            (iso,)).fetchall()),
    )


def values_on(db: Database, as_of=None) -> tuple[dict[int, float], dict[int, float]]:
    """The values that count on a date — every holding carries its last value forward.

    Retirement needs no special case here. Closing a holding (see
    :func:`retire_asset`) records a **0** on the closing date, and that 0 simply
    carries forward like any other value: the holding counts fully before that
    date and contributes nothing after it. Unticking "Active" without a date
    would be a guess, so Kosha dates the closure instead of inferring it.
    """
    return latest_values(db, as_of)


def retire_asset(db: Database, asset_id: int, as_of=None) -> None:
    """Mark an asset closed and record a 0 for it on ``as_of`` (default today).

    The 0 is what makes net worth drop from that date — you then enter the money
    wherever it actually went (bank, another fund) in the usual update.
    """
    record_snapshot(db, as_of or date.today(), {asset_id: 0.0})
    update_asset(db, asset_id, is_active=False)


def retire_liability(db: Database, liability_id: int, as_of=None) -> None:
    """Mark a loan closed/paid off and record 0 outstanding on ``as_of``."""
    record_snapshot(db, as_of or date.today(), liability_values={liability_id: 0.0})
    update_liability(db, liability_id, is_active=False)


def networth_series(db: Database, carry_forward: bool = True) -> list[NetWorthPoint]:
    """Totals per snapshot date, with growth % against the previous snapshot.

    With ``carry_forward`` (the default) a holding keeps its last known value on
    later dates, so a snapshot that only updated a few holdings still totals the
    whole portfolio — matching how a spreadsheet column is read. Without it, only
    values explicitly recorded on that date are counted.
    """
    countable = _countable_asset_ids(db)
    out: list[NetWorthPoint] = []
    prev_net: Optional[float] = None
    for iso in snapshot_dates(db):
        if carry_forward:
            asset_vals, liab_vals = values_on(db, iso)
        else:
            asset_vals, liab_vals = explicit_values(db, iso)
        assets_total = sum(v for aid, v in asset_vals.items() if aid in countable)
        liabs_total = sum(liab_vals.values())
        net = assets_total - liabs_total
        growth = ((net - prev_net) / abs(prev_net) * 100.0) if prev_net else None
        out.append(NetWorthPoint(iso, assets_total, liabs_total, net, growth))
        prev_net = net
    return out


def current_networth(db: Database) -> NetWorthPoint:
    """The most recent snapshot's totals (zeros when nothing is recorded yet)."""
    series = networth_series(db)
    if series:
        return series[-1]
    return NetWorthPoint(as_of="", assets=0.0, liabilities=0.0, net_worth=0.0, growth_pct=None)


def total_growth_pct(db: Database) -> Optional[float]:
    """Growth from the first snapshot to the latest, as a percentage."""
    series = networth_series(db)
    if len(series) < 2 or not series[0].net_worth:
        return None
    first, last = series[0].net_worth, series[-1].net_worth
    return (last - first) / abs(first) * 100.0


def allocation(db: Database, by: str = "liquidity", as_of=None) -> list[tuple[str, float, float]]:
    """Rows (bucket, amount, percent) of current asset value grouped by ``by``.

    ``by`` is one of liquidity / asset_type / owner / category. Percentages are
    of total counted assets (not net of liabilities), matching the spreadsheet.
    """
    column = _ALLOCATION_COLUMNS.get(by)
    if column is None:
        raise ValueError(f"unknown allocation dimension {by!r}")
    asset_vals, _ = values_on(db, as_of)     # retired holdings don't carry forward
    buckets: dict[str, float] = {}
    rows = db.connection.execute(
        f"SELECT id, {column} FROM assets WHERE counts_toward_networth=1").fetchall()
    for asset_id, bucket in rows:
        value = asset_vals.get(asset_id, 0.0)
        if value:
            buckets[bucket] = buckets.get(bucket, 0.0) + value
    total = sum(buckets.values())
    out = [(b, amt, (amt / total * 100.0) if total else 0.0) for b, amt in buckets.items()]
    out.sort(key=lambda r: r[1], reverse=True)
    return out


_LATEST_INVESTED_SQL = """
    SELECT v.asset_id, v.invested
    FROM asset_valuations v
    WHERE v.as_of <= ? AND v.invested IS NOT NULL
      AND v.as_of = (SELECT MAX(x.as_of) FROM asset_valuations x
                     WHERE x.asset_id = v.asset_id AND x.as_of <= ?
                       AND x.invested IS NOT NULL)
"""


def latest_invested(db: Database, as_of=None) -> dict[int, float]:
    """Most recent recorded *invested* per asset, on or before ``as_of``.

    Falls back to the asset's static ``invested`` for holdings that have never had
    it snapshotted, so a one-off purchase still shows a cost basis.
    """
    cutoff = _as_iso(as_of) if as_of is not None else _MAX_DATE
    dated = dict(db.connection.execute(_LATEST_INVESTED_SQL, (cutoff, cutoff)).fetchall())
    out = {a.id: a.invested for a in list_assets(db) if a.invested}
    out.update(dated)                     # a dated figure always wins
    return out


def invested_vs_current(db: Database, as_of=None) -> list[tuple[str, float, float, float]]:
    """Rows (asset name, invested, current, gain) using the cost basis on that date."""
    asset_vals, _ = latest_values(db, as_of)
    invested_by_id = latest_invested(db, as_of)
    out = []
    for asset in list_assets(db):
        current = asset_vals.get(asset.id, 0.0)
        invested = invested_by_id.get(asset.id, 0.0)
        if invested or current:
            out.append((asset.name, invested, current, current - invested))
    out.sort(key=lambda r: r[2], reverse=True)
    return out


def gain_series(db: Database) -> list[tuple[str, float, float, float]]:
    """Rows (date, invested, current value, gain) across every snapshot.

    Both figures move over time — you keep investing *and* the market moves — so
    this shows contributions and growth side by side rather than conflating them.
    Only assets that count toward net worth are included.
    """
    countable = _countable_asset_ids(db)
    out = []
    for iso in snapshot_dates(db):
        asset_vals, _ = values_on(db, iso)
        invested_by_id = latest_invested(db, iso)
        value = sum(v for aid, v in asset_vals.items() if aid in countable)
        invested = sum(v for aid, v in invested_by_id.items()
                       if aid in countable and aid in asset_vals)
        out.append((iso, invested, value, value - invested))
    return out


def months_between(start, end) -> int:
    """Whole months from ``start`` to ``end`` (0 if end is on/before start)."""
    if start is None or end is None:
        return 0
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    if isinstance(end, str):
        end = date.fromisoformat(end[:10])
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1                       # a partial month doesn't count as paid
    return max(0, months)


def monthly_interest(principal: float, annual_rate: Optional[float]) -> float:
    """One month's interest on ``principal`` — the floor an EMI must clear."""
    return (principal or 0.0) * (annual_rate or 0.0) / 100.0 / 12.0


def required_emi(principal: float, annual_rate: Optional[float], months: int) -> Optional[float]:
    """The EMI that clears ``principal`` in ``months`` — the standard EMI formula."""
    if not principal or months <= 0:
        return None
    rate = (annual_rate or 0.0) / 100.0 / 12.0
    if rate <= 0:
        return round(principal / months, 2)
    growth = (1.0 + rate) ** months
    return round(principal * rate * growth / (growth - 1.0), 2)


def emi_shortfall(principal: float, annual_rate: Optional[float],
                  emi: float) -> Optional[float]:
    """How far an EMI falls short of covering monthly interest (None if it's fine).

    An EMI at or below one month's interest never reduces the balance — the debt
    grows instead of amortizing, which is almost always a data-entry mistake.
    """
    if not principal or not emi or not annual_rate:
        return None
    interest = monthly_interest(principal, annual_rate)
    return round(interest - emi, 2) if emi <= interest else None


def estimate_outstanding(principal: float, annual_rate: Optional[float],
                         emi: float, start_date, as_of) -> Optional[float]:
    """Loan balance still owed on ``as_of``, by standard amortization.

    ``balance = P(1+r)^n − EMI·((1+r)^n − 1)/r`` where *r* is the monthly rate and
    *n* the number of EMIs paid. Returns None when there isn't enough information
    (no principal, no EMI, or no start date), and never returns less than 0.

    This is an *estimate* — real schedules move with rate changes, prepayments and
    payment dates — so it's offered as a starting figure you can overwrite, not as
    a substitute for the number on your statement.
    """
    if not principal or not emi or start_date is None:
        return None
    n = months_between(start_date, as_of)
    if n <= 0:
        return float(principal)
    rate = (annual_rate or 0.0) / 100.0 / 12.0
    if rate <= 0:
        balance = principal - emi * n
    else:
        growth = (1.0 + rate) ** n
        balance = principal * growth - emi * (growth - 1.0) / rate
    return max(0.0, round(balance, 2))


def estimate_outstanding_for(db: Database, liability_id: int, as_of) -> Optional[float]:
    """``estimate_outstanding`` for a stored liability, or None if not computable."""
    liab = next((l for l in list_liabilities(db) if l.id == liability_id), None)
    if liab is None:
        return None
    return estimate_outstanding(liab.principal, liab.interest_rate, liab.emi_amount,
                                liab.start_date, as_of)


def record_estimated_outstanding(db: Database, liability_id: int,
                                 as_of=None) -> Optional[float]:
    """Work out a loan's balance on ``as_of`` and record it as a snapshot.

    Saves a trip to the update screen when a loan's terms are enough to compute
    its balance. Returns the value recorded, or None when it isn't computable
    (no principal, EMI or start date).
    """
    as_of = as_of or date.today()
    estimate = estimate_outstanding_for(db, liability_id, as_of)
    if estimate is None:
        return None
    record_snapshot(db, as_of, liability_values={liability_id: estimate})
    return estimate


def monthly_obligations(db: Database) -> float:
    """Total EMI across active liabilities — the monthly debt commitment."""
    row = db.connection.execute(
        "SELECT COALESCE(SUM(emi_amount), 0) FROM liabilities WHERE is_active=1").fetchone()
    return row[0] or 0.0


def debt_to_asset(db: Database) -> Optional[float]:
    """Liabilities as a percentage of assets at the latest snapshot."""
    point = current_networth(db)
    if not point.assets:
        return None
    return point.liabilities / point.assets * 100.0


#: Attribute columns shown alongside the dates in :func:`snapshot_matrix`.
MATRIX_ATTRIBUTES = ["Kind", "Category", "Type", "Liquidity", "Owner", "Active"]


def snapshot_matrix(db: Database):
    """(dates, rows) where each row is (name, [attributes], [value per date]).

    Reads like the spreadsheet: holdings down the side with their attributes, then
    one column per snapshot date. Liabilities appear as negative values so the grid
    sums to net worth.
    """
    dates = snapshot_dates(db)
    con = db.connection
    rows: list[tuple[str, list[str], list[Optional[float]]]] = []
    for asset in list_assets(db):
        recorded = dict(con.execute(
            "SELECT as_of, value FROM asset_valuations WHERE asset_id=?", (asset.id,)).fetchall())
        attributes = ["Asset", asset.category, asset.asset_type, asset.liquidity,
                      asset.owner, "yes" if asset.is_active else "retired"]
        rows.append((asset.name, attributes, [recorded.get(d) for d in dates]))
    for liab in list_liabilities(db):
        recorded = dict(con.execute(
            "SELECT as_of, outstanding FROM liability_valuations WHERE liability_id=?",
            (liab.id,)).fetchall())
        attributes = ["Liability", liab.kind, "", "", liab.owner,
                      "yes" if liab.is_active else "closed"]
        rows.append((liab.name, attributes,
                     [(-recorded[d] if d in recorded else None) for d in dates]))
    return dates, rows


# --- insurance (informational; never part of net worth) ----------------------

def add_insurance(db: Database, name: str, kind: Optional[str] = None,
                  premium_per_year: float = 0.0, coverage: float = 0.0,
                  renews_on=None, owner: str = "Me", notes: Optional[str] = None) -> int:
    if not name.strip():
        raise ValueError("insurance name is required")
    con = db.connection
    cur = con.execute(
        "INSERT INTO insurance(name, kind, premium_per_year, coverage, renews_on, owner, notes) "
        "VALUES (?,?,?,?,?,?,?)",
        (name.strip(), kind, premium_per_year or 0.0, coverage or 0.0,
         _as_iso(renews_on) if renews_on else None, owner.strip() or "Me", notes),
    )
    con.commit()
    return cur.lastrowid


def list_insurance(db: Database) -> list[Insurance]:
    rows = db.connection.execute(
        "SELECT id, name, kind, premium_per_year, coverage, renews_on, owner, notes "
        "FROM insurance ORDER BY name").fetchall()
    return [Insurance(i, n, k, p or 0.0, c or 0.0, r, o, note)
            for i, n, k, p, c, r, o, note in rows]


def delete_insurance(db: Database, insurance_id: int) -> None:
    con = db.connection
    con.execute("DELETE FROM insurance WHERE id=?", (insurance_id,))
    con.commit()


def insurance_summary(db: Database) -> tuple[float, float]:
    """(total annual premium, total coverage) across policies."""
    row = db.connection.execute(
        "SELECT COALESCE(SUM(premium_per_year),0), COALESCE(SUM(coverage),0) FROM insurance"
    ).fetchone()
    return (row[0] or 0.0, row[1] or 0.0)


# --- settings ----------------------------------------------------------------

def get_setting(db: Database, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.connection.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(db: Database, key: str, value: str) -> None:
    con = db.connection
    con.execute(
        "INSERT INTO app_settings(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    con.commit()
