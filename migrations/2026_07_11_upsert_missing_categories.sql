-- migrations/2026_07_11_upsert_missing_categories.sql
-- Upsert missing categories emitted by the frontend to prevent dashboard leakage.
-- Idempotent config-only change: INSERT ... ON CONFLICT DO UPDATE.
-- This does not rewrite historical v_daybook/bank-statement rows.
-- sort_order is intentionally omitted so new rows use the table default (999)
-- and reruns do not overwrite an operator-managed order.

INSERT INTO public.expense_categories (code, name_th, parent_code, direction, is_active)
VALUES 
    ('beverage_raw', 'วัตถุดิบเครื่องดื่ม (เบียร์/น้ำ)', 'beverage_cost', 'expense', true),
    ('gas', 'ค่าแก๊ส', 'food_cost', 'expense', true)
ON CONFLICT (code) DO UPDATE 
SET 
    name_th = EXCLUDED.name_th,
    parent_code = EXCLUDED.parent_code,
    direction = EXCLUDED.direction,
    is_active = EXCLUDED.is_active;
