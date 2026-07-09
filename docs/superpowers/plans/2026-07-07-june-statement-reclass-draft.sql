-- 2026-07-07-june-statement-reclass-draft.sql
-- VEXONHQ June 2026 bank_statement_entries reclass DRAFT.
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end. Replace ROLLBACK with COMMIT only after
-- Claude/Antigravity review + TUM explicit production-DB approval.
--
-- Goal: fix bank-statement classification for reconciliation accuracy.
-- Expected P&L impact: none or minimal; these settlement sources are excluded
-- from P&L. The main beneficiary is platform/payment reconciliation.
--
-- Source evidence:
-- - Dry-run report: C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_20260707_051413\statement_reclass_dry_run_report.xlsx
-- - Dry-run JSON:   C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_20260707_051413\statement_reclass_dry_run.json
-- - POS payment mapping from TUM: Line Man - Rabbit Linepay = LINE MAN sales,
--   QR Scan = storefront QR, K Plus shop = Grab, ?????? = storefront cash.
-- - Statement LINE PAY/???? ???? is settlement/cash movement, not LINE MAN by default.
--
-- Expected target set:
--   total: 102 rows / 175899.24 THB
--   grab_payout: 26 rows / 14180.13 THB
--   payment_gateway_payout: 76 rows / 161719.11 THB
--
-- Safety properties:
-- - Literal reviewed ID list only; no keyword-derived UPDATE.
-- - Guards current source/category/match_status/date/credit/branch before update.
-- - Refuses to touch match_status='manual'.
-- - Creates backup table in non-public audit schema before UPDATE.
-- - In-transaction row-count and credit-sum assertions abort on mismatch.
-- - Appends rationale to notes, including both Grab evidence exceptions
--   (345.79 on 2026-06-07 and 551.31 on 2026-06-30).

BEGIN;

SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';
SET LOCAL idle_in_transaction_session_timeout = '60s';

