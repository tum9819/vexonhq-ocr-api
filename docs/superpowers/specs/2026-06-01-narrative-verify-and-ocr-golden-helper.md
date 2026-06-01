# Spec — P&L Narrative number-verify (F7) + OCR golden-set helper

**Date:** 2026-06-01 · **Repo:** vexonhq-ocr-api · **Author:** Claude (for TUM)
**Origin:** AI Life-Cycle Audit roadmap (ทันที). Findings **F7** (narrative may cite a wrong number) + the Testing baseline (make a real OCR golden set from confirmed bills).

---

## Part A — F7: narrative number-verify

### Problem
`/pnl/narrative` shows a correct header (numbers straight from the DB) but the **Claude-generated prose** could print a baht figure that doesn't match `v_daybook_pnl` (hallucination / mis-calculation). A shareholder/owner reading the prose could be misled. There is no check today.

### Design (advisory, NON-mutating)
Add a pure helper `_verify_narrative(text, known_values) -> dict` in `phase10_narrative_routes.py`:
- Extract every baht figure from the prose: regex on `฿?[\d,]+(\.\d+)?` → normalize to float (strip `฿`, commas).
- Build the set of **known-true** values from the data already gathered: `total_income`, `total_expense`, `net`, `margin_pct`, `txn_count`, each `income_by_source.amount`, each `top_expenses.amount`, and the previous-month equivalents. Also accept their rounded forms (the prompt formats with `_fmt` = no decimals).
- A prose number is "matched" if it's within **1%** (or ±1 for small ints like counts) of any known value. Numbers clearly not money (years 2025/2026, percentages already followed by `%`, the margin) are whitelisted/skipped.
- Return `{ok: bool, checked: int, unmatched: [floats]}`. **Do NOT rewrite the text.**

Wire into `generate_narrative` + `preview_narrative`:
- After `_call_claude`, run `_verify_narrative`.
- If `not ok`: `logger.warning("narrative has %d unverified numbers: %s", ...)` and include a `verification` block in the JSON response.
- Strengthen the prompt: add a rule "ใช้เฉพาะตัวเลขที่ให้ไว้ด้านบนเท่านั้น ห้ามคำนวณหรือปัดเศษเพิ่มเอง".

Why advisory not auto-correct: rewriting AI prose to "fix" a number risks changing meaning / introducing a worse error on a money report. Detect + log + surface is the safe control; the human (or a future stricter mode) decides.

### Tests
`tests/test_narrative_verify.py` (offline, no API key): feed a known_values dict + crafted prose with (a) all-matching numbers → ok; (b) a planted wrong number → `ok=False` and that number in `unmatched`; (c) years/percentages not falsely flagged.

---

## Part B — OCR golden-set helper (real ground truth, kept OUT of the repo)

### Problem
`tests/ocr_golden/` can score OCR but ships only synthetic fixtures — no real accuracy number. Real ground truth already exists in the DB: **confirmed** vendor bills (`review_status='confirmed'`) are human-verified.

### Design
`tools/gen_golden_from_confirmed.py` (standalone CLI, run locally):
- Query `vendor_bills` WHERE `review_status='confirmed'` (+ optional `--limit N`, `--since YYYY-MM-DD`), join `invoice_items` for line items.
- For each bill write `<out_dir>/<invoice_id>.expected.json` in the exact shape the scorer expects (vendor_name, invoice_no, bill_date, merchant_tax_id, subtotal, vat, amount, items[]). Also emit `<invoice_id>.source.txt` with the stored image URL (from `ocr_json`/storage) so TUM can fetch the image to pair with it.
- **`--out` is REQUIRED and must be OUTSIDE the repo** (refuse a path under the repo root) — real financial data must never land in git.
- Print a summary + the exact `compare.py --dir` command to run next.

This turns "confirmed bills" → a real golden set in one command, then `python -m tests.ocr_golden.compare --dir <out>` gives the real gpt-4o-vs-Claude accuracy + cost numbers (the audit Testing baseline).

### Tests
The row→expected mapping is a pure function `bill_row_to_expected(row, items)` → unit-test it in `tests/test_ocr_golden.py` with a fake row (no DB).

---

## Files
- EDIT `phase10_narrative_routes.py` (+ `_verify_narrative`, wire in, prompt rule)
- NEW `tests/test_narrative_verify.py`
- NEW `tools/gen_golden_from_confirmed.py` + `bill_row_to_expected` helper (importable)
- EDIT `tests/test_ocr_golden.py` (+ bill_row_to_expected case)
- docs: DAILY_LOG, AGENTS pitfall if a new rule emerges

## Test plan
`ast.parse` per file → `pytest tests/test_narrative_verify.py tests/test_ocr_golden.py -v` → `.\verify.ps1` → after deploy, `GET /pnl/narrative/preview?month=2026-04` still 200 + now includes `verification` (preview path runs the verifier on the would-be prose? no — preview has no Claude call; verification only on the POST path). Confirm POST path response carries `verification`.

## Out of scope (later batches)
F6 (OCR field confidence), F8 (order backtest), F9 (IG verify), drift alerting.
