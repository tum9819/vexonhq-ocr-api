-- 2026-07-07-february-statement-reclass-draft.sql
-- VEXONHQ February 2026 bank_statement_entries reclass DRAFT.
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end.
--
-- Goal: fix February bank-statement settlement classifications for reconciliation
-- accuracy. These rows are cash settlement movements, not new sales.
-- Expected P&L impact: none. The settlement sources are excluded from v_daybook_pnl.
--
-- Source evidence:
-- - Fresh dry-run JSON: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_february_dry_run_20260707_230000\statement_reclass_february_dry_run.json
-- - February evidence folder: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS\2026-02-February
-- - February Grab CSV aggregate by payout date = 10887.80 THB.
-- - Additional cross-month evidence: 2026-02-01 bank Grab 333.88 exactly matches January Grab export payout-date aggregate (3 orders).
-- - February Grab CSV payout 735.80 on 2026-03-01 is intentionally handled in the March draft as a March bank settlement.
-- - No exact duplicate bank statement keys were found for February.
--
-- Expected target set:
--   total: 98 rows / 179513.36 THB
--   payment_gateway_payout: 73 rows / 169027.48 THB
--   grab_payout: 25 rows / 10485.88 THB
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

CREATE TEMP TABLE _february_statement_reclass_targets (
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

INSERT INTO _february_statement_reclass_targets (
    id, expected_txn_date, expected_credit, current_source_type, current_category_code,
    current_match_status, new_source_type, new_category_code, new_match_status, note_append
)
VALUES
    ('1b14cde3-6f0d-4de9-b8b1-e8d1fb838c3a'::uuid, '2026-02-01'::date, 12761.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('55ccb7bc-2ac2-4c7d-86da-1b2dd32d7b24'::uuid, '2026-02-01'::date, 1648.87::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('9ed0b320-4364-4d0a-90b8-4318fe6aeaee'::uuid, '2026-02-01'::date, 2594.07::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('ec0e52a5-e8f4-4b05-b43a-5d07525984f9'::uuid, '2026-02-01'::date, 333.88::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; exact payout-date aggregate match to January Grab export on 2026-02-01 (3 orders), cross-month settlement; report statement_reclass_february_dry_run_20260707_230000.'),
    ('6ddfa5b0-d42e-4f75-ae79-b350f62875a7'::uuid, '2026-02-03'::date, 114.07::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('b7a7c9bb-5b8f-4de4-be79-1c432bc3224c'::uuid, '2026-02-03'::date, 504.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('e94921fe-a010-47d8-8c2e-705e46e21398'::uuid, '2026-02-03'::date, 1259.33::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('ead29568-cdb8-4f47-af74-f864c5ffb4f5'::uuid, '2026-02-03'::date, 4740.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('6395902a-31e3-4773-bec5-a972c204c0d8'::uuid, '2026-02-04'::date, 281.60::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('71e1bb24-8f72-4a8d-bc1c-1576345cf51c'::uuid, '2026-02-04'::date, 230.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('74fe37d3-5fdc-4741-b7b2-cacdac38d7bb'::uuid, '2026-02-04'::date, 782.30::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('76e89113-9f67-46b6-a5ba-9d47f51a3378'::uuid, '2026-02-04'::date, 5483.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('041987a9-150c-4fc7-ae68-a38982e7d515'::uuid, '2026-02-05'::date, 528.26::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('42fc2926-248b-4fa2-99d1-cd1aa8b2c2d2'::uuid, '2026-02-05'::date, 3136.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('b83280bd-4ef0-4621-b01a-e1ff86d973c0'::uuid, '2026-02-05'::date, 1083.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('e3cab60b-f916-42af-b1f3-7cc45f38f627'::uuid, '2026-02-05'::date, 984.05::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('187a98ca-d982-4c16-9c03-1d12564d2a4d'::uuid, '2026-02-06'::date, 318.73::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('7ec8d117-3b10-41ba-a0b9-d76e7c873091'::uuid, '2026-02-06'::date, 1463.60::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('c693c9b6-a023-4771-8b6c-8d038f56da71'::uuid, '2026-02-06'::date, 106.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('fe21a1c1-08c9-458a-b0eb-e7e0ecbe2726'::uuid, '2026-02-06'::date, 2010.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('990c3557-06ef-47a6-9be6-a1b90c52e51d'::uuid, '2026-02-07'::date, 103.21::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('a24a0fc0-8f3d-4755-8b5d-8851269106b4'::uuid, '2026-02-07'::date, 837.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('d6c6b58f-5b60-4ae7-b194-10f9b1a305cb'::uuid, '2026-02-07'::date, 14356.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('e03d8a68-6aa2-4b7e-853c-efec5f47d417'::uuid, '2026-02-07'::date, 1132.91::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('0aebb8e9-6a24-428a-b760-390977327ff3'::uuid, '2026-02-08'::date, 359.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('1f3048d7-1bdc-487f-95dd-6b8a909856a3'::uuid, '2026-02-08'::date, 1245.63::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('815872be-8761-4f3d-aca8-b291cefa934d'::uuid, '2026-02-08'::date, 862.84::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('ec934628-699c-41b4-8310-bd4121cc6b0b'::uuid, '2026-02-08'::date, 7525.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('6b63a562-18ff-4cf6-9749-9d03e365e732'::uuid, '2026-02-09'::date, 1102.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('b75500e0-6158-4a20-823e-4842f2b76046'::uuid, '2026-02-09'::date, 304.93::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('e909d195-af24-4003-af61-4b4a6d4a8ac4'::uuid, '2026-02-09'::date, 1822.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('66a4becf-6b0d-4f06-a518-19ed443a334e'::uuid, '2026-02-10'::date, 70.62::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('78847c97-08e1-4a31-a928-c0a456f1c925'::uuid, '2026-02-10'::date, 672.48::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('d7dcd3e1-1343-4754-8219-ea20fe33e786'::uuid, '2026-02-10'::date, 78.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('e552ce67-592d-481d-adcc-f570729ec2ec'::uuid, '2026-02-10'::date, 2358.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('38b968b6-b16b-4840-aa5c-53f349ffe63c'::uuid, '2026-02-11'::date, 2897.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('4eeb58ff-e9d6-4cf0-aa55-ddeacd9a5898'::uuid, '2026-02-11'::date, 280.42::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('878aa9c3-05c9-46f9-ae12-a71eacc56c6a'::uuid, '2026-02-11'::date, 577.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('facf24ae-b3a9-46d8-8593-010051d9de30'::uuid, '2026-02-11'::date, 1263.21::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('0d2b81cf-eca7-4d8d-81e7-520e613272d8'::uuid, '2026-02-12'::date, 2223.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('5339946c-239f-45e8-8590-1eabcff4f96a'::uuid, '2026-02-12'::date, 498.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('c51b9892-0b4f-44de-b4a9-13b8d7d29b7d'::uuid, '2026-02-12'::date, 790.73::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('22d63c65-b278-432a-a477-60eb0cfcf904'::uuid, '2026-02-13'::date, 1652.45::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('5ecc898b-6f39-4bb5-8345-5c8f0a004468'::uuid, '2026-02-13'::date, 856.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('8011f9d4-3bfb-4173-8f06-70679f2f7e76'::uuid, '2026-02-13'::date, 770.33::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('c31e1566-456e-4ff1-9190-f7653f0e0947'::uuid, '2026-02-13'::date, 5397.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('3db5074d-cbd1-456f-9fe6-3932a8c692b0'::uuid, '2026-02-14'::date, 1594.05::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('4781057d-7f96-42db-a6de-5f7a45dc22ef'::uuid, '2026-02-14'::date, 990.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('861e2048-218b-4df6-a338-eaa442655f02'::uuid, '2026-02-14'::date, 2150.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('8b430d1d-669e-4bf9-86fa-0de316bc017e'::uuid, '2026-02-14'::date, 686.80::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('bf917735-8e00-41e2-b8b3-ba724d795bc7'::uuid, '2026-02-15'::date, 1761.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('c00d9833-eec0-4d83-b46a-a536b392d3c6'::uuid, '2026-02-15'::date, 10235.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('f5541601-7f2c-4e5c-9ef4-d4cc0c99297c'::uuid, '2026-02-15'::date, 321.10::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('8d85d952-9329-4c5f-a8c4-420da3e709d4'::uuid, '2026-02-16'::date, 8132.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('5f0858c5-3e71-4055-85e3-e29f009ffc98'::uuid, '2026-02-17'::date, 3859.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('79692abc-9f86-424a-9f67-a43ce044c196'::uuid, '2026-02-17'::date, 995.10::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('7a663de6-3296-467e-bcaa-1db3e6c7eded'::uuid, '2026-02-17'::date, 519.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('bab42d8e-715d-4120-8889-0ae9623b5cd7'::uuid, '2026-02-17'::date, 439.30::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('62f89c85-d857-48bf-996f-87f5b6970011'::uuid, '2026-02-18'::date, 1300.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('818bf4d3-a501-4ffb-ad3b-f54ca1421a1d'::uuid, '2026-02-18'::date, 242.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('95ee4cd1-8886-4434-ab43-3ca276b974cb'::uuid, '2026-02-18'::date, 335.32::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('c25de502-e5bc-4bf3-855d-4e4ac20ba2a5'::uuid, '2026-02-18'::date, 694.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('5bc44591-189c-4062-b0c1-2f5176950571'::uuid, '2026-02-19'::date, 960.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('c33bf845-8419-4262-a0c5-34611b946b48'::uuid, '2026-02-19'::date, 617.11::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('e12795f5-b1da-4842-b80a-c567d7199f51'::uuid, '2026-02-19'::date, 1738.40::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('2f5ead0c-ee6c-4cae-8e0d-eac0768a4f5d'::uuid, '2026-02-20'::date, 910.46::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('543ef79b-a666-4f2b-9a00-91206e98fe85'::uuid, '2026-02-20'::date, 1735.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('8d972a6c-d230-4fca-9d50-abf977c3a9f6'::uuid, '2026-02-20'::date, 954.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('f268057b-29bc-4648-a1fa-8c6a323d061b'::uuid, '2026-02-20'::date, 111.35::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('026fbbc4-967e-4534-8643-67b1298ee37e'::uuid, '2026-02-21'::date, 1030.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('5d85b43e-d1fc-4a12-a669-1e5dc425652a'::uuid, '2026-02-21'::date, 237.65::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('6f182da5-dbf9-4058-8a01-bf953c3c55ca'::uuid, '2026-02-21'::date, 3416.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('5c69f373-c2ed-4b4b-972e-61334eec1286'::uuid, '2026-02-22'::date, 1947.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('6dedf1ac-7ac5-4706-b9b0-936591f87e8a'::uuid, '2026-02-22'::date, 372.82::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('76282375-dd8d-4ad4-bf59-c257c07ba128'::uuid, '2026-02-22'::date, 11075.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('ccbc755f-433d-47d9-a505-b7a79a294c04'::uuid, '2026-02-22'::date, 818.31::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('4ed55ac7-eb16-4545-b0e2-24558b301f94'::uuid, '2026-02-23'::date, 254.11::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('5e8a94a4-338a-4c92-b803-7dde034bcc01'::uuid, '2026-02-23'::date, 2015.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('aee0deae-5d15-4c5f-9b43-b91e641828d4'::uuid, '2026-02-23'::date, 922.46::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('f510c6d1-e594-4390-bbfa-0abc70b5a189'::uuid, '2026-02-23'::date, 222.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('6fa4fb08-99b0-4ccd-ac39-64a216c362a4'::uuid, '2026-02-24'::date, 1936.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('c685a3b7-1c2b-42ee-8d4a-51de67c22daa'::uuid, '2026-02-24'::date, 672.23::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('e80ba6a0-d44b-4154-9692-e07270f0a6b3'::uuid, '2026-02-24'::date, 999.38::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('20da242a-dbf9-49ae-953b-66b6059c0f2b'::uuid, '2026-02-25'::date, 744.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('3baea462-e1cf-48cd-820a-4d4d39c7feac'::uuid, '2026-02-25'::date, 838.23::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('44669f01-4e87-4a6f-89b6-9b01a43ea151'::uuid, '2026-02-25'::date, 3796.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('d3525cc9-6255-4743-8397-7cc13366f903'::uuid, '2026-02-25'::date, 256.14::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('013770d5-46a9-458d-8912-1cd4034feb4a'::uuid, '2026-02-26'::date, 203.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('028bc848-a364-4771-829c-c8f37d27e739'::uuid, '2026-02-26'::date, 772.59::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('69615978-cde3-4187-9d11-7aa202816ad7'::uuid, '2026-02-26'::date, 696.86::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('eb17263c-eaca-4a0c-852e-f196b7fbf62b'::uuid, '2026-02-26'::date, 907.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('4a20086d-f2e9-4ff0-9c8b-79c7aa943033'::uuid, '2026-02-27'::date, 584.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('a6858194-63f3-4a03-8af4-6bcd336f93d6'::uuid, '2026-02-27'::date, 2865.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('ac2eaf78-7dee-4258-907f-95df5a8c37d8'::uuid, '2026-02-27'::date, 853.43::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('11a1270f-b210-4c19-918b-a80d62561ce7'::uuid, '2026-02-28'::date, 6419.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('75fea5bc-7e1e-4ba2-b554-f59f8628cc36'::uuid, '2026-02-28'::date, 1149.32::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.'),
    ('b4d688d5-2698-48fa-bef4-38dfc30143a2'::uuid, '2026-02-28'::date, 398.32::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed February reclass: Grab bank settlement -> grab_payout; February Grab export payout-date evidence reviewed; report statement_reclass_february_dry_run_20260707_230000.'),
    ('dfe4c55a-1d68-435c-9b74-84d833b3d146'::uuid, '2026-02-28'::date, 1436.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed February reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_february_dry_run_20260707_230000.');

SELECT 'BEFORE' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _february_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

DO $february_reclass_lock_and_preflight$
DECLARE
    v_locked_count integer; v_target_count integer; v_exact_count integer; v_manual_current_count integer;
    v_target_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*) INTO v_locked_count FROM (
        SELECT b.id FROM public.bank_statement_entries b JOIN _february_statement_reclass_targets t ON t.id = b.id ORDER BY b.id FOR UPDATE
    ) locked_rows;
    IF v_locked_count <> 98 THEN RAISE EXCEPTION 'Locked row count mismatch: expected %, got %', 98, v_locked_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_target_count, v_target_sum FROM _february_statement_reclass_targets;
    IF v_target_count <> 98 THEN RAISE EXCEPTION 'Target count mismatch: expected %, got %', 98, v_target_count; END IF;
    IF v_target_sum <> 179513.36::numeric(12,2) THEN RAISE EXCEPTION 'Target credit sum mismatch: expected %, got %', 179513.36, v_target_sum; END IF;

    SELECT COUNT(*) INTO v_exact_count
    FROM _february_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date AND b.credit=t.expected_credit
      AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status=t.current_match_status AND b.match_status <> 'manual';
    IF v_exact_count <> 98 THEN RAISE EXCEPTION 'Current-state guard mismatch: expected %, got %', 98, v_exact_count; END IF;

    SELECT COUNT(*) INTO v_manual_current_count FROM _february_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id=t.id WHERE b.match_status='manual';
    IF v_manual_current_count <> 0 THEN RAISE EXCEPTION 'Refusing to overwrite currently manual rows: %', v_manual_current_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _february_statement_reclass_targets WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 73 OR v_gateway_sum <> 169027.48::numeric(12,2) THEN RAISE EXCEPTION 'Gateway target bucket mismatch: expected % / %, got % / %', 73, 169027.48, v_gateway_count, v_gateway_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _february_statement_reclass_targets WHERE new_source_type='grab_payout';
    IF v_grab_count <> 25 OR v_grab_sum <> 10485.88::numeric(12,2) THEN RAISE EXCEPTION 'Grab target bucket mismatch: expected % / %, got % / %', 25, 10485.88, v_grab_count, v_grab_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _february_statement_reclass_targets WHERE new_match_status='manual';
    IF v_new_manual_count <> 0 OR v_new_manual_sum <> 0.00::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 0, 0.00, v_new_manual_count, v_new_manual_sum; END IF;
END
$february_reclass_lock_and_preflight$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260707_february AS
SELECT b.* FROM public.bank_statement_entries b JOIN _february_statement_reclass_targets t ON t.id = b.id;
COMMENT ON TABLE audit.bank_statement_reclass_backup_20260707_february IS 'Pre-reclass backup of 98 February 2026 bank_statement_entries rows reviewed on 2026-07-07.';
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_february FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_february FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_february FROM authenticated;

DO $february_reclass_backup_check$
DECLARE v_backup_count integer; v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_backup_count, v_backup_sum FROM audit.bank_statement_reclass_backup_20260707_february;
    IF v_backup_count <> 98 THEN RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 98, v_backup_count; END IF;
    IF v_backup_sum <> 179513.36::numeric(12,2) THEN RAISE EXCEPTION 'Backup credit sum mismatch: expected %, got %', 179513.36, v_backup_sum; END IF;
END
$february_reclass_backup_check$;

CREATE TEMP TABLE _february_statement_reclass_updated (
    id uuid PRIMARY KEY, old_source_type text, old_category_code text, old_match_status text,
    new_source_type text, new_category_code text, new_match_status text, credit numeric(12,2) NOT NULL
) ON COMMIT DROP;

WITH updated AS (
    UPDATE public.bank_statement_entries b
    SET source_type=t.new_source_type,
        category_code=t.new_category_code,
        match_status=t.new_match_status,
        notes=CASE WHEN COALESCE(BTRIM(b.notes),'')='' THEN t.note_append ELSE b.notes || E'\n' || t.note_append END,
        classified_by='codex_reviewed_february_reclass_20260707',
        classified_at=now()
    FROM _february_statement_reclass_targets t
    WHERE b.id=t.id AND b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date
      AND b.credit=t.expected_credit AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code AND b.match_status=t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING b.id, t.current_source_type AS old_source_type, t.current_category_code AS old_category_code,
              t.current_match_status AS old_match_status, b.source_type AS new_source_type,
              b.category_code AS new_category_code, b.match_status AS new_match_status, b.credit
)
INSERT INTO _february_statement_reclass_updated
SELECT id, old_source_type, old_category_code, old_match_status, new_source_type, new_category_code, new_match_status, credit FROM updated;

DO $february_reclass_postupdate$
DECLARE
    v_updated_count integer; v_updated_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_updated_count, v_updated_sum FROM _february_statement_reclass_updated;
    IF v_updated_count <> 98 THEN RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 98, v_updated_count; END IF;
    IF v_updated_sum <> 179513.36::numeric(12,2) THEN RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 179513.36, v_updated_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _february_statement_reclass_updated WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 73 OR v_gateway_sum <> 169027.48::numeric(12,2) THEN RAISE EXCEPTION 'Gateway bucket mismatch: expected % / %, got % / %', 73, 169027.48, v_gateway_count, v_gateway_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _february_statement_reclass_updated WHERE new_source_type='grab_payout';
    IF v_grab_count <> 25 OR v_grab_sum <> 10485.88::numeric(12,2) THEN RAISE EXCEPTION 'Grab bucket mismatch: expected % / %, got % / %', 25, 10485.88, v_grab_count, v_grab_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _february_statement_reclass_updated WHERE new_match_status='manual';
    IF v_new_manual_count <> 0 OR v_new_manual_sum <> 0.00::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 0, 0.00, v_new_manual_count, v_new_manual_sum; END IF;
END
$february_reclass_postupdate$;

SELECT 'AFTER' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _february_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

SELECT 'FEBRUARY_PNL_AFTER_IN_TRANSACTION' AS phase, direction, COUNT(*) AS row_count, SUM(amount)::numeric(12,2) AS amount_sum
FROM public.v_daybook_pnl
WHERE entry_date >= '2026-02-01'::date AND entry_date < '2026-03-01'::date
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
-- FROM audit.bank_statement_reclass_backup_20260707_february bak
-- WHERE b.id=bak.id;
-- COMMIT;
