-- 2026-07-07-may-statement-duplicate-cleanup-draft.sql
-- VEXONHQ May 2026 bank_statement_entries duplicate cleanup DRAFT.
-- REVIEW ONLY. DO NOT RUN with COMMIT without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end. Replace ROLLBACK with COMMIT only after
-- Claude/Antigravity review + TUM explicit production-DB approval.
--
-- Goal: remove 7 exact duplicate statement rows caused by overlapping May
-- statement imports, while preserving the better row in each duplicate pair.
--
-- Expected accounting impact after COMMIT:
-- - May P&L expense should drop by 700.00 THB because one duplicate musician
--   fee is currently counted twice in v_daybook_pnl.
-- - Other removed duplicates are cash movement / settlement duplicates and
--   clean up raw ledger/reconciliation noise.
-- - One slip reference is repointed before delete so the 3-way evidence link
--   does not become NULL.
--
-- Source evidence:
-- - Read-only report:
--   C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_may_dry_run_20260707_180504\MAY_READONLY_FINDINGS.md
-- - May POS/export evidence folder:
--   C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS\2026-05-May
--
-- Safety properties:
-- - Literal reviewed delete/keep UUID mapping only; no keyword-derived delete.
-- - Guards delete and keep rows by date, debit, credit, balance, source,
--   category, match_status, and branch before any write.
-- - Repoints the one known slip FK before deleting the duplicate statement row.
-- - Creates backup tables in the non-public audit schema before UPDATE/DELETE.
-- - In-transaction assertions abort on any mismatch.

BEGIN;

SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';
SET LOCAL idle_in_transaction_session_timeout = '60s';

CREATE TEMP TABLE _may_statement_duplicate_targets (
    delete_id uuid PRIMARY KEY,
    keep_id uuid NOT NULL UNIQUE,
    expected_txn_date date NOT NULL,
    expected_debit numeric(12,2) NOT NULL,
    expected_credit numeric(12,2) NOT NULL,
    expected_balance numeric(12,2) NOT NULL,
    delete_source_type text NOT NULL,
    delete_category_code text,
    delete_match_status text NOT NULL,
    keep_source_type text NOT NULL,
    keep_category_code text,
    keep_match_status text NOT NULL,
    reason text NOT NULL
) ON COMMIT DROP;

INSERT INTO _may_statement_duplicate_targets (
    delete_id,
    keep_id,
    expected_txn_date,
    expected_debit,
    expected_credit,
    expected_balance,
    delete_source_type,
    delete_category_code,
    delete_match_status,
    keep_source_type,
    keep_category_code,
    keep_match_status,
    reason
)
VALUES
    (
        'c4df2b65-e3c7-448e-8ae7-467892c12d3a'::uuid,
        '521707d6-995b-43f0-b601-283a53c24da7'::uuid,
        '2026-05-06'::date,
        649.00::numeric(12,2),
        0.00::numeric(12,2),
        5820.19::numeric(12,2),
        'cash_withdrawal',
        'bank_statement',
        'auto',
        'cash_withdrawal',
        'bank_statement',
        'auto',
        'Exact duplicate imported from overlapping May statements; keep cleaner later-batch row.'
    ),
    (
        'b1102c88-c6a1-40c0-bbb7-2467f8ea9f2e'::uuid,
        '379d9bd6-4c7f-4a89-88b8-f7721d692744'::uuid,
        '2026-05-12'::date,
        0.00::numeric(12,2),
        5567.00::numeric(12,2),
        34423.65::numeric(12,2),
        'lineman_payout',
        'pos_cash',
        'auto',
        'pos_cash_deposit',
        'pos_cash',
        'auto',
        'Exact LINE PAY settlement duplicate; keep later-batch row for later reclass review.'
    ),
    (
        '4ca007d4-2855-4f68-813e-b7be370d870b'::uuid,
        'c28b40b5-d320-4d57-ba09-ca5d536b9ab8'::uuid,
        '2026-05-18'::date,
        0.00::numeric(12,2),
        788.00::numeric(12,2),
        17795.57::numeric(12,2),
        'lineman_payout',
        'pos_cash',
        'auto',
        'pos_cash_deposit',
        'pos_cash',
        'auto',
        'Exact LINE PAY settlement duplicate; keep later-batch row for later reclass review.'
    ),
    (
        '0debd52e-5b41-46ff-aad1-d70bcaff25c5'::uuid,
        'bc648435-3366-4206-93d3-9ee78d60e1f6'::uuid,
        '2026-05-24'::date,
        0.00::numeric(12,2),
        1197.00::numeric(12,2),
        25454.92::numeric(12,2),
        'lineman_payout',
        'pos_cash',
        'auto',
        'pos_cash_deposit',
        'pos_cash',
        'auto',
        'Exact LINE PAY settlement duplicate; keep later-batch row for later reclass review.'
    ),
    (
        '8cdfaf5a-6834-4b70-b3fc-144defdde3f3'::uuid,
        '202e4ebc-a843-4d2a-804b-5a1092e815e3'::uuid,
        '2026-05-28'::date,
        0.00::numeric(12,2),
        147.94::numeric(12,2),
        37902.18::numeric(12,2),
        'bank_statement',
        NULL,
        'needs_review',
        'rider_income_grab',
        'delivery_income',
        'auto',
        'Exact Grab settlement duplicate; keep later-batch row with Grab classification/evidence.'
    ),
    (
        '92b64182-9954-47ce-b7dc-d2f0cab2257f'::uuid,
        '89aa5df5-e7b0-4492-8d74-a40b3c8d62d7'::uuid,
        '2026-05-29'::date,
        700.00::numeric(12,2),
        0.00::numeric(12,2),
        43757.58::numeric(12,2),
        'payroll_expense',
        'musician_fee',
        'auto',
        'payroll_expense',
        'musician_fee',
        'auto',
        'Exact musician-fee duplicate; repoint linked slip to kept row before delete.'
    ),
    (
        'db814525-5340-492b-8286-e21006200e1a'::uuid,
        'bc99b402-32c3-40f7-b701-6a12f1bbe8af'::uuid,
        '2026-05-29'::date,
        0.00::numeric(12,2),
        196.15::numeric(12,2),
        38820.54::numeric(12,2),
        'bank_statement',
        NULL,
        'needs_review',
        'rider_income_grab',
        'delivery_income',
        'auto',
        'Exact Grab settlement duplicate; keep later-batch row with Grab classification/evidence.'
    );

