# Batch 7 Audit — Data Ingestion + AI-Write Flows (READ-ONLY)

Date: 2026-05-27
Scope: `pos_import.py`, `product_classifier.py`, `phase3a_ai_categorize_routes.py`, `phase3a_anomaly_routes.py`, `ai_exec_routes.py`
Focus: data-integrity / money-corruption / AI-text-into-typed-column / event-loop-block

## Summary

| Severity | Count | IDs |
|---|---|---|
| CRITICAL | 5 | C1, C2, C3, C4, C5 |
| MEDIUM | 6 | M1, M2, M3, M4, M5, M6 |
| LOW | 3 | L1, L2, L3 |

Headline: **C1** is a guaranteed runtime crash (undefined function `_try_rules`) on every cashflow categorization call — the entire `/ai/categorize/cashflow/batch` flow is dead. **C2** causes cascading "transaction is aborted" failures in both AI-categorize batch loops. **C3** is the Session-36-class event-loop block (sync `/import_sync` + sync DB list endpoints calling blocking I/O without `to_thread`). **C4/C5** are data-integrity dedup/parse bugs that silently lose or double money.

## ✅ Closure status (Session 44, 2026-05-28)

- **C1** `_try_rules` → `_try_rule_match` — ✅ fixed in `766bdc0`
- **C2** rollback in batch loops — ✅ fixed in `29870ee`
- **C3** `/pos/import_sync` async → sync def + sync file read — ✅ fixed in `29870ee`
- **C4** `pos_sales_items` dedup — 🟡 **DEFERRED** (data-integrity design; needs migration plan)
- **C5** `r.iloc[4]` → `r.get("ส่วนลด")` — ✅ fixed in `29870ee`

MEDIUM closures:
- **M6** LLM confidence clamp — ✅ fixed in `6d2b895`
- **M4** `to_num` accounting parens + currency strip — ✅ fixed (this commit, 2026-05-28). Verified 13/13 unit cases including `(1,234.00) → -1234.0`, `1,234.00 ฿ → 1234.0`, `-฿100 → -100`.

Remaining: C4 + 4 MEDIUM + 3 LOW. Open items are design decisions or cosmetic.

---

## [C1] `_try_rules` is undefined — every cashflow categorization raises NameError

**File:** `phase3a_ai_categorize_routes.py:597`

**Current code:**
```python
# Tier 1 — rules
rule_result = _try_rules(cur, description)
```

**Issue:** There is no function named `_try_rules` anywhere in the module (confirmed by grep) or imported. The only similar function is `_try_rule_match(cur, vendor_name)` (line 93), which has a different name AND a different semantic (it matches a vendor name via `vendor_category_rules`, not a free-text cashflow description). Calling `_categorize_cashflow_one` therefore raises `NameError: name '_try_rules' is not defined` for **every** entry. `categorize_cashflow_batch` catches it as a generic `Exception` and appends to `errors[]`, so the endpoint returns 200 with `processed: 0` and N errors — the cashflow AI categorization has never worked. All `pos_cashflow_entries` with `ai_cat_status='pending'` stay pending forever; petty-cash expenses never reach the P&L category breakdown.

**Suggested fix:** Replace `_try_rules(cur, description)` with a real rule lookup. If cashflow rules should reuse `vendor_category_rules`, call `_try_rule_match(cur, description)` (it ILIKE-matches the passed string against `pattern`). Otherwise define `_try_rules` against the correct rules table. Note the return-shape: `_try_rule_match` returns a dict with `category_code` — compatible with line 599's `rule_result["category_code"]`.

**Test plan:** Insert one `pos_cashflow_entries` row with `ai_cat_status='pending', is_refund=false`, call `POST /ai/categorize/cashflow/batch?allow_llm=false`. Before fix: `errors` contains `name '_try_rules' is not defined`. After fix: entry is categorized or skipped cleanly.

---