CREATE TEMP TABLE _june_statement_reclass_targets (
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

INSERT INTO _june_statement_reclass_targets (
    id,
    expected_txn_date,
    expected_credit,
    current_source_type,
    current_category_code,
    current_match_status,
    new_source_type,
    new_category_code,
    new_match_status,
    note_append
)
VALUES
    ('08c29622-b3f6-4922-88d8-6572ea157586'::uuid, '2026-06-01'::date, 4803.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('9682c5c0-ae7d-430b-9098-a6785d5138d7'::uuid, '2026-06-01'::date, 478.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('5f6ef0bb-4a9b-48b9-85c8-cecfd39cd230'::uuid, '2026-06-02'::date, 442.43::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('74363157-a06d-4c00-a898-fb9117e8ded6'::uuid, '2026-06-02'::date, 483.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('b4b79351-3dac-4fd8-bd5d-8b34afd0a96d'::uuid, '2026-06-02'::date, 7232.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('20a0adfc-4811-4a48-b960-f58a499bf3a6'::uuid, '2026-06-03'::date, 540.04::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('90bd032c-f5c6-4896-9d26-2b16d07866a4'::uuid, '2026-06-03'::date, 210.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('b9294501-70d6-4968-8196-de5c328a5fc2'::uuid, '2026-06-03'::date, 2001.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('ded42c2f-2232-4bc2-8152-ef70052e2322'::uuid, '2026-06-03'::date, 2314.99::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('aece2355-a69a-4b59-8828-1b8d8c47f585'::uuid, '2026-06-04'::date, 385.13::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('c53c14e6-de69-410a-8667-76eb1d6166bf'::uuid, '2026-06-04'::date, 718.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('c6959c81-0a72-4988-aefa-35482c4eb2a6'::uuid, '2026-06-04'::date, 989.83::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('ee5c9185-7820-4b4b-89ed-06c90d96fe96'::uuid, '2026-06-04'::date, 4708.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('472e7d93-70d7-408e-bed5-77cbfe253846'::uuid, '2026-06-05'::date, 190.01::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('8f826bf6-a5e7-41d6-8b53-4447556048f9'::uuid, '2026-06-05'::date, 1912.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('b682314d-fbd4-453d-9e70-bf4db2b4405e'::uuid, '2026-06-05'::date, 848.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('e7358fb4-0bfd-49da-adf3-c149564bbd52'::uuid, '2026-06-05'::date, 774.92::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('1612221d-b4ee-4bac-8832-20b4ae775e3e'::uuid, '2026-06-06'::date, 182.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('2b672236-56bb-40e7-80e6-ed5cd76ec433'::uuid, '2026-06-06'::date, 396.31::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('5278938f-6f29-4cec-af03-4b23675358e2'::uuid, '2026-06-06'::date, 576.01::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('93649f26-c1e5-45d5-8426-f644debd59df'::uuid, '2026-06-06'::date, 4501.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('5c3f3753-e36d-4083-91d3-3ef4be74f389'::uuid, '2026-06-07'::date, 2067.47::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('97532877-e3c6-4807-ad05-3c465a0201dc'::uuid, '2026-06-07'::date, 3092.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('9a2cfd81-137c-4832-802a-b918a8d9b012'::uuid, '2026-06-07'::date, 11271.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('c9beb936-8887-45c0-bcec-23356ba9f6f8'::uuid, '2026-06-07'::date, 345.79::numeric(12,2), 'pos_cash_deposit', 'pos_cash', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-07 reviewed June reclass exception: 2026-06-07 345.79 exact payout-date aggregate match to June Grab export (2 orders); historical parser/footer pollution caused pos_cash_deposit; report statement_reclass_20260707_051413.'),
    ('e73a89b1-3906-4c26-ae75-ab61bb1b0328'::uuid, '2026-06-07'::date, 689.62::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('5fa6856c-97b0-48d4-817d-821c9dae6baf'::uuid, '2026-06-08'::date, 897.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('620707f4-4876-42c3-8f7a-1ac0424dd785'::uuid, '2026-06-08'::date, 508.11::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('77db84c5-1ee5-46e5-adf5-3f3722a3e513'::uuid, '2026-06-08'::date, 728.17::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('b59c3533-bed5-42fa-b937-aa0c6ba0f326'::uuid, '2026-06-08'::date, 2773.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('47101bc1-1901-4a1a-bed2-a50755b59d54'::uuid, '2026-06-09'::date, 963.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('adcfbbb0-5ed9-4e1e-b23c-07330a94f954'::uuid, '2026-06-09'::date, 939.53::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('e8f8baf0-ad51-4e58-bba9-b470253e2534'::uuid, '2026-06-09'::date, 1128.55::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('0bdf6a2c-5fa2-4fee-b4a5-513d97db83d6'::uuid, '2026-06-10'::date, 825.09::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('214e6ae7-7941-44f3-810d-55a9733a48f0'::uuid, '2026-06-10'::date, 352.83::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('5d5f7819-54d5-4260-8299-20e4b49bb7d5'::uuid, '2026-06-10'::date, 931.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('5f47b678-bb85-4305-b0ed-b654e21cbd4a'::uuid, '2026-06-10'::date, 2633.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('048b1514-fe75-4e93-a4b8-c53730ea4556'::uuid, '2026-06-11'::date, 458.65::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('952f9b18-4e90-4805-ad0e-70512b987a44'::uuid, '2026-06-11'::date, 509.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('bc82422a-521d-4f2d-889c-ddaff99cae96'::uuid, '2026-06-11'::date, 608.56::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('eff920cf-f957-4e74-b616-51743d17ee01'::uuid, '2026-06-11'::date, 662.76::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('fd5f3ca8-6f02-4446-8c52-6bb818bb3fae'::uuid, '2026-06-11'::date, 803.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('0fdd6545-72ed-4f69-be63-f136a11cae39'::uuid, '2026-06-12'::date, 582.44::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('cfc65303-9652-4e31-b746-449770d50aa4'::uuid, '2026-06-12'::date, 2492.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('7b14024b-df9e-4885-9dae-cf9794859064'::uuid, '2026-06-13'::date, 772.51::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('bf0d6624-0d5f-4182-aa40-4c087cb02996'::uuid, '2026-06-13'::date, 153.45::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('c68fd20b-13d2-4456-859a-125c5b807477'::uuid, '2026-06-13'::date, 132.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('e0c26913-bcca-45b9-9742-30bcd6b2262c'::uuid, '2026-06-13'::date, 6915.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('27ed30d3-8d68-4010-aa69-60aa7f5a9a80'::uuid, '2026-06-14'::date, 2057.43::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('2dd8512c-9bde-481c-b321-cb0815269b7c'::uuid, '2026-06-14'::date, 4838.77::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('36ba1d4c-b673-4250-815c-a102315d5c61'::uuid, '2026-06-14'::date, 287.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('670d26b3-8241-4668-b302-19f0269554e6'::uuid, '2026-06-14'::date, 1084.64::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('b0c7de1c-4c82-4f21-af20-da1bdffc7b1f'::uuid, '2026-06-14'::date, 8543.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('102d371d-9748-45da-8ced-664c02a22188'::uuid, '2026-06-15'::date, 1290.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('3c14311b-c29e-4293-b742-900c213e4651'::uuid, '2026-06-15'::date, 4186.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('ad05ce49-ced0-42c7-9969-6682884b8a29'::uuid, '2026-06-15'::date, 602.67::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('24a14ea6-bcd8-4957-b04f-8a26dcd6fc88'::uuid, '2026-06-16'::date, 566.29::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('48953087-a3b2-48e2-bd10-ba9a9956824f'::uuid, '2026-06-16'::date, 4108.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('9304c4d2-5e39-431c-bf96-06b9d26f5b9b'::uuid, '2026-06-16'::date, 732.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('f01a3f66-8856-434a-a5e0-410cfb59ff6a'::uuid, '2026-06-16'::date, 1085.39::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('14e2c5b1-1c46-462f-a6ca-e2e7697ce75e'::uuid, '2026-06-17'::date, 568.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('1de77b06-5e27-470b-9593-ef061502923a'::uuid, '2026-06-17'::date, 2949.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('d47afc22-fdcd-4fc7-aa16-9de73b6528bd'::uuid, '2026-06-17'::date, 833.09::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('318f05ca-0154-4c7c-b741-6f433bd0a408'::uuid, '2026-06-18'::date, 591.08::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('3c627bd5-85a5-43f9-86ad-3b9cfaa946cc'::uuid, '2026-06-18'::date, 2390.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('425d2378-8b33-418a-bb34-13c97d872b4c'::uuid, '2026-06-18'::date, 571.42::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('a7f62abc-7325-4ee8-b875-9716488aaf99'::uuid, '2026-06-18'::date, 445.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('2a7cf9db-f725-478e-8d46-99f404d7cf12'::uuid, '2026-06-19'::date, 5852.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('7d426069-f158-4215-a0cf-df99b62f8739'::uuid, '2026-06-19'::date, 1030.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('7f663847-8555-44b2-9b13-8158cb127070'::uuid, '2026-06-19'::date, 278.55::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('449d3db7-a205-4f5a-a93a-2f2e97f33ce6'::uuid, '2026-06-20'::date, 567.26::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('5af01210-0fa0-44c1-942a-bb3dd883e131'::uuid, '2026-06-20'::date, 171.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('76d76c3d-0e9b-4d7b-bcb2-209843081179'::uuid, '2026-06-20'::date, 4875.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('f51fa165-f974-4e09-b9c5-2e46d289ece1'::uuid, '2026-06-20'::date, 562.12::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('174542eb-7204-49c7-9a60-e02dafada666'::uuid, '2026-06-21'::date, 1018.75::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('22d63f67-cf7a-4717-ab7b-66a474b78b6d'::uuid, '2026-06-21'::date, 785.20::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('32d9b753-2db2-44a0-87f8-397a36a2ac83'::uuid, '2026-06-21'::date, 10049.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('cf1d22ab-c5ea-4b5b-aea7-458eb1226005'::uuid, '2026-06-21'::date, 888.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('02c0dd37-0feb-4f1c-9dc3-3bd89191996a'::uuid, '2026-06-23'::date, 719.25::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('25587cd2-f21c-40cb-94e8-8c466d1c0e35'::uuid, '2026-06-23'::date, 714.83::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('71e77dbb-ccdb-4c59-96fc-eeebaebe834a'::uuid, '2026-06-23'::date, 8128.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('963051c7-acb3-4114-8902-e34e7411859b'::uuid, '2026-06-23'::date, 214.87::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('99f52290-1de7-4871-9eb8-892d3a4e7ccf'::uuid, '2026-06-24'::date, 2675.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('c45aa769-ed59-4bc5-ba13-91f928eedd13'::uuid, '2026-06-24'::date, 335.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('d2bc2560-9ab5-4882-b3ac-eb17ae65ba05'::uuid, '2026-06-24'::date, 436.36::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('230f7678-fe6f-4161-8924-5e46ae7605ac'::uuid, '2026-06-25'::date, 796.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('65642f3b-ff06-4314-b188-fca6e35a32bc'::uuid, '2026-06-25'::date, 723.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('e1d3e39b-881f-4212-be22-0b2106b1907f'::uuid, '2026-06-25'::date, 498.78::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('21e669f4-80ab-444e-b258-46640e04d822'::uuid, '2026-06-26'::date, 568.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('25517579-b149-4568-b8f5-16db81f2842d'::uuid, '2026-06-26'::date, 670.02::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('2cbf43e2-e58f-49b7-a38f-0966d3955abf'::uuid, '2026-06-26'::date, 2976.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('7bc7c798-f3a3-487e-b47f-465af22a149b'::uuid, '2026-06-26'::date, 1198.86::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('3173e05f-c746-4338-93f7-9c27390f4e07'::uuid, '2026-06-27'::date, 401.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('3f36d7c3-12e0-44bc-8b12-af1880c1eaca'::uuid, '2026-06-27'::date, 710.47::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed June reclass: Grab bank settlement -> grab_payout; report statement_reclass_20260707_051413.'),
    ('46e44fa1-1640-4b16-b287-d3f897b1bf2f'::uuid, '2026-06-27'::date, 4402.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('e150ef95-dfe6-4613-a397-8b98e4d9521e'::uuid, '2026-06-27'::date, 628.18::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('7e6bc08d-cb6a-4ec8-8db3-e87e6176fa50'::uuid, '2026-06-28'::date, 109.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('90d4a90a-dd1a-45a0-96d5-c33246ece300'::uuid, '2026-06-28'::date, 730.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('5d7b22ec-ea91-441b-ad7c-4478c473b6b1'::uuid, '2026-06-30'::date, 435.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('97ad8924-c2d1-42e0-bdeb-c0b35d6971c4'::uuid, '2026-06-30'::date, 3991.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('9f5346bf-bc72-4839-bfef-177c02418aaa'::uuid, '2026-06-30'::date, 561.45::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed June reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_20260707_051413.'),
    ('94eec063-88b7-48de-8d7b-8c66526511a7'::uuid, '2026-06-30'::date, 551.31::numeric(12,2), 'pos_cash_deposit', 'pos_cash', 'auto', 'grab_payout', 'delivery_grab', 'manual', '2026-07-07 reviewed June reclass exception: 2026-06-30 551.31 exact payout-date aggregate match to June Grab export (6 orders); historical parser lost Grab text; report statement_reclass_20260707_051413.');

-- Preflight assertions: reviewed set size/sum and current DB state must match.
DO $june_reclass_preflight$
DECLARE
    v_target_count integer;
    v_target_sum numeric(12,2);
    v_missing_count integer;
    v_mismatch_count integer;
    v_manual_count integer;
BEGIN
    SELECT COUNT(*), COALESCE(SUM(expected_credit), 0)::numeric(12,2)
    INTO v_target_count, v_target_sum
    FROM _june_statement_reclass_targets;

    IF v_target_count <> 102 THEN
        RAISE EXCEPTION 'Target count mismatch: expected %, got %', 102, v_target_count;
    END IF;

    IF v_target_sum <> 175899.24::numeric(12,2) THEN
        RAISE EXCEPTION 'Target sum mismatch: expected %, got %', 175899.24, v_target_sum;
    END IF;

    SELECT COUNT(*)
    INTO v_missing_count
    FROM _june_statement_reclass_targets t
    LEFT JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.id IS NULL;

    IF v_missing_count <> 0 THEN
        RAISE EXCEPTION 'Missing target rows: %', v_missing_count;
    END IF;

    SELECT COUNT(*)
    INTO v_mismatch_count
    FROM _june_statement_reclass_targets t
    JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code <> 'thawi_watthana'
       OR b.txn_date <> t.expected_txn_date
       OR b.txn_date < DATE '2026-06-01'
       OR b.txn_date >= DATE '2026-07-01'
       OR b.credit::numeric(12,2) <> t.expected_credit
       OR b.credit <= 0
       OR b.source_type IS DISTINCT FROM t.current_source_type
       OR b.category_code IS DISTINCT FROM t.current_category_code
       OR b.match_status IS DISTINCT FROM t.current_match_status;

    IF v_mismatch_count <> 0 THEN
        RAISE EXCEPTION 'Current-state guard mismatch rows: %', v_mismatch_count;
    END IF;

    SELECT COUNT(*)
    INTO v_manual_count
    FROM _june_statement_reclass_targets t
    JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.match_status = 'manual';

    IF v_manual_count <> 0 THEN
        RAISE EXCEPTION 'Refusing to update manual rows: %', v_manual_count;
    END IF;
END
$june_reclass_preflight$;

-- Preview before update.
SELECT
    'BEFORE' AS phase,
    b.source_type,
    b.category_code,
    b.match_status,
    COUNT(*) AS row_count,
    SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _june_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

-- Backup table lives outside public to avoid exposing bank rows via public Data API.
CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC, anon, authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260707_june AS
SELECT
    now() AS backup_captured_at,
    current_user AS backup_captured_by,
    '2026-07-07 June statement reclass reviewed target set'::text AS backup_reason,
    t.new_source_type AS planned_source_type,
    t.new_category_code AS planned_category_code,
    t.new_match_status AS planned_match_status,
    b.*
FROM public.bank_statement_entries b
JOIN _june_statement_reclass_targets t ON t.id = b.id;

REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_june FROM PUBLIC, anon, authenticated;
COMMENT ON TABLE audit.bank_statement_reclass_backup_20260707_june IS
    'Rollback backup captured before June 2026 statement reclass. Created by reviewed SQL draft 2026-07-07.';

-- Backup invariant: prove the persisted rollback table captured the same
-- rows and credit total before any UPDATE runs.
DO $june_reclass_backup_check$
DECLARE
    v_backup_count integer;
    v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_backup_count, v_backup_sum
    FROM audit.bank_statement_reclass_backup_20260707_june;

    IF v_backup_count <> 102 THEN
        RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 102, v_backup_count;
    END IF;

    IF v_backup_sum <> 175899.24::numeric(12,2) THEN
        RAISE EXCEPTION 'Backup sum mismatch: expected %, got %', 175899.24, v_backup_sum;
    END IF;
END
$june_reclass_backup_check$;

CREATE TEMP TABLE _june_statement_reclass_updated (
    id uuid PRIMARY KEY,
    credit numeric(12,2) NOT NULL,
    new_source_type text NOT NULL,
    new_category_code text NOT NULL
) ON COMMIT DROP;

WITH updated AS (
    UPDATE public.bank_statement_entries b
    SET source_type = t.new_source_type,
        category_code = t.new_category_code,
        match_status = t.new_match_status,
        notes = CASE
            WHEN NULLIF(b.notes, '') IS NULL THEN t.note_append
            ELSE b.notes || E'
' || t.note_append
        END,
        classified_by = 'codex_reviewed_june_reclass_20260707',
        classified_at = now()
    FROM _june_statement_reclass_targets t
    WHERE b.id = t.id
      AND b.branch_code = 'thawi_watthana'
      AND b.txn_date >= DATE '2026-06-01'
      AND b.txn_date < DATE '2026-07-01'
      AND b.credit::numeric(12,2) = t.expected_credit
      AND b.credit > 0
      AND b.source_type IS NOT DISTINCT FROM t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status IS NOT DISTINCT FROM t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING b.id, b.credit::numeric(12,2) AS credit, b.source_type, b.category_code
)
INSERT INTO _june_statement_reclass_updated (id, credit, new_source_type, new_category_code)
SELECT id, credit, source_type, category_code
FROM updated;

-- Post-update assertions: abort transaction if anything drifted.
DO $june_reclass_postupdate$
DECLARE
    v_updated_count integer;
    v_updated_sum numeric(12,2);
    v_gateway_count integer;
    v_gateway_sum numeric(12,2);
    v_grab_count integer;
    v_grab_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_updated_count, v_updated_sum
    FROM _june_statement_reclass_updated;

    IF v_updated_count <> 102 THEN
        RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 102, v_updated_count;
    END IF;

    IF v_updated_sum <> 175899.24::numeric(12,2) THEN
        RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 175899.24, v_updated_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_gateway_count, v_gateway_sum
    FROM _june_statement_reclass_updated
    WHERE new_source_type = 'payment_gateway_payout';

    IF v_gateway_count <> 76 OR v_gateway_sum <> 161719.11::numeric(12,2) THEN
        RAISE EXCEPTION 'Gateway bucket mismatch: expected % / %, got % / %', 76, 161719.11, v_gateway_count, v_gateway_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_grab_count, v_grab_sum
    FROM _june_statement_reclass_updated
    WHERE new_source_type = 'grab_payout';

    IF v_grab_count <> 26 OR v_grab_sum <> 14180.13::numeric(12,2) THEN
        RAISE EXCEPTION 'Grab bucket mismatch: expected % / %, got % / %', 26, 14180.13, v_grab_count, v_grab_sum;
    END IF;
END
$june_reclass_postupdate$;

-- Preview after update.
SELECT
    'AFTER' AS phase,
    b.source_type,
    b.category_code,
    b.match_status,
    COUNT(*) AS row_count,
    SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _june_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

-- Reconciliation helper preview for June target rows.
SELECT
    b.source_type,
    COUNT(*) AS row_count,
    SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _june_statement_reclass_targets t ON t.id = b.id
WHERE b.source_type IN ('grab_payout', 'payment_gateway_payout')
GROUP BY b.source_type
ORDER BY b.source_type;

-- Leave as ROLLBACK for review/dry-run. For the real run, only after final
-- review + TUM Confirm, replace this ROLLBACK with COMMIT.
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
-- FROM audit.bank_statement_reclass_backup_20260707_june bak
-- WHERE b.id = bak.id;
-- COMMIT;
