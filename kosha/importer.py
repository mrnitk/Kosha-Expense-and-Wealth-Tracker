"""Import pipeline: parser output -> enrichment -> deduplicated insert.

Ties the pieces together for Phase 2:

    parser.parse(file)  ->  RawTransaction stream
    features.derive_*   ->  txn_type, merchant_keyword
    dedup_hash + UNIQUE ->  re-importing overlapping statements skips duplicates

Everything runs inside one transaction per file so a failed import leaves no
half-written batch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import features
from .db import Database
from .parsers import REGISTRY
from .parsers.base import BaseParser

# Friendly account names auto-created when bulk-importing by institution.
INSTITUTION_LABELS = {
    "hdfc_bank": "HDFC Bank",
    "hdfc_card": "HDFC Card",
}


@dataclass
class ImportResult:
    account_id: int
    batch_id: int
    parsed: int          # rows the parser produced
    inserted: int        # new rows written
    skipped_duplicates: int

    def __str__(self) -> str:
        return (
            f"parsed {self.parsed}, inserted {self.inserted}, "
            f"skipped {self.skipped_duplicates} duplicate(s)"
        )


def get_or_create_account(db: Database, name: str, account_type: str, institution: str) -> int:
    """Return the id of the matching account, creating it if absent."""
    con = db.connection
    row = con.execute(
        "SELECT id FROM accounts WHERE name = ? AND institution = ?",
        (name, institution),
    ).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO accounts(name, account_type, institution) VALUES (?,?,?)",
        (name, account_type, institution),
    )
    return cur.lastrowid


def import_stream(db: Database, source_file: str, account_id: int, raws, *, force_card: bool) -> ImportResult:
    """Insert a stream of ``RawTransaction`` for ``account_id`` (one batch, deduped).

    Shared core for both the built-in parsers and the generic mapping importer.
    On a credit-card source every line is a card transaction; otherwise the
    narration's own UPI/NEFT/etc. prefix classifies it.
    """
    con = db.connection
    parsed = inserted = skipped = 0
    try:
        cur = con.execute(
            "INSERT INTO import_batches(source_file, account_id, row_count) VALUES (?,?,0)",
            (source_file, account_id),
        )
        batch_id = cur.lastrowid

        from . import categorization
        for raw in raws:
            parsed += 1
            txn_type = features.CARD if force_card else features.derive_txn_type(raw.raw_description)
            keyword = features.derive_merchant_keyword(raw.raw_description, txn_type)
            keyword = categorization.apply_alias(db, keyword)   # fold merged keywords
            dhash = features.dedup_hash(
                raw.txn_date, raw.amount, raw.direction, raw.raw_description, account_id
            )
            result = con.execute(
                """INSERT OR IGNORE INTO transactions
                   (txn_date, raw_description, amount, direction, account_id,
                    txn_type, merchant_keyword, import_batch_id, dedup_hash)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    raw.txn_date.isoformat(), raw.raw_description, raw.amount,
                    raw.direction, account_id, txn_type, keyword, batch_id, dhash,
                ),
            )
            if result.rowcount == 1:
                inserted += 1
            else:
                skipped += 1

        con.execute("UPDATE import_batches SET row_count = ? WHERE id = ?", (inserted, batch_id))
        con.commit()
    except Exception:
        con.rollback()
        raise

    return ImportResult(
        account_id=account_id, batch_id=batch_id,
        parsed=parsed, inserted=inserted, skipped_duplicates=skipped,
    )


def import_file(db: Database, parser: BaseParser, path: Path, account_id: int) -> ImportResult:
    """Parse ``path`` with ``parser`` and insert new transactions for ``account_id``."""
    path = Path(path)
    return import_stream(
        db, path.name, account_id, parser.parse(path),
        force_card=(parser.account_type == "credit_card"),
    )


@dataclass
class TemplateResult:
    """Aggregate outcome of a standard-template import (possibly many sources)."""

    per_source: list[tuple[str, ImportResult]]
    problems: list[str] = field(default_factory=list)   # rows skipped by validation

    @property
    def total_parsed(self) -> int:
        return sum(r.parsed for _s, r in self.per_source)

    @property
    def total_inserted(self) -> int:
        return sum(r.inserted for _s, r in self.per_source)

    @property
    def total_skipped(self) -> int:
        return sum(r.skipped_duplicates for _s, r in self.per_source)

    @property
    def has_problems(self) -> bool:
        return bool(self.problems)

    def summary(self) -> str:
        if not self.per_source and not self.problems:
            return "No transactions found in the template."
        lines = [
            f"Imported {self.total_inserted} new transaction(s), "
            f"{self.total_skipped} duplicate(s) skipped."
        ]
        if len(self.per_source) > 1:
            for source, r in self.per_source:
                lines.append(f"  • {source}: {r.inserted} new, {r.skipped_duplicates} skipped")
        if self.problems:
            lines.append("")
            lines.append(f"⚠ {len(self.problems)} row(s) were skipped due to problems:")
            lines.extend(f"  • {p}" for p in self.problems[:15])
            if len(self.problems) > 15:
                lines.append(f"  … and {len(self.problems) - 15} more")
        return "\n".join(lines)


