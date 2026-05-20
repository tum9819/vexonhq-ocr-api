-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-20 — Phase 1 slips table (Slip Processing System, Session 27)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context: TUM uses KBank K+ transfer slips daily for payments to musicians,
-- staff salaries, beverage suppliers (วัฒนา), and reimbursements (นุศรา).
-- The slip MEMO field (บันทึกช่วยจำ) is GOLD — TUM types intent himself, so
-- it's a more reliable categorization signal than OCR'd invoice product
-- names. Goal: store every slip, OCR it, then 3-way match it against the
-- already-imported bank_statement_entries + vendor_bills rows.
--
-- Accounting standard reference: 3-Way Match (Invoice + Receipt/Slip +
-- Statement). Used by SAP/Oracle/NetSuite. Internal-control friendly per
-- SOX/COSO and ISO 9001. The match status here tells you exactly which leg
-- of the triple is missing for each slip.
--
-- Foreign keys:
--   - matched_invoice_id    → vendor_bills(id)             (which bill paid)
--   - matched_statement_id  → bank_statement_entries(id)   (which row in PDF)
--   - canonical_sku         → products(sku)                (memo classifier)
--
-- All three are nullable + ON DELETE SET NULL so we never block deletes
-- upstream just because a slip pointed at the row.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── 1. slips table ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.slips (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Header / when-and-how-much
    transfer_date          DATE          NOT NULL,
    transfer_time          TIME,
    amount                 NUMERIC(12,2) NOT NULL,
    fee                    NUMERIC(12,2) NOT NULL DEFAULT 0,

    -- Counterparties
    sender_name            TEXT,
    sender_account         TEXT,
    sender_bank            TEXT,
    recipient_name         TEXT,
    recipient_account      TEXT,
    recipient_bank         TEXT,

    -- The "gold" signal — what TUM typed into the K+ memo field
    memo                   TEXT,

    -- Bank reference + raw OCR audit
    ref_no                 TEXT,
    raw_image_url          TEXT,
    ocr_json               JSONB,

    -- Three-way match links
    matched_invoice_id     UUID REFERENCES public.vendor_bills(id)            ON DELETE SET NULL,
    matched_statement_id   UUID REFERENCES public.bank_statement_entries(id)  ON DELETE SET NULL,
    canonical_sku          TEXT REFERENCES public.products(sku)               ON DELETE SET NULL,
    canonical_confidence   NUMERIC(3,2),  -- [0, 1], filled by memo classifier

    -- State machine
    --   unmatched       — just uploaded, no statement / no invoice yet
    --   matched_stmt    — paired to a bank_statement_entries row but no bill
    --   matched_full    — paired to both statement + vendor_bill (3-way ✓)
    --   needs_review    — multiple statement candidates, TUM must pick
    --   rejected        — TUM marked the slip as not relevant (test, refund)
    match_status           TEXT NOT NULL DEFAULT 'unmatched'
                                CHECK (match_status IN
                                       ('unmatched', 'matched_stmt',
                                        'matched_full', 'needs_review',
                                        'rejected')),

    -- Provenance + audit
    source                 TEXT NOT NULL DEFAULT 'web'
                                CHECK (source IN ('line', 'web', 'manual')),
    created_by             TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by             TEXT,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    notes                  TEXT
);

-- ─── 2. Indexes ─────────────────────────────────────────────────────────────
-- Match engine joins on (transfer_date, amount). The statement matcher in
-- main.py uses ±2 days + ±1 baht — a btree on transfer_date keeps that fast.
CREATE INDEX IF NOT EXISTS idx_slips_transfer_date
    ON public.slips (transfer_date);

CREATE INDEX IF NOT EXISTS idx_slips_amount
    ON public.slips (amount);

-- Frontend filters by status constantly (Phase 5 queue page).
CREATE INDEX IF NOT EXISTS idx_slips_match_status
    ON public.slips (match_status);

-- For backfilling the 3-way matcher after a statement import lands.
CREATE INDEX IF NOT EXISTS idx_slips_matched_statement
    ON public.slips (matched_statement_id);
CREATE INDEX IF NOT EXISTS idx_slips_matched_invoice
    ON public.slips (matched_invoice_id);

-- Memo full-text — Thai supplier names + product keywords are searchable
-- via the same `simple` configuration we use everywhere else (Postgres
-- doesn't ship a Thai dictionary, but `simple` + ILIKE handles ~95% of the
-- 'beer ช้าง 620 มล.' style memos TUM types).
CREATE INDEX IF NOT EXISTS idx_slips_memo
    ON public.slips
    USING gin (to_tsvector('simple', COALESCE(memo, '')));

-- ─── 3. updated_at trigger (mirrors invoice_items / vendor_bills) ───────────
CREATE OR REPLACE FUNCTION public.fn_slips_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_slips_set_updated_at ON public.slips;
CREATE TRIGGER trg_slips_set_updated_at
    BEFORE UPDATE ON public.slips
    FOR EACH ROW
    EXECUTE FUNCTION public.fn_slips_set_updated_at();

-- ─── 4. Preview ─────────────────────────────────────────────────────────────
SELECT 'slips columns' AS metric, COUNT(*)::text AS value
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'slips'
UNION ALL
SELECT 'slips indexes' AS metric, COUNT(*)::text AS value
FROM pg_indexes
WHERE schemaname = 'public' AND tablename = 'slips';

COMMIT;
-- ROLLBACK;