-- Preview before cleanup.
SELECT
    'BEFORE_DUPLICATE_PAIRS' AS phase,
    t.expected_txn_date,
    t.expected_debit,
    t.expected_credit,
    t.expected_balance,
    d.id AS delete_id,
    d.source_type AS delete_source_type,
    d.category_code AS delete_category_code,
    d.match_status AS delete_match_status,
    k.id AS keep_id,
    k.source_type AS keep_source_type,
    k.category_code AS keep_category_code,
    k.match_status AS keep_match_status,
    t.reason
FROM _may_statement_duplicate_targets t
JOIN public.bank_statement_entries d ON d.id = t.delete_id
JOIN public.bank_statement_entries k ON k.id = t.keep_id
ORDER BY t.expected_txn_date, t.expected_debit, t.expected_credit, t.delete_id;

DO $may_duplicate_preflight$
DECLARE
    v_target_count integer;
    v_delete_exact_count integer;
    v_keep_exact_count integer;
    v_pair_match_count integer;
    v_slip_ref_count integer;
    v_slip_exact_count integer;
BEGIN
    SELECT COUNT(*) INTO v_target_count
    FROM _may_statement_duplicate_targets;

    IF v_target_count <> 7 THEN
        RAISE EXCEPTION 'Target count mismatch: expected %, got %', 7, v_target_count;
    END IF;

    SELECT COUNT(*) INTO v_delete_exact_count
    FROM _may_statement_duplicate_targets t
    JOIN public.bank_statement_entries b ON b.id = t.delete_id
    WHERE b.branch_code = 'thawi_watthana'
      AND b.txn_date = t.expected_txn_date
      AND b.debit = t.expected_debit
      AND b.credit = t.expected_credit
      AND b.balance = t.expected_balance
      AND b.source_type = t.delete_source_type
      AND b.category_code IS NOT DISTINCT FROM t.delete_category_code
      AND b.match_status = t.delete_match_status;

    IF v_delete_exact_count <> 7 THEN
        RAISE EXCEPTION 'Delete-row current-state guard mismatch: expected %, got %', 7, v_delete_exact_count;
    END IF;

    SELECT COUNT(*) INTO v_keep_exact_count
    FROM _may_statement_duplicate_targets t
    JOIN public.bank_statement_entries b ON b.id = t.keep_id
    WHERE b.branch_code = 'thawi_watthana'
      AND b.txn_date = t.expected_txn_date
      AND b.debit = t.expected_debit
      AND b.credit = t.expected_credit
      AND b.balance = t.expected_balance
      AND b.source_type = t.keep_source_type
      AND b.category_code IS NOT DISTINCT FROM t.keep_category_code
      AND b.match_status = t.keep_match_status;

    IF v_keep_exact_count <> 7 THEN
        RAISE EXCEPTION 'Keep-row current-state guard mismatch: expected %, got %', 7, v_keep_exact_count;
    END IF;

    SELECT COUNT(*) INTO v_pair_match_count
    FROM _may_statement_duplicate_targets t
    JOIN public.bank_statement_entries d ON d.id = t.delete_id
    JOIN public.bank_statement_entries k ON k.id = t.keep_id
    WHERE d.txn_date = k.txn_date
      AND d.debit = k.debit
      AND d.credit = k.credit
      AND d.balance = k.balance;

    IF v_pair_match_count <> 7 THEN
        RAISE EXCEPTION 'Duplicate pair exact-key mismatch: expected %, got %', 7, v_pair_match_count;
    END IF;

    SELECT COUNT(*) INTO v_slip_ref_count
    FROM public.slips s
    JOIN _may_statement_duplicate_targets t ON t.delete_id = s.matched_statement_id;

    IF v_slip_ref_count <> 1 THEN
        RAISE EXCEPTION 'Unexpected slip refs to delete rows: expected %, got %', 1, v_slip_ref_count;
    END IF;

    SELECT COUNT(*) INTO v_slip_exact_count
    FROM public.slips s
    WHERE s.id = '5c788be1-50d5-4a9c-81d6-20c61efa81d9'::uuid
      AND s.matched_statement_id = '92b64182-9954-47ce-b7dc-d2f0cab2257f'::uuid
      AND s.amount = 700.00::numeric(12,2);

    IF v_slip_exact_count <> 1 THEN
        RAISE EXCEPTION 'Expected musician slip link not found or changed';
    END IF;
