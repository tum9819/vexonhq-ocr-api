-- ============================================================
-- VEXONHQ Phase 12 — Bank Statement Import
-- Run in Supabase SQL Editor
-- ============================================================

-- ── 1. bank_statement_entries ────────────────────────────────
CREATE TABLE IF NOT EXISTS public.bank_statement_entries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    import_batch_id UUID,                          -- groups rows from same upload
    txn_date        DATE        NOT NULL,
    description     TEXT,                          -- raw text from PDF
    debit           NUMERIC(12,2) NOT NULL DEFAULT 0,
    credit          NUMERIC(12,2) NOT NULL DEFAULT 0,
    balance         NUMERIC(12,2),
    -- derived
    direction       TEXT GENERATED ALWAYS AS (
                        CASE WHEN credit > 0 THEN 'income' ELSE 'expense' END
                    ) STORED,
    amount          NUMERIC(12,2) GENERATED ALWAYS AS (
                        GREATEST(credit, debit)
                    ) STORED,
    -- classification
    category_code   TEXT,
    source_type     TEXT DEFAULT 'bank_statement', -- rider_income_grab, staff_salary, etc.
    match_status    TEXT NOT NULL DEFAULT 'auto'
                        CHECK (match_status IN ('auto','manual','needs_review')),
    matched_invoice_id UUID REFERENCES public.vendor_bills(id) ON DELETE SET NULL,
    branch_code     TEXT NOT NULL DEFAULT 'thawi_watthana',
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bse_txn_date   ON public.bank_statement_entries (txn_date);
CREATE INDEX IF NOT EXISTS idx_bse_status     ON public.bank_statement_entries (match_status);
CREATE INDEX IF NOT EXISTS idx_bse_batch      ON public.bank_statement_entries (import_batch_id);
CREATE INDEX IF NOT EXISTS idx_bse_direction  ON public.bank_statement_entries (direction);

-- ── 2. statement_rules ───────────────────────────────────────
-- Learned rules: name/keyword/amount → category + source_type
CREATE TABLE IF NOT EXISTS public.statement_rules (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_type    TEXT NOT NULL CHECK (rule_type IN ('keyword','name','amount_pattern')),
    match_value  TEXT NOT NULL,    -- keyword / name fragment / amount as text
    direction    TEXT NOT NULL CHECK (direction IN ('income','expense')),
    category_code TEXT NOT NULL,
    source_type  TEXT,             -- rider_income_grab, rider_income_lineman, etc.
    priority     INT  NOT NULL DEFAULT 10,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (rule_type, match_value)
);

-- ── 3. Seed default rules (confirmed by TUM 2026-05-15) ──────
INSERT INTO public.statement_rules (rule_type, match_value, direction, category_code, source_type, priority)
VALUES
    -- รายรับ: Delivery platforms
    ('keyword', 'ไลน์ เพย์',     'income',  'delivery_income', 'rider_income_lineman', 100),
    ('keyword', 'LINE PAY',       'income',  'delivery_income', 'rider_income_lineman', 100),
    ('keyword', 'แกร็บ',          'income',  'delivery_income', 'rider_income_grab',    100),
    ('keyword', 'GRAB',           'income',  'delivery_income', 'rider_income_grab',    100),
    -- รายจ่าย: เงินเดือน
    ('name',    'มยุรฉัตร',       'expense', 'staff_salary',    'bank_statement',        90),
    -- รายจ่าย: เครื่องดื่ม
    ('name',    'วัฒนา',          'expense', 'beverage_raw',    'bank_statement',        90),
    ('keyword', 'เบียร์สิงห์',    'expense', 'beverage_raw',    'bank_statement',        90),
    ('keyword', 'สิงห์คอร์เปอ',   'expense', 'beverage_raw',    'bank_statement',        90),
    -- รายจ่าย: ค่าเช่า
    ('name',    'กาญจนา',         'expense', 'rent',            'bank_statement',        90),
    -- รายจ่าย: สำรองจ่าย
    ('name',    'นุศรา',          'expense', 'reimbursement',   'bank_statement',        80),
    -- รายจ่าย: วัตถุดิบ
    ('keyword', 'ย่างเนื้อ',      'expense', 'food_raw',        'bank_statement',        80),
    ('keyword', 'เนื้อ โปร',      'expense', 'food_raw',        'bank_statement',        80),
    -- รายจ่าย: ค่าดนตรี (amount patterns)
    ('amount_pattern', '600',     'expense', 'musician_fee',    'bank_statement',        70),
    ('amount_pattern', '700',     'expense', 'musician_fee',    'bank_statement',        70),
    ('amount_pattern', '2100',    'expense', 'musician_fee',    'bank_statement',        70),
    ('amount_pattern', '2800',    'expense', 'musician_fee',    'bank_statement',        70)
