-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-19 — Fix musician_fee + payment-gateway classification
-- ════════════════════════════════════════════════════════════════════════════
--
-- Two follow-ups to the previous reclassification migration after we
-- inspected actual April 2026 statement entries:
--
-- 1) The musician-fee branch of _classify() was tagging rows as
--    category_code='musician_fee' BUT source_type='bank_statement'.
--    After the Phase 1 filter started excluding 'bank_statement' from
--    P&L, these legitimate performer payments (~฿20-30k/month) silently
--    disappeared from the books. Move them to source_type='payroll_expense'
--    which IS counted.
--
-- 2) KBank "เพื่อชำระ Ref" rows for payment-gateway services (MPAY, 2C2P,
--    LINE MAN Wongnai QR by ttb) are real bank fees and should count
--    in P&L. Re-tag from generic 'bank_statement' → 'bank_fee'.
--
-- Run after the Coolify deploy that includes commit 66cf358 — the
-- _BUILTIN_PATTERNS table has been updated to apply these labels to
-- future imports automatically.
--
-- Safety: each UPDATE is narrow (specific category or description
-- substrings); one transaction with preview SELECT before COMMIT.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── 1. Musician fees: source_type bank_statement -> payroll_expense ────
UPDATE public.bank_statement_entries
SET source_type = 'payroll_expense'
WHERE source_type = 'bank_statement'
  AND category_code = 'musician_fee';

-- ─── 2. Payment-gateway / QR fees: bank_statement -> bank_fee ───────────
UPDATE public.bank_statement_entries
SET source_type   = 'bank_fee',
    category_code = COALESCE(category_code, 'payment_gateway_fee')
WHERE source_type = 'bank_statement'
  AND debit > 0
  AND (description ILIKE '%mpay%'
    OR description ILIKE '%2c2p%'
    OR description ILIKE '%ทูซีทูพี%'
    OR description ILIKE '%line man wongnai%'
    OR description ILIKE '%qr by ttb%');

-- ════════════════════════════════════════════════════════════════════════
-- Preview — verify the new classification.
-- Expect:
--   • payroll_expense rows go up by however many musician_fee rows existed
--   • bank_fee rows include the MPAY / 2C2P / LINE MAN Wongnai entries
--   • bank_statement (catch-all) count goes down accordingly
-- ════════════════════════════════════════════════════════════════════════
SELECT
  source_type,
  category_code,
  COUNT(*) FILTER (WHERE debit  > 0) AS expense_rows,
  COUNT(*) FILTER (WHERE credit > 0) AS income_rows,
  SUM(debit)::numeric(12,2)         AS total_debit,
  SUM(credit)::numeric(12,2)        AS total_credit
FROM public.bank_statement_entries
WHERE source_type IN ('bank_statement', 'payroll_expense', 'bank_fee')
GROUP BY source_type, category_code
ORDER BY source_type, (SUM(debit) + SUM(credit)) DESC NULLS LAST;

COMMIT;
-- ROLLBACK;  -- uncomment + re-run if the preview looks wrong
