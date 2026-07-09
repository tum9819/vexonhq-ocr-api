-- 2026-07-07-may-statement-reclass-draft.sql
-- VEXONHQ May 2026 bank_statement_entries reclass DRAFT.
-- REVIEW ONLY. DO NOT RUN without TUM final Confirm.
-- This file is intentionally under docs/superpowers/plans, NOT migrations.
-- It defaults to ROLLBACK at the end. Replace ROLLBACK with COMMIT only after
-- Claude/Antigravity review + TUM explicit production-DB approval.
--
-- Prerequisite already completed: May duplicate cleanup COMMIT.
-- Do not run this before confirming duplicate cleanup remains committed.
--
-- Goal: fix May bank-statement settlement classifications for reconciliation
-- accuracy. These rows are cash settlement movements, not new sales.
-- Expected P&L impact: none. The settlement sources are excluded from
-- v_daybook_pnl; May P&L should remain income 325695.51 / expense 275226.22.
--
-- Source evidence:
-- - Fresh dry-run JSON after May duplicate cleanup:
--   C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\statement_reclass_may_fresh_after_cleanup_20260707_222025\statement_reclass_may_fresh_after_cleanup.json
-- - May evidence folder:
--   C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS\2026-05-May
-- - Manual Grab exception evidence: May Grab CSV aggregate by transfer date
--   2026-05-30 = 513.32 THB / 4 rows, matching bank row a13569a8...
--
-- Expected target set:
--   total: 110 rows / 234703.16 THB
--   payment_gateway_payout: 84 rows / 221034.91 THB
--   grab_payout: 26 rows / 13668.25 THB
--   manual exceptions: 1 rows / 513.32 THB
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

