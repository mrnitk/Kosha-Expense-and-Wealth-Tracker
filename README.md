# Kosha — Private, Secure & Offline Expense and Wealth Tracker

Kosha is a **fully local, encrypted** personal finance tracker for Windows. It
does two things, both entirely offline:

- **Expenses** — import your bank and credit-card statements and Kosha turns them
  into a categorized, searchable picture of your spending.
- **Net worth** — track assets and liabilities as dated snapshots (bank, stocks,
  SIPs, PF, NPS, real estate, loans) and watch your wealth trend over time.

Everything runs **with zero network calls and no cloud**. Your data lives only on
your machine, encrypted at rest.

Built for Indian bank statements (UPI / NEFT / IMPS / cards / SI mandates) and
₹ Indian number formatting, but the generic importer works with any bank.

> **Privacy by design:** the entire database is encrypted with SQLCipher
> (AES‑256). It's unlocked with a master password whose key is derived via Argon2
> and never written to disk. The app makes no network requests, ever.

---

## Features

**Import**
- Auto-detecting parsers for HDFC **bank** and **credit-card** `.xls` statements.
- A **standard template** importer (`Date | Transaction Remarks | Debit | Credit
  | Source | Account Type`) so you can bring in **any bank** from CSV / XLSX /
  XLS — one file can hold multiple accounts via the `Source` column.
- Automatic **deduplication** — re-importing overlapping statements skips
  duplicates.

**Categorization**
- **Direction-aware keyword rules** that apply **retroactively** to all history
  (resolved live in a SQL view — no data rewrite). A merchant can map two ways,
  e.g. an investment platform's debits → *Savings*, its credits → *Income*.
- Categories: **Income / Expense / Savings / Transfer** (credit-card bill
  payments are Transfers, kept out of the income/expense math to avoid
  double-counting), plus free-text **sub-category** and a single **tag**.
- **Keyword merge** to group messy variants (`A S`, `NEST`, `A S NEST` → one
  keyword) — retroactive and auto-applied to future imports.
- **Exclude** noisy keywords from all visuals and tables.

**Dashboard & insights**
- Interactive **Plotly** charts: income vs expense vs savings, expense by
  sub-category, spend share (donut), top merchants.
- Filters: date range, category, sub-category, **source (account)**, and a
  **global search** across description / keyword / amount.
- Monthly **average / min / max** stats, and a drill-down transaction table.

**Net worth**
- Track **assets** (bank, stocks, mutual funds, PF, NPS …) and **liabilities**
  (home/car/personal loans, credit-card outstanding) as dated **snapshots** —
  net worth is assets − liabilities on each date.
- **Trend over time** with growth per snapshot and overall, **allocation** by
  liquidity / type / owner / category, **invested vs current** gains, total
  **monthly EMI** and **debt-to-asset** ratio.
- Bring your spreadsheet history in one go: the **net-worth template** takes one
  row per holding and one column per date (`Jun'26`, `2026-06`, …). Export
  round-trips, so it doubles as a readable backup.
- **Insurance** policies tracked for premium/cover — never counted as assets.

**More**
- **Recurring / subscriptions** detection — surfaces regular payments (SIPs,
  EMIs, rent, subscriptions) with cadence, next-date estimate, and your total
  **monthly committed outflow**.
- **Encrypted backup & restore** of the whole vault (a password-protected copy).
- Consistent light theme; Indian number formatting (`3,00,000`).

**Security**
- **Change master password** any time (re-keys the vault and adopts the current
  Argon2 settings), with a strength meter.
- **Auto-lock** after idle, **Lock now** (`Ctrl+L`), and a **privacy mask**
  (`Ctrl+H`) that hides every amount on screen, charts included.
- **Failed-attempt backoff** that survives restarting the app.

---

## Tech stack

| Area | Choice |
|------|--------|
| Language | Python 3.14 |
| UI | PySide6 (Qt Widgets), Plotly charts in a `QWebEngineView` |
| Encryption / DB | SQLite + SQLCipher (AES‑256) via `sqlcipher3-wheels`; Argon2 key derivation (`argon2-cffi`) |
| Statement parsing | `xlrd` (.xls), `openpyxl` (.xlsx), stdlib `csv` |
| Packaging | PyInstaller (onedir) + Inno Setup installer (Windows) |
| Data location | `%APPDATA%\Kosha\` (survives updates) |

---

## Getting started (from source)

Requires **Python 3.14** on Windows.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m kosha
```

On first launch you set a **master password** — this creates the encrypted vault
at `%APPDATA%\Kosha\`. There is no password recovery (that's the point), so keep
it safe and use **File ▸ Backup vault** for a spare copy.

### Importing your statements
- **File ▸ Import statements…** — drop in HDFC `.xls` files (auto-detected).
- **File ▸ Download blank template…**, fill it in, then **Import from template…**
  — works for any bank; set the `Source` column to label each account.

---

## Build a Windows installer

See [PACKAGING.md](PACKAGING.md). In short:

```bash
python -m PyInstaller --noconfirm --clean kosha.spec      # -> dist\Kosha\
# then, with Inno Setup 6 installed:
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\kosha.iss
```

---

## Development

```bash
pip install -r requirements.txt
pytest -q          # ~200 tests
```

Design notes:
- **Raw statement text is immutable** — transaction type, merchant keyword,
  category and tag are all *derived* and re-derivable.
- Category resolution lives in the `v_transactions_resolved` **SQL view**, so
  rule/keyword changes apply to all history instantly.
- The encryption key is set as the SQLCipher pragma at unlock and dropped from
  memory on close.

---

## Privacy & data

- **Nothing leaves your machine.** No telemetry, no accounts, no sync.
- The encrypted database (`kosha.db`) and its salt live in `%APPDATA%\Kosha\` and
  are **git-ignored** — real financial data is never part of this repository.
- The only sample data in the repo is **fabricated** test fixtures
  (`tests/fixtures/`), used to test the parsers.

---

## License

[MIT](LICENSE) © 2026 Nitesh Kumar
