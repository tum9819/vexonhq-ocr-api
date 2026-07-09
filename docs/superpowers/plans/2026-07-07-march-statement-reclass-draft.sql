-- 2026-07-07-march-statement-reclass-draft.sql
-- VEXONHQ March 2026 bank_statement_entries reclass DRAFT.
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end.
--
-- Goal: fix March bank-statement settlement classifications for reconciliation
-- accuracy. These rows are cash settlement movements, not new sales.
-- Expected P&L impact: none. The settlement sources are excluded from v_daybook_pnl.
--
-- Source evidence:
-- - Fresh dry-run JSON: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_march_dry_run_20260707_225057\statement_reclass_march_dry_run.json
-- - March evidence folder: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS\2026-03-March
-- - March Grab CSV aggregate by payout date = 14696.56 THB.
-- - Additional cross-month evidence: 2026-03-01 bank Grab 735.80 exactly matches February Grab export payout-date aggregate (4 orders).
-- - Manual parser-miss evidence: 2026-03-31 bank row 588.18 exactly matches March Grab export payout-date aggregate (4 orders), but the statement description was generic and dry-run missed it.
-- - No exact duplicate bank statement keys were found for March.
--
-- Expected target set:
--   total: 105 rows / 212939.86 THB
--   payment_gateway_payout: 78 rows / 197507.50 THB
--   grab_payout: 27 rows / 15432.36 THB
--   manual exceptions: 1 rows / 588.18 THB
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

