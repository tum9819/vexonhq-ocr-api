# Batch 9 — Bill Payment + Slip OCR Match + Supplier Analytics + Rules + Exports + Search

Money-first preventive audit. READ-ONLY (no source touched). Date: 2026-05-27.

Files in scope:
- `bill_payment_routes.py`
- `slip_routes.py`
- `supplier_routes.py`
- `rules_routes.py`
- `export_routes.py`
- `phase11_search_routes.py`

## Summary

| Severity | Count | IDs |
|---|---|---|
| CRITICAL | 3 | C1, C2, C3 |
| MEDIUM | 6 | M1, M2, M3, M4, M5, M6 |
| LOW | 3 | L1, L2, L3 |

Headline money risks:
- **C1** — slip-match ±10฿ tolerance produces wrong-bill matches (the ฿600.50-vs-฿600 class, plus ฿595↔฿600). Multiple unpaid bills near the same amount → TUM can confirm payment against the wrong invoice.
- **C2** — `pnd3` export + `/export/summary` filter `amount IN (600,700,2100,2800)` is a brittle hard-coded amount whitelist; WHT computed on gross with a hard-coded 3% applies to ALL musician/freelance rows including ones already net-of-tax → wrong tax totals filed to สรรพากร.
- **C3** — `statement_rules` creation has no minimum match-length guard; a 1–2 char `match_value` (e.g. `keyword='ค'`) becomes a catch-all `ILIKE '%ค%'` that misclassifies nearly every slip/statement on the next rematch.

## ✅ Closure status (Session 44, 2026-05-28)

- **C1** slip-match ±10฿ tolerance — 🟡 **DEFERRED** (design decision: epsilon-tier + `ambiguous` flag needs UX call)
- **C2** PND3 WHT whitelist + flat 3% — 🛑 **DEFERRED — needs bookkeeper/accountant input** (tax filing risk; do not touch without confirming net-of-tax vs gross treatment)
- **C3** `statement_rules` length guard — ✅ fixed in `414a858` (rejects match_value with trimmed length < 2)

DB checked at time of fix: zero existing rules had trimmed_len < 2 — fix purely prevents future bad rules.

Schema check result: **no hallucinated columns found.** Specifically verified as REAL columns (do not "fix"): `bank_statement_entries.amount` and `.direction` are STORED generated columns (`migrations/16_bank_statement.sql` L16–21, `amount = GREATEST(credit,debit)`); `invoice_items.quantity` is real (`main.py:598`, used at `main.py:1054`), so `supplier_routes.py:355 SUM(ii.quantity)` is correct. Auth-header omission treated as false positive per scope.

---

## [C1] Slip-match ±10฿ tolerance → wrong-bill match (money confirmed against wrong invoice)

**File:** `bill_payment_routes.py:421-447` (also affects the false-match class generally)

**Current code:**
```python
tolerance = 10.0
...
WHERE review_status = 'confirmed'
  AND payment_status = 'unpaid'
  AND ABS(amount - %s) <= %s          -- %s = slip_amount, tolerance
ORDER BY ABS(amount - %s) ASC, bill_date DESC
LIMIT 10
```

**Issue:**
A ±10 baht window is wide for a skewer shop where many bills cluster at round amounts (600 / 700 musician fees, beverage cases). A slip of ฿600.50 matches a ฿600 bill; a slip of ฿605 matches both a ฿600 and a ฿610 bill. The endpoint returns up to 10 candidates ranked by `ABS(amount-slip)`, so the UI's top suggestion can be a *different* unpaid invoice of nearly-equal value. If TUM accepts the top candidate and then marks it paid via `PATCH /bills/payment/{id}`, the wrong invoice is cleared while the real one stays unpaid — silent AP corruption. This is the documented Session-class false-match (`฿600.50 vs ฿600`).

