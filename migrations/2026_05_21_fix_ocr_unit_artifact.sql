-- ════════════════════════════════════════════════════════════════════════════
-- 2026-05-21 — Fix OCR unit artifact 'ตคก' → 'ดอก' in invoice_items
-- ════════════════════════════════════════════════════════════════════════════
--
-- THE PROBLEM
-- -----------
-- 4 invoice_items rows for สิงห์เลมอนไลม์ variants had unit = 'ตคก'.
-- This is an OCR misread of 'ดอก' (the Thai word for "flower", used
-- colloquially to mean a shrink-wrapped multipack/tray of cans, usually 24).
--
-- The tone marks and consonant shapes in ด-อ-ก were garbled by the OCR
-- engine on the original PDF scan, producing the nonsense string 'ตคก'.
--
-- IMPACT
-- ------
-- sync-from-invoices Python code:
--   ingredient.invoice_unit = 'ดอก'
--   invoice_items.unit      = 'ตคก'   ← doesn't match
--   _unit_matches("ดอก", "ตคก") → False → Case C → unit_mismatch = True
--
-- Result: all สิงห์เลมอนไลม์ flavours showed "หน่วยไม่ตรง" in the
-- sync preview and never received the ÷24 pack-size conversion.
-- True cost should be ฿308.41 ÷ 24 = ฿12.85/กระป๋อง.
--
-- THE FIX
-- -------
-- Normalise the OCR garbage to the correct Thai word. Idempotent — if
-- 'ตคก' no longer exists the WHERE clause matches 0 rows.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

UPDATE public.invoice_items
SET unit = 'ดอก'
WHERE unit = 'ตคก';

-- Confirm
SELECT unit, COUNT(*) AS rows
FROM public.invoice_items
WHERE unit IN ('ตคก', 'ดอก')
GROUP BY unit;

COMMIT;
