-- 2026-07-07-november-statement-reclass-verified-subset-draft.sql
-- VEXONHQ November 2025 bank_statement_entries reclass DRAFT (verified subset only).
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end.
--
-- Goal: fix November bank-statement settlement classifications for reconciliation
-- accuracy where supporting evidence is available. These rows are cash settlement
-- movements, not new sales.
-- Expected P&L impact: none for this verified subset. Settlement sources are excluded from v_daybook_pnl.
--
-- Source evidence:
-- - Fresh dry-run JSON: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_november_dry_run_20260707_233000\statement_reclass_november_dry_run.json
-- - November evidence folder: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS\2025-11-November
-- - Available November Grab CSV covers only 2025-11-17..2025-11-30 and totals 3474.91 THB by payout date.
-- - The 2025-12-01 Grab payout 137.16 from this November CSV is intentionally handled in the December draft as a December bank settlement.
-- - EXCLUDED pending evidence: 11 early-November Grab bank rows / 3168.81 THB (2025-11-02..2025-11-16) because the 2025-11-01..2025-11-16 Grab export is not available locally.
-- - No exact duplicate bank statement keys were found for November.
--
-- Expected target set:
--   total: 96 rows / 253502.11 THB
--   payment_gateway_payout: 87 rows / 250164.36 THB
--   grab_payout: 9 rows / 3337.75 THB
--   manual exceptions: 0 rows / 0.00 THB
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

