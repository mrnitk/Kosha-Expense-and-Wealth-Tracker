-- Kosha schema v1
-- Applied inside the encrypted (SQLCipher) database only.

-- Institutions the user has statements from; maps to a parser class.
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,              -- e.g. 'HDFC Savings', 'Amex Card'
    account_type  TEXT NOT NULL,              -- 'bank' | 'credit_card'
    institution   TEXT NOT NULL               -- maps to parser class
);

-- One row per imported file.
CREATE TABLE IF NOT EXISTS import_batches (
    id           INTEGER PRIMARY KEY,
    source_file  TEXT NOT NULL,
    account_id   INTEGER REFERENCES accounts(id),
    imported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_count    INTEGER
);

-- All statements land here with a common schema.
CREATE TABLE IF NOT EXISTS transactions (
    id                    INTEGER PRIMARY KEY,
    txn_date              DATE NOT NULL,
    raw_description       TEXT NOT NULL,       -- verbatim from statement, never modified
    amount                REAL NOT NULL,
    direction             TEXT NOT NULL,       -- 'debit' | 'credit'
    account_id            INTEGER NOT NULL REFERENCES accounts(id),
    txn_type              TEXT,                -- 'UPI'|'NEFT'|'IMPS'|'CARD'|'ATM'|'SI'|'OTHER'
    merchant_keyword      TEXT,                -- extracted, normalized (e.g. 'SWIGGY')
    category_override     TEXT,                -- manual per-txn override; NULL = use rules
    sub_category_override TEXT,
    tag_override          TEXT,                -- manual per-txn tag; NULL = inherit rule
    excluded_override     INTEGER,             -- manual per-txn exclude; NULL = inherit rule
    note                  TEXT,                -- free-form per-transaction note
    import_batch_id       INTEGER REFERENCES import_batches(id),
    dedup_hash            TEXT UNIQUE          -- hash(date+amount+norm desc+account)
);

-- Merge similar merchant keywords under one canonical name. Applied when the
-- keyword is written (import + Merge action), so merchant_keyword ends up holding
-- the canonical value and all existing queries group correctly.
CREATE TABLE IF NOT EXISTS keyword_aliases (
    raw_keyword       TEXT PRIMARY KEY,        -- a normalized keyword to fold away
    canonical_keyword TEXT NOT NULL            -- the keyword it becomes
);

-- User-controlled keyword -> category mappings; applied retroactively.
CREATE TABLE IF NOT EXISTS category_rules (
    id            INTEGER PRIMARY KEY,
    keyword       TEXT NOT NULL,              -- matched against merchant_keyword
    category      TEXT NOT NULL,
    sub_category  TEXT,
    tag           TEXT,                       -- free-form tag, resolved like sub_category
    priority      INTEGER DEFAULT 0,          -- higher priority wins on conflicts
    excluded      INTEGER DEFAULT 0,          -- 1 = hide these txns from visuals + table
    direction     TEXT                        -- NULL = any; 'debit'|'credit' = scoped
);

-- === Net-worth tracking =====================================================
-- Manually maintained holdings, valued at points in time. Net worth is
-- assets - liabilities at a given snapshot date; ``asset_valuations`` /
-- ``liability_valuations`` hold one row per (holding, date) so history is a
-- series of dated snapshots (like columns in a spreadsheet).

CREATE TABLE IF NOT EXISTS assets (
    id                     INTEGER PRIMARY KEY,
    name                   TEXT NOT NULL,          -- 'HDFC-Cash', 'Stocks(Me)'
    category               TEXT NOT NULL,          -- Bank | Stocks | Mutual funds | PF | NPS | Other
    asset_type             TEXT NOT NULL,          -- Cash | Debt | Equity | Hybrid
    liquidity              TEXT NOT NULL,          -- High | Medium | Low | Lowest
    owner                  TEXT NOT NULL DEFAULT 'Me',
    invested               REAL DEFAULT 0,         -- cost basis, for gain calc
    counts_toward_networth INTEGER NOT NULL DEFAULT 1,
    is_active              INTEGER NOT NULL DEFAULT 1,
    sort_order             INTEGER DEFAULT 0,
    notes                  TEXT
);

-- ``value`` is what the holding was worth on that date; ``invested`` is how much
-- had been put in by then. Both move for SIPs and stocks (you keep investing and
-- the market moves), so both are dated — a single cost basis would make gains
-- wrong for every past date. NULL invested falls back to assets.invested.
CREATE TABLE IF NOT EXISTS asset_valuations (
    id       INTEGER PRIMARY KEY,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    as_of    DATE NOT NULL,
    value    REAL NOT NULL,
    invested REAL,
    UNIQUE(asset_id, as_of)                        -- one value per asset per date
);