ON CONFLICT (rule_type, match_value) DO NOTHING;

-- ── 4. v_daybook — DROP and recreate to add Branch 7 (bank_statement_entries) ──
-- NOTE: Must DROP first (cannot reduce columns with CREATE OR REPLACE)

DROP VIEW IF EXISTS public.v_daybook CASCADE;

CREATE VIEW public.v_daybook AS

-- Branch 1: POS daily sales
SELECT
    ps.sales_date          AS entry_date,
    'income'               AS direction,
    ps.net_total           AS amount,
    'pos_sale'             AS source,
    NULL                   AS category_code,
    'POS ขายหน้าร้าน'      AS label,
    NULL                   AS counterparty,
    ps.branch_code,
    ps.id::text            AS ref_id
FROM public.pos_sales_daily ps

UNION ALL

-- Branch 2: Rider income — Grab
SELECT
    rd.delivery_date       AS entry_date,
    'income'               AS direction,
    rd.net_payout          AS amount,
    'rider_income_grab'    AS source,
    'delivery_income'      AS category_code,
    CONCAT('ขาย grab (', rd.order_count, ' orders)') AS label,
    'Grab'                 AS counterparty,
    rd.branch_code,
    rd.id::text            AS ref_id
FROM public.rider_deliveries rd
WHERE rd.platform = 'grab'

UNION ALL

-- Branch 3: Rider income — Lineman
SELECT
    rd.delivery_date       AS entry_date,
    'income'               AS direction,
    rd.net_payout          AS amount,
    'rider_income_lineman' AS source,
    'delivery_income'      AS category_code,
    CONCAT('ขาย lineman (', rd.order_count, ' orders)') AS label,
    'Lineman'              AS counterparty,
    rd.branch_code,
    rd.id::text            AS ref_id
FROM public.rider_deliveries rd
WHERE rd.platform = 'lineman'

UNION ALL

-- Branch 4: POS cashflow entries
SELECT
    pce.txn_date           AS entry_date,
    pce.direction          AS direction,
    pce.amount             AS amount,
    'pos_cashflow'         AS source,
    pce.category_code      AS category_code,
    pce.description        AS label,
    NULL                   AS counterparty,
    pce.branch_code,
    pce.id::text           AS ref_id
FROM public.pos_cashflow_entries pce

UNION ALL

-- Branch 5: AR/AP payments
SELECT
    p.payment_date         AS entry_date,
    CASE ae.direction
        WHEN 'ar' THEN 'income'
        ELSE 'expense'
    END                    AS direction,
    p.amount               AS amount,
    CASE ae.direction
        WHEN 'ar' THEN 'ar_payment'
        ELSE 'ap_payment'
    END                    AS source,
    NULL                   AS category_code,
    CONCAT(
        CASE ae.direction WHEN 'ar' THEN 'รับชำระ' ELSE 'จ่ายชำระ' END,
        ': ', ae.counterparty_name_snapshot
    )                      AS label,
    ae.counterparty_name_snapshot AS counterparty,
    'thawi_watthana'       AS branch_code,
    p.id::text             AS ref_id
FROM public.ar_ap_payments p
JOIN public.ar_ap_entries ae ON ae.id = p.entry_id

UNION ALL

-- Branch 6: Quick / manual entries
SELECT
    me.entry_date          AS entry_date,
    me.direction           AS direction,
    me.amount              AS amount,
    'manual'               AS source,
    me.category_code       AS category_code,
    me.description         AS label,
    NULL                   AS counterparty,
    COALESCE(me.branch_code, 'thawi_watthana') AS branch_code,
    me.id::text            AS ref_id
FROM public.manual_entries me

UNION ALL

-- Branch 7: Bank statement entries (classified only)
SELECT
    bse.txn_date           AS entry_date,
    bse.direction          AS direction,
    bse.amount             AS amount,
    bse.source_type        AS source,
    bse.category_code      AS category_code,
    bse.description        AS label,
    NULL                   AS counterparty,
    bse.branch_code,
    bse.id::text           AS ref_id
FROM public.bank_statement_entries bse
WHERE bse.match_status != 'needs_review';