CREATE TEMP TABLE _november_statement_reclass_targets (
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

INSERT INTO _november_statement_reclass_targets (
    id, expected_txn_date, expected_credit, current_source_type, current_category_code,
    current_match_status, new_source_type, new_category_code, new_match_status, note_append
)
VALUES
    ('0fb7339c-5d29-4ff6-a045-35c41bb144fc'::uuid, '2025-11-01'::date, 11433.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('9c62f012-ecfa-4393-a1a0-3b867174e297'::uuid, '2025-11-01'::date, 788.87::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('f4da2dcf-31fc-4e07-ade9-896ff79bcdbc'::uuid, '2025-11-01'::date, 644.47::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('215dc73d-6162-4fd1-b8f9-1d497971f0de'::uuid, '2025-11-02'::date, 1351.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('5fac5d8c-e292-4e56-9b1f-f77abc9b05a2'::uuid, '2025-11-02'::date, 22808.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('eb747b5c-991a-4e9c-b4ce-523406faba2e'::uuid, '2025-11-02'::date, 652.84::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('6f95edf5-0385-4e72-97ce-ed3faf53e7d3'::uuid, '2025-11-03'::date, 2554.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('234e3cf4-7898-4e10-a3d3-ce264ad5c588'::uuid, '2025-11-04'::date, 5711.71::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('7362c949-a801-40d2-8bb6-c51ec1c5028d'::uuid, '2025-11-04'::date, 968.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('7a757993-e38e-42f2-a543-5efb5f91ba96'::uuid, '2025-11-04'::date, 4442.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('3b1233be-857b-462c-9f88-0dcbca8b0d5f'::uuid, '2025-11-05'::date, 1025.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('41e819f6-edf9-4c61-8498-0ef2c365f0ce'::uuid, '2025-11-05'::date, 2646.48::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('99b23693-cbec-4385-9a70-b8ef47e9fc4e'::uuid, '2025-11-05'::date, 6334.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('4a0676ec-cc09-41ac-8f97-ab415d944c4f'::uuid, '2025-11-06'::date, 3203.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('944bdcea-cd1f-4d61-be1b-50879c29352d'::uuid, '2025-11-06'::date, 5614.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('bed85162-2729-4725-a435-3dcd1647cc73'::uuid, '2025-11-06'::date, 633.02::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('1ac31ee0-aded-4694-a9ea-e4230a2d62db'::uuid, '2025-11-07'::date, 842.16::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('37daf80b-1595-46ae-bf74-c2c1155fc187'::uuid, '2025-11-07'::date, 678.41::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('92355adf-88ce-4ab9-b4a3-f74a93054ce2'::uuid, '2025-11-07'::date, 290.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('fb566c4b-2015-400d-8830-e85765b78710'::uuid, '2025-11-07'::date, 4012.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('adcf0d1e-9ac3-45d7-bc31-1a358ffd525b'::uuid, '2025-11-08'::date, 10105.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('b0e821d4-6cdd-40bc-b777-1f043efe3282'::uuid, '2025-11-08'::date, 1141.74::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('fe16b14d-b11a-46ca-852e-79f85b92a0d3'::uuid, '2025-11-08'::date, 357.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('34c74e4c-af55-4584-8719-4ba921eeb773'::uuid, '2025-11-09'::date, 378.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('36d5ef57-eb17-4fbb-a2ca-f3302331ea53'::uuid, '2025-11-09'::date, 10451.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('f3b44680-d7a0-4451-8cb1-a3cb2c636ad3'::uuid, '2025-11-09'::date, 1316.08::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('51a61c57-7dae-42fd-9b19-8274c122e086'::uuid, '2025-11-10'::date, 6016.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('b2bb1554-3663-4747-91bc-de9a643523bf'::uuid, '2025-11-10'::date, 280.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('cd3c9096-bca5-49c5-a3a9-6b5a18423b6a'::uuid, '2025-11-10'::date, 732.28::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('f58d51e8-d0e3-46d9-b937-54a5cd9179ae'::uuid, '2025-11-11'::date, 657.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('fa2751c4-1dbd-44e6-9cfa-31a219fdb5d7'::uuid, '2025-11-11'::date, 2616.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('fbc03317-02d2-45e6-a8fc-9155a53efc6a'::uuid, '2025-11-11'::date, 503.75::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('069ffadd-dee7-4248-af66-4ffdb7a62082'::uuid, '2025-11-12'::date, 885.37::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('b5405d9d-8657-4790-941a-84a591879e7f'::uuid, '2025-11-12'::date, 988.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('22d18f87-1346-4425-bd0f-abdfe822d929'::uuid, '2025-11-13'::date, 3279.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('3a6bb891-42e4-4e68-b953-3d134a2b9173'::uuid, '2025-11-13'::date, 549.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('ab37e258-bb95-42c5-a080-41b4b3b751d1'::uuid, '2025-11-13'::date, 1302.90::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('45d72acb-ac54-4ed5-a3f0-b2cbcf57fb28'::uuid, '2025-11-14'::date, 513.99::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('4607cc31-368d-4d24-8bc8-cf026763e5af'::uuid, '2025-11-14'::date, 4910.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('fa1ebbd6-9513-41a8-b372-3c1329018364'::uuid, '2025-11-14'::date, 556.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('01f7ec99-1b2f-4936-9886-57294501e5ef'::uuid, '2025-11-15'::date, 324.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('98e3b3e7-e5b2-4787-9a72-5982ccbb6bc0'::uuid, '2025-11-15'::date, 503.01::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('d3340bdc-8ad1-4a84-a92f-6b5936759e80'::uuid, '2025-11-15'::date, 4348.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('060d2b56-7437-49f3-856f-2437d7f2e392'::uuid, '2025-11-16'::date, 259.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('3f6c3a0f-1c54-4b21-af0f-595a0b1d89cb'::uuid, '2025-11-16'::date, 735.95::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('a7f61eb0-1628-4c4f-aa29-2fcc038d840b'::uuid, '2025-11-16'::date, 14366.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('04567a4c-d995-4ddd-b4bf-50294bfd20fc'::uuid, '2025-11-17'::date, 171.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('b71addc5-f891-4000-9a6c-985d24bd8b46'::uuid, '2025-11-17'::date, 7568.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('11304ebb-ddcc-43c3-a926-76f1cdeca39d'::uuid, '2025-11-18'::date, 2983.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('3a53c1e8-0866-4eb1-a7d2-b44a151afb29'::uuid, '2025-11-18'::date, 196.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('fafbbbc3-6ab0-41c0-9b7c-022c3b43ff9a'::uuid, '2025-11-18'::date, 1462.36::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('3752d61a-be6c-490c-a510-c24f884d9497'::uuid, '2025-11-19'::date, 326.50::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('3de47299-ea54-4b8c-ba18-78e1a1cafdc6'::uuid, '2025-11-19'::date, 6086.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('4f3587b6-fb71-4916-970c-9b8c5438a55b'::uuid, '2025-11-19'::date, 1249.27::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('870456d0-10b5-4678-8e66-77d78558c98d'::uuid, '2025-11-19'::date, 121.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('cc09bc95-fc2d-4f7b-8b48-386799f34441'::uuid, '2025-11-19'::date, 591.56::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('0d2771a5-44d9-44c0-bd0f-1cc0b9ab5969'::uuid, '2025-11-20'::date, 919.79::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('2fe7b202-8991-4e8c-84f2-d3b81f2d8504'::uuid, '2025-11-20'::date, 731.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('6c29aaa5-3cf6-427c-99ab-09a5cf69225a'::uuid, '2025-11-20'::date, 5115.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('974f7686-b4d2-4226-bee7-c344913319a3'::uuid, '2025-11-20'::date, 280.87::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('172ba9da-122e-4052-8dce-4d83fe93ce8c'::uuid, '2025-11-21'::date, 721.29::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('1be55950-b933-4ea9-95af-d00085d94640'::uuid, '2025-11-21'::date, 1850.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('97ab39c8-9d4f-4e1e-a5b2-096b17fe1145'::uuid, '2025-11-21'::date, 9381.60::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('838ff38f-5fe3-4bf8-88a4-3c6b70e2eac8'::uuid, '2025-11-22'::date, 491.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('8476400d-085a-443e-a39c-f91b64fa958c'::uuid, '2025-11-22'::date, 4126.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('9c103088-8d5f-49d0-ae4c-c729bcc27ba4'::uuid, '2025-11-22'::date, 157.53::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('a03e8690-9494-4035-885a-490f8b16e13e'::uuid, '2025-11-22'::date, 878.84::numeric(12,2), 'lineman_payout', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('b0b4cbe8-10b8-4f4c-82aa-69d792405f3e'::uuid, '2025-11-23'::date, 1322.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('d0127240-43d1-4306-b749-69b37c469f00'::uuid, '2025-11-23'::date, 482.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('f7de6210-f83c-41b2-9400-9e25f0f70bcb'::uuid, '2025-11-23'::date, 189.72::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('fb34c88c-067d-49d5-bf6b-6bbe00b344a2'::uuid, '2025-11-23'::date, 7355.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('09a19a72-deb5-4eff-9295-d7288ea1dd08'::uuid, '2025-11-24'::date, 692.72::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('371b85b4-43a3-43f8-b201-26ebe41fd731'::uuid, '2025-11-24'::date, 2758.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('a649ca35-ec42-49b0-89e2-9cc53d996120'::uuid, '2025-11-24'::date, 753.47::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('d1a49cb5-315f-43a6-9a44-261f02171d9d'::uuid, '2025-11-24'::date, 6283.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('1a459151-96f5-4162-ba15-8b29f33fbfa2'::uuid, '2025-11-25'::date, 532.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('6a0f0cef-c6b5-4570-8104-d431ffdfbe85'::uuid, '2025-11-25'::date, 151.42::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('829ea463-c026-4469-8f1f-d926894632a0'::uuid, '2025-11-25'::date, 521.32::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('f8edacc8-1593-4d0e-ad56-976d443f4b1e'::uuid, '2025-11-25'::date, 5849.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('056c86fa-63b3-4ebc-a05e-f9baccc62ce5'::uuid, '2025-11-26'::date, 382.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('5a40fe87-37ff-4421-a891-f91a36f36c19'::uuid, '2025-11-26'::date, 1721.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('a6feffee-2f85-4c15-9d0a-f8d16bddaacd'::uuid, '2025-11-27'::date, 1481.39::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('a746f49c-029a-46ee-bb7e-9c1ae55f9757'::uuid, '2025-11-27'::date, 587.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('c964ae29-aa19-483a-8e1c-0a8f9555bd4b'::uuid, '2025-11-27'::date, 3382.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('32db4dfc-f1a8-47e7-9fe1-bdd31f040c79'::uuid, '2025-11-28'::date, 1409.11::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('7de2e2fc-fba3-421b-a1a1-bbfbe0c6f17c'::uuid, '2025-11-28'::date, 1255.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('83805c47-a961-4513-82e5-f72b0e456d38'::uuid, '2025-11-28'::date, 972.37::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('0e50b9ed-999f-4497-b989-6003edeb8815'::uuid, '2025-11-29'::date, 7643.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('1b1875f1-42c4-42c5-976e-2ccc7994545b'::uuid, '2025-11-29'::date, 151.86::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('54d1fce3-c6e4-4e0b-b15e-0212537e4a8c'::uuid, '2025-11-29'::date, 274.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('728aa572-e669-46cc-a284-1d91c7bb429d'::uuid, '2025-11-29'::date, 873.66::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('184fcb5e-e41e-4fe1-82b6-007930f93afb'::uuid, '2025-11-30'::date, 1186.73::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('386400ab-6b49-43b2-9022-e8ca27c37ff5'::uuid, '2025-11-30'::date, 13680.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('4b5e4ee0-0cb1-4029-954c-be6a78e914e1'::uuid, '2025-11-30'::date, 88.95::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed November reclass: Grab bank settlement -> grab_payout; verified against available November Grab export 2025-11-17..2025-11-30; report statement_reclass_november_dry_run_20260707_233000.'),
    ('534e9078-5250-4566-902f-142f10949d7c'::uuid, '2025-11-30'::date, 1256.75::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.'),
    ('fb0d2c96-4999-4379-9eab-de14fb5701d7'::uuid, '2025-11-30'::date, 154.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed November reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_november_dry_run_20260707_233000.');

SELECT 'BEFORE' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _november_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

DO $november_reclass_lock_and_preflight$
DECLARE
    v_locked_count integer; v_target_count integer; v_exact_count integer; v_manual_current_count integer;
    v_target_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*) INTO v_locked_count FROM (
        SELECT b.id FROM public.bank_statement_entries b JOIN _november_statement_reclass_targets t ON t.id = b.id ORDER BY b.id FOR UPDATE
    ) locked_rows;
    IF v_locked_count <> 96 THEN RAISE EXCEPTION 'Locked row count mismatch: expected %, got %', 96, v_locked_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_target_count, v_target_sum FROM _november_statement_reclass_targets;
    IF v_target_count <> 96 THEN RAISE EXCEPTION 'Target count mismatch: expected %, got %', 96, v_target_count; END IF;
    IF v_target_sum <> 253502.11::numeric(12,2) THEN RAISE EXCEPTION 'Target credit sum mismatch: expected %, got %', 253502.11, v_target_sum; END IF;

    SELECT COUNT(*) INTO v_exact_count
    FROM _november_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date AND b.credit=t.expected_credit
      AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status=t.current_match_status AND b.match_status <> 'manual';
    IF v_exact_count <> 96 THEN RAISE EXCEPTION 'Current-state guard mismatch: expected %, got %', 96, v_exact_count; END IF;

    SELECT COUNT(*) INTO v_manual_current_count FROM _november_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id=t.id WHERE b.match_status='manual';
    IF v_manual_current_count <> 0 THEN RAISE EXCEPTION 'Refusing to overwrite currently manual rows: %', v_manual_current_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _november_statement_reclass_targets WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 87 OR v_gateway_sum <> 250164.36::numeric(12,2) THEN RAISE EXCEPTION 'Gateway target bucket mismatch: expected % / %, got % / %', 87, 250164.36, v_gateway_count, v_gateway_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _november_statement_reclass_targets WHERE new_source_type='grab_payout';
    IF v_grab_count <> 9 OR v_grab_sum <> 3337.75::numeric(12,2) THEN RAISE EXCEPTION 'Grab target bucket mismatch: expected % / %, got % / %', 9, 3337.75, v_grab_count, v_grab_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _november_statement_reclass_targets WHERE new_match_status='manual';
    IF v_new_manual_count <> 0 OR v_new_manual_sum <> 0.00::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 0, 0.00, v_new_manual_count, v_new_manual_sum; END IF;
END
$november_reclass_lock_and_preflight$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260707_november_verified_subset AS
SELECT b.* FROM public.bank_statement_entries b JOIN _november_statement_reclass_targets t ON t.id = b.id;
COMMENT ON TABLE audit.bank_statement_reclass_backup_20260707_november_verified_subset IS 'Pre-reclass backup of 96 November 2025 bank_statement_entries verified-subset rows reviewed on 2026-07-07.';
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_november_verified_subset FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_november_verified_subset FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_november_verified_subset FROM authenticated;

DO $november_reclass_backup_check$
DECLARE v_backup_count integer; v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_backup_count, v_backup_sum FROM audit.bank_statement_reclass_backup_20260707_november_verified_subset;
    IF v_backup_count <> 96 THEN RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 96, v_backup_count; END IF;
    IF v_backup_sum <> 253502.11::numeric(12,2) THEN RAISE EXCEPTION 'Backup credit sum mismatch: expected %, got %', 253502.11, v_backup_sum; END IF;
END
$november_reclass_backup_check$;

CREATE TEMP TABLE _november_statement_reclass_updated (
    id uuid PRIMARY KEY, old_source_type text, old_category_code text, old_match_status text,
    new_source_type text, new_category_code text, new_match_status text, credit numeric(12,2) NOT NULL
) ON COMMIT DROP;

WITH updated AS (
    UPDATE public.bank_statement_entries b
    SET source_type=t.new_source_type,
        category_code=t.new_category_code,
        match_status=t.new_match_status,
        notes=CASE WHEN COALESCE(BTRIM(b.notes),'')='' THEN t.note_append ELSE b.notes || E'\n' || t.note_append END,
        classified_by='codex_reviewed_november_reclass_20260707',
        classified_at=now()
    FROM _november_statement_reclass_targets t
    WHERE b.id=t.id AND b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date
      AND b.credit=t.expected_credit AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code AND b.match_status=t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING b.id, t.current_source_type AS old_source_type, t.current_category_code AS old_category_code,
              t.current_match_status AS old_match_status, b.source_type AS new_source_type,
              b.category_code AS new_category_code, b.match_status AS new_match_status, b.credit
)
INSERT INTO _november_statement_reclass_updated
SELECT id, old_source_type, old_category_code, old_match_status, new_source_type, new_category_code, new_match_status, credit FROM updated;

DO $november_reclass_postupdate$
DECLARE
    v_updated_count integer; v_updated_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_updated_count, v_updated_sum FROM _november_statement_reclass_updated;
    IF v_updated_count <> 96 THEN RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 96, v_updated_count; END IF;
    IF v_updated_sum <> 253502.11::numeric(12,2) THEN RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 253502.11, v_updated_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _november_statement_reclass_updated WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 87 OR v_gateway_sum <> 250164.36::numeric(12,2) THEN RAISE EXCEPTION 'Gateway bucket mismatch: expected % / %, got % / %', 87, 250164.36, v_gateway_count, v_gateway_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _november_statement_reclass_updated WHERE new_source_type='grab_payout';
    IF v_grab_count <> 9 OR v_grab_sum <> 3337.75::numeric(12,2) THEN RAISE EXCEPTION 'Grab bucket mismatch: expected % / %, got % / %', 9, 3337.75, v_grab_count, v_grab_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _november_statement_reclass_updated WHERE new_match_status='manual';
    IF v_new_manual_count <> 0 OR v_new_manual_sum <> 0.00::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 0, 0.00, v_new_manual_count, v_new_manual_sum; END IF;
END
$november_reclass_postupdate$;

SELECT 'AFTER' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _november_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

SELECT 'NOVEMBER_PNL_AFTER_IN_TRANSACTION' AS phase, direction, COUNT(*) AS row_count, SUM(amount)::numeric(12,2) AS amount_sum
FROM public.v_daybook_pnl
WHERE entry_date >= '2025-11-01'::date AND entry_date < '2025-12-01'::date
GROUP BY direction ORDER BY direction;

ROLLBACK;
-- COMMIT;

-- Rollback after a committed run, if ever needed:
-- BEGIN;
-- UPDATE public.bank_statement_entries b
-- SET source_type=bak.source_type,
--     category_code=bak.category_code,
--     match_status=bak.match_status,
--     notes=bak.notes,
--     classified_by=bak.classified_by,
--     classified_at=bak.classified_at
-- FROM audit.bank_statement_reclass_backup_20260707_november_verified_subset bak
-- WHERE b.id=bak.id;
-- COMMIT;