END
$may_duplicate_preflight$;

DO $may_duplicate_lock_rows$
DECLARE
    v_bank_locked_count integer;
    v_slip_locked_count integer;
BEGIN
    SELECT COUNT(*) INTO v_bank_locked_count
    FROM (
        SELECT b.id
        FROM public.bank_statement_entries b
        JOIN _may_statement_duplicate_targets t
          ON b.id IN (t.delete_id, t.keep_id)
        ORDER BY b.id
        FOR UPDATE
    ) locked_bank_rows;

    IF v_bank_locked_count <> 14 THEN
        RAISE EXCEPTION 'Bank row lock count mismatch: expected %, got %', 14, v_bank_locked_count;
    END IF;

    SELECT COUNT(*) INTO v_slip_locked_count
    FROM (
        SELECT s.id
        FROM public.slips s
        WHERE s.id = '5c788be1-50d5-4a9c-81d6-20c61efa81d9'::uuid
        FOR UPDATE
    ) locked_slip_rows;

    IF v_slip_locked_count <> 1 THEN
        RAISE EXCEPTION 'Slip row lock count mismatch: expected %, got %', 1, v_slip_locked_count;
    END IF;
END
$may_duplicate_lock_rows$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_duplicate_cleanup_backup_202605_20260707 AS
SELECT b.*
FROM public.bank_statement_entries b
JOIN _may_statement_duplicate_targets t
  ON b.id IN (t.delete_id, t.keep_id);

COMMENT ON TABLE audit.bank_statement_duplicate_cleanup_backup_202605_20260707
IS 'Pre-cleanup backup of 14 May 2026 bank_statement_entries rows (7 delete + 7 keep) for duplicate cleanup reviewed on 2026-07-07.';

REVOKE ALL ON TABLE audit.bank_statement_duplicate_cleanup_backup_202605_20260707 FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_duplicate_cleanup_backup_202605_20260707 FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_duplicate_cleanup_backup_202605_20260707 FROM authenticated;

CREATE TABLE audit.statement_duplicate_cleanup_slip_backup_202605_20260707 AS
SELECT s.*
FROM public.slips s
WHERE s.id = '5c788be1-50d5-4a9c-81d6-20c61efa81d9'::uuid;

COMMENT ON TABLE audit.statement_duplicate_cleanup_slip_backup_202605_20260707
IS 'Pre-cleanup backup of the one slip repointed during May 2026 statement duplicate cleanup reviewed on 2026-07-07.';

