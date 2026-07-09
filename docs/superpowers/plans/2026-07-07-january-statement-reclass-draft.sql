-- 2026-07-07-january-statement-reclass-draft.sql
-- VEXONHQ January 2026 bank_statement_entries reclass DRAFT.
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end.
--
-- Goal: fix January bank-statement settlement classifications for reconciliation
-- accuracy. These rows are cash settlement movements, not new sales.
-- Expected P&L impact: none. The settlement sources are excluded from v_daybook_pnl.
--
-- Source evidence:
-- - Fresh dry-run JSON: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_january_dry_run_20260707_231000\statement_reclass_january_dry_run.json
-- - January evidence folder: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS\2026-01-January
-- - January Grab CSV aggregate by payout date = 13380.43 THB.
-- - Two category-only Grab corrections were missed by dry-run heuristic because source_type was already grab_payout, but category_code was still pos_cash: 396.71 on 2026-01-14 and 576.61 on 2026-01-25.
-- - January Grab CSV payout 333.88 on 2026-02-01 is intentionally handled in the February draft as a February bank settlement.
-- - No exact duplicate bank statement keys were found for January.
--
-- Expected target set:
--   total: 102 rows / 197126.35 THB
--   payment_gateway_payout: 76 rows / 184079.80 THB
--   grab_payout: 26 rows / 13046.55 THB
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