def import_template(db: Database, path) -> TemplateResult:
    """Import a filled standard template (any bank), one account per Source value."""
    from collections import OrderedDict

    from . import template_import
    path = Path(path)
    records, problems = template_import.read_template(path)

    groups: "OrderedDict[tuple[str, str], list]" = OrderedDict()
    for raw, source, account_type in records:
        groups.setdefault((source, account_type), []).append(raw)

    results: list[tuple[str, ImportResult]] = []
    for (source, account_type), raws in groups.items():
        account_id = get_or_create_account(db, source, account_type, "template")
        res = import_stream(
            db, path.name, account_id, raws,
            force_card=(account_type == "credit_card"),
        )
        results.append((source, res))
    return TemplateResult(per_source=results, problems=problems)


def detect_parser(path) -> BaseParser | None:
    """Return the first registered parser that recognizes ``path``."""
    path = Path(path)
    for parser_cls in REGISTRY.values():
        parser = parser_cls()
        try:
            if parser.can_parse(path):
                return parser
        except Exception:
            continue
    return None


@dataclass
class BulkResult:
    imported: list[tuple[str, ImportResult]]     # (filename, result)
    unrecognized: list[str]                      # filenames with no parser
    failed: list[tuple[str, str]]                # (filename, error message)

    @property
    def total_inserted(self) -> int:
        return sum(r.inserted for _f, r in self.imported)

    @property
    def total_skipped(self) -> int:
        return sum(r.skipped_duplicates for _f, r in self.imported)

    def summary(self) -> str:
        lines = [
            f"{len(self.imported)} file(s) imported: "
            f"{self.total_inserted} new, {self.total_skipped} duplicate(s) skipped."
        ]
        if self.unrecognized:
            lines.append(f"Unrecognized ({len(self.unrecognized)}): " + ", ".join(self.unrecognized))
        if self.failed:
            lines.append("Failed: " + "; ".join(f"{f}: {e}" for f, e in self.failed))
        return "\n".join(lines)


#: What each clear scope wipes, in the order the tables must be emptied.
CLEAR_SCOPES = {
    "transactions": ["transactions", "import_batches"],
    "expenses": ["transactions", "import_batches", "category_rules",
                 "keyword_aliases", "accounts"],
    "networth_values": ["asset_valuations", "liability_valuations"],
    "networth": ["asset_valuations", "liability_valuations", "assets",
                 "liabilities", "insurance"],
    "all": ["transactions", "import_batches", "category_rules", "keyword_aliases",
            "accounts", "asset_valuations", "liability_valuations", "assets",
            "liabilities", "insurance"],
}

#: Tables counted when reporting what a clear removed.
_COUNT_TABLES = ("transactions", "category_rules", "accounts", "assets",
                 "liabilities", "asset_valuations", "liability_valuations",
                 "insurance")


def data_counts(db: Database) -> dict[str, int]:
    """Row counts per table, so the clear dialog can say what's at stake."""
    con = db.connection
    counts = {}
    for table in _COUNT_TABLES:
        try:
            counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except Exception:
            counts[table] = 0          # table not present in an older vault
    return counts


def clear_data(db: Database, scope: str = "transactions") -> dict[str, int]:
    """Wipe data for a fresh start. Returns the row counts removed per table.

    ``scope``:
        * ``"transactions"`` — expense transactions + import batches, keeping your
          categorization rules and accounts so a re-import re-applies them.
        * ``"expenses"`` — all expense data: transactions, rules, keywords, accounts.
        * ``"networth_values"`` — every dated snapshot, keeping the assets and
          liabilities themselves so you can start the history again.
        * ``"networth"`` — all net-worth data: holdings, snapshots and insurance.
        * ``"all"`` — everything (expenses and net worth).

    Irreversible. Runs as one transaction, then reclaims file space with VACUUM.
    """
    tables = CLEAR_SCOPES.get(scope)
    if tables is None:
        raise ValueError(f"unknown scope {scope!r} (expected one of "
                         f"{', '.join(sorted(CLEAR_SCOPES))})")
    con = db.connection
    before = data_counts(db)
    try:
        for table in tables:
            con.execute(f"DELETE FROM {table}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    after = data_counts(db)
    # VACUUM can't run inside a transaction; sqlcipher3 auto-opens one, so commit
    # first (done above) then reclaim space outside it.
    con.execute("VACUUM")
    return {table: before[table] - after[table]
            for table in before if before[table] - after[table] > 0}


def import_paths(db: Database, paths) -> BulkResult:
    """Bulk-import many statement files, auto-filing each by institution.

    Each file's parser is auto-detected; transactions land in a per-institution
    account (created on first sight). Unrecognized or failing files are reported
    but don't abort the batch.
    """
    imported: list[tuple[str, ImportResult]] = []
    unrecognized: list[str] = []
    failed: list[tuple[str, str]] = []

    for raw_path in paths:
        path = Path(raw_path)
        parser = detect_parser(path)
        if parser is None:
            unrecognized.append(path.name)
            continue
        try:
            name = INSTITUTION_LABELS.get(parser.institution, parser.institution)
            account_id = get_or_create_account(db, name, parser.account_type, parser.institution)
            result = import_file(db, parser, path, account_id)
            imported.append((path.name, result))
        except Exception as exc:
            failed.append((path.name, str(exc)))

    return BulkResult(imported=imported, unrecognized=unrecognized, failed=failed)