REVOKE ALL ON TABLE audit.statement_duplicate_cleanup_slip_backup_202605_20260707 FROM PUBLIC;
REVOKE ALL ON TABLE audit.statement_duplicate_cleanup_slip_backup_202605_20260707 FROM anon;
REVOKE ALL ON TABLE audit.statement_duplicate_cleanup_slip_backup_202605_20260707 FROM authenticated;

DO $may_duplicate_backup_check$
DECLARE
    v_bank_backup_count integer;
    v_bank_backup_delete_count integer;
    v_bank_backup_keep_count integer;
    v_slip_backup_count integer;
BEGIN
    SELECT COUNT(*) INTO v_bank_backup_count
    FROM audit.bank_statement_duplicate_cleanup_backup_202605_20260707;

    IF v_bank_backup_count <> 14 THEN
        RAISE EXCEPTION 'Bank backup count mismatch: expected %, got %', 14, v_bank_backup_count;
    END IF;

    SELECT
        COUNT(*) FILTER (WHERE bak.id = t.delete_id),
        COUNT(*) FILTER (WHERE bak.id = t.keep_id)
    INTO v_bank_backup_delete_count, v_bank_backup_keep_count
    FROM _may_statement_duplicate_targets t
    JOIN audit.bank_statement_duplicate_cleanup_backup_202605_20260707 bak
      ON bak.id IN (t.delete_id, t.keep_id);

    IF v_bank_backup_delete_count <> 7 OR v_bank_backup_keep_count <> 7 THEN
        RAISE EXCEPTION 'Bank backup split mismatch: expected 7/7, got %/%',
            v_bank_backup_delete_count, v_bank_backup_keep_count;
    END IF;

    SELECT COUNT(*) INTO v_slip_backup_count
    FROM audit.statement_duplicate_cleanup_slip_backup_202605_20260707;

    IF v_slip_backup_count <> 1 THEN
        RAISE EXCEPTION 'Slip backup count mismatch: expected %, got %', 1, v_slip_backup_count;
    END IF;
END
$may_duplicate_backup_check$;

CREATE TEMP TABLE _may_statement_duplicate_repointed (
    slip_id uuid PRIMARY KEY,
    old_statement_id uuid NOT NULL,
    new_statement_id uuid NOT NULL,
    amount numeric(12,2) NOT NULL
) ON COMMIT DROP;

WITH repointed AS (
    UPDATE public.slips s
    SET matched_statement_id = '89aa5df5-e7b0-4492-8d74-a40b3c8d62d7'::uuid,
        updated_by = 'codex_reviewed_may_duplicate_cleanup_20260707',
        updated_at = now(),
        notes = CASE
            WHEN COALESCE(BTRIM(s.notes), '') = '' THEN
                '2026-07-07 May duplicate cleanup: repointed from duplicate statement row 92b64182-9954-47ce-b7dc-d2f0cab2257f to kept row 89aa5df5-e7b0-4492-8d74-a40b3c8d62d7 before statement delete.'
            ELSE
                s.notes || E'\n2026-07-07 May duplicate cleanup: repointed from duplicate statement row 92b64182-9954-47ce-b7dc-d2f0cab2257f to kept row 89aa5df5-e7b0-4492-8d74-a40b3c8d62d7 before statement delete.'
        END
    WHERE s.id = '5c788be1-50d5-4a9c-81d6-20c61efa81d9'::uuid
      AND s.matched_statement_id = '92b64182-9954-47ce-b7dc-d2f0cab2257f'::uuid
      AND s.amount = 700.00::numeric(12,2)
    RETURNING
        s.id AS slip_id,
        '92b64182-9954-47ce-b7dc-d2f0cab2257f'::uuid AS old_statement_id,
        s.matched_statement_id AS new_statement_id,
        s.amount
)
INSERT INTO _may_statement_duplicate_repointed (
    slip_id,
    old_statement_id,
    new_statement_id,
    amount
)
SELECT
    slip_id,
    old_statement_id,
    new_statement_id,
    amount
FROM repointed;

CREATE TEMP TABLE _may_statement_duplicate_deleted (
    id uuid PRIMARY KEY,
    txn_date date NOT NULL,
    debit numeric(12,2) NOT NULL,
    credit numeric(12,2) NOT NULL,
    balance numeric(12,2),
    source_type text,
    category_code text,
    match_status text NOT NULL,
    reason text NOT NULL
) ON COMMIT DROP;