## [C2] AI-categorize batch loops never rollback on per-bill failure → cascading "transaction is aborted"

**File:** `phase3a_ai_categorize_routes.py:362-369` (bill batch) and `658-665` (cashflow batch); root cause in `_categorize_one` / `_categorize_cashflow_one`.

**Current code:**
```python
for bill_id in bill_ids:
    try:
        result = _categorize_one(conn, bill_id, allow_llm=allow_llm)
        processed.append(result)
    except HTTPException as e:
        errors.append({"bill_id": bill_id, "status": e.status_code, "error": e.detail})
    except Exception as e:
        errors.append({"bill_id": bill_id, "status": 500, "error": str(e)})
```

**Issue:** `_categorize_one` runs `UPDATE vendor_bills` (Tier-1 rule hit-count bump at line 112, and possibly the category UPDATE) before it can raise — e.g. `_call_llm` raises `HTTPException(502)` after the rule-table `UPDATE hit_count`. The single shared `conn` is now in an **aborted transaction** state, but the loop only records the error and continues to the next `bill_id`. Every subsequent iteration fails with psycopg2 `InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block`. One transient LLM 502 (or one C1-style error in cashflow) poisons the rest of the batch — the cron job silently stops categorizing. Also a partial side effect can leak: `vendor_category_rules.hit_count` may be bumped for a bill that never gets categorized if commit ordering differs.

**Suggested fix:** Add `conn.rollback()` in both `except` branches of both batch loops before appending the error. Better: wrap each bill in a savepoint, or open a fresh connection/transaction per bill.

**Test plan:** Mock `_call_llm` to raise on the 1st bill but succeed on the rest; run batch over 3 LLM-bound bills. Before fix: 1 error + 2 "transaction is aborted" errors, 0 processed. After fix: 1 error, 2 processed.

---

## [C3] Blocking I/O on event loop: sync `/import_sync` + sync DB endpoints in async-defined handlers

**File:** `pos_import.py:1332` (`async def import_pos_excel_sync`), also list endpoints `1501`, `1529`; `phase3a_*` endpoints are sync `def` (run in threadpool — OK).

**Current code:**
```python
@router.post("/import_sync", response_model=ImportResponse)
async def import_pos_excel_sync(...):
    ...
    df, rtype = read_and_detect(content, file.filename or "")   # blocking pd.read_excel
    conn = get_db_conn()
    ... cur.executemany(...)                                    # blocking psycopg2
```

**Issue:** `import_pos_excel_sync` is declared `async` but calls `read_and_detect` (blocking `pd.read_excel`/`openpyxl`, 10-30 s on large `bill_detail` XLSX) and synchronous psycopg2 directly on the event loop. This is the exact Session-36 class: the entire uvicorn worker stalls for the whole parse+insert, freezing all other requests (health checks → Uptime Robot false DOWN). The async `/detect-only` (line 1031) was correctly fixed with `asyncio.to_thread`, but `/import_sync` was not. (The primary `/import` route correctly offloads to `BackgroundTasks`, so this only bites whoever still calls the legacy sync route.)

**Suggested fix:** Either change `async def import_pos_excel_sync` to plain `def` (so Starlette runs it in the threadpool), or wrap the blocking body in `await asyncio.to_thread(...)`. Simplest: drop `async`.

**Test plan:** Hit `/import_sync` with a large XLSX while concurrently polling `/health` — before fix `/health` latency spikes to the full parse duration; after fix it stays responsive.

---

## [C4] POS bill_detail line items silently dropped when bill UPSERT updates an existing receipt

**File:** `pos_import.py:1139-1156` (background) and `1406-1423` (sync).