CREATE TABLE IF NOT EXISTS liabilities (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,                   -- 'Car loan - HDFC'
    kind          TEXT NOT NULL,                   -- Home | Car | Personal | Credit card | Other
    owner         TEXT NOT NULL DEFAULT 'Me',
    principal     REAL DEFAULT 0,                  -- original sanctioned amount
    interest_rate REAL,                            -- annual %, informational
    emi_amount    REAL DEFAULT 0,                  -- monthly obligation
    start_date    DATE,
    end_date      DATE,
    is_active     INTEGER NOT NULL DEFAULT 1,
    sort_order    INTEGER DEFAULT 0,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS liability_valuations (
    id           INTEGER PRIMARY KEY,
    liability_id INTEGER NOT NULL REFERENCES liabilities(id) ON DELETE CASCADE,
    as_of        DATE NOT NULL,
    outstanding  REAL NOT NULL,                    -- balance still owed
    UNIQUE(liability_id, as_of)
);

-- Policies are tracked for cover/premium visibility only; they are never part
-- of the net-worth total.
CREATE TABLE IF NOT EXISTS insurance (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    kind             TEXT,                          -- Life | Medical | Term | Other
    premium_per_year REAL DEFAULT 0,
    coverage         REAL DEFAULT 0,
    renews_on        DATE,
    owner            TEXT NOT NULL DEFAULT 'Me',
    notes            TEXT
);

-- Small key/value store for app preferences (auto-lock minutes, blur, ...).
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_asset_val_asset ON asset_valuations(asset_id, as_of);
CREATE INDEX IF NOT EXISTS idx_liab_val_liab   ON liability_valuations(liability_id, as_of);

CREATE INDEX IF NOT EXISTS idx_txn_date        ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_txn_account     ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_txn_keyword     ON transactions(merchant_keyword);
CREATE INDEX IF NOT EXISTS idx_rules_keyword   ON category_rules(keyword);

-- Effective category resolution, evaluated live so new/edited rules apply
-- retroactively to all history with no data rewrite.
--
-- Two levels:
--   * category     in {Income, Expense, Savings, Transfer}. Defaults by
--     direction (credit -> Income, debit -> Expense); a rule/override can
--     reclassify (e.g. an investment debit as Savings, or a credit-card bill
--     payment as Transfer so it's kept out of the income/expense/savings math).
--   * sub_category  free-text detail (Food, Rent, Investments, ...).
-- Precedence: manual override > best matching rule > direction default.
--
-- Rules are direction-aware: a rule with direction NULL applies to both
-- directions of its keyword; a rule scoped to 'debit'/'credit' applies only to
-- that side and OUTRANKS the generic rule. This lets one merchant map two ways
-- (e.g. ZERODHA debit -> Savings, ZERODHA credit -> Income). "best matching
-- rule" = for the txn's direction, direction-specific beats generic, then higher
-- priority, then newest id. Each rule is expanded to the direction(s) it covers
-- and ranked within (keyword, direction).
--
-- ``effective_excluded`` (0/1): a per-txn override wins, else the rule's flag,
-- else 0. Excluded transactions are hidden from every visual and the table
-- (analytics filters them out by default).
-- Dropped-and-recreated so re-applying the schema (on migration) always picks
-- up a changed definition.
DROP VIEW IF EXISTS v_transactions_resolved;
CREATE VIEW v_transactions_resolved AS
SELECT
    t.id, t.txn_date, t.raw_description, t.amount, t.direction,
    t.account_id, t.txn_type, t.merchant_keyword,
    t.category_override, t.sub_category_override,
    t.tag_override, t.excluded_override,
    t.import_batch_id, t.dedup_hash,
    COALESCE(
        t.category_override,
        br.category,
        CASE t.direction WHEN 'credit' THEN 'Income' ELSE 'Expense' END
    ) AS effective_category,
    COALESCE(t.sub_category_override, br.sub_category) AS effective_sub_category,
    COALESCE(t.tag_override, br.tag) AS effective_tag,
    COALESCE(t.excluded_override, br.excluded, 0) AS effective_excluded,
    t.note AS note,
    a.name AS account_name,          -- 'source': which bank/card the txn came from
    a.account_type AS account_type   -- 'bank' | 'credit_card'
FROM transactions t
LEFT JOIN accounts a ON a.id = t.account_id
LEFT JOIN (
    SELECT keyword, tgt_direction, category, sub_category, tag, excluded FROM (
        SELECT r.keyword AS keyword, d.dir AS tgt_direction,
               r.category AS category, r.sub_category AS sub_category,
               r.tag AS tag, r.excluded AS excluded,
               ROW_NUMBER() OVER (
                   PARTITION BY r.keyword, d.dir
                   ORDER BY (CASE WHEN r.direction IS NULL THEN 0 ELSE 1 END) DESC,
                            r.priority DESC, r.id DESC
               ) AS rn
        FROM category_rules r
        JOIN (SELECT 'debit' AS dir UNION ALL SELECT 'credit' AS dir) d
          ON (r.direction IS NULL OR r.direction = d.dir)
    ) WHERE rn = 1
) br ON br.keyword = t.merchant_keyword AND br.tgt_direction = t.direction;
