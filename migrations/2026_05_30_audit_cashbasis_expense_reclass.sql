-- ============================================================
-- VEXONHQ Migration 2026-05-30 — System-auditor round: fix cash-basis EXPENSE leaks
-- APPLIED to prod via Supabase MCP 2026-05-30. Committed for repo<->prod parity.
-- (Data corrections already live; do NOT re-run blindly — kept for a fresh-DB rebuild.)
-- ============================================================
-- AUDIT FINDING (critical): after the cash-basis overhaul the dashboard P&L showed an
-- IMPOSSIBLE ~66% net margin. Root cause: ~1.53M of REAL, already-categorised bank
-- expenses (beer 625k, salary 549k, food, utility, other) carried source_type
-- 'bank_statement', which is on the P&L exclusion list -> they were silently dropped
-- from v_daybook_pnl. The seeded statement_rules (and the food rules added earlier the
-- same day) set source_type='bank_statement' for expense categories, and phase12
-- _classify copies the rule's source_type onto the bank row. Net effect: every bank
-- expense that matched a keyword rule was categorised but EXCLUDED from profit.
-- After the fixes below the per-month margin lands at a realistic ~ -6% .. +35%
-- (avg ~15%) and the only remaining excluded expenses are genuine non-P&L items:
-- owner_advance (owner's own drawings 828k), pos_cash_deposit (savings 79k),
-- cash_withdrawal (ATM 50k).
-- ============================================================

-- ── Fix 1: categorised bank EXPENSES wrongly tagged 'bank_statement' (excluded)
--          -> a COUNTED source by category. ~1.53M restored to P&L. ──
UPDATE public.bank_statement_entries
SET source_type = CASE
  WHEN category_code IN ('staff_salary','musician_fee','daily_wage') THEN 'payroll_expense'
  WHEN category_code = 'rent' THEN 'rent_expense'
  WHEN category_code IN ('utility','utility_electricity','utility_water','utility_telecom') THEN 'utility_expense'
  WHEN category_code IN ('food_raw','raw_meat','raw_veggies','raw_seasoning','beverage_raw','raw_beverage','raw_oil_gas','beverage') THEN 'vendor_purchase'
  WHEN category_code = 'bank_fee' THEN 'bank_fee'
  WHEN category_code = 'tax' THEN 'tax_expense'
  ELSE 'other_expense'
END
WHERE direction='expense' AND source_type='bank_statement' AND match_status <> 'manual';

-- ── Fix 2: stop FUTURE imports from re-creating the leak. statement_rules EXPENSE
--          rules must never emit source_type='bank_statement'. ──
UPDATE public.statement_rules
SET source_type = CASE
  WHEN category_code IN ('staff_salary','musician_fee','daily_wage') THEN 'payroll_expense'
  WHEN category_code = 'rent' THEN 'rent_expense'
  WHEN category_code IN ('utility','utility_electricity','utility_water','utility_telecom') THEN 'utility_expense'
  WHEN category_code IN ('food_raw','raw_meat','raw_veggies','raw_seasoning','beverage_raw','raw_beverage','raw_oil_gas','beverage') THEN 'vendor_purchase'
  WHEN category_code = 'bank_fee' THEN 'bank_fee'
  WHEN category_code = 'tax' THEN 'tax_expense'
  ELSE 'other_expense'
END
WHERE direction='expense' AND source_type='bank_statement';

-- ── Fix 3: นุศรา (Nussara) transfers = reimbursement of shop expenses she fronts
--          (TUM: "นุศรา เป็นเงินค่าใช้จ่าย"), NOT an owner drawing. Count them.
--          (Owner's own drawings to นาย ระพีภัทร์ stay owner_advance / excluded.) ──
UPDATE public.bank_statement_entries
SET source_type='other_expense'
WHERE direction='expense' AND source_type='owner_advance'
  AND (description ILIKE '%นุศรา%' OR description ILIKE '%ปราง%');

-- ── #8: 11 daily-wage rows (คนงาน/ค่าแรง/โบนัส) were keyword-bucketed into
--        raw_seasoning COGS -> overstated food-cost% up to 0.7pt/month. Move out of COGS. ──
UPDATE public.pos_cashflow_entries
SET category_code='other_expense'
WHERE category_code='raw_seasoning'
  AND (description ILIKE '%คนงาน%' OR description ILIKE '%ค่าแรง%' OR description ILIKE '%โบนัส%' OR description ILIKE '%พนักงาน%');

-- ── #10: 41 delivery payouts (34 LINE MAN + 7 Grab) were mislabeled
--        source_type='pos_cash_deposit' (savings). Correct to the real platform payout
--        source (still excluded from P&L; this just fixes the audit trail). ──
UPDATE public.bank_statement_entries
SET source_type = CASE WHEN description ILIKE '%LINE%' OR description ILIKE '%ไลน%' THEN 'lineman_payout' ELSE 'grab_payout' END
WHERE source_type='pos_cash_deposit' AND direction='income'
  AND (description ILIKE '%LINE%' OR description ILIKE '%ไลน%' OR description ILIKE '%GRAB%' OR description ILIKE '%แกร%');
