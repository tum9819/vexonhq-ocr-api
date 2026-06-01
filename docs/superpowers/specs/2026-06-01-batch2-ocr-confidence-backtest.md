# Spec — Batch 2: OCR confidence (F6) + order-advice backtest (F8) + IG sync verify (F9)

**Date:** 2026-06-01 · **Repos:** vexonhq-ocr-api (F6, F8) + marastation-web (F9)
**Origin:** AI Life-Cycle Audit roadmap (ระยะกลาง). F6 (no per-field OCR confidence / no image-quality check), F8 (order advice has no backtest), F9 (IG sync once silently used fallback for months).

---

## F6 — OCR field confidence + low-image-quality warning (backend)

**Problem:** the human reviewing an OCR'd bill can't tell which field the AI guessed, and a blurry/skewed image is processed with no warning.

**Design (additive — does NOT change the extracted values):**
- `VISION_PROMPT` gains two top-level output keys: `field_confidence` (object: field → 0-1 for vendor_name/invoice_no/merchant_tax_id/bill_date/subtotal/vat/amount) and `image_quality` (`{level:"good"|"fair"|"poor", reason:str}`).
- New pure helper `_confidence_warnings(parsed) -> list[warning]`:
  - per field with confidence < 0.6 → `{severity:"warn", code:"LOW_CONFIDENCE", field, message:"AI ไม่มั่นใจ <field> (NN%) — โปรดตรวจ"}`.
  - `image_quality.level=="poor"` → `{severity:"warn", code:"LOW_IMAGE_QUALITY", field:"image", message:"คุณภาพรูปต่ำ (<reason>) — ถ่ายใหม่ให้ชัดขึ้นจะแม่นกว่า"}`.
  - Tolerates missing/garbage confidence (non-dict, string, out-of-range) — never raises.
- `_validate_invoice` appends `_confidence_warnings(parsed)`. These ride the EXISTING `warnings` array already shown on the invoice review screen → no new UI. `field_confidence`/`image_quality` persist in `ocr_json` (already stored verbatim).

**Tests** `tests/test_ocr_confidence.py` (offline): low field → LOW_CONFIDENCE; poor image → LOW_IMAGE_QUALITY; high confidence + good image → none; garbage confidence → no crash, no false warning.

---

## F8 — order-advice backtest (backend)

**Problem:** `/inventory/ai-order-advice` gives DOW-based advice with no measure of whether the pattern actually predicts. 

**Design:** `GET /inventory/ai-order-advice/backtest?branch=&train_weeks=8&test_weeks=4` (read-only):
- Pull daily POS income (same sources as the advice) for `train_weeks+test_weeks` back, split into train (older) / test (newer).
- Train: mean daily sales per DOW + grand mean → `dow_index[dow]`.
- Predict each test day = `grand_mean_train × dow_index[its dow]`; compute **MAPE** vs actual.
- Also: did train's top-2 DOW (by mean) land in test's top-2 actual DOW? (`best_day_hit`: 0-2).
- Return `{train_days, test_days, mape_pct, accuracy_pct (=100-MAPE, floored 0), best_day_hit, verdict_th}`. Bounds the query params (`ge/le`); 422 on bad input; graceful "ไม่พอข้อมูล" when test/train empty.
- Pure scorer `backtest_dow(train_daily, test_daily) -> dict` (lists of `{date,dow,sales}`) → unit-tested with synthetic series, NO DB.

**Tests** `tests/test_order_backtest.py`: a clean weekly-seasonal synthetic series → low MAPE + best_day_hit==2; flat series → defined output no crash; empty test → graceful.

---

## F9 — Instagram sync verify + fallback visibility (web)

**Problem:** the providers array was once `FACEBOOK` everywhere → every sync silently used Facebook fallback for months (pitfall #6). And a fallback run isn't visible.

**Design:**
- `scripts/verify-social-sync.ts` (Node native TS, like `verify-live-music.ts`): a static-source guard asserting the scheduled/admin sync uses `SocialProvider.INSTAGRAM` — reads the two source files and checks the `providers:` array literal. (Pure string check; no DB/network.) Wire `npm run verify:social-sync`.
- Fallback visibility: `SocialSyncRun` already exists + adapters return `fallbackReason`. Confirm the run row persists a fallback flag/reason; if the admin `social/page.tsx` doesn't already surface it, add a small warning badge "ใช้ข้อมูลสำรอง (fallback) — เช็ค IG token" when the latest run was fallback. Minimal, no new dep.

**Tests:** the verify script IS the test (run in CI). Lint+tsc+build gate as usual.

---

## Files
- backend: EDIT `main.py` (VISION_PROMPT + `_confidence_warnings` + `_validate_invoice`); NEW `tests/test_ocr_confidence.py`; EDIT `inventory_forecast_routes.py` (+ `backtest_dow` + endpoint); NEW `tests/test_order_backtest.py`; AGENTS pitfall.
- web: NEW `scripts/verify-social-sync.ts` + npm script; maybe EDIT `admin/social/page.tsx` (fallback badge); AGENTS pitfall.
- docs: DAILY_LOG, CHANGELOG.

## Test plan
backend: `pytest tests/test_ocr_confidence.py tests/test_order_backtest.py -v` + `.\verify.ps1`. web: `npm run verify:social-sync` + lint+tsc+build. All offline / no API key.
