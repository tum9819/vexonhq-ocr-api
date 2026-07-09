-- 2026-07-07-april-statement-reclass-draft.sql
-- VEXONHQ April 2026 bank_statement_entries reclass DRAFT.
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end.
--
-- Goal: fix April bank-statement settlement classifications for reconciliation
-- accuracy. These rows are cash settlement movements, not new sales.
-- Expected P&L impact: none. The settlement sources are excluded from v_daybook_pnl.
--
-- Source evidence:
-- - Fresh dry-run JSON: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_april_dry_run_20260707_224608\statement_reclass_april_dry_run.json
-- - April evidence folder: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS\2026-04-April
-- - April Grab CSV aggregate total = 11961.14 THB, matching the 22 Grab candidates.
-- - No exact duplicate bank statement keys were found for April.
--
-- Expected target set:
--   total: 91 rows / 207763.87 THB
--   payment_gateway_payout: 69 rows / 195802.73 THB
--   grab_payout: 22 rows / 11961.14 THB
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

CREATE TEMP TABLE _april_statement_reclass_targets (
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

INSERT INTO _april_statement_reclass_targets (
    id, expected_txn_date, expected_credit, current_source_type, current_category_code,
    current_match_status, new_source_type, new_category_code, new_match_status, note_append
)
VALUES
    ('521b0357-6c74-4884-936c-9ee6519346db'::uuid, '2026-04-01'::date, 13318.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('78a6572d-a822-45cd-a327-f85d1ccf76af'::uuid, '2026-04-01'::date, 1897.58::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('a53af2a6-2900-44e0-a9ac-4219ea5ecae2'::uuid, '2026-04-02'::date, 570.83::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('ee7ea596-cb2e-4240-9f24-338ae7f63104'::uuid, '2026-04-02'::date, 5828.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('f8dbcd6f-9c4d-4151-82a9-bc45f0940cc1'::uuid, '2026-04-02'::date, 696.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('fbb589fc-de95-4db9-85a8-34e3975b1fd7'::uuid, '2026-04-02'::date, 315.22::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('52f941d4-c28f-42aa-8272-1eb875b95b36'::uuid, '2026-04-03'::date, 2011.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('7a1ddf4e-1cad-49f5-a63c-d29bc251dbcc'::uuid, '2026-04-03'::date, 1834.67::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('a96aea13-26d5-43ef-a1f0-9456639cecb1'::uuid, '2026-04-03'::date, 1210.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('4c9abc11-69ab-4ced-8473-6a5de9f14ae0'::uuid, '2026-04-04'::date, 978.30::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('68ed29af-101d-49c1-ad99-7ba59a65e659'::uuid, '2026-04-04'::date, 2193.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('884e3e62-f652-4198-8fd7-a38bd09a3397'::uuid, '2026-04-04'::date, 194.53::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('b847f533-84bc-4f7a-85a0-23f4af662abf'::uuid, '2026-04-04'::date, 6121.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('35f1f0e2-390c-44c2-af9a-b6dd5304b626'::uuid, '2026-04-05'::date, 531.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('54c4c4f8-51d9-437b-8dde-5a783346e4cd'::uuid, '2026-04-05'::date, 395.48::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('8af79109-0d49-4edd-b5b7-7683c145084a'::uuid, '2026-04-05'::date, 23024.20::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('88c8ca65-4c0a-484d-b576-49a78d307044'::uuid, '2026-04-06'::date, 4253.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('0a382e2a-8f3d-4147-b38a-239197c369ec'::uuid, '2026-04-07'::date, 722.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('85709768-a47b-42ee-9196-dddd60e66aca'::uuid, '2026-04-07'::date, 3475.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('a2721ccc-3118-40d3-bd1d-66684cbd549f'::uuid, '2026-04-07'::date, 891.45::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('c2f8aba3-5145-43da-8c8c-7eca737b080d'::uuid, '2026-04-07'::date, 742.63::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('5c5497f5-5414-468c-8bb0-e1105fb6b14e'::uuid, '2026-04-08'::date, 504.26::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('d3f535b2-68c4-43b9-ae78-bea28ab78b3b'::uuid, '2026-04-08'::date, 3964.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('e00e31f3-2054-45d2-88d2-eb23e840fe2b'::uuid, '2026-04-08'::date, 380.00::numeric(12,2), 'lineman_payout', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('617344a0-38ba-40c6-83cf-8f3e4f952100'::uuid, '2026-04-09'::date, 447.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('660395c9-be79-4d1a-91ab-527499d91793'::uuid, '2026-04-09'::date, 378.93::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('749f2966-0d8a-4855-b0df-017a2717530e'::uuid, '2026-04-09'::date, 1892.09::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('e500daa8-6f4e-4085-a884-2b1aa82ec00e'::uuid, '2026-04-09'::date, 518.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('741670b3-6101-464e-97ec-d632fd4f9dc8'::uuid, '2026-04-10'::date, 3377.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('a93e78f1-d001-4dba-8735-6d1f726d1dae'::uuid, '2026-04-10'::date, 748.43::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('aed304b5-6b1a-46c3-a31e-1981834ccac1'::uuid, '2026-04-10'::date, 882.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('bf8ddf2a-3b0b-4bc7-a29b-ff9c489bf176'::uuid, '2026-04-10'::date, 310.83::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('37007068-c94a-4a71-927a-19d2e116b33e'::uuid, '2026-04-11'::date, 1162.44::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('5fb5fb62-b4e2-4828-bbed-84bdbef253ae'::uuid, '2026-04-11'::date, 342.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('cfd033d1-7933-4034-8f52-53e1de3a2492'::uuid, '2026-04-11'::date, 7288.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('4fa30db1-88d0-4e22-9508-f78b3d28e032'::uuid, '2026-04-12'::date, 904.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('6c2ce329-c30c-4f1b-a350-d639bba3873a'::uuid, '2026-04-12'::date, 13714.30::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('8dab4947-11c8-4cce-8b90-a78f76f211e0'::uuid, '2026-04-12'::date, 595.35::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('d48dd02a-0199-4480-82d4-42c8c975cdb7'::uuid, '2026-04-12'::date, 309.97::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('128018e5-80a1-4157-b4ae-78ebe92aa760'::uuid, '2026-04-17'::date, 1180.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('3dd37762-16f9-4ccd-9f13-b90243f6e97e'::uuid, '2026-04-17'::date, 978.11::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('4d638eda-1dbd-49db-985e-5d6a94cf9cae'::uuid, '2026-04-17'::date, 672.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('b1be4d5c-8af0-4d57-9ece-a72992f2e785'::uuid, '2026-04-17'::date, 866.03::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('0faa6834-ab30-4d22-b41a-5adfec40cc59'::uuid, '2026-04-18'::date, 140.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('119ade03-0644-4aa0-9388-1c4a62da6a90'::uuid, '2026-04-18'::date, 4182.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('6992501d-7912-4b46-8f91-51da12e1604b'::uuid, '2026-04-18'::date, 605.76::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('85206caf-79c2-462c-a047-753d4b9f7cb8'::uuid, '2026-04-18'::date, 404.85::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('3acd6725-73e3-4aad-a6ab-c732f3775167'::uuid, '2026-04-19'::date, 640.39::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('a791ccda-de72-406d-9590-eb25aba8685d'::uuid, '2026-04-19'::date, 10845.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('b2a97bc6-b205-46bc-acf7-a59e6913fc12'::uuid, '2026-04-19'::date, 976.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('f53fe553-d13e-4d26-a585-000bd027abc9'::uuid, '2026-04-19'::date, 758.13::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('119f6050-c78a-4837-9d93-a81f4ac97f94'::uuid, '2026-04-20'::date, 423.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('6f5030a1-5c4d-4941-bfc2-5d0c5e0b3eae'::uuid, '2026-04-20'::date, 229.50::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('d7c01298-b6d2-4e0b-bdf7-d01194bfec46'::uuid, '2026-04-20'::date, 687.67::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('f6faa871-68bb-44f2-b9ea-fe957734bc84'::uuid, '2026-04-20'::date, 77.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('5ef0f927-300e-4f93-9f4a-6fb40309c18d'::uuid, '2026-04-21'::date, 998.77::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('7f8edad7-53a2-49e3-a758-bff6860ce0f5'::uuid, '2026-04-21'::date, 1442.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('c0069f4b-d89a-4c43-93b9-cb2851866cdd'::uuid, '2026-04-21'::date, 809.07::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('d5293fcf-d5e9-429d-88e4-8d531147730c'::uuid, '2026-04-21'::date, 3661.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('4efe92ff-c2b1-4105-9c14-b1550556bbd4'::uuid, '2026-04-22'::date, 233.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('ad426d1c-8e8a-43b9-889f-91b551f3e4c8'::uuid, '2026-04-22'::date, 2078.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('c4cb6d82-e47a-41fb-ad95-0005210623d2'::uuid, '2026-04-22'::date, 427.25::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('1dbcc774-dcf1-4e87-8eb6-2d4da66ca639'::uuid, '2026-04-23'::date, 264.87::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('3e54f064-aca4-4542-9dcb-c9da867d15a5'::uuid, '2026-04-23'::date, 801.40::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('3e992770-4e00-4a46-8d83-13ea6e982cd4'::uuid, '2026-04-23'::date, 6801.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('f3c9394c-a929-4d59-be8c-0701f5ad9f2c'::uuid, '2026-04-23'::date, 341.21::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('04a0cf79-4991-4c8d-ab6e-45da3e36e9f2'::uuid, '2026-04-24'::date, 566.18::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('115cc341-95a3-435f-a273-3324eef4337a'::uuid, '2026-04-24'::date, 130.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('3d79322b-506b-4113-8834-a547392981fc'::uuid, '2026-04-24'::date, 280.03::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('f95b16ed-ff9a-4b5d-aa51-10c76310c8e1'::uuid, '2026-04-24'::date, 2941.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('4c7841c7-36d6-4b48-b28d-3ee5e3044e92'::uuid, '2026-04-25'::date, 504.27::numeric(12,2), 'grab_payout', 'pos_cash', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('61d2d65f-99c5-49d9-9986-74c2ad87579d'::uuid, '2026-04-25'::date, 818.07::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('cbf6cc74-55a7-4412-bae1-da3c2e79c43e'::uuid, '2026-04-25'::date, 465.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('eb1e5a13-8809-4f11-a1ee-da11c02225f1'::uuid, '2026-04-25'::date, 1062.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('be9e8845-aac2-4ad0-8055-4ee6d7c3ad36'::uuid, '2026-04-26'::date, 1014.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('db9b0c98-9a47-43e6-aaa7-5d8c99fa0f0a'::uuid, '2026-04-26'::date, 898.85::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('f416f2c4-d408-4e7a-a2f7-5e37be2246c3'::uuid, '2026-04-26'::date, 20506.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('0d1ce358-5109-4cb8-86c5-0ca0e6124419'::uuid, '2026-04-27'::date, 687.08::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('3d917cfd-e13d-49d4-8935-8551063b7f96'::uuid, '2026-04-27'::date, 1160.02::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('6810e4c4-d8c5-4d10-9715-9f179ebfe4a1'::uuid, '2026-04-27'::date, 582.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('e01ba02d-b85a-4d82-bf47-1508ebed2d50'::uuid, '2026-04-27'::date, 3972.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('21a46d11-87e2-49e5-b7c2-3e9b4d7c8af8'::uuid, '2026-04-28'::date, 276.41::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('2573076a-6e34-425d-9014-b1e35105339a'::uuid, '2026-04-28'::date, 797.54::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('308be38d-0e40-4b78-83d9-be56e3bf0e2f'::uuid, '2026-04-28'::date, 8201.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('fc54ae8b-8e27-4500-9ab1-b08a644d7ba8'::uuid, '2026-04-28'::date, 353.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('596990b4-43f5-4ac8-a96e-9328fe907698'::uuid, '2026-04-29'::date, 205.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('a3849348-a499-4870-8cc3-3cd11d08afed'::uuid, '2026-04-29'::date, 9180.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('0b4e0533-ab10-4b34-a422-f0e7a6abd54d'::uuid, '2026-04-30'::date, 234.01::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed April reclass: Grab bank settlement -> grab_payout; April Grab export total matches bank candidate set; report statement_reclass_april_dry_run_20260707_224608.'),
    ('2cb22ae5-cb54-461a-be24-e0afb14ef067'::uuid, '2026-04-30'::date, 668.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('52eeca55-89a0-44c5-92a4-767f6cfc62ea'::uuid, '2026-04-30'::date, 1137.91::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.'),
    ('6460f923-8df7-48fd-93c4-5cfe8e872b6e'::uuid, '2026-04-30'::date, 680.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed April reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_april_dry_run_20260707_224608.');

SELECT 'BEFORE' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _april_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

DO $april_reclass_lock_and_preflight$
DECLARE
    v_locked_count integer; v_target_count integer; v_exact_count integer; v_manual_current_count integer;
    v_target_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2);
BEGIN
    SELECT COUNT(*) INTO v_locked_count FROM (
        SELECT b.id FROM public.bank_statement_entries b JOIN _april_statement_reclass_targets t ON t.id = b.id ORDER BY b.id FOR UPDATE
    ) locked_rows;
    IF v_locked_count <> 91 THEN RAISE EXCEPTION 'Locked row count mismatch: expected %, got %', 91, v_locked_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_target_count, v_target_sum FROM _april_statement_reclass_targets;
    IF v_target_count <> 91 THEN RAISE EXCEPTION 'Target count mismatch: expected %, got %', 91, v_target_count; END IF;
    IF v_target_sum <> 207763.87::numeric(12,2) THEN RAISE EXCEPTION 'Target credit sum mismatch: expected %, got %', 207763.87, v_target_sum; END IF;

    SELECT COUNT(*) INTO v_exact_count
    FROM _april_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date AND b.credit=t.expected_credit
      AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status=t.current_match_status AND b.match_status <> 'manual';
    IF v_exact_count <> 91 THEN RAISE EXCEPTION 'Current-state guard mismatch: expected %, got %', 91, v_exact_count; END IF;

    SELECT COUNT(*) INTO v_manual_current_count FROM _april_statement_reclass_targets t JOIN public.bank_statement_entries b ON b.id=t.id WHERE b.match_status='manual';
    IF v_manual_current_count <> 0 THEN RAISE EXCEPTION 'Refusing to overwrite currently manual rows: %', v_manual_current_count; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _april_statement_reclass_targets WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 69 OR v_gateway_sum <> 195802.73::numeric(12,2) THEN RAISE EXCEPTION 'Gateway target bucket mismatch: expected % / %, got % / %', 69, 195802.73, v_gateway_count, v_gateway_sum; END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _april_statement_reclass_targets WHERE new_source_type='grab_payout';
    IF v_grab_count <> 22 OR v_grab_sum <> 11961.14::numeric(12,2) THEN RAISE EXCEPTION 'Grab target bucket mismatch: expected % / %, got % / %', 22, 11961.14, v_grab_count, v_grab_sum; END IF;
END
$april_reclass_lock_and_preflight$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260707_april AS
SELECT b.* FROM public.bank_statement_entries b JOIN _april_statement_reclass_targets t ON t.id = b.id;
COMMENT ON TABLE audit.bank_statement_reclass_backup_20260707_april IS 'Pre-reclass backup of 91 April 2026 bank_statement_entries rows reviewed on 2026-07-07.';
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_april FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_april FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_april FROM authenticated;

DO $april_reclass_backup_check$
DECLARE v_backup_count integer; v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_backup_count, v_backup_sum FROM audit.bank_statement_reclass_backup_20260707_april;
    IF v_backup_count <> 91 THEN RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 91, v_backup_count; END IF;
    IF v_backup_sum <> 207763.87::numeric(12,2) THEN RAISE EXCEPTION 'Backup credit sum mismatch: expected %, got %', 207763.87, v_backup_sum; END IF;
END
$april_reclass_backup_check$;

CREATE TEMP TABLE _april_statement_reclass_updated (
    id uuid PRIMARY KEY, old_source_type text, old_category_code text, old_match_status text,
    new_source_type text, new_category_code text, new_match_status text, credit numeric(12,2) NOT NULL
) ON COMMIT DROP;

WITH updated AS (
    UPDATE public.bank_statement_entries b
    SET source_type=t.new_source_type,
        category_code=t.new_category_code,
        match_status=t.new_match_status,
        notes=CASE WHEN COALESCE(BTRIM(b.notes),'')='' THEN t.note_append ELSE b.notes || E'
' || t.note_append END,
        classified_by='codex_reviewed_april_reclass_20260707',
        classified_at=now()
    FROM _april_statement_reclass_targets t
    WHERE b.id=t.id AND b.branch_code='thawi_watthana' AND b.txn_date=t.expected_txn_date
      AND b.credit=t.expected_credit AND COALESCE(b.debit,0)=0 AND b.source_type=t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code AND b.match_status=t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING b.id, t.current_source_type AS old_source_type, t.current_category_code AS old_category_code,
              t.current_match_status AS old_match_status, b.source_type AS new_source_type,
              b.category_code AS new_category_code, b.match_status AS new_match_status, b.credit
)
INSERT INTO _april_statement_reclass_updated
SELECT id, old_source_type, old_category_code, old_match_status, new_source_type, new_category_code, new_match_status, credit FROM updated;

DO $april_reclass_postupdate$
DECLARE
    v_updated_count integer; v_updated_sum numeric(12,2); v_gateway_count integer; v_gateway_sum numeric(12,2); v_grab_count integer; v_grab_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_updated_count, v_updated_sum FROM _april_statement_reclass_updated;
    IF v_updated_count <> 91 THEN RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 91, v_updated_count; END IF;
    IF v_updated_sum <> 207763.87::numeric(12,2) THEN RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 207763.87, v_updated_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_gateway_count, v_gateway_sum FROM _april_statement_reclass_updated WHERE new_source_type='payment_gateway_payout';
    IF v_gateway_count <> 69 OR v_gateway_sum <> 195802.73::numeric(12,2) THEN RAISE EXCEPTION 'Gateway bucket mismatch: expected % / %, got % / %', 69, 195802.73, v_gateway_count, v_gateway_sum; END IF;
    SELECT COUNT(*), COALESCE(SUM(credit),0)::numeric(12,2) INTO v_grab_count, v_grab_sum FROM _april_statement_reclass_updated WHERE new_source_type='grab_payout';
    IF v_grab_count <> 22 OR v_grab_sum <> 11961.14::numeric(12,2) THEN RAISE EXCEPTION 'Grab bucket mismatch: expected % / %, got % / %', 22, 11961.14, v_grab_count, v_grab_sum; END IF;
END
$april_reclass_postupdate$;

SELECT 'AFTER' AS phase, b.source_type, b.category_code, b.match_status,
       COUNT(*) AS row_count, SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _april_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

SELECT 'APRIL_PNL_AFTER_IN_TRANSACTION' AS phase, direction, COUNT(*) AS row_count, SUM(amount)::numeric(12,2) AS amount_sum
FROM public.v_daybook_pnl
WHERE entry_date >= '2026-04-01'::date AND entry_date < '2026-05-01'::date
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
-- FROM audit.bank_statement_reclass_backup_20260707_april bak
-- WHERE b.id=bak.id;
-- COMMIT;