WITH deleted AS (
    DELETE FROM public.bank_statement_entries b
    USING _may_statement_duplicate_targets t
    WHERE b.id = t.delete_id
      AND b.branch_code = 'thawi_watthana'
      AND b.txn_date = t.expected_txn_date
      AND b.debit = t.expected_debit
      AND b.credit = t.expected_credit
      AND b.balance = t.expected_balance
      AND b.source_type = t.delete_source_type
      AND b.category_code IS NOT DISTINCT FROM t.delete_category_code
      AND b.match_status = t.delete_match_status
    RETURNING
        b.id,
        b.txn_date,
        b.debit,
        b.credit,
        b.balance,
        b.source_type,
        b.category_code,
        b.match_status,
        t.reason
)
INSERT INTO _may_statement_duplicate_deleted (
    id,
    txn_date,
    debit,
    credit,
    balance,
    source_type,
    category_code,
    match_status,
    reason
)
SELECT
    id,
    txn_date,
    debit,
    credit,
    balance,
    source_type,
    category_code,
    match_status,
    reason
FROM deleted;

DO $may_duplicate_postupdate$
DECLARE
    v_repointed_count integer;
    v_deleted_count integer;
    v_remaining_delete_ids integer;
    v_keep_count integer;
    v_remaining_exact_duplicate_keys integer;
    v_slip_now_keep_count integer;
    v_pnl_deleted_refs integer;
BEGIN
    SELECT COUNT(*) INTO v_repointed_count
    FROM _may_statement_duplicate_repointed;

    IF v_repointed_count <> 1 THEN
        RAISE EXCEPTION 'Repointed slip count mismatch: expected %, got %', 1, v_repointed_count;
    END IF;

    SELECT COUNT(*) INTO v_deleted_count
    FROM _may_statement_duplicate_deleted;

    IF v_deleted_count <> 7 THEN
        RAISE EXCEPTION 'Deleted count mismatch: expected %, got %', 7, v_deleted_count;
    END IF;

    SELECT COUNT(*) INTO v_remaining_delete_ids
    FROM public.bank_statement_entries b
    JOIN _may_statement_duplicate_targets t ON t.delete_id = b.id;

    IF v_remaining_delete_ids <> 0 THEN
        RAISE EXCEPTION 'Delete IDs still present after cleanup: %', v_remaining_delete_ids;
    END IF;

    SELECT COUNT(*) INTO v_keep_count
    FROM public.bank_statement_entries b
    JOIN _may_statement_duplicate_targets t ON t.keep_id = b.id
    WHERE b.branch_code = 'thawi_watthana'
      AND b.txn_date = t.expected_txn_date
      AND b.debit = t.expected_debit
      AND b.credit = t.expected_credit
      AND b.balance = t.expected_balance
      AND b.source_type = t.keep_source_type
      AND b.category_code IS NOT DISTINCT FROM t.keep_category_code
      AND b.match_status = t.keep_match_status;

    IF v_keep_count <> 7 THEN
        RAISE EXCEPTION 'Keep rows missing or changed after cleanup: expected %, got %', 7, v_keep_count;
    END IF;

    SELECT COUNT(*) INTO v_remaining_exact_duplicate_keys
    FROM (
        SELECT b.txn_date, b.debit, b.credit, b.balance
        FROM public.bank_statement_entries b
        JOIN _may_statement_duplicate_targets t
          ON b.txn_date = t.expected_txn_date
         AND b.debit = t.expected_debit
         AND b.credit = t.expected_credit
         AND b.balance = t.expected_balance
        WHERE b.branch_code = 'thawi_watthana'
        GROUP BY b.txn_date, b.debit, b.credit, b.balance
        HAVING COUNT(*) > 1
    ) dupes;

    IF v_remaining_exact_duplicate_keys <> 0 THEN
        RAISE EXCEPTION 'Target exact duplicate keys remain after cleanup: %', v_remaining_exact_duplicate_keys;
    END IF;

    SELECT COUNT(*) INTO v_slip_now_keep_count
    FROM public.slips s
    WHERE s.id = '5c788be1-50d5-4a9c-81d6-20c61efa81d9'::uuid
      AND s.matched_statement_id = '89aa5df5-e7b0-4492-8d74-a40b3c8d62d7'::uuid
      AND s.amount = 700.00::numeric(12,2);

    IF v_slip_now_keep_count <> 1 THEN
        RAISE EXCEPTION 'Slip did not remain linked to the kept musician-fee row';
    END IF;

    SELECT COUNT(*) INTO v_pnl_deleted_refs
    FROM public.v_daybook_pnl p
    JOIN _may_statement_duplicate_deleted d ON p.ref_id = d.id::text;

    IF v_pnl_deleted_refs <> 0 THEN
        RAISE EXCEPTION 'Deleted statement rows still appear in v_daybook_pnl: %', v_pnl_deleted_refs;
    END IF;