Note: this endpoint itself is read-only (returns candidates), so it does NOT auto-move money — that's why it is C1 not "auto-paid". But it is the decision surface that drives the paying action.

**Suggested fix:**
- Tighten to an exact/epsilon match first (`ABS(amount - slip) <= 0.01`) and only widen to a small band (e.g. ±1฿ to absorb OCR rounding) as a clearly-labelled "near match" tier.
- Return `exact_matches` and `near_matches` as separate arrays so the UI never silently auto-selects a near match.
- When `matched_count > 1` within the tight band, flag `ambiguous=True` and require explicit selection (mirror slip_routes `needs_review` behaviour).

**Test plan:**
- Seed two confirmed unpaid bills: ฿600.00 and ฿595.00. POST a slip OCR'ing to ฿600.50 → assert only the ฿600.00 bill is returned as an exact/near match and ฿595 is excluded (currently both return).
- Seed ฿600.00 and ฿600.00 (two vendors). Slip ฿600.00 → assert `ambiguous=True`.

---

## [C2] PND3 export: hard-coded amount whitelist + 3% WHT on gross → wrong tax totals

**File:** `export_routes.py:512-518, 532-535` (and the mirror in `/export/summary` at `663-669`)

**Current code:**
```python
WHERE direction = 'expense'
  AND entry_date BETWEEN %s AND %s
  AND ( category_code IN ('musician_fee', 'freelance', 'pnd3')
        OR (amount IN (600, 700, 2100, 2800) AND category_code = 'musician_fee') )
...
amount = float(r["amount"])
tax = round(amount * 0.03, 2)        # 3% on gross, every row
```

**Issue:**
1. The `amount IN (600,700,2100,2800)` clause is redundant *and* fragile — it is already gated by `category_code = 'musician_fee'`, so it adds nothing except the implication that only those four amounts count. A ฿1,500 or ฿900 musician fee is still included via the first branch, so the whitelist is dead/confusing logic that future edits may "fix" the wrong way.
2. WHT is applied as a flat 3% to **every** matched row's `amount`. If any `v_daybook` musician/freelance row is stored *net* of tax (common when TUM records the actual transfer amount from a slip, which is already after withholding), the export over-states both `ยอดเงิน` and `ภาษีที่หัก`. The official ภ.ง.ด.3 the restaurant files would then be wrong.
3. `freelance` / `pnd3` rows are taxed at 3% too, but freelancer service income (40(2)) and other 40(8) categories can carry different rates — one flat rate misfiles them.

**Suggested fix:**
- Drop the `amount IN (...)` sub-clause entirely; rely on `category_code`.
- Store/track a `tax_base` (gross) explicitly, or add a per-row WHT-rate column, instead of assuming gross == recorded amount and rate == 3%.
- At minimum, surface a visible note that the export assumes "recorded amount = gross" so TUM can verify before filing (the red note exists but does not state the gross assumption).

