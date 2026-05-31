-- 2026_05_31_loan_repayment_keyword_rules.sql
-- Phase 2: auto-tag loan REPAYMENT slips. A slip memo containing "คืนยืม" or
-- "คืนเงินยืม" classifies the matched OUTGOING bank row as loan_repayment via the
-- nightly slip-reconcile (_classify_slip_category -> _CAT_TO_SOURCE). Borrow/incoming
-- stays manual (the slip pipeline is expense-only).
-- "คืนยืม" is NOT a substring of "คืนเงินยืม", so both keywords are needed.
-- priority=100 so they beat generic name rules in the keyword cascade.
-- Idempotent: ON CONFLICT (rule_type, match_value) DO UPDATE.

INSERT INTO public.statement_rules
    (rule_type, match_value, direction, category_code, source_type, priority)
VALUES
    ('keyword', 'คืนยืม',     'expense', 'loan_repayment', 'loan_repayment', 100),
    ('keyword', 'คืนเงินยืม', 'expense', 'loan_repayment', 'loan_repayment', 100)
ON CONFLICT (rule_type, match_value) DO UPDATE
    SET direction     = EXCLUDED.direction,
        category_code = EXCLUDED.category_code,
        source_type   = EXCLUDED.source_type,
        priority      = EXCLUDED.priority;
