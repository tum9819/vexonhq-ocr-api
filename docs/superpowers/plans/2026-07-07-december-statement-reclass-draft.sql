-- 2026-07-07-december-statement-reclass-draft.sql
-- VEXONHQ December 2025 bank_statement_entries reclass DRAFT.
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end.
--
-- Goal: fix December bank-statement settlement classifications for reconciliation
-- accuracy. These rows are cash settlement movements, not new sales.
-- Expected P&L impact: none. The settlement sources are excluded from v_daybook_pnl.
--
-- Source evidence:
-- - Fresh dry-run JSON: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_december_dry_run_20260707_232000\statement_reclass_december_dry_run.json
-- - December evidence folder: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS\2025-12-December
-- - December Grab CSV aggregate by payout date = 10676.64 THB.
-- - Additional cross-month evidence: 2025-12-01 bank Grab 137.16 exactly matches November Grab export payout-date aggregate (1 order).
-- - No exact duplicate bank statement keys were found for December.
--
-- Expected target set:
--   total: 102 rows / 181553.15 THB
--   payment_gateway_payout: 75 rows / 170739.35 THB
--   grab_payout: 27 rows / 10813.80 THB
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

CREATE TEMP TABLE _december_statement_reclass_targets (
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

INSERT INTO _december_statement_reclass_targets (
    id, expected_txn_date, expected_credit, current_source_type, current_category_code,
    current_match_status, new_source_type, new_category_code, new_match_status, note_append
)
VALUES
    ('2f7156bf-aa83-4b0d-8d9f-d149509c484f'::uuid, '2025-12-01'::date, 389.87::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('de8158d4-c0ff-4d2d-9eeb-9f8540344ccf'::uuid, '2025-12-01'::date, 441.47::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('ea18fe97-2d3b-439b-891e-35fd15a8ff62'::uuid, '2025-12-01'::date, 1399.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('ecf94533-1841-4956-9fd2-c3fed1f7f5b8'::uuid, '2025-12-01'::date, 137.16::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; exact payout-date aggregate match to November Grab export on 2025-12-01 (1 order), cross-month settlement; report statement_reclass_december_dry_run_20260707_232000.'),
    ('166cf530-5b46-40fe-b607-4e66142dfd31'::uuid, '2025-12-02'::date, 2000.97::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('35ed93e0-6e31-4064-aaa2-1c58c45f67a9'::uuid, '2025-12-02'::date, 332.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('4bd0cab7-d394-4f3d-85bf-6275a83fb9ee'::uuid, '2025-12-02'::date, 358.41::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('ef8583c3-0799-44b2-82cc-dc0aefddba50'::uuid, '2025-12-02'::date, 2929.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('134ed7a2-5935-40de-b5b4-3df936e88752'::uuid, '2025-12-03'::date, 140.32::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('354e471a-4e08-4dad-9b05-641c624409c3'::uuid, '2025-12-03'::date, 4533.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('78950107-47df-4561-b60f-b61505c2d61a'::uuid, '2025-12-03'::date, 624.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('e45dbc15-8737-48e2-bbfe-cee98c710113'::uuid, '2025-12-03'::date, 1955.54::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('742fb2f0-b3e0-4a90-9f85-86a63ddae3d2'::uuid, '2025-12-04'::date, 4998.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('83091dec-4118-4fa2-bb5a-bc4aaca567af'::uuid, '2025-12-04'::date, 1713.09::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('cbc9164d-c679-4191-874a-85d7e31336fe'::uuid, '2025-12-04'::date, 830.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('ce8be7cf-73c6-4b84-92c9-8ada29bebe9b'::uuid, '2025-12-04'::date, 395.75::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('5efbd70f-77e6-4144-a696-c66e85c66f5b'::uuid, '2025-12-05'::date, 994.03::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('7b5415fc-d5eb-49bd-905d-2c2a8af9b3bc'::uuid, '2025-12-05'::date, 1178.57::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('d99b94ce-e18a-4ae6-8a96-dac469f31e19'::uuid, '2025-12-05'::date, 7643.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('008416f3-df6d-4c2c-9737-ad3dbafbfa40'::uuid, '2025-12-06'::date, 4927.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('1676182a-e1c1-44db-ad49-d88a74b56906'::uuid, '2025-12-06'::date, 216.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('3da734cc-1c22-4d9e-bb48-953241f7a262'::uuid, '2025-12-06'::date, 2449.88::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('b24581c7-3110-460f-b712-1e8c8139aed0'::uuid, '2025-12-06'::date, 221.63::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('fc76209d-8dba-46d0-abce-fcead517602f'::uuid, '2025-12-07'::date, 1188.61::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('fd260e91-c020-43d7-ae75-a4206536e431'::uuid, '2025-12-07'::date, 147.11::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('fe528510-2d89-4745-935b-a762fe6cce90'::uuid, '2025-12-07'::date, 12054.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('55d68ac3-5423-42b3-81f9-8fa4368d9df6'::uuid, '2025-12-09'::date, 2054.52::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('98b3f3d0-8421-4268-8b6a-cc81493a93ab'::uuid, '2025-12-09'::date, 811.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('c15aacce-e1e1-4498-a308-0f129f0db9c7'::uuid, '2025-12-09'::date, 187.98::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('e05cd992-f876-46b5-8d5a-923442c567c9'::uuid, '2025-12-09'::date, 219.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('0610e593-edb3-48fb-9d16-4df6676e3885'::uuid, '2025-12-10'::date, 266.74::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('84177be5-632f-47c4-8c5e-3d42817a4bef'::uuid, '2025-12-10'::date, 385.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('aa5497ea-2588-410d-95a6-a36e90f862bf'::uuid, '2025-12-10'::date, 2059.56::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('f1b1c386-d660-4704-9d3e-49fc18e8a044'::uuid, '2025-12-10'::date, 1316.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('71dce65b-1bff-4f36-aedd-125684f4e44b'::uuid, '2025-12-11'::date, 678.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('9b2f0c81-958e-4e43-b183-5bc5b83d2f81'::uuid, '2025-12-11'::date, 181.29::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('9d792597-833e-4708-90b6-0f912ace9915'::uuid, '2025-12-11'::date, 4825.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('627329ed-e346-4113-bd9b-20ab1d4a10e9'::uuid, '2025-12-12'::date, 5651.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('677587d2-efab-4d3d-8d3e-e9a4bd894f57'::uuid, '2025-12-12'::date, 1126.14::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('d7189bd5-29c4-4065-8958-eddbf376f041'::uuid, '2025-12-12'::date, 262.26::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('0a21c58e-3d48-4702-9bc9-f604e69584dc'::uuid, '2025-12-13'::date, 301.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('146c50b6-d796-4e15-88a4-927be9abf2c6'::uuid, '2025-12-13'::date, 1099.98::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('29570e11-f0c0-4b6d-862c-da4b35e9892e'::uuid, '2025-12-13'::date, 610.19::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('958a6c1f-bfaf-4bf4-becb-91ceccdea5b9'::uuid, '2025-12-13'::date, 3198.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('338436fe-85fc-4550-8fd3-4abac15b635b'::uuid, '2025-12-14'::date, 4674.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('55271429-b442-4993-8e40-8497c8176a09'::uuid, '2025-12-14'::date, 622.41::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('7d8aa9e3-8837-4864-9b93-63f8786f6f28'::uuid, '2025-12-14'::date, 504.60::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('874ba83d-a403-47eb-b797-3c369c79e283'::uuid, '2025-12-14'::date, 678.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('16cb2408-c765-49fd-aac6-7da9d3747d94'::uuid, '2025-12-15'::date, 1983.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('2e6e09ac-04ae-4c0f-b22f-a0bcc1510032'::uuid, '2025-12-15'::date, 1303.63::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('af3221bd-03c1-4fde-aef3-96d35d269bcb'::uuid, '2025-12-15'::date, 623.32::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('d4daa594-c7ef-434d-805d-1e0f964c3db6'::uuid, '2025-12-15'::date, 932.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('3fa90016-d6cb-4c0e-974a-9e603e03f977'::uuid, '2025-12-16'::date, 2936.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('42ad5757-3dfc-456b-be96-d4fc6ee3d79b'::uuid, '2025-12-16'::date, 524.98::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('62636e72-4251-45a2-b4b2-f260f7cc231b'::uuid, '2025-12-16'::date, 108.64::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('803a90ab-4dfe-4e06-9458-19dc5a071918'::uuid, '2025-12-16'::date, 332.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('28f51c38-d8d7-4b45-ab29-b8aa0d9f6229'::uuid, '2025-12-17'::date, 340.88::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('70b87b16-bd39-4163-930a-f8113a44472c'::uuid, '2025-12-17'::date, 5418.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('74d9110c-feb2-4bea-a86b-9e6fd92e124f'::uuid, '2025-12-17'::date, 1325.90::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('5cd66ccc-a353-44c1-9b73-afc9797aafa4'::uuid, '2025-12-18'::date, 742.53::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('950d0656-261f-4e72-8942-35963195e0f2'::uuid, '2025-12-18'::date, 217.93::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('a1a6739a-cacd-475e-8268-a54faef5cd25'::uuid, '2025-12-18'::date, 181.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('b386f473-bb13-46ed-8a76-1a93dd78d76d'::uuid, '2025-12-18'::date, 2831.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('3853830f-fbc9-4708-9630-fe751a1426a1'::uuid, '2025-12-19'::date, 1132.95::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('8e66aef8-9294-4a05-81c9-23e4762edaf9'::uuid, '2025-12-19'::date, 2194.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('a6203a49-d50d-45ea-9d56-5228cbb0b4c4'::uuid, '2025-12-19'::date, 411.48::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('dafaf1ac-7832-4b0d-878e-20a30c5596de'::uuid, '2025-12-19'::date, 92.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('4faae4e4-f87f-4e98-8488-87a1843d44f7'::uuid, '2025-12-20'::date, 242.37::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('895255e7-bcc3-41db-a949-e76cdfd32a71'::uuid, '2025-12-20'::date, 1383.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('8fcab09a-74c5-4f77-a804-29ee73b63699'::uuid, '2025-12-20'::date, 511.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('dbfc7df4-730f-48d6-ad82-2ff250061eeb'::uuid, '2025-12-20'::date, 970.02::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('4620cf16-a280-4146-ac98-4ff0dd6085db'::uuid, '2025-12-21'::date, 128.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('825a980e-3788-4abe-ac41-7ec6f5ec14df'::uuid, '2025-12-21'::date, 365.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('95618baa-effa-4dcc-b6b9-1648328939e6'::uuid, '2025-12-21'::date, 408.96::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('9b122c16-d929-40c5-a6d4-2e6ae2a755e7'::uuid, '2025-12-21'::date, 1131.48::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('593256e5-c253-4d98-a851-139be7667459'::uuid, '2025-12-22'::date, 4638.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('95c9d1db-cee0-4eca-98cc-032e296aad3f'::uuid, '2025-12-23'::date, 325.90::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('ad5ef564-cc6c-4ec4-a0ae-41aaab973027'::uuid, '2025-12-23'::date, 10484.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('d23b290d-b105-4014-ad7e-7f993d142557'::uuid, '2025-12-23'::date, 189.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('597bfe20-2b05-431a-978b-e5b45c87ffc3'::uuid, '2025-12-24'::date, 402.84::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('c4571365-b9b8-4c3e-9b3b-2ed11ba7c37a'::uuid, '2025-12-24'::date, 599.68::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('f872abec-8577-4247-a5e5-c5c2311b00df'::uuid, '2025-12-24'::date, 593.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('12f3fdaa-ec85-4970-9ff7-db98be0675db'::uuid, '2025-12-25'::date, 563.52::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('40f4b40b-2838-4e51-9ed8-7ba915871d80'::uuid, '2025-12-25'::date, 550.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('aaa1474d-e380-4185-bcb3-e849dc6d2356'::uuid, '2025-12-25'::date, 1792.93::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('fc0269a2-5fa0-4963-ae69-6c3a0c1024db'::uuid, '2025-12-25'::date, 1261.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('08207873-2cc4-42b1-8aac-51d58c684b54'::uuid, '2025-12-26'::date, 194.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('0da67b49-a6c5-458f-8f1e-0be370456523'::uuid, '2025-12-26'::date, 1211.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('2180723b-727f-475d-806c-8c1e62b61e69'::uuid, '2025-12-26'::date, 483.10::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('d7ab255a-0751-43eb-bf4c-d5cde8b97fbb'::uuid, '2025-12-26'::date, 641.44::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('629c85bc-1efa-4def-92b0-d27fd1bd74aa'::uuid, '2025-12-27'::date, 1283.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('818dbf82-7182-4c9a-b19b-7f64b9d99672'::uuid, '2025-12-27'::date, 702.68::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('82e7eebb-9a2a-41ef-8509-00ef7b70a348'::uuid, '2025-12-27'::date, 18019.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('c4a56c62-45da-4be2-a836-8bd068ab6d7f'::uuid, '2025-12-27'::date, 444.75::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('33adabdc-3466-46d6-af3f-b706afbb3083'::uuid, '2025-12-28'::date, 730.07::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('83389dc9-b2b2-4bc1-be34-5b8f8523a5f0'::uuid, '2025-12-28'::date, 454.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('b8b788eb-d595-4896-bb9a-c62739ec65ef'::uuid, '2025-12-28'::date, 13551.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('c2aa33c0-da2f-45ec-8166-4a599a5c70cc'::uuid, '2025-12-28'::date, 1157.56::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('283b5e6c-0d93-4940-ab9e-029411fcdbc5'::uuid, '2025-12-29'::date, 551.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('7c279424-546f-4598-824a-ecd2da60fbe5'::uuid, '2025-12-29'::date, 4608.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.'),
    ('afd1f4c9-1bb7-4c54-a7a6-6dde40ce2914'::uuid, '2025-12-29'::date, 675.08::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed December reclass: Grab bank settlement -> grab_payout; December Grab export payout-date evidence reviewed; report statement_reclass_december_dry_run_20260707_232000.'),
    ('c16d8093-cd37-41ea-91aa-14411aec0f94'::uuid, '2025-12-29'::date, 767.45::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed December reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_december_dry_run_20260707_232000.');

SELECT 'BEFORE' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _december_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

DO $december_reclass_lock_and_preflight$
DECLARE
    v_locked_count integer; v_target_count integer; v_exact_count integer; v_manual_current_count integer;
    v_target_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*) INTO v_locked_count FROM (
        SELECT b.id FROM public.bank_statement_entries b JOIN _december_statement_reclass_targets t ON t.id = b.id ORDER BY b.id FOR UPDATE
    ) locked_rows;
    IF v_locked_count <> 102 THEN RAISE EXCEPTION 'Locked row count mismatch: expected %, got %', 102, v_locked_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_target_count, v_target_sum FROM _december_statement_reclass_targets;
    IF v_target_count <> 102 THEN RAISE EXCEPTION 'Target count mismatch: expected %, got %', 102, v_target_count; END IF;
    IF v_target_sum <> 181553.15::numeric(12,2) THEN RAISE EXCEPTION 'Target credit sum mismatch: expected %, got %', 181553.15, v_target_sum; END IF;

    SELECT COUNT(*) INTO v_exact_count
    FROM _december_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date AND b.credit=t.expected_credit
      AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status=t.current_match_status AND b.match_status <> 'manual';
    IF v_exact_count <> 102 THEN RAISE EXCEPTION 'Current-state guard mismatch: expected %, got %', 102, v_exact_count; END IF;

    SELECT COUNT(*) INTO v_manual_current_count FROM _december_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id=t.id WHERE b.match_status='manual';
    IF v_manual_current_count <> 0 THEN RAISE EXCEPTION 'Refusing to overwrite currently manual rows: %', v_manual_current_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _december_statement_reclass_targets WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 75 OR v_gateway_sum <> 170739.35::numeric(12,2) THEN RAISE EXCEPTION 'Gateway target bucket mismatch: expected % / %, got % / %', 75, 170739.35, v_gateway_count, v_gateway_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _december_statement_reclass_targets WHERE new_source_type='grab_payout';
    IF v_grab_count <> 27 OR v_grab_sum <> 10813.80::numeric(12,2) THEN RAISE EXCEPTION 'Grab target bucket mismatch: expected % / %, got % / %', 27, 10813.80, v_grab_count, v_grab_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _december_statement_reclass_targets WHERE new_match_status='manual';
    IF v_new_manual_count <> 0 OR v_new_manual_sum <> 0.00::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 0, 0.00, v_new_manual_count, v_new_manual_sum; END IF;
END
$december_reclass_lock_and_preflight$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260707_december AS
SELECT b.* FROM public.bank_statement_entries b JOIN _december_statement_reclass_targets t ON t.id = b.id;
COMMENT ON TABLE audit.bank_statement_reclass_backup_20260707_december IS 'Pre-reclass backup of 102 December 2025 bank_statement_entries rows reviewed on 2026-07-07.';
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_december FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_december FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_december FROM authenticated;

DO $december_reclass_backup_check$
DECLARE v_backup_count integer; v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_backup_count, v_backup_sum FROM audit.bank_statement_reclass_backup_20260707_december;
    IF v_backup_count <> 102 THEN RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 102, v_backup_count; END IF;
    IF v_backup_sum <> 181553.15::numeric(12,2) THEN RAISE EXCEPTION 'Backup credit sum mismatch: expected %, got %', 181553.15, v_backup_sum; END IF;
END
$december_reclass_backup_check$;

CREATE TEMP TABLE _december_statement_reclass_updated (
    id uuid PRIMARY KEY, old_source_type text, old_category_code text, old_match_status text,
    new_source_type text, new_category_code text, new_match_status text, credit numeric(12,2) NOT NULL
) ON COMMIT DROP;

WITH updated AS (
    UPDATE public.bank_statement_entries b
    SET source_type=t.new_source_type,
        category_code=t.new_category_code,
        match_status=t.new_match_status,
        notes=CASE WHEN COALESCE(BTRIM(b.notes),'')='' THEN t.note_append ELSE b.notes || E'\n' || t.note_append END,
        classified_by='codex_reviewed_december_reclass_20260707',
        classified_at=now()
    FROM _december_statement_reclass_targets t
    WHERE b.id=t.id AND b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date
      AND b.credit=t.expected_credit AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code AND b.match_status=t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING b.id, t.current_source_type AS old_source_type, t.current_category_code AS old_category_code,
              t.current_match_status AS old_match_status, b.source_type AS new_source_type,
              b.category_code AS new_category_code, b.match_status AS new_match_status, b.credit
)
INSERT INTO _december_statement_reclass_updated
SELECT id, old_source_type, old_category_code, old_match_status, new_source_type, new_category_code, new_match_status, credit FROM updated;

DO $december_reclass_postupdate$
DECLARE
    v_updated_count integer; v_updated_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_updated_count, v_updated_sum FROM _december_statement_reclass_updated;
    IF v_updated_count <> 102 THEN RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 102, v_updated_count; END IF;
    IF v_updated_sum <> 181553.15::numeric(12,2) THEN RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 181553.15, v_updated_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _december_statement_reclass_updated WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 75 OR v_gateway_sum <> 170739.35::numeric(12,2) THEN RAISE EXCEPTION 'Gateway bucket mismatch: expected % / %, got % / %', 75, 170739.35, v_gateway_count, v_gateway_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _december_statement_reclass_updated WHERE new_source_type='grab_payout';
    IF v_grab_count <> 27 OR v_grab_sum <> 10813.80::numeric(12,2) THEN RAISE EXCEPTION 'Grab bucket mismatch: expected % / %, got % / %', 27, 10813.80, v_grab_count, v_grab_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _december_statement_reclass_updated WHERE new_match_status='manual';
    IF v_new_manual_count <> 0 OR v_new_manual_sum <> 0.00::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 0, 0.00, v_new_manual_count, v_new_manual_sum; END IF;
END
$december_reclass_postupdate$;

SELECT 'AFTER' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _december_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

SELECT 'DECEMBER_PNL_AFTER_IN_TRANSACTION' AS phase, direction, COUNT(*) AS row_count, SUM(amount)::numeric(12,2) AS amount_sum
FROM public.v_daybook_pnl
WHERE entry_date >= '2025-12-01'::date AND entry_date < '2026-01-01'::date
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
-- FROM audit.bank_statement_reclass_backup_20260707_december bak
-- WHERE b.id=bak.id;
-- COMMIT;