CREATE TEMP TABLE _january_statement_reclass_targets (
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

INSERT INTO _january_statement_reclass_targets (
    id, expected_txn_date, expected_credit, current_source_type, current_category_code,
    current_match_status, new_source_type, new_category_code, new_match_status, note_append
)
VALUES
    ('0e3a1d3e-61c6-4d27-9d02-a966b291c4e6'::uuid, '2026-01-03'::date, 3037.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('663a2efc-8049-4518-acb4-395abc98d85a'::uuid, '2026-01-03'::date, 1193.81::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c19f0c81-295f-4dff-8531-2d54aa318b2f'::uuid, '2026-01-03'::date, 972.83::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('d71ec64e-dd11-414b-80f2-138ebb8a8f0c'::uuid, '2026-01-03'::date, 914.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('5fe19340-17fd-4435-b89c-ce9c8d491508'::uuid, '2026-01-04'::date, 6404.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('6f927512-9255-4187-9631-9a40596a255e'::uuid, '2026-01-04'::date, 1305.53::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('8db8a84c-48ce-4243-8de3-d86efb8b6b2b'::uuid, '2026-01-04'::date, 691.55::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('b4630497-9523-406b-8819-aadc2535895f'::uuid, '2026-01-04'::date, 1194.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('47ef4fa1-d564-443f-9fed-69afb41e9277'::uuid, '2026-01-05'::date, 294.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('7e4c244b-ccc3-41ad-8300-1c90fb9035a6'::uuid, '2026-01-05'::date, 796.76::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('e67b95ba-e766-4a3b-9f75-374cb24049e6'::uuid, '2026-01-05'::date, 3598.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('5d864313-dc9a-49f4-a45d-a316144a632f'::uuid, '2026-01-06'::date, 1280.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('7ca4ba6b-0047-469e-8634-63376aa58105'::uuid, '2026-01-06'::date, 478.01::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('7dc6d415-ca87-48d7-b565-f2c1c7d419c8'::uuid, '2026-01-06'::date, 456.42::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('d4134d1e-eecb-461c-bb89-86b4f199094e'::uuid, '2026-01-06'::date, 851.32::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('2bed5bc8-7711-43cf-9dc0-6dbedc9a7d69'::uuid, '2026-01-07'::date, 689.36::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('ad4dcbf9-56a2-4445-b09f-6d8c6dda53b3'::uuid, '2026-01-07'::date, 12528.30::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('b5672269-19b8-4ae1-bf13-1118985a3e62'::uuid, '2026-01-07'::date, 405.91::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('f743999a-a3a6-47cc-9821-a777a30a2d30'::uuid, '2026-01-07'::date, 310.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('1baf9e68-6969-4f88-aafd-95d9e19bb555'::uuid, '2026-01-08'::date, 931.26::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('2dde123c-d157-402c-9e3a-5a7ecbcc5436'::uuid, '2026-01-08'::date, 58.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('87ad8c6f-1f06-4328-ba1d-03cd43fcbc6f'::uuid, '2026-01-08'::date, 918.94::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('4f9915f2-8b46-4bdc-987e-2f7af2f21033'::uuid, '2026-01-09'::date, 957.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('ebaed96e-9ff6-4f42-b0d6-038f99ee890a'::uuid, '2026-01-09'::date, 419.25::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('fdfbe12d-635b-449d-91f3-684e6f045107'::uuid, '2026-01-09'::date, 3320.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('243b5e89-8932-47f5-9a32-9949ee28383a'::uuid, '2026-01-10'::date, 178.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('8838c8bb-7029-4ced-865b-f57b72e28b63'::uuid, '2026-01-10'::date, 858.87::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('b5154c04-fe4d-4f60-8ad6-3d6629a21906'::uuid, '2026-01-10'::date, 4306.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c5cead74-9a19-4f58-8c54-96d1d1089fa7'::uuid, '2026-01-10'::date, 1636.49::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('269066bc-8224-4bd2-a8b8-43d2e4e975cb'::uuid, '2026-01-11'::date, 753.51::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('74e6efce-585c-4708-b79c-5cd0aaacb4fa'::uuid, '2026-01-11'::date, 1056.25::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('882349b3-032d-4df8-82e8-108794c78306'::uuid, '2026-01-11'::date, 3548.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c4aa2bba-823a-46df-8950-15aa5cf10bda'::uuid, '2026-01-11'::date, 313.60::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('ff0b4993-8d6d-4f69-a102-147ff22c4aaa'::uuid, '2026-01-11'::date, 8955.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('b3529164-f8c8-485b-8037-e6e2406f9f95'::uuid, '2026-01-12'::date, 2901.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('0e0b1ccd-a428-4bed-9fc7-54913fd1f16f'::uuid, '2026-01-13'::date, 906.11::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('4424cee7-0e5e-414c-8d5a-ea188564e5df'::uuid, '2026-01-13'::date, 167.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('bb1bc8e0-60a3-440b-81e5-9947619a9ee9'::uuid, '2026-01-13'::date, 1953.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('df805793-f133-4cbf-b11a-25c7d9112672'::uuid, '2026-01-13'::date, 601.58::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('396d1b06-4335-4641-b58a-191de089e34e'::uuid, '2026-01-14'::date, 1037.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('55ed4ca2-2e09-4e54-a127-577f01eda450'::uuid, '2026-01-14'::date, 396.71::numeric(12,2), 'grab_payout', 'pos_cash', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: category-only correction for Grab payout; bank row already source_type=grab_payout but category_code=pos_cash; exact January Grab export payout-date aggregate 2026-01-14 (3 orders); report statement_reclass_january_dry_run_20260707_231000.'),
    ('77556e9e-d611-435b-b4a4-95f57cafcd56'::uuid, '2026-01-14'::date, 2273.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c2c3cc44-925e-4567-95be-ef2e55ddb4fa'::uuid, '2026-01-14'::date, 1151.39::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('52ca17d5-79c3-40c9-bb21-7b1364870912'::uuid, '2026-01-15'::date, 152.49::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('6788c977-c422-4e07-9f01-d8d468c7214a'::uuid, '2026-01-15'::date, 825.66::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('6843b999-dd54-40c9-ade4-603af40453e8'::uuid, '2026-01-15'::date, 340.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c46f1c2a-4704-4c0d-8675-a519c6967756'::uuid, '2026-01-15'::date, 5417.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('49f569b9-d749-489c-b3a1-f939898cab4d'::uuid, '2026-01-16'::date, 344.26::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('6bbdece6-c608-4d30-adf4-cdfa50c35e18'::uuid, '2026-01-16'::date, 637.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('810d11e9-7583-4e20-a13e-c1e22b889d9e'::uuid, '2026-01-16'::date, 7282.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('384879f5-19e6-46ea-af47-e6c80b584f79'::uuid, '2026-01-17'::date, 1223.26::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('4821fa3e-4c2f-43b9-9263-1979ac41df6d'::uuid, '2026-01-17'::date, 211.90::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('54e3a412-d787-497e-8859-40f00b81911a'::uuid, '2026-01-17'::date, 3038.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('feddc0d7-3394-4376-9eff-372445a0d26c'::uuid, '2026-01-17'::date, 93.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('5f0ee40c-4bb6-489d-b151-d0fd1a334ae4'::uuid, '2026-01-18'::date, 1111.03::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('642ce601-f47c-4582-bbb6-f37ce6f1bd42'::uuid, '2026-01-18'::date, 867.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('bdf2997d-80dd-416f-803c-082b46042c8b'::uuid, '2026-01-18'::date, 8157.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('e237a729-2e96-4ff0-a373-f7df8893a2f5'::uuid, '2026-01-18'::date, 728.45::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('09704464-ff21-417f-a9e7-09db8e4ee554'::uuid, '2026-01-19'::date, 281.25::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('6c068443-f732-453c-bf9b-f89ddc6b8614'::uuid, '2026-01-19'::date, 293.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c91b7ec7-1cea-4101-83f7-eae6836def7e'::uuid, '2026-01-19'::date, 8683.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('43dfd60b-c6ed-4428-a2e2-68ca16bcf130'::uuid, '2026-01-20'::date, 375.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('dcc6b592-4201-4817-b9c0-2076b7a75112'::uuid, '2026-01-20'::date, 1188.84::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('348adb1e-f144-4c28-befe-c67391cdc03a'::uuid, '2026-01-21'::date, 3590.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('5bdde16b-1fe8-4e35-83f0-4e3fe0a031ee'::uuid, '2026-01-21'::date, 348.13::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('810336d0-0d5e-4a8c-986f-d8a03db31bb5'::uuid, '2026-01-21'::date, 922.24::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c3fada5c-52db-4ec9-bbd5-931fb58fa13c'::uuid, '2026-01-21'::date, 1505.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('0a8b73bc-220d-4ebc-8f25-09dac1211025'::uuid, '2026-01-22'::date, 338.80::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('5a5cf70e-0ef5-46b8-898f-0c4488ba37b3'::uuid, '2026-01-22'::date, 1061.41::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c9880a15-6988-4f6d-b7a2-a5fa96e38058'::uuid, '2026-01-22'::date, 5614.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('e36b4c30-e748-4f8f-8e5b-331982a275a0'::uuid, '2026-01-22'::date, 347.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('209c8338-a7be-4aed-8862-175b31faae6b'::uuid, '2026-01-23'::date, 521.50::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('8a2afe28-0d87-4a8d-bf3e-75a858eeec64'::uuid, '2026-01-23'::date, 936.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('9b4196fd-d9a4-4e59-993e-a145d913a208'::uuid, '2026-01-23'::date, 5589.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('cc8e2985-28b3-4d6d-825d-3212330c3b6a'::uuid, '2026-01-23'::date, 911.71::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('0eeb4c1f-fc3f-4326-a29f-326313ffa5ea'::uuid, '2026-01-24'::date, 664.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('9ca52a1e-7283-4382-9787-f24c314b4544'::uuid, '2026-01-24'::date, 204.39::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('a868be76-27cf-4a75-a78e-f06f1764a859'::uuid, '2026-01-24'::date, 963.98::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('a95e410c-6fe8-4ef9-8ddc-adbfe4159073'::uuid, '2026-01-24'::date, 3047.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('11f6e620-9a80-435d-b59a-2564203ad092'::uuid, '2026-01-25'::date, 2070.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('576b0595-c9d1-45a7-81e3-870625ae9461'::uuid, '2026-01-25'::date, 620.44::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('648f8b05-f0b2-4f7b-9edd-2efdbd25e4d3'::uuid, '2026-01-25'::date, 9327.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c2ed67ff-f98d-4b2c-bc1e-3b675c5556f9'::uuid, '2026-01-25'::date, 576.61::numeric(12,2), 'grab_payout', 'pos_cash', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: category-only correction for Grab payout; bank row already source_type=grab_payout but category_code=pos_cash; exact January Grab export payout-date aggregate 2026-01-25 (3 orders); report statement_reclass_january_dry_run_20260707_231000.'),
    ('288a4164-1463-41d5-a9af-7e663f695317'::uuid, '2026-01-27'::date, 334.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('73102b0e-220b-4263-a333-2bcf651d6ef9'::uuid, '2026-01-27'::date, 630.97::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('cc3423f4-cc0e-40a1-ba37-35190ac8cadf'::uuid, '2026-01-27'::date, 3548.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('f4707595-26ac-4065-bd07-b00af7af26e2'::uuid, '2026-01-27'::date, 479.71::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('3196b16e-8ec3-418b-b240-148701c6b7ed'::uuid, '2026-01-28'::date, 908.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('40ff48c0-2d9b-46f6-a05a-ae5439749838'::uuid, '2026-01-28'::date, 5831.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('8cea8dca-3dbb-4391-b137-518863da8d31'::uuid, '2026-01-28'::date, 624.89::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('37570689-cacc-4749-97da-ea6498709b85'::uuid, '2026-01-29'::date, 764.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('6bc5677a-bc8a-4219-852f-2709dbc0ce80'::uuid, '2026-01-29'::date, 3995.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('c89d2c02-3ac2-4585-91e1-6e8ab248782a'::uuid, '2026-01-29'::date, 809.92::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('d89123b0-d082-4b95-8510-2a0923343102'::uuid, '2026-01-29'::date, 207.30::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('008b8ee0-4407-49a7-81da-a7c44509a4bf'::uuid, '2026-01-30'::date, 264.94::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('2b6999c0-d417-4202-b8e9-ed83974d0e5f'::uuid, '2026-01-30'::date, 510.69::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('374f39ce-fced-4f0c-9fda-37255fcfd2f6'::uuid, '2026-01-30'::date, 10997.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('f30b3450-5c55-4619-9030-234e718f4165'::uuid, '2026-01-30'::date, 368.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('5ae5c979-15d3-4c81-8727-a6531114c34d'::uuid, '2026-01-31'::date, 477.28::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed January reclass: Grab bank settlement -> grab_payout; January Grab export payout-date evidence reviewed; report statement_reclass_january_dry_run_20260707_231000.'),
    ('7f839f74-d3df-4d14-ab25-eca5c98dd8e4'::uuid, '2026-01-31'::date, 6978.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('dbe66dc2-9004-472a-a842-a1d0918f2101'::uuid, '2026-01-31'::date, 1548.54::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.'),
    ('f9195d54-6c8b-4d39-8b63-c16c86fddead'::uuid, '2026-01-31'::date, 444.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed January reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_january_dry_run_20260707_231000.');

SELECT 'BEFORE' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _january_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

DO $january_reclass_lock_and_preflight$
DECLARE
    v_locked_count integer; v_target_count integer; v_exact_count integer; v_manual_current_count integer;
    v_target_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*) INTO v_locked_count FROM (
        SELECT b.id FROM public.bank_statement_entries b JOIN _january_statement_reclass_targets t ON t.id = b.id ORDER BY b.id FOR UPDATE
    ) locked_rows;
    IF v_locked_count <> 102 THEN RAISE EXCEPTION 'Locked row count mismatch: expected %, got %', 102, v_locked_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_target_count, v_target_sum FROM _january_statement_reclass_targets;
    IF v_target_count <> 102 THEN RAISE EXCEPTION 'Target count mismatch: expected %, got %', 102, v_target_count; END IF;
    IF v_target_sum <> 197126.35::numeric(12,2) THEN RAISE EXCEPTION 'Target credit sum mismatch: expected %, got %', 197126.35, v_target_sum; END IF;

    SELECT COUNT(*) INTO v_exact_count
    FROM _january_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date AND b.credit=t.expected_credit
      AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status=t.current_match_status AND b.match_status <> 'manual';
    IF v_exact_count <> 102 THEN RAISE EXCEPTION 'Current-state guard mismatch: expected %, got %', 102, v_exact_count; END IF;

    SELECT COUNT(*) INTO v_manual_current_count FROM _january_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id=t.id WHERE b.match_status='manual';
    IF v_manual_current_count <> 0 THEN RAISE EXCEPTION 'Refusing to overwrite currently manual rows: %', v_manual_current_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _january_statement_reclass_targets WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 76 OR v_gateway_sum <> 184079.80::numeric(12,2) THEN RAISE EXCEPTION 'Gateway target bucket mismatch: expected % / %, got % / %', 76, 184079.80, v_gateway_count, v_gateway_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _january_statement_reclass_targets WHERE new_source_type='grab_payout';
    IF v_grab_count <> 26 OR v_grab_sum <> 13046.55::numeric(12,2) THEN RAISE EXCEPTION 'Grab target bucket mismatch: expected % / %, got % / %', 26, 13046.55, v_grab_count, v_grab_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _january_statement_reclass_targets WHERE new_match_status='manual';
    IF v_new_manual_count <> 0 OR v_new_manual_sum <> 0.00::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 0, 0.00, v_new_manual_count, v_new_manual_sum; END IF;
END
$january_reclass_lock_and_preflight$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260707_january AS
SELECT b.* FROM public.bank_statement_entries b JOIN _january_statement_reclass_targets t ON t.id = b.id;
COMMENT ON TABLE audit.bank_statement_reclass_backup_20260707_january IS 'Pre-reclass backup of 102 January 2026 bank_statement_entries rows reviewed on 2026-07-07.';
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_january FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_january FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_january FROM authenticated;

DO $january_reclass_backup_check$
DECLARE v_backup_count integer; v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_backup_count, v_backup_sum FROM audit.bank_statement_reclass_backup_20260707_january;
    IF v_backup_count <> 102 THEN RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 102, v_backup_count; END IF;
    IF v_backup_sum <> 197126.35::numeric(12,2) THEN RAISE EXCEPTION 'Backup credit sum mismatch: expected %, got %', 197126.35, v_backup_sum; END IF;
END
$january_reclass_backup_check$;

CREATE TEMP TABLE _january_statement_reclass_updated (
    id uuid PRIMARY KEY, old_source_type text, old_category_code text, old_match_status text,
    new_source_type text, new_category_code text, new_match_status text, credit numeric(12,2) NOT NULL
) ON COMMIT DROP;

WITH updated AS (
    UPDATE public.bank_statement_entries b
    SET source_type=t.new_source_type,
        category_code=t.new_category_code,
        match_status=t.new_match_status,
        notes=CASE WHEN COALESCE(BTRIM(b.notes),'')='' THEN t.note_append ELSE b.notes || E'\n' || t.note_append END,
        classified_by='codex_reviewed_january_reclass_20260707',
        classified_at=now()
    FROM _january_statement_reclass_targets t
    WHERE b.id=t.id AND b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date
      AND b.credit=t.expected_credit AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code AND b.match_status=t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING b.id, t.current_source_type AS old_source_type, t.current_category_code AS old_category_code,
              t.current_match_status AS old_match_status, b.source_type AS new_source_type,
              b.category_code AS new_category_code, b.match_status AS new_match_status, b.credit
)
INSERT INTO _january_statement_reclass_updated
SELECT id, old_source_type, old_category_code, old_match_status, new_source_type, new_category_code, new_match_status, credit FROM updated;

DO $january_reclass_postupdate$
DECLARE
    v_updated_count integer; v_updated_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_updated_count, v_updated_sum FROM _january_statement_reclass_updated;
    IF v_updated_count <> 102 THEN RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 102, v_updated_count; END IF;
    IF v_updated_sum <> 197126.35::numeric(12,2) THEN RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 197126.35, v_updated_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _january_statement_reclass_updated WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 76 OR v_gateway_sum <> 184079.80::numeric(12,2) THEN RAISE EXCEPTION 'Gateway bucket mismatch: expected % / %, got % / %', 76, 184079.80, v_gateway_count, v_gateway_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _january_statement_reclass_updated WHERE new_source_type='grab_payout';
    IF v_grab_count <> 26 OR v_grab_sum <> 13046.55::numeric(12,2) THEN RAISE EXCEPTION 'Grab bucket mismatch: expected % / %, got % / %', 26, 13046.55, v_grab_count, v_grab_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _january_statement_reclass_updated WHERE new_match_status='manual';
    IF v_new_manual_count <> 0 OR v_new_manual_sum <> 0.00::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 0, 0.00, v_new_manual_count, v_new_manual_sum; END IF;
END
$january_reclass_postupdate$;

SELECT 'AFTER' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _january_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

SELECT 'JANUARY_PNL_AFTER_IN_TRANSACTION' AS phase, direction, COUNT(*) AS row_count, SUM(amount)::numeric(12,2) AS amount_sum
FROM public.v_daybook_pnl
WHERE entry_date >= '2026-01-01'::date AND entry_date < '2026-02-01'::date
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
-- FROM audit.bank_statement_reclass_backup_20260707_january bak
-- WHERE b.id=bak.id;
-- COMMIT;
