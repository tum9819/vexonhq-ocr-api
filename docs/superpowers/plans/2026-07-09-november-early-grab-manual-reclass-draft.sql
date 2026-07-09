-- 2026-07-09-november-early-grab-manual-reclass-draft.sql
-- VEXONHQ November 2025 early-Grab bank_statement_entries reclass DRAFT.
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end.
--
-- Goal: close the 11 early-November Grab bank settlement rows that were
-- intentionally excluded from the 2026-07-07 verified-subset COMMIT because
-- the local Grab export only covers 2025-11-17..2025-11-30.
--
-- Evidence level:
-- - TUM confirmed on 2026-07-09 that the available Grab data has only that export.
-- - These 11 bank rows carry Thai KBank statement text identifying
--   "บจก. แกร็บแท็กซี่" and are exact credit rows dated 2025-11-02..2025-11-16.
-- - Because no Grab CSV exists for 2025-11-01..2025-11-16, these are marked
--   match_status='manual' and notes explicitly record bank-text-only evidence.
--
-- Expected target set:
--   total: 11 rows / 3168.81 THB
--   grab_payout manual: 11 rows / 3168.81 THB
--
-- Safety properties:
-- - Literal reviewed ID list only; no keyword-derived UPDATE.
-- - Guards current source/category/match_status/date/credit/branch before update.
-- - Refuses to overwrite any row that is currently match_status='manual'.
-- - Locks all target rows before backup/update to prevent concurrent changes.
-- - Creates backup table in non-public audit schema before UPDATE.
-- - In-transaction row-count and credit-sum assertions abort on mismatch.

BEGIN;

SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';
SET LOCAL idle_in_transaction_session_timeout = '60s';

CREATE TEMP TABLE _november_early_grab_targets (
    id uuid PRIMARY KEY,
    expected_txn_date date NOT NULL,
    expected_credit numeric(12,2) NOT NULL,
    current_source_type text NOT NULL,
    current_category_code text,
    current_match_status text NOT NULL,
    new_source_type text NOT NULL,
    new_category_code text NOT NULL,
    new_match_status text NOT NULL,
    note_append text NOT NULL
) ON COMMIT DROP;

