-- ============================================================
-- VEXONHQ Migration 2026-05-30 (Session 47b) — split COGS into Food vs Beverage cost
-- APPLIED to prod via Supabase MCP 2026-05-30. Committed for repo<->prod parity.
-- (Data already live; kept for a fresh-DB rebuild.)
-- ============================================================
-- Why: food-cost% was undercounted (~15%) because the big bank beverage purchases
-- (beer / วิลล์มิกซ์) sat under a duplicate code `beverage_raw` that was NOT in the
-- hard-coded food-cost code list. After merging them in, the COMBINED food-cost%
-- swung 18-48%/month -- because cash basis records purchases when PAID, and beer is
-- bought in big lumpy lots. Splitting Food vs Beverage shows the truth: FOOD cost is
-- stable ~13-19%, BEVERAGE cost is the volatile one (7-33%).
--
-- The backend now sums by SUBTREE (parent_code) instead of a hard-coded list, so any
-- new COGS sub-code is counted automatically and `food_raw` is no longer dropped.
-- (menu_routes.py /scorecard + phase2_routes.py /dashboard/overview.)
-- ============================================================

-- 1. Merge the duplicate beverage code into the canonical one (counts in COGS + has a Thai name)
UPDATE public.bank_statement_entries SET category_code='raw_beverage' WHERE category_code='beverage_raw';

-- 2. Give the non-COGS bank codes a Thai name so exports stop showing the raw English code
INSERT INTO public.expense_categories (code, name_th, name_en, direction, is_active)
VALUES
 ('staff_salary','เงินเดือน/ค่าแรงพนักงาน','Staff salary','expense',true),
 ('utility_electricity','ค่าไฟฟ้า','Electricity','expense',true),
 ('payment_gateway_fee','ค่าธรรมเนียมรับชำระเงิน','Payment gateway fee','expense',true)
ON CONFLICT (code) DO NOTHING;

-- 3. New parent for Beverage cost, and move the beverage codes out of food_cost into it.
--    food_cost subtree => FOOD only (raw_meat/raw_veggies/raw_seasoning/raw_oil_gas/food_raw/packaging)
--    beverage_cost subtree => raw_beverage, beverage
INSERT INTO public.expense_categories (code, name_th, name_en, direction, is_active, parent_code)
VALUES ('beverage_cost','ต้นทุนเครื่องดื่ม','Beverage cost','expense',true,NULL)
ON CONFLICT (code) DO NOTHING;

UPDATE public.expense_categories SET parent_code='beverage_cost' WHERE code IN ('raw_beverage','beverage');

-- (Also done earlier this day, migration 2026_05_30_audit_cashbasis_expense_reclass.sql:
--  11 daily-wage rows คนงาน/ค่าแรง/โบนัส moved out of raw_seasoning COGS.)