**Test plan:**
- Insert a musician_fee row of ฿1,500 → assert it appears (confirms whitelist removal doesn't drop it; currently it appears only via branch 1).
- Insert a freelance row recorded as a net transfer → confirm with TUM whether tax should be `net/0.97*0.03` vs `net*0.03`; today it is `net*0.03` (under-withholds).

---

## [C3] No minimum match-length on statement_rules → catch-all rule misclassifies everything

**File:** `rules_routes.py:106-147` (creation); consumed at `slip_routes.py:363-373` and `387-397`

**Current code:**
```python
class StatementRuleIn(BaseModel):
    rule_type:   str
    match_value: str           # no length / blank guard
    ...
# create_statement_rule: validates rule_type + direction only, then upserts
# match_value.strip() with no minimum length.
```
Consumed as:
```sql
WHERE rule_type='keyword' AND direction='expense'
  AND %s ILIKE '%%' || match_value || '%%'
ORDER BY priority DESC, char_length(match_value) DESC
LIMIT 1
```

**Issue:**
A rule saved with a 1–2 character `match_value` (e.g. a bare prefix `ค`, `น`, or even an accidental empty-after-trim that DB stores as `''`) becomes `ILIKE '%ค%'`, which matches almost every Thai memo/recipient. Because category resolution picks `ORDER BY priority DESC, char_length DESC LIMIT 1`, a high-priority short rule will win and stamp the wrong `category_code` on essentially every slip during `/slips/rematch-all`. That silently corrupts the category breakdown that feeds P&L/exports. An empty `match_value` (`''`) makes `ILIKE '%%'` match literally everything.

**Suggested fix:**
- In `create_statement_rule`, reject `match_value.strip()` shorter than e.g. 2–3 chars (Thai keywords are short, so pick the threshold with TUM; even 2 is safer than 0/1), and reject empty.
- Optionally warn when a new keyword rule would match an outsized share of existing rows.

**Test plan:**
- POST `{rule_type:'keyword', match_value:'ค', direction:'expense', category_code:'rent'}` → expect 400 after fix (currently 200).
- POST with `match_value:'   '` → expect 400 (currently stores `''`, an everything-matcher).

---

## [M1] Supplier month bucketing uses `timedelta(days=30)` → month drift / mislabeled trend columns

**File:** `supplier_routes.py:129, 195-198, 338, 343`

**Current code:**
```python
date_from = date(today.year, today.month, 1) - timedelta(days=months * 30)
...
m_date = date(today.year, today.month, 1) - timedelta(days=i * 30)
month_list.append(f"{m_date.year}-{m_date.month:02d}")
```

**Issue:**
Subtracting `i*30` days from the 1st of the current month does not land on the 1st of prior months. Over 6–24 months the cumulative drift skips or duplicates a month label. Example from a 31-day-heavy stretch: stepping back 30/60/90… days can produce `['2026-05','2026-04','2026-02', ...]` (March missing) or repeat a month. The trend pivot then drops a real month's spend or buckets it under the wrong column, so the chart TUM reads for "is this supplier getting more expensive" is wrong. `/supplier/top` and `/price-trend` share the same `date_from` drift on the lower bound (can under/over-include a few days at the window edge).

**Suggested fix:** Use proper month arithmetic (e.g. `dateutil.relativedelta(months=i)` or a manual year/month decrement) to build both `month_list` and `date_from`.

**Test plan:** Run `/supplier/trend?months=6` on the 31st of a month and assert `month_list` is exactly the 6 consecutive calendar months with no gaps/dupes.

---

## [M2] `statement_by_category` / `statement_unmatched` exclude `needs_review` but not `rejected`, and only `by-category` is direction-agnostic

**File:** `slip_routes.py:1486, 1532`

**Current code:**
```sql
WHERE bse.match_status != 'needs_review'      -- by-category
...
WHERE ... AND bse.match_status != 'needs_review' AND bse.direction='expense'  -- unmatched
```

**Issue:**
`bank_statement_entries.match_status` allows `auto / manual / needs_review` (migration 16 L25–26) — there is no `rejected`, so that part is fine. But `statement_by_category` sums BOTH income and expense rows grouped by `direction`; a caller that totals the returned `total` across rows without separating direction will net income against expense. More importantly, `statement_by_category` includes `direction='income'` rows in a view whose slip-count/invoice-count columns only make sense for expenses (slips are outgoing per the L2/L3 comment at `slip_routes.py:357`). Low risk of a 500, but the totals can be misread.

**Suggested fix:** Either filter `by-category` to `direction='expense'` for parity with `unmatched`, or clearly document that consumers must group by `direction`. No code bug, but a reporting-correctness footgun.

**Test plan:** Verify the frontend consuming `/statement/by-category` separates income vs expense before showing a single "total".

---

## [M3] Slip duplicate fingerprint uses `ABS(amount-%s) <= 0.01` but ref_no path is exact — OCR rounding can dupe

**File:** `slip_routes.py:273-298`

**Issue:**
Priority 1 dedupe is exact `ref_no = %s`. If GPT Vision drops or mis-OCRs one char of the ref_no on a re-upload (common on screenshots), Priority 1 misses and falls to the `(transfer_date, amount, recipient_name)` triple. That triple is reasonable, but `recipient_name` is also OCR-derived and `COALESCE(...,'')` equality is strict — a one-character OCR difference in the recipient name on the second upload defeats dedupe and creates a second slip that fights over the same statement (the exact failure the docstring says it prevents). Net effect: occasional duplicate slips → double-counted in `/statement/by-category` slip_count and potential double 3-way match.

**Suggested fix:** Loosen the fallback to not require recipient_name equality when ref_no is absent (date+amount within 0.01 is already strong for a small shop), or normalize recipient_name before comparison.

**Test plan:** Upload the same slip twice with a deliberately altered recipient_name on the 2nd → assert it is still detected as duplicate.

---

## [M4] `_match_slip` ambiguity guard does not re-clear a prior link

**File:** `slip_routes.py:559-574`

**Issue:**
When >1 statement candidates are found, the code sets `match_status='needs_review'` but does NOT null out `matched_statement_id` / `matched_invoice_id`. If a slip was previously auto-matched (say after one statement import) and a later import adds a second near-identical statement row, a rematch flips status to `needs_review` while leaving the stale `matched_statement_id` populated. Downstream `/statement/by-category` still counts that slip against the old statement, and `/slip/{id}` shows a "matched" statement under a `needs_review` badge — contradictory state TUM may act on.

**Suggested fix:** In the `len(stmt_candidates) > 1` branch, also set `matched_statement_id=NULL, matched_invoice_id=NULL` so `needs_review` means "unlinked, pick one".

**Test plan:** Auto-match a slip, then insert a second matching statement row, rematch → assert `matched_statement_id IS NULL` and status `needs_review`.

---

## [M5] `/bills/payment/line-alert` and slip-match ignore `branch_code`; alert has no upper amount/age sanity

**File:** `bill_payment_routes.py:286-292, 426-435`

**Issue:**
`list_bills_payment` and `summary` filter by branch (`COALESCE(branch_code,%s)=%s`), but `line-alert` and `slip-match` query `vendor_bills` with NO branch filter. For a single-branch shop today this is harmless, but the moment a second branch's bills land in the same table, the Monday LINE alert and the slip candidate list will leak the other branch's unpaid bills, and slip-match could match a slip to the wrong branch's invoice. Inconsistent with the rest of the module.

**Suggested fix:** Add the same `COALESCE(branch_code, DEFAULT_BRANCH) = %s` filter (default to `DEFAULT_BRANCH`) to both queries for consistency.

**Test plan:** Insert an unpaid bill with a different `branch_code`; assert it does not appear in the default-branch slip-match candidates.

---

## [M6] `slip-match` MIME inference defaults non-jpg to PNG; webp/heic sent to Vision as image/png

**File:** `bill_payment_routes.py:400-403`

**Current code:**
```python
mime = "image/jpeg" if fname.endswith((".jpg", ".jpeg")) else "image/png"
```

**Issue:**
Any non-`.jpg` file (`.webp`, `.heic`, an extensionless LINE download) is labelled `image/png` in the data URL. If the bytes are actually webp/heic, OpenAI may reject or mis-read, surfacing as a generic 500 ("ไม่สามารถอ่านสลิปได้"). The newer `slip_routes.slip_upload` correctly uses `file.content_type` and accepts webp — `bill_payment_routes.slip-match` is the older/inconsistent path.

**Suggested fix:** Use `file.content_type` (fallback to extension) and accept `image/webp` like `slip_upload` does.

**Test plan:** POST a `.webp` slip to `/bills/payment/slip-match` → assert it OCRs instead of 500.

---

## [L1] `_month_bounds` ignores `branch` typing inconsistency; `summary` dict seeded from a set

**File:** `bill_payment_routes.py:158-161`

**Issue:** `summary = {s: 0.0 for s in VALID_STATUSES}` iterates a `set`, so key order is non-deterministic across runs (cosmetic in JSON). `summary.get(s, 0.0)` also tolerates a `payment_status` not in `VALID_STATUSES` by creating a new key, so a stray DB value (e.g. legacy `'credit'`) silently appears as its own bucket and is excluded from the four known buckets the UI sums. Polish/robustness only.

**Suggested fix:** Seed from a fixed list and coerce unknown statuses into a known bucket or an explicit `"other"`.

**Test plan:** Set a bill's `payment_status` to an unexpected value, GET `/bills/payment` → confirm UI total still reconciles.

---

## [L2] Search/empty-hints LIKE injection of `%`/`_` is unescaped (no SQL injection, but wildcard leakage)

**File:** `phase11_search_routes.py:153, 332, 357`; `supplier_routes.py:307, 366`

**Issue:** User/AI-supplied keywords are wrapped as `f"%{kw}%"` and passed to `ILIKE`. A keyword containing `%` or `_` is treated as a wildcard, so a search for `50%` or `a_b` matches far more than intended. Parameterization prevents injection (safe), but results can be confusingly broad. Low impact for Thai-text searches.

**Suggested fix:** Escape `%`, `_`, `\` in the keyword before interpolation, or use `ILIKE ... ESCAPE`.

**Test plan:** Search `/search/empty-hints?q=%` → assert it doesn't return the whole vendor table as fuzzy matches.

---

## [L3] `export` builders reuse independent `v_daybook` queries with no equity exclusion guard documented

**File:** `export_routes.py:188-194, 207-217, 300-317, 378-389, 504-517, 650-686`

**Issue:** CLAUDE.md rule: P&L queries over `v_daybook` must exclude `source IN ('owner_capital','owner_advance','transfer_error')` (Session 6 negative-expense incident). The export queries filter on `direction='expense'` and `category_code` but do NOT exclude those equity/error sources. The category-summary "no-budget" rollup (`L202-218`) and `/export/summary` totals (`L650-686`) will fold owner capital/advance and transfer-error rows into expense/spend totals if any such row has `direction='expense'` and a `category_code`. The `daybook` sheet intentionally shows everything (acceptable for a ledger), but the *summary/category* totals should match the P&L convention. Whether this currently mis-totals depends on whether equity rows carry `direction='expense'` + a category — verify against data.

**Suggested fix:** Add `AND source NOT IN ('owner_capital','owner_advance','transfer_error')` to the category-summary aggregate, `/export/summary` totals, and (if equity is ever expense-directed) the daybook totals — matching the documented P&L rule.

**Test plan:** Insert an `owner_advance` expense-directed row with a category_code; compare `/export/summary` total_expense against the canonical P&L endpoint for the month — assert they agree.

---

## Verified NOT bugs (do not "fix")

- `bank_statement_entries.amount` / `.direction` — real STORED generated columns (`migrations/16_bank_statement.sql`). `SUM(bse.amount)` in `slip_routes.py:1479` is valid.
- `supplier_routes.py:355 SUM(ii.quantity)` — `invoice_items.quantity` is real (`main.py:598/1054`). (`ii.qty` elsewhere refers to a different table.)
- `supplier_summary` pct: `round(spend/grand_total*100,1) if grand_total>0 else 0` — div-by-zero guarded.
- `export` `pct_used`: `CASE WHEN b.amount = 0 THEN NULL` — div-by-zero guarded.
- Slip dedupe / matcher `ABS(amount-...) <= 0.01` and `<= 1.00` — uses epsilon, not float `==`. Good.
- Auth header omission — false positive per scope (global JWT middleware in main.py).
