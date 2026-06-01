# Spec — Structured-Output OCR (experimental; audit roadmap final-final)

**Date:** 2026-06-01 · **Repo:** vexonhq-ocr-api · **Author:** Claude (for TUM)
**Origin:** AI Life-Cycle Audit roadmap (ระยะยาว) — "structured-output / fine-tune OCR to reduce hallucination". TUM chose **experimental + compare only** (no production-path change).

## Problem
Production OCR (`_run_gpt_vision`) uses `response_format={"type":"json_object"}` (free-form JSON). The model can omit fields, return wrong types, or invent an out-of-set enum — the exact class behind AGENTS #24 (`payment_type` CHECK 23514), #32 (list-shape), #34 (status enum). A **strict JSON Schema** (`response_format={"type":"json_schema", strict:true}`, supported by openai 2.37) makes OpenAI structurally guarantee the shape: every field present, typed, enums constrained, `additionalProperties:false`.

## Scope (experimental — ZERO production-path change)
`_run_gpt_vision` and the live `/invoice/upload` flow are untouched. We ship the *capability + a measurement hook* so a future promotion is an evidence-based one-liner (same discipline as the OpenAI→Claude OCR switch).

## Design
1. **NEW `ocr_schema.py`** (pure, no I/O):
   - `invoice_json_schema() -> dict` — the strict JSON Schema for the parsed dict: scalars `vendor_name, merchant_tax_id, invoice_no, bill_date, due_date, subtotal, vat, amount, payment_type, currency, notes`; `payment_type` a real `enum` (`credit_card|transfer|cash|cheque|other` + null); `field_confidence` (object of 0-1) + `image_quality` ({level enum, reason}) from F6; `items[]` with `{line_no, sku, product_name, quantity, unit, unit_price, amount}`. `additionalProperties:false`, all keys `required` (OpenAI strict mode requires every property listed in `required`; "optional" is expressed as a nullable type `["number","null"]`).
   - `normalize_structured(parsed) -> dict` — pass-through/typing shim so the result is a drop-in for the existing consumers (`parsed.get("amount")`, `_insert_items(parsed["items"])`, the `payment_type` normalizer, the F6 `_confidence_warnings`). Strict output already matches, so this is mostly identity + defensive coercion — but it's the seam a future promotion plugs into.
2. **NEW `llm.openai_chat_structured(task, *, messages, schema, model=None, **kwargs)`** — sibling of `openai_chat`; sets `response_format={"type":"json_schema","json_schema":{"name":"invoice","schema":schema,"strict":True}}`, logs to `ai_call_log`, returns the raw response (caller reads `.choices[0].message.content` → already valid JSON). Same error/logging contract as `openai_chat`.
3. **EDIT `tests/ocr_golden/compare.py`** — add `run_openai_structured_ocr(image_bytes, mime, prompt)` (task `vision_ocr_compare_structured`) and include it in the per-image comparison, so `compare --dir` scores **free-form vs structured vs Claude** side-by-side (accuracy + cost via `/ai/stats`). The owner decides promotion from real numbers.
4. **NEW `tests/test_ocr_schema.py`** (offline, no API key): schema is structurally valid (required==properties keys, `additionalProperties:false`, `payment_type` enum incl. null, items subfields present); `normalize_structured` round-trips a sample to the consumer shape and tolerates a missing/garbage block without raising.

## Why not promote to production now
Strict mode can change behaviour per model and *reject* a borderline response; must be measured on real invoices first (the compare harness is exactly that). Promotion = later one-line swap in `_run_gpt_vision` to `openai_chat_structured(..., schema=invoice_json_schema())` + route the result through `normalize_structured`, once compare shows structured ≥ free-form on real bills.

## Files
- NEW `ocr_schema.py`
- EDIT `llm.py` (+ `openai_chat_structured`)
- EDIT `tests/ocr_golden/compare.py` (+ structured runner, 3-way)
- NEW `tests/test_ocr_schema.py`
- EDIT `AGENTS.md`, docs (DAILY_LOG, CHANGELOG)

## Test plan
`pytest tests/test_ocr_schema.py -v` (offline) + `.\verify.ps1`. Live (TUM, optional): `python -m tests.ocr_golden.compare --dir <real images outside repo>` now scores 3 ways.