CREATE TEMP TABLE _may_statement_reclass_targets (
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

INSERT INTO _may_statement_reclass_targets (
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
    ('34593c22-2e04-4912-bcc2-deb5135cecd5'::uuid, '2026-05-01'::date, 10984.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('35483c13-0ba0-447f-96d2-7a352e5db6ff'::uuid, '2026-05-01'::date, 946.99::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('61be4105-3698-4f89-b374-5fd498996e9a'::uuid, '2026-05-01'::date, 250.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('124a44de-4884-42be-b76b-a6e7ce7a6c66'::uuid, '2026-05-02'::date, 682.44::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('1a490f32-a6c4-41dd-bde5-488143256852'::uuid, '2026-05-02'::date, 4700.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('5fa54718-1dfd-4cf1-a12b-8c70bd19aa61'::uuid, '2026-05-02'::date, 373.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('9c3262ed-d6bb-4c01-ada0-c759eb03f481'::uuid, '2026-05-02'::date, 1497.61::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('136c9b33-0989-48a7-8011-5857fd8e86da'::uuid, '2026-05-03'::date, 833.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('4ea2eb2a-6a13-4133-9dcb-c431d2ff6b58'::uuid, '2026-05-03'::date, 15125.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('6961aea4-b689-4ad4-8d71-1bf7ebd6aea4'::uuid, '2026-05-03'::date, 981.51::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('b6397d92-d4e2-43f8-a946-2295a6716839'::uuid, '2026-05-03'::date, 192.91::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('53c88881-d794-408e-b802-d2518fc9b744'::uuid, '2026-05-04'::date, 498.54::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('22703e38-771b-4d0a-86ae-5cbc1cdba151'::uuid, '2026-05-05'::date, 720.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('69a18a14-a735-481c-9615-9aee7c305f6c'::uuid, '2026-05-05'::date, 1005.13::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('a0f657a4-d19e-4960-a24e-bf21ce0c3fa3'::uuid, '2026-05-05'::date, 505.56::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('bcaec84f-134a-4132-bf42-dcb01ac895a1'::uuid, '2026-05-05'::date, 545.46::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('e918cd48-7515-4acb-a1e1-7d763a47b06d'::uuid, '2026-05-05'::date, 2801.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('87db0963-98b4-41c4-ae11-cffe3ba18bc4'::uuid, '2026-05-06'::date, 2056.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('c2fad0d8-91df-4cb9-8ebe-49004d41e48b'::uuid, '2026-05-06'::date, 183.49::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('cc2f33a7-24e3-4e36-98bb-f17c609944eb'::uuid, '2026-05-06'::date, 1207.02::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('e1588f23-a6f5-4bbb-8c35-435146755838'::uuid, '2026-05-06'::date, 313.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('00e384d9-9b0e-4253-b500-d2b19519f598'::uuid, '2026-05-07'::date, 279.88::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('278ae5b9-2100-4b8f-923a-d58b2e152f6c'::uuid, '2026-05-07'::date, 716.74::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('96e19ca5-822f-4862-8b5c-369c572766c5'::uuid, '2026-05-07'::date, 67.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('99e70b5d-adab-496b-aa2d-04dd641562be'::uuid, '2026-05-07'::date, 4686.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('0ecceb0d-c447-482d-957c-0fef537b4a95'::uuid, '2026-05-08'::date, 402.93::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('4734aaae-75dc-4dbf-bf7e-aa231575e84b'::uuid, '2026-05-08'::date, 1183.68::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('7b3d407f-ba19-4aad-9a5c-3ca5199d269a'::uuid, '2026-05-08'::date, 3680.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('7c5da950-72f4-447f-a4a0-f20941b91bf4'::uuid, '2026-05-08'::date, 1521.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('51a97cfa-4143-472d-8e32-cfcdb3d65189'::uuid, '2026-05-09'::date, 639.83::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('6f11cf55-029c-4f52-809d-3fcbe4680d62'::uuid, '2026-05-09'::date, 12787.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('8752f256-d412-4452-8c78-47e13ffcc038'::uuid, '2026-05-09'::date, 895.41::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('b5975996-c1cf-4987-a831-74ecaf30e1ad'::uuid, '2026-05-09'::date, 1104.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('bb4f1742-b1b7-4b0d-9590-2f9c8a7c95a8'::uuid, '2026-05-09'::date, 472.14::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('4d036966-4523-4df3-b861-2c8530956288'::uuid, '2026-05-10'::date, 850.89::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('5df417b3-34eb-45b7-946f-79875ed92a4f'::uuid, '2026-05-10'::date, 549.36::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('7385d226-2fac-4bd9-957f-425b0e51b870'::uuid, '2026-05-10'::date, 13077.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('0f8941de-7a57-40af-8dfc-f4383d862d2d'::uuid, '2026-05-11'::date, 108.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('3117c160-2309-4768-a559-cbeff8b0d6d1'::uuid, '2026-05-11'::date, 638.66::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('a5f82c3b-2f12-45a7-a1be-6d56b993fe8f'::uuid, '2026-05-11'::date, 533.24::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('b535ebb5-61ea-43d3-b6d8-e51d6f8ed629'::uuid, '2026-05-11'::date, 8005.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('379d9bd6-4c7f-4a89-88b8-f7721d692744'::uuid, '2026-05-12'::date, 5567.00::numeric(12,2), 'pos_cash_deposit', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('41f79d35-a1f4-4fb1-9889-a8f259679d63'::uuid, '2026-05-12'::date, 1227.12::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('62006b03-d13c-443c-952d-1c142e0015c2'::uuid, '2026-05-12'::date, 498.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('6c6b6e0d-acac-4c10-a12e-f519bb863062'::uuid, '2026-05-12'::date, 536.67::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('1e836f78-9ee4-404b-8477-93dfc7cbfff1'::uuid, '2026-05-13'::date, 2864.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('3b3161d6-38cb-4373-ba45-55618cab74e7'::uuid, '2026-05-13'::date, 529.71::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('5647bee4-685f-4020-a9af-bb5ce9df81df'::uuid, '2026-05-13'::date, 1614.50::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('c2a9d71c-6aab-4136-abca-d15df11fa83b'::uuid, '2026-05-13'::date, 414.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('263db398-cba6-49be-a308-078c11725e98'::uuid, '2026-05-14'::date, 608.12::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('737773ef-0cb5-40cf-818c-0a27c317581b'::uuid, '2026-05-14'::date, 173.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('7a21cf5f-5340-4a7d-ade7-b6a07724b12a'::uuid, '2026-05-14'::date, 180.22::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('cf101c3c-8d39-45af-8c77-db2bed2f2eb8'::uuid, '2026-05-14'::date, 1664.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('0ae4bef4-4ef4-4495-bdff-5672996b0988'::uuid, '2026-05-15'::date, 439.47::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('10af3c06-bf14-4491-b31f-24501a320c2a'::uuid, '2026-05-15'::date, 986.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('ad14e8e4-4f5a-4e4b-b74c-7bd5bcf52496'::uuid, '2026-05-15'::date, 8508.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('0222542c-0115-451f-a69a-575e269f2417'::uuid, '2026-05-16'::date, 492.37::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('23d86b26-c0d1-40fb-9e5c-9a4de28d3bb2'::uuid, '2026-05-16'::date, 7091.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('9edade16-7da3-4ab3-a78c-a5a2977220e9'::uuid, '2026-05-16'::date, 715.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('c4c3a27f-2a92-4c38-aa0c-987cde1744b2'::uuid, '2026-05-16'::date, 622.74::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('54963931-3911-44d8-9e52-261a9a2ed641'::uuid, '2026-05-17'::date, 13389.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('6fe80716-1ada-4fe2-93a0-3eb9fb78b64a'::uuid, '2026-05-17'::date, 542.77::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('b08366d2-bc1a-4dbe-9aee-a624090c06c6'::uuid, '2026-05-17'::date, 1074.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('ece66701-cd28-487b-afd8-6a02773caa1a'::uuid, '2026-05-17'::date, 630.78::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('2291e15e-e190-4c4f-bdfc-f7142b1befec'::uuid, '2026-05-18'::date, 1497.58::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('c28b40b5-d320-4d57-ba09-ca5d536b9ab8'::uuid, '2026-05-18'::date, 788.00::numeric(12,2), 'pos_cash_deposit', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('4ac467be-0e1c-400c-8112-368660ec8f3f'::uuid, '2026-05-19'::date, 238.08::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('5390790f-125f-4b40-8ddc-d11f13dc8bc1'::uuid, '2026-05-19'::date, 757.92::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('da1038f2-e5b5-4dbb-aff0-165cb972dd22'::uuid, '2026-05-19'::date, 5399.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('f1c9090a-a8fd-489d-b883-681c32c9413d'::uuid, '2026-05-19'::date, 816.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('091a7174-8eda-4bc9-b428-270933be0dd0'::uuid, '2026-05-20'::date, 11235.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('bbda4c23-b914-4851-8076-3a0ce02ae083'::uuid, '2026-05-20'::date, 923.43::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('d7f3895c-409e-4bd9-8df1-38115f910cc1'::uuid, '2026-05-20'::date, 658.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('1192d349-c2c2-4a3c-adcf-af8f9a3ff8d0'::uuid, '2026-05-21'::date, 761.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('89362f62-d750-4895-b0c1-22af8668af71'::uuid, '2026-05-21'::date, 741.11::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('b122238a-70c1-4839-bfd5-ab9b9f1028d2'::uuid, '2026-05-21'::date, 2943.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('28fcb66f-3993-4cbb-9975-6d65c2c24e75'::uuid, '2026-05-22'::date, 588.59::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('37cd8f14-451f-4665-bec7-0675ff2a35e2'::uuid, '2026-05-22'::date, 504.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('c147afbd-9a68-4344-8f6e-abf6baba8ee4'::uuid, '2026-05-22'::date, 4943.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('d8439016-a082-4e8c-aebf-ca43de708ad3'::uuid, '2026-05-22'::date, 543.10::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('2e7a885c-499c-4ebf-95e7-53eef5e2b007'::uuid, '2026-05-23'::date, 949.57::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('9aed1d18-2e2a-4de5-9d6d-98fc0b33a238'::uuid, '2026-05-23'::date, 760.87::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('dd9d2acd-4410-4ec1-8115-6480d1cdf76a'::uuid, '2026-05-23'::date, 5946.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('073f50f7-74b1-45ff-9898-8ccf1b67e22b'::uuid, '2026-05-24'::date, 481.84::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('739a3f16-ddac-4533-96b5-13573cd947ad'::uuid, '2026-05-24'::date, 5712.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('a8faa02f-2713-4dc0-b7b9-d85ff016d6ef'::uuid, '2026-05-24'::date, 1313.44::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('bc648435-3366-4206-93d3-9ee78d60e1f6'::uuid, '2026-05-24'::date, 1197.00::numeric(12,2), 'pos_cash_deposit', 'pos_cash', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('12704a0e-afb9-44d1-a282-0a3939e9bb23'::uuid, '2026-05-25'::date, 817.76::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('2bce693d-c96e-492d-a025-73ce8576f18d'::uuid, '2026-05-25'::date, 1331.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('58ba7562-9161-43ff-9a96-5c2ece949b2c'::uuid, '2026-05-25'::date, 2046.95::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('75d9fdaf-2a34-4e1e-a8bd-05b96ba9566d'::uuid, '2026-05-25'::date, 543.78::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('d1baa715-0db5-4925-bd0f-d2a622ed72de'::uuid, '2026-05-25'::date, 674.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('03778607-5543-4b5f-9e92-064a4cc8eef9'::uuid, '2026-05-26'::date, 8455.80::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('4433dbc2-c83a-4a47-9302-986f6cc6f858'::uuid, '2026-05-26'::date, 928.86::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('c3d628f7-712c-4304-9dee-43f3b79b9d1f'::uuid, '2026-05-26'::date, 727.02::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('f442bcf1-3695-40ea-8563-d8d12c78a8e2'::uuid, '2026-05-26'::date, 279.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('47837142-5be3-424a-a7cf-546ddd029b7e'::uuid, '2026-05-27'::date, 1743.71::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('558ba6e1-2f64-42f5-ba7c-bd664953e2c1'::uuid, '2026-05-27'::date, 3597.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('69534f39-b023-41e4-98aa-8baf7706fce6'::uuid, '2026-05-27'::date, 383.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('202e4ebc-a843-4d2a-804b-5a1092e815e3'::uuid, '2026-05-28'::date, 147.94::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('6c87baca-e7ed-41fb-b937-e9fe983cd82b'::uuid, '2026-05-28'::date, 234.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('b0494d8f-115f-47f8-b0fe-f05207044dab'::uuid, '2026-05-28'::date, 1376.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('02cf05a8-5573-4950-bd2e-d5d3d9784771'::uuid, '2026-05-29'::date, 3987.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('9b7964b2-e14e-4582-9901-f2e89dd94dba'::uuid, '2026-05-29'::date, 128.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('bc99b402-32c3-40f7-b701-6a12f1bbe8af'::uuid, '2026-05-29'::date, 196.15::numeric(12,2), 'rider_income_grab', 'delivery_income', 'auto', 'grab_payout', 'delivery_grab', 'auto', '2026-07-07 reviewed May reclass: Grab bank settlement -> grab_payout; May Grab export evidence; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('fe31d67d-bb7b-4bac-983f-54cb5ba00443'::uuid, '2026-05-29'::date, 1522.04::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('36ccc23c-4b7d-4a8f-9cd3-a7216343a3ed'::uuid, '2026-05-30'::date, 4671.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('8b655be8-4e9e-4e5a-8094-4504d5f8af49'::uuid, '2026-05-30'::date, 969.41::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('a13569a8-7463-4874-b9a9-91d0372f8d67'::uuid, '2026-05-30'::date, 513.32::numeric(12,2), 'bank_statement', NULL, 'needs_review', 'grab_payout', 'delivery_grab', 'manual', '2026-07-07 reviewed May reclass exception: 2026-05-30 513.32 exact payout-date aggregate match to May Grab export (4 rows); statement text lacks Grab keyword, so manual exception; report statement_reclass_may_fresh_after_cleanup_20260707_222025.'),
    ('a5db4bd0-fa7f-499d-a9c4-23ce64c3fca4'::uuid, '2026-05-30'::date, 766.00::numeric(12,2), 'rider_income_lineman', 'delivery_income', 'auto', 'payment_gateway_payout', 'payment_gateway', 'auto', '2026-07-07 reviewed May reclass: statement LINE PAY / Thai Line Pay is payment-gateway settlement, not LINE MAN sales; POS channel evidence lives in pos_bills.payment_type_raw; report statement_reclass_may_fresh_after_cleanup_20260707_222025.');

-- Preview before update.
SELECT
    'BEFORE' AS phase,
    b.source_type,
    b.category_code,
    b.match_status,
    COUNT(*) AS row_count,
    SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _may_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

DO $may_reclass_lock_and_preflight$
DECLARE
    v_locked_count integer;
    v_target_count integer;
    v_exact_count integer;
    v_manual_current_count integer;
    v_target_sum numeric(12,2);
    v_gateway_count integer;
    v_gateway_sum numeric(12,2);
    v_grab_count integer;
    v_grab_sum numeric(12,2);
    v_new_manual_count integer;
    v_new_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*) INTO v_locked_count
    FROM (
        SELECT b.id
        FROM public.bank_statement_entries b
        JOIN _may_statement_reclass_targets t ON t.id = b.id
        ORDER BY b.id
        FOR UPDATE
    ) locked_rows;

    IF v_locked_count <> 110 THEN
        RAISE EXCEPTION 'Locked row count mismatch: expected %, got %', 110, v_locked_count;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit), 0)::numeric(12,2)
    INTO v_target_count, v_target_sum
    FROM _may_statement_reclass_targets;

    IF v_target_count <> 110 THEN
        RAISE EXCEPTION 'Target count mismatch: expected %, got %', 110, v_target_count;
    END IF;

    IF v_target_sum <> 234703.16::numeric(12,2) THEN
        RAISE EXCEPTION 'Target credit sum mismatch: expected %, got %', 234703.16, v_target_sum;
    END IF;

    SELECT COUNT(*) INTO v_exact_count
    FROM _may_statement_reclass_targets t
    JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.branch_code = 'thawi_watthana'
      AND b.txn_date = t.expected_txn_date
      AND b.credit = t.expected_credit
      AND COALESCE(b.debit, 0) = 0
      AND b.source_type = t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status = t.current_match_status
      AND b.match_status <> 'manual';

    IF v_exact_count <> 110 THEN
        RAISE EXCEPTION 'Current-state guard mismatch: expected %, got %', 110, v_exact_count;
    END IF;

    SELECT COUNT(*) INTO v_manual_current_count
    FROM _may_statement_reclass_targets t
    JOIN public.bank_statement_entries b ON b.id = t.id
    WHERE b.match_status = 'manual';

    IF v_manual_current_count <> 0 THEN
        RAISE EXCEPTION 'Refusing to overwrite currently manual rows: %', v_manual_current_count;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit), 0)::numeric(12,2)
    INTO v_gateway_count, v_gateway_sum
    FROM _may_statement_reclass_targets
    WHERE new_source_type = 'payment_gateway_payout';

    IF v_gateway_count <> 84 OR v_gateway_sum <> 221034.91::numeric(12,2) THEN
        RAISE EXCEPTION 'Gateway target bucket mismatch: expected % / %, got % / %',
            84, 221034.91, v_gateway_count, v_gateway_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit), 0)::numeric(12,2)
    INTO v_grab_count, v_grab_sum
    FROM _may_statement_reclass_targets
    WHERE new_source_type = 'grab_payout';

    IF v_grab_count <> 26 OR v_grab_sum <> 13668.25::numeric(12,2) THEN
        RAISE EXCEPTION 'Grab target bucket mismatch: expected % / %, got % / %',
            26, 13668.25, v_grab_count, v_grab_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(expected_credit), 0)::numeric(12,2)
    INTO v_new_manual_count, v_new_manual_sum
    FROM _may_statement_reclass_targets
    WHERE new_match_status = 'manual';

    IF v_new_manual_count <> 1 OR v_new_manual_sum <> 513.32::numeric(12,2) THEN
        RAISE EXCEPTION 'Manual target bucket mismatch: expected % / %, got % / %',
            1, 513.32, v_new_manual_count, v_new_manual_sum;
    END IF;
END
$may_reclass_lock_and_preflight$;

CREATE SCHEMA IF NOT EXISTS audit;
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON SCHEMA audit FROM anon;
REVOKE ALL ON SCHEMA audit FROM authenticated;

CREATE TABLE audit.bank_statement_reclass_backup_20260707_may AS
SELECT b.*
FROM public.bank_statement_entries b
JOIN _may_statement_reclass_targets t ON t.id = b.id;

COMMENT ON TABLE audit.bank_statement_reclass_backup_20260707_may
IS 'Pre-reclass backup of 110 May 2026 bank_statement_entries rows reviewed on 2026-07-07.';

REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_may FROM PUBLIC;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_may FROM anon;
REVOKE ALL ON TABLE audit.bank_statement_reclass_backup_20260707_may FROM authenticated;

DO $may_reclass_backup_check$
DECLARE
    v_backup_count integer;
    v_backup_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_backup_count, v_backup_sum
    FROM audit.bank_statement_reclass_backup_20260707_may;

    IF v_backup_count <> 110 THEN
        RAISE EXCEPTION 'Backup count mismatch: expected %, got %', 110, v_backup_count;
    END IF;

    IF v_backup_sum <> 234703.16::numeric(12,2) THEN
        RAISE EXCEPTION 'Backup credit sum mismatch: expected %, got %', 234703.16, v_backup_sum;
    END IF;
END
$may_reclass_backup_check$;

CREATE TEMP TABLE _may_statement_reclass_updated (
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
            WHEN COALESCE(BTRIM(b.notes), '') = '' THEN t.note_append
            ELSE b.notes || E'
' || t.note_append
        END,
        classified_by = 'codex_reviewed_may_reclass_20260707',
        classified_at = now()
    FROM _may_statement_reclass_targets t
    WHERE b.id = t.id
      AND b.branch_code = 'thawi_watthana'
      AND b.txn_date = t.expected_txn_date
      AND b.credit = t.expected_credit
      AND COALESCE(b.debit, 0) = 0
      AND b.source_type = t.current_source_type
      AND b.category_code IS NOT DISTINCT FROM t.current_category_code
      AND b.match_status = t.current_match_status
      AND b.match_status <> 'manual'
    RETURNING
        b.id,
        t.current_source_type AS old_source_type,
        t.current_category_code AS old_category_code,
        t.current_match_status AS old_match_status,
        b.source_type AS new_source_type,
        b.category_code AS new_category_code,
        b.match_status AS new_match_status,
        b.credit
)
INSERT INTO _may_statement_reclass_updated (
    id,
    old_source_type,
    old_category_code,
    old_match_status,
    new_source_type,
    new_category_code,
    new_match_status,
    credit
)
SELECT
    id,
    old_source_type,
    old_category_code,
    old_match_status,
    new_source_type,
    new_category_code,
    new_match_status,
    credit
FROM updated;

DO $may_reclass_postupdate$
DECLARE
    v_updated_count integer;
    v_updated_sum numeric(12,2);
    v_gateway_count integer;
    v_gateway_sum numeric(12,2);
    v_grab_count integer;
    v_grab_sum numeric(12,2);
    v_manual_count integer;
    v_manual_sum numeric(12,2);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_updated_count, v_updated_sum
    FROM _may_statement_reclass_updated;

    IF v_updated_count <> 110 THEN
        RAISE EXCEPTION 'Updated count mismatch: expected %, got %', 110, v_updated_count;
    END IF;

    IF v_updated_sum <> 234703.16::numeric(12,2) THEN
        RAISE EXCEPTION 'Updated sum mismatch: expected %, got %', 234703.16, v_updated_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_gateway_count, v_gateway_sum
    FROM _may_statement_reclass_updated
    WHERE new_source_type = 'payment_gateway_payout';

    IF v_gateway_count <> 84 OR v_gateway_sum <> 221034.91::numeric(12,2) THEN
        RAISE EXCEPTION 'Gateway bucket mismatch: expected % / %, got % / %',
            84, 221034.91, v_gateway_count, v_gateway_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_grab_count, v_grab_sum
    FROM _may_statement_reclass_updated
    WHERE new_source_type = 'grab_payout';

    IF v_grab_count <> 26 OR v_grab_sum <> 13668.25::numeric(12,2) THEN
        RAISE EXCEPTION 'Grab bucket mismatch: expected % / %, got % / %',
            26, 13668.25, v_grab_count, v_grab_sum;
    END IF;

    SELECT COUNT(*), COALESCE(SUM(credit), 0)::numeric(12,2)
    INTO v_manual_count, v_manual_sum
    FROM _may_statement_reclass_updated
    WHERE new_match_status = 'manual';

    IF v_manual_count <> 1 OR v_manual_sum <> 513.32::numeric(12,2) THEN
        RAISE EXCEPTION 'Manual bucket mismatch: expected % / %, got % / %',
            1, 513.32, v_manual_count, v_manual_sum;
    END IF;
END
$may_reclass_postupdate$;

-- Preview after update.
SELECT
    'AFTER' AS phase,
    b.source_type,
    b.category_code,
    b.match_status,
    COUNT(*) AS row_count,
    SUM(b.credit)::numeric(12,2) AS credit_sum
FROM public.bank_statement_entries b
JOIN _may_statement_reclass_targets t ON t.id = b.id
GROUP BY b.source_type, b.category_code, b.match_status
ORDER BY b.source_type, b.category_code, b.match_status;

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
-- UPDATE public.bank_statement_entries b
-- SET source_type = bak.source_type,
--     category_code = bak.category_code,
--     match_status = bak.match_status,
--     notes = bak.notes,
--     classified_by = bak.classified_by,
--     classified_at = bak.classified_at
-- FROM audit.bank_statement_reclass_backup_20260707_may bak
-- WHERE b.id = bak.id;
-- COMMIT;
