-- 2026-07-12 FA-003: deterministic slip memo rules for beverage purchases.
-- TUM-confirmed audit backfill showed these memos represent beverage raw cost.
-- Idempotent: keeps statement_rules as the existing slip memo rule store.

INSERT INTO public.statement_rules
    (rule_type, match_value, direction, category_code, source_type, priority)
VALUES
    ('keyword', 'เหล้า', 'expense', 'beverage_raw', 'vendor_purchase', 105),
    ('keyword', 'เบียร์', 'expense', 'beverage_raw', 'vendor_purchase', 105),
    ('keyword', 'beer', 'expense', 'beverage_raw', 'vendor_purchase', 105),
    ('keyword', 'singh', 'expense', 'beverage_raw', 'vendor_purchase', 105),
    ('keyword', 'chang', 'expense', 'beverage_raw', 'vendor_purchase', 105)
ON CONFLICT (rule_type, match_value) DO UPDATE
SET direction = EXCLUDED.direction,
    category_code = EXCLUDED.category_code,
    source_type = EXCLUDED.source_type,
    priority = GREATEST(public.statement_rules.priority, EXCLUDED.priority);