INSERT INTO _november_early_grab_targets (
    id, expected_txn_date, expected_credit, current_source_type, current_category_code,
    current_match_status, new_source_type, new_category_code, new_match_status, note_append
)
VALUES
    ('acaa718c-63bb-404b-b232-fcb91d069da6'::uuid, '2025-11-02'::date, 584.61::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('a91d4811-e6bb-4559-a246-e0fa684b5033'::uuid, '2025-11-04'::date, 203.59::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('298cdeaa-b850-4abc-8fee-4281c4290622'::uuid, '2025-11-05'::date, 119.10::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('a6c5737c-148b-4942-be5e-073253340e49'::uuid, '2025-11-06'::date, 237.81::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('6ba74b34-26a1-408a-836b-8ad730fa412e'::uuid, '2025-11-07'::date, 97.26::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('a093c216-f000-49cf-87d7-d4d6570cea41'::uuid, '2025-11-09'::date, 342.22::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('a54fd10b-ef5a-40df-834c-ad4b5fc3a721'::uuid, '2025-11-10'::date, 112.71::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('b7fd5719-ac2c-4b63-aebc-707df9892201'::uuid, '2025-11-12'::date, 291.29::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('5c70e297-d54e-4ff1-9e43-70652266f87a'::uuid, '2025-11-13'::date, 463.55::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('e8febd64-a23e-4aa4-8953-498adb903e77'::uuid, '2025-11-14'::date, 399.58::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.'),
    ('58edfbbd-6294-4b2d-91de-e1a93a46925a'::uuid, '2025-11-16'::date, 317.09::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-09 reviewed November early-Grab manual reclass: TUM confirmed no additional Grab export exists for 2025-11-01..2025-11-16; classified from KBank statement text "บจก. แกร็บแท็กซี่" only; no CSV payout aggregate available.');

SELECT 'BEFORE' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _november_early_grab_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

DO $november_early_grab_preflight$
DECLARE
    v_locked_count integer;
    v_target_count integer;
    v_exact_count integer;
    v_manual_current_count integer;
    v_target_sum numeric(12,2);
    v_grab_count integer;
    v_grab_sum numeric(12,2);
    v_new_manual_count integer;
    v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*) INTO v_locked_count FROM (
        SELECT b.id
        FROM public.bank_statement_entries b
        JOIN _november_early_grab_targets t ON t.id = b.id
        ORDER BY b.id
        FOR UPDATE
    ) locked_rows;
    IF v_locked_count <> 11 THEN
        RAISE EXCEPTION 'Locked row count mismatch: expected %, got %', 11, v_locked_count;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2)
    INTO v_target_count, v_target_sum
    FROM _november_early_grab_targets;
    IF v_target_count <> 11 THEN
        RAISE EXCEPTION 'Target count mismatch: expected %, got %', 11, v_target_count;
    END IF;
    IF v_target_sum <> 3168.81::numeric(12,2) THEN
        RAISE EXCEPTION 'Target credit sum mismatch: expected %, got %', 3168.81, v_target_sum;
    END IF;

    SELECT COUNT(*) INTO v_exact_count
    FROM _november_early_grab_targets t
    JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code = 'thawi_watthana'
      AND b.txn_date = t.expected_txn_date
      AND b.credit = t.expected_credit
      AND COALESCE(b.debit,0) = 0
      AND b.source_type = t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status = t.current_match_status
      AND b.match_status <> 'manual';
    IF v_exact_count <> 11 THEN
        RAISE EXCEPTION 'Current-state guard mismatch: expected %, got %', 11, v_exact_count;
    END IF;

    SELECT COUNT(*) INTO v_manual_current_count
    FROM _november_early_grab_targets t
    JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.match_status = 'manual';
    IF v_manual_current_count <> 0 THEN
        RAISE EXCEPTION 'Refusing to overwrite currently manual rows: %', v_manual_current_count;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2)
    INTO v_grab_count, v_grab_sum
    FROM _november_early_grab_targets
    WHERE new_source_type = 'grab_payout';
    IF v_grab_count <> 11 OR v_grab_sum <> 3168.81::numeric(12,2) THEN
        RAISE EXCEPTION 'Grab target bucket mismatch: expected % / %, got % / %', 11, 3168.81, v_grab_count, v_grab_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2)
    INTO v_new_manual_count, v_new_manual_sum
    FROM _november_early_grab_targets
    WHERE new_match_status = 'manual';
    IF v_new_manual_count <> 11 OR v_new_manual_sum <> 3168.81::numeric(12,2) THEN
        RAISE EXCEPTION 'Manual target bucket mismatch: expected % / %, got % / %', 11, 3168.81, v_new_manual_count, v_new_manual_sum;
    END IF;
END
$november_early_grab_preflight$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260709_november_early_grab AS
SELECT b.*
FROM public.bank_statement_entries b
JOIN _november_early_grab_targets t ON t.id = b.id;
COMMENT ON TABLE audit.bank_statement_reclass_backup_20260709_november_early_grab IS 'Pre-reclass backup of 11 early-November 2025 Grab bank settlement rows reviewed on 2026-07-09.';
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260709_november_early_grab FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260709_november_early_grab FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260709_november_early_grab FROM authenticated;

DO $november_early_grab_backup_check$
DECLARE
    v_backup_count integer;
    v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2)
    INTO v_backup_count, v_backup_sum
    FROM audit.bank_statement_reclass_backup_20260709_november_early_grab;
    IF v_backup_count <> 11 THEN
        RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 11, v_backup_count;
    END IF;
    IF v_backup_sum <> 3168.81::numeric(12,2) THEN
        RAISE EXCEPTION 'Backup credit sum mismatch: expected %, got %', 3168.81, v_backup_sum;
    END IF;
END
$november_early_grab_backup_check$;

CREATE TEMP TABLE _november_early_grab_updated (
    id uuid PRIMARY KEY,
    old_source_type text,
    old_category_code text,
    old_match_status text,
    new_source_type text,
    new_category_code text,
    new_match_status text,
    credit numeric(12,2) NOT NULL
) ON COMMIT DROP;

WITH updated AS (
    UPDATE public.bank_statement_entries b
    SET source_type = t.new_source_type,
        category_code = t.new_category_code,
        match_status = t.new_match_status,
        notes = CASE
            WHEN COALESCE(BTRIM(b.notes),'') = '' THEN t.note_append
            ELSE b.notes || E'\n' || t.note_append
        END,
        classified_by = 'codex_reviewed_november_early_grab_20260709',
        classified_at = now()
    FROM _november_early_grab_targets t
    WHERE b.id = t.id
      AND b.branch_code = 'thawi_watthana'
      AND b.txn_date = t.expected_txn_date
      AND b.credit = t.expected_credit
      AND COALESCE(b.debit,0) = 0
      AND b.source_type = t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status = t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING b.id,
              t.current_source_type AS old_source_type,
              t.current_category_code AS old_category_code,
              t.current_match_status AS old_match_status,
              b.source_type AS new_source_type,
              b.category_code AS new_category_code,
              b.match_status AS new_match_status,
              b.credit
)
INSERT INTO _november_early_grab_updated
SELECT id, old_source_type, old_category_code, old_match_status, new_source_type, new_category_code, new_match_status, credit
FROM updated;

DO $november_early_grab_postupdate$
DECLARE
    v_updated_count integer;
    v_updated_sum numeric(12,2);
    v_grab_count integer;
    v_grab_sum numeric(12,2);
    v_manual_count integer;
    v_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2)
    INTO v_updated_count, v_updated_sum
    FROM _november_early_grab_updated;
    IF v_updated_count <> 11 THEN
        RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 11, v_updated_count;
    END IF;
    IF v_updated_sum <> 3168.81::numeric(12,2) THEN
        RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 3168.81, v_updated_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2)
    INTO v_grab_count, v_grab_sum
    FROM _november_early_grab_updated
    WHERE new_source_type = 'grab_payout';
    IF v_grab_count <> 11 OR v_grab_sum <> 3168.81::numeric(12,2) THEN
        RAISE EXCEPTION 'Grab updated bucket mismatch: expected % / %, got % / %', 11, 3168.81, v_grab_count, v_grab_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2)
    INTO v_manual_count, v_manual_sum
    FROM _november_early_grab_updated
    WHERE new_match_status = 'manual';
    IF v_manual_count <> 11 OR v_manual_sum <> 3168.81::numeric(12,2) THEN
        RAISE EXCEPTION 'Manual updated bucket mismatch: expected % / %, got % / %', 11, 3168.81, v_manual_count, v_manual_sum;
    END IF;
END
$november_early_grab_postupdate$;

SELECT 'AFTER' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _november_early_grab_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

SELECT 'NOVEMBER_PNL_AFTER_IN_TRANSACTION' AS phase, direction,
       COUNT(*) AS row_count, SUM(amount)::numeric(12,2) AS amount_sum
FROM public.v_daybook_pnl
WHERE entry_date >= '2025-11-01'::date
  AND entry_date < '2025-12-01'::date
GROUP BY direction
ORDER BY direction;

ROLLBACK;
-- COMMIT;

-- Rollback after a committed run, if ever needed:
-- BEGIN;
-- UPDATE public.bank_statement_entries b
-- SET source_type = bak.source_type,
--     category_code = bak.category_code,
--     match_status = bak.match_status,
--     notes = bak.notes,
--     classified_by = bak.classified_by,
--     classified_at = bak.classified_at
-- FROM audit.bank_statement_reclass_backup_20260709_november_early_grab bak
-- WHERE b.id = bak.id;
-- COMMIT;