END
$may_duplicate_postupdate$;

-- Preview after cleanup.
SELECT
    'DELETED' AS phase,
    id,
    txn_date,
    debit,
    credit,
    balance,
    source_type,
    category_code,
    match_status,
    reason
FROM _may_statement_duplicate_deleted
ORDER BY txn_date, debit, credit, id;

SELECT
    'KEPT' AS phase,
    b.id,
    b.txn_date,
    b.debit,
    b.credit,
    b.balance,
    b.source_type,
    b.category_code,
    b.match_status
FROM public.bank_statement_entries b
JOIN _may_statement_duplicate_targets t ON t.keep_id = b.id
ORDER BY b.txn_date, b.debit, b.credit, b.id;

SELECT
    'SLIP_REPOINTED' AS phase,
    slip_id,
    old_statement_id,
    new_statement_id,
    amount
FROM _may_statement_duplicate_repointed;

SELECT
    'MAY_PNL_AFTER_IN_TRANSACTION' AS phase,
    direction,
    COUNT(*) AS row_count,
    SUM(amount)::numeric(12,2) AS amount_sum
FROM public.v_daybook_pnl
WHERE entry_date >= '2026-05-01'::date
  AND entry_date < '2026-06-01'::date
GROUP BY direction
ORDER BY direction;

-- Leave as ROLLBACK for review/dry-run. For the real run, only after final
-- review + TUM Confirm, replace this ROLLBACK with COMMIT.
ROLLBACK;
-- COMMIT;

-- Rollback after a committed run, if ever needed:
-- BEGIN;
-- INSERT INTO public.bank_statement_entries (
--     id,
--     import_batch_id,
--     txn_date,
--     description,
--     debit,
--     credit,
--     balance,
--     category_code,
--     source_type,
--     match_status,
--     matched_invoice_id,
--     branch_code,
--     notes,
--     created_at,
--     classified_by,
--     classified_at,
--     loan_amount_override
-- )
-- SELECT
--     bak.id,
--     bak.import_batch_id,
--     bak.txn_date,
--     bak.description,
--     bak.debit,
--     bak.credit,
--     bak.balance,
--     bak.category_code,
--     bak.source_type,
--     bak.match_status,
--     bak.matched_invoice_id,
--     bak.branch_code,
--     bak.notes,
--     bak.created_at,
--     bak.classified_by,
--     bak.classified_at,
--     bak.loan_amount_override
-- FROM audit.bank_statement_duplicate_cleanup_backup_202605_20260707 bak
-- JOIN (
--     VALUES
--         ('c4df2b65-e3c7-448e-8ae7-467892c12d3a'::uuid),
--         ('b1102c88-c6a1-40c0-bbb7-2467f8ea9f2e'::uuid),
--         ('4ca007d4-2855-4f68-813e-b7be370d870b'::uuid),
--         ('0debd52e-5b41-46ff-aad1-d70bcaff25c5'::uuid),
--         ('8cdfaf5a-6834-4b70-b3fc-144defdde3f3'::uuid),
--         ('92b64182-9954-47ce-b7dc-d2f0cab2257f'::uuid),
--         ('db814525-5340-492b-8286-e21006200e1a'::uuid)
-- ) AS del(id) ON del.id = bak.id
-- WHERE NOT EXISTS (
--     SELECT 1
--     FROM public.bank_statement_entries live
--     WHERE live.id = bak.id
-- );
--
-- UPDATE public.slips s
-- -- Note: public.slips has an updated_at trigger, so this restores the
-- -- business link/audit fields but the trigger may stamp updated_at to NOW().
-- SET matched_statement_id = bak.matched_statement_id,
--     updated_by = bak.updated_by,
--     updated_at = bak.updated_at,
--     notes = bak.notes
-- FROM audit.statement_duplicate_cleanup_slip_backup_202605_20260707 bak
-- WHERE s.id = bak.id;
-- COMMIT;