**Current code:**
```python
if table == "_sales_items":
    for it in rows:
        bk = it.pop("_bill_key")
        cur.execute("""SELECT id FROM pos_bills
                       WHERE branch_code=%s AND receipt_code=%s
                         AND sales_date=%s""", bk)
        bid = cur.fetchone()
        if bid:
            it["bill_id"] = bid[0]
    rows = [r for r in rows if "bill_id" in r]   # items with no matched bill are DROPPED
    ...
    cur.executemany("INSERT INTO public.pos_sales_items ...", rows)
```

**Issue (two problems):**
1. **Orphan-drop is silent.** Any line item whose bill key didn't resolve is dropped with no log and no counter — if `pos_bills` UPSERT skipped a row (e.g. conflict on a void bill with `bill_net=0` that mapped to the same receipt key), its items vanish from `pos_sales_items` while the bill total still exists, corrupting per-item analytics (menu engineering, food cost).
2. **Re-import duplicates line items.** `pos_bills` is idempotent via `ON CONFLICT (branch_code, receipt_code, sales_date) DO UPDATE`, but `pos_sales_items` is a plain `INSERT` with **no dedup / no delete-existing**. Re-importing the same `bill_detail` file under a *different* filename (so the `file_hash` dedup at C-level doesn't trigger) re-inserts every line item again → `pos_sales_items` double-counts qty and net_amount. Item-level revenue is then 2× while bill-level revenue stays correct — a silent reconciliation break.

**Suggested fix:** (a) Log+count dropped orphan items. (b) Before inserting `_sales_items`, `DELETE FROM pos_sales_items WHERE bill_id = ANY(<resolved bill ids>)`, or add a unique constraint on `(bill_id, line_no)` with `ON CONFLICT DO UPDATE`. The same applies to `_inventory_items` (line 1120) which is also a plain INSERT keyed off the latest snapshot.

**Test plan:** Import a `bill_detail` XLSX, note `sum(net_amount)` in `pos_sales_items`; re-import the same content with a renamed file; assert the sum did not double.

---

## [C5] `payment_type_summary` reads discount from hardcoded `r.iloc[4]` — wrong column = wrong money

**File:** `pos_import.py:493`

**Current code:**
```python
"total_discount":   to_num(r.iloc[4])             or 0,  # 'ส่วนลด' merged col
```

**Issue:** Positional `iloc[4]` assumes the discount is always the 5th column after `normalize_columns`. Unlike every other field in this parser (which uses `r.get("<canon>")`), this is fragile: if FoodStory adds/reorders a column, or `normalize_columns` collapses a verbose header differently, `iloc[4]` silently reads the wrong cell (e.g. `ยอดรวม` or `ค่าบริการ`) and writes it into `total_discount`. That feeds the payment-type P&L breakdown with a wrong number — no crash, just wrong money. The generic `"ส่วนลด"` canonical name already exists in `_CANONICAL_COLS` (line 305) specifically for this report, so a named `.get` is available.

**Suggested fix:** Replace `r.iloc[4]` with `r.get("ส่วนลด")`. If the merged-cell name truly can't be normalized, at minimum guard the index (`len(r) > 4`) and log when the positional column header isn't the expected discount label.

**Test plan:** Import a `payment_type_summary` XLSX and assert `pos_sales_payment_summary.total_discount` equals the `ส่วนลด` column value, not the column physically at index 4.

---

## [M1] AI may write `category_code='misc'` even when 'misc' is not a valid category

**File:** `phase3a_ai_categorize_routes.py:197, 201, 289, 620-622`

**Issue:** Multiple fallbacks hardcode `"misc"` (JSON-parse failure → `misc`; invalid-LLM-code → `misc`; cashflow `llm.get("category_code","misc")`). The bill path validates the *LLM* code with `_validate_category_exists` and falls back to `misc` — but never validates that `misc` itself exists in `expense_categories`. If `misc` is not an active row, the subsequent `UPDATE vendor_bills SET category_code='misc'` either violates an FK or stores an orphan code that downstream P&L joins drop. `pos_import.py:755` already documents "no customer_refund in expense_categories" — proof the category set is curated and `misc` membership should not be assumed.

**Suggested fix:** Verify `misc` exists at startup (or use a constant validated once), and if the LLM code is invalid AND `misc` is absent, leave `category_code` NULL + set a needs-review flag rather than writing an unverified code.

**Test plan:** Temporarily ensure no `misc` row; run categorize on a bill the LLM can't place; assert no orphan `category_code` is written.

---

## [M2] AI-categorize batch ignores `created_at` column it orders by from a view it doesn't own

**File:** `phase3a_ai_categorize_routes.py:354-355`

**Current code:**
```python
"""SELECT id FROM public.v_bills_needing_category
   ORDER BY created_at ASC LIMIT %s"""
```

**Issue:** Orders by `created_at` from view `v_bills_needing_category`. If that view doesn't expose `created_at` (the `pending` endpoint at line 441 selects `id, vendor_name, amount, bill_date, invoice_no, item_count` — no `created_at`), this raises `column "created_at" does not exist` → 500 on the whole cron batch. This is the hallucinated-column class. Cannot confirm the view's columns from the audited files; flag for schema verification.

**Suggested fix:** Verify `v_bills_needing_category` exposes `created_at`; if not, order by `bill_date` or a column the view actually has.

**Test plan:** `SELECT created_at FROM public.v_bills_needing_category LIMIT 1;` — if it errors, fix the ORDER BY.

---

## [M3] `bill_anomalies` insert relies on string-matching the DB error to detect dedup

**File:** `phase3a_anomaly_routes.py:220-224`

**Current code:**
```python
except Exception as e:
    if "duplicate" in str(e).lower() or "unique" in str(e).lower():
        return None
    raise
```

**Issue:** After a failed INSERT, psycopg2 leaves the transaction aborted. `_insert_anomaly` swallows the duplicate and returns None, but does **not** rollback — and the scan loop (line 254-262) keeps calling `_insert_anomaly` for the next bill on the same aborted transaction, so every subsequent insert fails with "transaction is aborted", `"duplicate"`/`"unique"` won't be in that message, so it `raise`s and the whole scan 500s after the first real duplicate. Same C2-class transaction-poisoning. Also: matching on English substrings is locale-fragile.

**Suggested fix:** Use a SAVEPOINT per insert (`cur.execute("SAVEPOINT sp")` … `ROLLBACK TO SAVEPOINT sp` on failure), or rollback the connection on the dedup path. Prefer `ON CONFLICT DO NOTHING RETURNING id` over exception-based dedup.

**Test plan:** Scan a set where bill #1 triggers a duplicate anomaly and bill #2 is a fresh anomaly; assert bill #2 is still inserted.

---

## [M4] `to_num` accepts Thai/locale numbers only via comma-strip — parentheses negatives & spaces unhandled

**File:** `pos_import.py:205-216`

**Issue:** `to_num` strips commas and handles `-`, but FoodStory/Grab exports sometimes render negatives as `(1,234.00)` (accounting parens) or with a trailing currency/space (`1,234.00 ฿`, `1 234.00`). These fall through to `float()` → `ValueError` → return None → coalesced to `0`. A negative cashflow/commission silently becomes 0, understating expense. The cashflow parser does `abs(raw_amount)` so sign is dropped anyway there, but Grab `ค่าคอมมิชชันแพลตฟอร์ม` (commission, expected negative) relies on the sign to subtract from payout.

**Suggested fix:** In `to_num`, also strip non-breaking spaces/`฿`, and convert leading-paren `(123)` to `-123` before `float()`.

**Test plan:** `to_num("(1,234.00)")` should return `-1234.0`; `to_num("1,234.00 ฿")` should return `1234.0`.

---

## [M5] `parse_cashflow_detail` pre-seeds refund `category_code='misc'` contradicting its own design comment

**File:** `pos_import.py:754-756` vs header comment `676`

**Issue:** Header documents `is_refund=True → category_code 'customer_refund'`, but the code writes `"misc"` because `customer_refund` doesn't exist in `expense_categories`. Refunds thus land in `misc` and are counted as ordinary opex instead of being deducted from revenue (the comment at 677-679 says refunds must show as `source='pos_cashflow_refund'` in `v_daybook`). Net effect: revenue overstated and opex overstated by the refund amount — a real P&L distortion, just self-cancelling at the bottom line but wrong on both top lines.

**Suggested fix:** Add a `customer_refund` category (or a dedicated handling) so refunds are deducted from revenue, not booked as misc expense; align code with the documented design.

**Test plan:** Import a cashflow file with a `คืนเงิน` row; verify it appears as a revenue deduction in `v_daybook`, not as a `misc` expense.

---

## [M6] `_call_llm` trusts `confidence`/`category_code` types from LLM JSON without bounds

**File:** `phase3a_ai_categorize_routes.py:201-206`, `product_classifier.py` clamps but categorize does not

**Issue:** `float(parsed.get("confidence", 0.5))` will raise `ValueError` if the LLM returns `"confidence": "high"` (non-numeric) → propagates as 500 inside `_categorize_one`. There's no clamp to [0,1] either, so a value like `5.0` is written to `ai_categorization_log.confidence` (likely a numeric CHECK 0-1 → constraint violation → 500). `product_classifier.py:219-221` does this correctly (clamps and try/except); the categorize path does not. This is the Session-34-class (AI text into typed column).

**Suggested fix:** Wrap the float cast in try/except defaulting to 0.5, then clamp `max(0.0, min(1.0, conf))` exactly like `product_classifier.py`.

**Test plan:** Mock LLM returning `{"category_code":"raw_meat","confidence":"very"}`; assert no 500 and confidence stored as 0.5.

---

## [L1] `ai_exec` timeout message says 10s but timeout is 30s

**File:** `ai_exec_routes.py:122, 132`

**Issue:** `subprocess.run(..., timeout=30)` but the `TimeoutExpired` handler returns `"Command timed out after 10s"`. Cosmetic/operator-confusing only. (Note: this file's `shell=True` is constrained to a fixed WHITELIST checked before the Coolify-map rewrite, so no injection — not flagged.)

**Suggested fix:** Change message to 30s or align the timeout to 10s.

---

## [L2] Duplicate `ImportResponse` class definition

**File:** `pos_import.py:1021-1028` and `1318-1325`

**Issue:** `ImportResponse` is defined twice (identical). Harmless (second wins) but confusing; the second was likely a merge artifact. Remove one.

---

## [L3] `_upsert` returns `cur.rowcount` as rows_imported — over/undercounts on UPSERT

**File:** `pos_import.py:946-947`

**Issue:** Comment admits `# may be approximate`. With `executemany` + `ON CONFLICT DO UPDATE`, psycopg2 `rowcount` reflects only the last statement, so `row_count` written to `pos_imports` is unreliable (often equals the batch size or just the last row). Cosmetic for analytics but means the import-history "rows imported" figure can mislead reconciliation. Consider `len(rows)` for the user-facing count.

---

## Notes / non-findings

- Auth headers: not flagged (global JWT interceptor per project rules).
- `ai_exec_routes.py` `shell=True`: constrained by exact-match WHITELIST before any rewrite — no injection vector. Not flagged.
- `product_classifier.py`: clean — validates SKUs against `valid_skus`, clamps confidence, handles non-JSON, handles empty names. Good reference implementation for the categorize path (see M6).
- `phase3a_*` endpoints are sync `def` (run in threadpool) — not event-loop blockers. Only `pos_import.py:import_pos_excel_sync` is the async+blocking offender (C3).
- File-hash dedup (`uq_pos_imports_hash`) handles identical re-uploads cleanly (409/already_imported) — but does NOT protect against same content under a different filename, which is the gap exploited by C4.