CREATE TEMP TABLE _march_statement_reclass_targets (
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

INSERT INTO _march_statement_reclass_targets (
    id, expected_txn_date, expected_credit, current_source_type, current_category_code,
    current_match_status, new_source_type, new_category_code, new_match_status, note_append
)
VALUES
    ('1da5bc93-88e0-45d7-be8c-264b9b9cbb93'::uuid, '2026-03-01'::date, 735.80::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; exact payout-date aggregate match to February Grab export on 2026-03-01 (4 orders), cross-month settlement; report statement_reclass_march_dry_run_20260707_225057.'),
    ('2dbfe840-3431-40ef-8bd2-85870ed4518a'::uuid, '2026-03-01'::date, 1220.82::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('8b921f09-b990-43e4-b0b0-2c35126ff05f'::uuid, '2026-03-01'::date, 3205.87::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('dc17dc7f-c9db-4e3b-b84e-aa98149f56ce'::uuid, '2026-03-01'::date, 23923.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('592a1610-0be4-4d46-bdfc-a7d025f9a8e9'::uuid, '2026-03-03'::date, 645.88::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('6bdd554f-757a-4543-b2a5-ad8d82f907f8'::uuid, '2026-03-03'::date, 1189.27::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('6d6694a2-6393-45a3-8c5a-1f461b3badab'::uuid, '2026-03-03'::date, 5719.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('c22539c1-6911-4f9c-93db-6c29239be8fe'::uuid, '2026-03-03'::date, 561.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('21b8d163-f1ef-4347-91d7-5f55f302f783'::uuid, '2026-03-04'::date, 641.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('70a4dc2e-4445-4f6f-b757-b4bd3e019c87'::uuid, '2026-03-04'::date, 740.96::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('7ce137b5-f295-40cd-b86f-6d66d67fa0e6'::uuid, '2026-03-04'::date, 630.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('f4254925-b52a-40ff-843e-90c386037d68'::uuid, '2026-03-04'::date, 104.05::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('9e26154b-06bf-4efa-af69-172386310708'::uuid, '2026-03-05'::date, 190.12::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('b46f4fa0-0fa8-42c6-9d5b-d9eecaa604c9'::uuid, '2026-03-05'::date, 3295.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('1cdfea9d-d367-4ca6-8a64-4c99a65006f8'::uuid, '2026-03-06'::date, 500.46::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('3a7b8c62-2819-4ed4-9e90-c71fc402ecec'::uuid, '2026-03-06'::date, 398.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('47975e7c-7cb7-4f20-9667-e4f0b5899a6b'::uuid, '2026-03-06'::date, 1693.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('14d2ccad-682c-4f5b-9d69-6f2327bcbf8d'::uuid, '2026-03-07'::date, 804.93::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('688f3a8e-f401-4488-af7c-f17b9aff87b9'::uuid, '2026-03-07'::date, 6659.00::numeric(12,2), 'lineman_payout', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('a28f3737-d9ed-491c-ac50-aacb00708ebf'::uuid, '2026-03-07'::date, 561.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('fde33921-5cf0-41c7-888f-2e3aa2c85714'::uuid, '2026-03-07'::date, 2107.98::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('27517a84-f4e3-4576-8538-bd17a921ac98'::uuid, '2026-03-08'::date, 1288.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('59258dff-c9d7-4a27-8720-e4eac7524d7a'::uuid, '2026-03-08'::date, 529.68::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('5a841954-c64c-404a-9df3-a2c4a51304f4'::uuid, '2026-03-08'::date, 17744.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('73d53bab-2bb8-4310-a3ef-1b8d0e228122'::uuid, '2026-03-08'::date, 1059.17::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('1cd13bbd-5def-412c-a130-dfc55bfea3b2'::uuid, '2026-03-09'::date, 3204.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('51e0dcb3-0aba-4383-b762-87a2ae39249d'::uuid, '2026-03-09'::date, 676.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('59a1a5ff-6e74-42f1-a76b-a2f8c19cfaca'::uuid, '2026-03-09'::date, 1058.88::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('aebeb9ed-9703-4363-a8fd-74e393f0cc3a'::uuid, '2026-03-09'::date, 705.76::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('56a67d92-1149-4ac6-a7d5-5e5e5cbb44ed'::uuid, '2026-03-10'::date, 432.66::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('8057784f-1bcb-4d51-87fe-9de04f9ace79'::uuid, '2026-03-10'::date, 631.19::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('d16b97bf-eb1d-4a33-b06d-68cca8f02302'::uuid, '2026-03-10'::date, 379.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('0e586230-0cfc-4b07-982c-c8bc0ba8d7c0'::uuid, '2026-03-11'::date, 3907.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('c7bcc41f-89a3-4099-99a5-e7e489421331'::uuid, '2026-03-11'::date, 1474.82::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('e100b3a9-8edd-4f81-924e-deaab0c46dc7'::uuid, '2026-03-11'::date, 828.86::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('f0fd8cd4-0b0c-4763-abd4-81872a955c99'::uuid, '2026-03-11'::date, 877.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('2a26f96c-14ff-4811-a8bb-2fc129605d93'::uuid, '2026-03-12'::date, 497.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('4891a077-a9b6-4d68-bca9-1c31e44a8f86'::uuid, '2026-03-12'::date, 1600.38::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('5b7c65c8-5369-408b-882d-50e7dcf3e201'::uuid, '2026-03-12'::date, 1020.07::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('ec7c9a28-2b43-4e0a-9d60-27edacce2a40'::uuid, '2026-03-12'::date, 303.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('dc59536e-ed36-4003-9321-3daf3330674f'::uuid, '2026-03-13'::date, 747.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('ed2c92e4-55e8-4722-9463-4ab10b89b0e9'::uuid, '2026-03-13'::date, 1190.56::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('edc18c1a-b38c-463f-85b9-7c9d14f36ed9'::uuid, '2026-03-13'::date, 892.97::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('fe36ac51-05c5-4e4e-a48e-aaaf42f40783'::uuid, '2026-03-13'::date, 2184.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('73016b14-806b-425e-89c7-7b8ed945f613'::uuid, '2026-03-14'::date, 7861.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('74286d30-f6cb-48f1-8a59-3feaf5b95b64'::uuid, '2026-03-14'::date, 602.36::numeric(12,2), 'lineman_payout', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('b137eeb0-13e9-4839-91a2-9e3bfb83596e'::uuid, '2026-03-14'::date, 2459.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('e924885d-852e-4fa5-9f07-9e47d296a04d'::uuid, '2026-03-14'::date, 591.46::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('04ee0f22-bc38-4840-a1f8-5d467f472ff8'::uuid, '2026-03-15'::date, 17394.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('78c6fb22-df06-48b5-9dee-8c5e6f9cce90'::uuid, '2026-03-15'::date, 651.50::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('8003c71a-5b5e-4b55-89bc-606c0567d0a9'::uuid, '2026-03-15'::date, 646.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('4127fbd9-7896-4a04-9f2f-562e3d5985cb'::uuid, '2026-03-17'::date, 1366.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('4d63f0bf-49d4-4d94-aeae-cb7a959ba3ee'::uuid, '2026-03-17'::date, 317.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('aaf3b107-fe87-4b6d-afc3-f6289c66c4d8'::uuid, '2026-03-17'::date, 776.56::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('b7247fa4-a233-457d-ac33-d80d5a6caff1'::uuid, '2026-03-17'::date, 1238.86::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('0b8a5e3b-7487-4086-b6bd-1d03cdbe84d4'::uuid, '2026-03-18'::date, 376.60::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('39eec195-aa7e-4655-83a6-2849cdf8c155'::uuid, '2026-03-18'::date, 655.84::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('a5767f63-270a-41d9-849c-e70b78ec9e63'::uuid, '2026-03-18'::date, 279.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('afcdd3b4-e7b7-4caf-bee7-6adb685bf63f'::uuid, '2026-03-18'::date, 1083.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('21314cda-6864-4e09-843c-b57e8a2283d1'::uuid, '2026-03-19'::date, 797.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('2dee350b-3123-4022-b0e4-cb82a2e0e8ad'::uuid, '2026-03-19'::date, 1271.50::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('a0db6bcb-f3bd-4cad-861a-605705aa3910'::uuid, '2026-03-19'::date, 219.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('ef7bbd1b-28c1-40bd-950c-68b440bcf7f3'::uuid, '2026-03-19'::date, 232.22::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('242f4a2b-9dd8-48a9-b493-9483e5ebab73'::uuid, '2026-03-20'::date, 388.83::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('4198590c-ff75-4f1e-a3f3-240a218b4507'::uuid, '2026-03-20'::date, 1919.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('e370a170-ec08-446d-bf61-a5fcc0d66f79'::uuid, '2026-03-20'::date, 2151.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('049baea3-8429-40ed-89d3-3cfd7642d1e7'::uuid, '2026-03-21'::date, 888.71::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('73768c23-c0f0-4ea4-8445-70bea61f1b83'::uuid, '2026-03-21'::date, 3137.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('7a7ca1e8-ac1d-4acd-9855-8f11a3386746'::uuid, '2026-03-21'::date, 1230.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('074af387-54e6-44f1-8a9b-b3dbd3fb26f2'::uuid, '2026-03-22'::date, 18174.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('276e9f39-ea7e-4c35-a5f8-b1e0b5f225a6'::uuid, '2026-03-22'::date, 144.23::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('590dfed0-4069-4f0b-b8f1-0ba8d5707a01'::uuid, '2026-03-22'::date, 834.00::numeric(12,2), 'lineman_payout', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('5ee59321-8562-4539-a943-7871f12b71f7'::uuid, '2026-03-22'::date, 1083.40::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('241832d5-181d-4001-9188-4e5d6e2d011c'::uuid, '2026-03-23'::date, 386.41::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('80087b93-cc74-4b6b-9725-aafbb181a957'::uuid, '2026-03-23'::date, 672.87::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('8d885eda-c97f-4b57-bb4f-686c789fe2f9'::uuid, '2026-03-23'::date, 1921.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('1b3cc184-507b-4d94-b137-60789ef7fe22'::uuid, '2026-03-24'::date, 753.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('56505ce3-5a43-4cc7-9710-92414c12e896'::uuid, '2026-03-24'::date, 1047.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('9ad36e7b-f007-48a7-87bb-4a84ce474870'::uuid, '2026-03-24'::date, 617.37::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('3ee63000-720c-4faa-8b66-322a26ee5e48'::uuid, '2026-03-25'::date, 994.94::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('678f97a2-5af8-49bf-95b7-585de669c933'::uuid, '2026-03-25'::date, 757.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('b6c2a199-1f0b-4360-b27f-70aca775283f'::uuid, '2026-03-25'::date, 577.20::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('cce43f6d-9783-4d34-b31e-05ff6313d5c9'::uuid, '2026-03-25'::date, 2029.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('27865348-4503-48ed-9c10-a6f6423580c8'::uuid, '2026-03-26'::date, 772.25::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('4c0bbaac-b641-47b8-8cd0-a697227f2cb8'::uuid, '2026-03-26'::date, 563.57::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('7dc2d992-b679-4df4-a4da-4cf4e356a917'::uuid, '2026-03-26'::date, 971.12::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('c0e31ad7-cf8e-46b8-bcca-5e86f9c37a97'::uuid, '2026-03-26'::date, 138.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('d46f4bb5-8153-46c5-b879-996aa0db4877'::uuid, '2026-03-26'::date, 3270.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('1e3c9160-4c7f-4fc7-b6ad-1e854763d4e0'::uuid, '2026-03-27'::date, 547.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('5fe6ee21-91c5-4512-843f-4a0fd8532fd0'::uuid, '2026-03-27'::date, 1044.71::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('bb08a77e-17f4-4bb7-978d-4304e1b79d1d'::uuid, '2026-03-27'::date, 3104.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('be464cd9-4b16-40e5-8e21-be83792a669a'::uuid, '2026-03-27'::date, 640.54::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('0fe56d2d-23d2-47b7-a244-48284f0ab4c8'::uuid, '2026-03-28'::date, 878.20::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('3fcaaed5-503b-4534-a5e1-16c862364a17'::uuid, '2026-03-28'::date, 3263.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('d504c430-cc79-4760-a84c-50872337eabb'::uuid, '2026-03-28'::date, 1039.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('f7f103f2-8db4-4060-8ead-d19452bf4579'::uuid, '2026-03-28'::date, 78.76::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('3a5252f3-e02a-44b0-adb0-16fe596c5c43'::uuid, '2026-03-29'::date, 12323.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('ab28bd4c-3ccf-4ab0-88d0-63234d955ea8'::uuid, '2026-03-29'::date, 817.05::numeric(12,2), 'lineman_payout', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('c5d3d75c-b361-4252-9503-043734ec874c'::uuid, '2026-03-29'::date, 324.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('d0625243-cc97-485a-adde-a7b8709dbca2'::uuid, '2026-03-29'::date, 510.66::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('4f068742-99cd-4488-a4be-d8bb1cd840f0'::uuid, '2026-03-30'::date, 674.90::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('9719ffb7-9fe9-4943-9f2d-14d9210f7459'::uuid, '2026-03-30'::date, 841.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('e6fdb939-a2ca-42e9-b119-803c8a08cbf1'::uuid, '2026-03-30'::date, 2720.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed March reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_march_dry_run_20260707_225057.'),
    ('f088c290-5262-4e33-a534-7cff6618479c'::uuid, '2026-03-30'::date, 549.92::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed March reclass: Grab bank settlement -> grab_payout; March Grab export payout-date evidence reviewed; report statement_reclass_march_dry_run_20260707_225057.'),
    ('a7de4b6b-8d07-4e98-b12c-a3e661e773a5'::uuid, '2026-03-31'::date, 588.18::numeric(12,2), 'bank_statement', NULL, 'needs_review', 'grab_payout', 'delivery_grab', 'manual', '2026-07-07 reviewed March reclass: manual Grab bank settlement -> grab_payout; statement parser produced generic transfer text, but amount/date exactly match March Grab export payout-date aggregate 2026-03-31 (4 orders); report statement_reclass_march_dry_run_20260707_225057.');

SELECT 'BEFORE' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _march_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

DO $march_reclass_lock_and_preflight$
DECLARE
    v_locked_count integer; v_target_count integer; v_exact_count integer; v_manual_current_count integer;
    v_target_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*) INTO v_locked_count FROM (
        SELECT b.id FROM public.bank_statement_entries b JOIN _march_statement_reclass_targets t ON t.id = b.id ORDER BY b.id FOR UPDATE
    ) locked_rows;
    IF v_locked_count <> 105 THEN RAISE EXCEPTION 'Locked row count mismatch: expected %, got %', 105, v_locked_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_target_count, v_target_sum FROM _march_statement_reclass_targets;
    IF v_target_count <> 105 THEN RAISE EXCEPTION 'Target count mismatch: expected %, got %', 105, v_target_count; END IF;
    IF v_target_sum <> 212939.86::numeric(12,2) THEN RAISE EXCEPTION 'Target credit sum mismatch: expected %, got %', 212939.86, v_target_sum; END IF;

    SELECT COUNT(*) INTO v_exact_count
    FROM _march_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date AND b.credit=t.expected_credit
      AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status=t.current_match_status AND b.match_status <> 'manual';
    IF v_exact_count <> 105 THEN RAISE EXCEPTION 'Current-state guard mismatch: expected %, got %', 105, v_exact_count; END IF;

    SELECT COUNT(*) INTO v_manual_current_count FROM _march_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id=t.id WHERE b.match_status='manual';
    IF v_manual_current_count <> 0 THEN RAISE EXCEPTION 'Refusing to overwrite currently manual rows: %', v_manual_current_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _march_statement_reclass_targets WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 78 OR v_gateway_sum <> 197507.50::numeric(12,2) THEN RAISE EXCEPTION 'Gateway target bucket mismatch: expected % / %, got % / %', 78, 197507.50, v_gateway_count, v_gateway_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _march_statement_reclass_targets WHERE new_source_type='grab_payout';
    IF v_grab_count <> 27 OR v_grab_sum <> 15432.36::numeric(12,2) THEN RAISE EXCEPTION 'Grab target bucket mismatch: expected % / %, got % / %', 27, 15432.36, v_grab_count, v_grab_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _march_statement_reclass_targets WHERE new_match_status='manual';
    IF v_new_manual_count <> 1 OR v_new_manual_sum <> 588.18::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 1, 588.18, v_new_manual_count, v_new_manual_sum; END IF;
END
$march_reclass_lock_and_preflight$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260707_march AS
SELECT b.* FROM public.bank_statement_entries b JOIN _march_statement_reclass_targets t ON t.id = b.id;
COMMENT ON TABLE audit.bank_statement_reclass_backup_20260707_march IS 'Pre-reclass backup of 105 March 2026 bank_statement_entries rows reviewed on 2026-07-07.';
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_march FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_march FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_march FROM authenticated;

DO $march_reclass_backup_check$
DECLARE v_backup_count integer; v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_backup_count, v_backup_sum FROM audit.bank_statement_reclass_backup_20260707_march;
    IF v_backup_count <> 105 THEN RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 105, v_backup_count; END IF;
    IF v_backup_sum <> 212939.86::numeric(12,2) THEN RAISE EXCEPTION 'Backup credit sum mismatch: expected %, got %', 212939.86, v_backup_sum; END IF;
END
$march_reclass_backup_check$;

CREATE TEMP TABLE _march_statement_reclass_updated (
    id uuid PRIMARY KEY, old_source_type text, old_category_code text, old_match_status text,
    new_source_type text, new_category_code text, new_match_status text, credit numeric(12,2) NOT NULL
) ON COMMIT DROP;

WITH updated AS (
    UPDATE public.bank_statement_entries b
    SET source_type=t.new_source_type,
        category_code=t.new_category_code,
        match_status=t.new_match_status,
        notes=CASE WHEN COALESCE(BTRIM(b.notes),'')='' THEN t.note_append ELSE b.notes || E'\n' || t.note_append END,
        classified_by='codex_reviewed_march_reclass_20260707',
        classified_at=now()
    FROM _march_statement_reclass_targets t
    WHERE b.id=t.id AND b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date
      AND b.credit=t.expected_credit AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code AND b.match_status=t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING b.id, t.current_source_type AS old_source_type, t.current_category_code AS old_category_code,
              t.current_match_status AS old_match_status, b.source_type AS new_source_type,
              b.category_code AS new_category_code, b.match_status AS new_match_status, b.credit
)
INSERT INTO _march_statement_reclass_updated
SELECT id, old_source_type, old_category_code, old_match_status, new_source_type, new_category_code, new_match_status, credit FROM updated;

DO $march_reclass_postupdate$
DECLARE
    v_updated_count integer; v_updated_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2); v_new_manual_count integer; v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_updated_count, v_updated_sum FROM _march_statement_reclass_updated;
    IF v_updated_count <> 105 THEN RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 105, v_updated_count; END IF;
    IF v_updated_sum <> 212939.86::numeric(12,2) THEN RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 212939.86, v_updated_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _march_statement_reclass_updated WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 78 OR v_gateway_sum <> 197507.50::numeric(12,2) THEN RAISE EXCEPTION 'Gateway bucket mismatch: expected % / %, got % / %', 78, 197507.50, v_gateway_count, v_gateway_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _march_statement_reclass_updated WHERE new_source_type='grab_payout';
    IF v_grab_count <> 27 OR v_grab_sum <> 15432.36::numeric(12,2) THEN RAISE EXCEPTION 'Grab bucket mismatch: expected % / %, got % / %', 27, 15432.36, v_grab_count, v_grab_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_new_manual_count, v_new_manual_sum FROM _march_statement_reclass_updated WHERE new_match_status='manual';
    IF v_new_manual_count <> 1 OR v_new_manual_sum <> 588.18::numeric(12,2) THEN RAISE EXCEPTION 'New-manual bucket mismatch: expected % / %, got % / %', 1, 588.18, v_new_manual_count, v_new_manual_sum; END IF;
END
$march_reclass_postupdate$;

SELECT 'AFTER' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _march_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

SELECT 'MARCH_PNL_AFTER_IN_TRANSACTION' AS phase, direction, COUNT(*) AS row_count, SUM(amount)::numeric(12,2) AS amount_sum
FROM public.v_daybook_pnl
WHERE entry_date >= '2026-03-01'::date AND entry_date < '2026-04-01'::date
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
-- FROM audit.bank_statement_reclass_backup_20260707_march bak
-- WHERE b.id=bak.id;
-- COMMIT;
