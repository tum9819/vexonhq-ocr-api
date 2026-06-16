-- Migration: add is_fixed flag to expense_categories
-- Used by breakeven_routes.py to identify fixed vs variable costs
-- Fixed costs = ค่าเช่า, เงินเดือน, ค่าสาธารณูปโภค, ค่าธนาคาร, ภาษี

ALTER TABLE public.expense_categories
ADD COLUMN IF NOT EXISTS is_fixed BOOLEAN NOT NULL DEFAULT false;

UPDATE public.expense_categories
SET is_fixed = true
WHERE code IN (
    'rent',                -- ค่าเช่า
    'staff_salary',        -- เงินเดือน
    'daily_wage',          -- ค่าแรงรายวัน
    'utility_electricity', -- ค่าไฟ
    'utility_water',       -- ค่าน้ำ
    'utility_telecom',     -- ค่าอินเทอร์เน็ต/โทรศัพท์
    'utility',             -- utility ทั่วไป
    'bank_fee',            -- ค่าธรรมเนียมธนาคาร
    'tax',                 -- ภาษี
    'musician_fee'         -- ค่านักดนตรี
);

-- Seed job_heartbeat for new scheduled jobs
INSERT INTO public.job_heartbeat (job_id, last_run_at, expected_interval_hours)
VALUES
    ('weekly_breakeven',        NOW(), 168),
    ('monthly_breakeven_close', NOW(), 760)
ON CONFLICT (job_id) DO UPDATE
    SET expected_interval_hours = EXCLUDED.expected_interval_hours;
