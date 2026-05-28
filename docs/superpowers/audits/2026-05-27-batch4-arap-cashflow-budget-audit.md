# Batch 4 Audit — AR/AP + Cashflow + Budget (Money-First, Read-Only)

Date: 2026-05-27
Scope: `phase3_arap_routes.py`, `cashflow_routes.py`, `budget_routes.py` (backend) +
`app/ar-ap/page.tsx`, `app/cashflow/page.tsx`, `app/budgets/page.tsx`, `app/budget/page.tsx` (frontend).
Auth headers treated as FALSE POSITIVE (global AuthProvider interceptor) — not flagged.

## Summary

| Severity | Count | IDs |
|---|---|---|
| CRITICAL | 3 | C1, C2, C3 |
| MEDIUM | 5 | M1, M2, M3, M4, M5 |
| LOW | 4 | L1, L2, L3, L4 |

Headline: **C1** crashes the entire `/budget/suggest` endpoint with hallucinated `vendor_bills` columns (`vb.confirmed`, `vb.direction` — neither exists; the column is `review_status`, and there is no `direction`). **C2** is a silent-failure class in `app/budget/page.tsx` where every write (`saveBudget`, `applyAll`, `checkAlerts`) ignores `res.ok`, so a 500 looks like success and budgets the owner thinks are set are not. **C3** is an endpoint/contract mismatch: `app/budgets/page.tsx` calls `/budgets*` with a completely different field shape than what `budget_routes.py` (`/budget/*`) serves.

## ✅ Closure status (Session 44, 2026-05-28)

- **C1** `/budget/suggest` hallucinated columns — ✅ fixed in `766bdc0` (use `review_status='confirmed'`, drop nonexistent `direction`)
- **C2** `app/budget/page.tsx` silent-success on write — 🟡 **DEFERRED** (frontend res.ok hardening; the safeFetch refactor in `295de44` covered the READ paths but write paths in `/budget` are still inline)
- **C3** `/budgets` vs `/budget/*` contract mismatch — 🟡 **DEFERRED** (needs verification of whether `/budgets` router exists separately, then route alignment)

---

## [C1] `/budget/suggest` queries non-existent vendor_bills columns → 500 on every call

File: `budget_routes.py:272-287`

Current code:
```python
cur.execute("""
    SELECT
        vb.category_code,
        COALESCE(ec.name_th, vb.category_code) AS category_name_th,
        DATE_TRUNC('month', vb.bill_date)::date AS month_dt,
        SUM(vb.amount)::numeric AS month_total
    FROM public.vendor_bills vb
    LEFT JOIN public.expense_categories ec ON ec.code = vb.category_code
    WHERE vb.branch_code = %s
      AND vb.confirmed = TRUE          -- <-- column does not exist
      AND vb.direction = 'expense'     -- <-- column does not exist
      AND vb.bill_date >= %s
      AND vb.bill_date < %s
      AND vb.category_code IS NOT NULL
    GROUP BY vb.category_code, ec.name_th, DATE_TRUNC('month', vb.bill_date)::date
""", (branch_code, start_date, end_date))
```

Issue:
- Verified schema (CLAUDE.md cheat sheet) for `vendor_bills`: `review_status` (`needs_review`/`confirmed`/`rejected`), `payment_status` (`unpaid`/`paid`), `paid_date`, `amount`, `bill_date`, `due_date`, `category_code`, `branch_code`. **There is NO `confirmed` boolean and NO `direction` column.**
- Every other file in the repo uses the correct form — `bill_payment_routes.py:145`, `inventory_forecast_routes.py:127`, `main.py:916`, `line_bot_routes.py:651` all use `vb.review_status = 'confirmed'`. `budget_routes.py:281-282` is the lone outlier.
- psycopg2 raises `UndefinedColumn`; this query is the first of three in `/budget/suggest`, so the endpoint 500s before any data is read. The "🤖 แนะนำงบ" (AI budget suggest) button in `app/budget/page.tsx` is dead — and because the frontend swallows the error (see C2/M1), the modal just shows "ไม่มีข้อมูลย้อนหลัง", silently hiding the crash. This is exactly the Session-18 silent-404 class.

Suggested fix:
```python
      AND vb.review_status = 'confirmed'
      -- drop the vb.direction filter entirely; vendor_bills are expenses by nature.
      -- (If income bills ever land in vendor_bills, gate on category instead.)
```
Remove the `vb.direction = 'expense'` line. `vendor_bills` is an expense-only table, so the filter is both wrong and unnecessary.

Test plan:
1. `python -c "import ast; ast.parse(open('budget_routes.py', encoding='utf-8').read())"`.
2. Live: `GET /budget/suggest?month=2026-05&lookback=3` should return 200 with `suggestions[]` populated, not 500.
3. Cross-check the returned `avg_monthly` against a manual `SELECT SUM(amount) ... WHERE review_status='confirmed'` for one category.
4. Confirm the manual_entries + bank_statement_entries sub-queries (which use real columns) still merge correctly.

---

## [C2] `app/budget/page.tsx` writes ignore `res.ok` — failed budget saves look successful

File: `app/budget/page.tsx:105-124` (`saveBudget`), `138-163` (`applyAll`), `165-182` (`checkAlerts`); reads `82-93`, `95-101`.

Current code (saveBudget):
```python
await fetch(`${API}/budget/targets`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ month, category_code: editCat, amount: parseFloat(editAmount), notes: editNotes || null }),
})
setEditOpen(false)
setEditCat(''); setEditAmount(''); setEditNotes('')
await loadStatus()
```

Issue:
- No `res.ok` check anywhere in this file. A 422 (validation), 500 (SQL error), or 401 returns a non-2xx response, but the code proceeds to close the modal, clear fields and toast nothing — the owner believes the budget was set when it was not. For a money-control screen this is a real wrong-decision risk: the owner stops watching a category they think is capped.
- `loadStatus` (line 82) also doesn't check `res.ok` and only populates rows when `data.success` is truthy — a 500 (no `success` key) silently leaves the table empty/stale with no error banner, unlike `budgets/page.tsx` which does surface errors.
- `applyAll` (line 138) loops fetches with `try/catch` that only `console.error` — a partial failure reports `✅ ตั้งงบสำเร็จ N หมวด` counting attempts that returned 500.
- This is the documented frontend anti-pattern (VEXONHQ CLAUDE.md pitfall #1, Session 18).

Suggested fix: after each fetch, `if (!res.ok) throw new Error(...)`; surface via `setAlertMsg`/a red toast. In `applyAll`, only increment `ok` when `res.ok`, and report failures. In `loadStatus`, set an error state on `!res.ok` instead of silently leaving rows empty.

Test plan:
1. Point `NEXT_PUBLIC_API_URL` at a backend returning 500 for `/budget/targets`; confirm the UI shows an error instead of "บันทึก" success.
2. With C1 unfixed, open the AI suggest modal and confirm an error is now visible (regression guard for the silent-hide).
3. `npm run lint && npx tsc --noEmit && npm run build`.

---

## [C3] `app/budgets/page.tsx` calls `/budgets*` with a field shape the audited backend does not serve

File: `app/budgets/page.tsx:69, 133, 159, 178, 186` vs `budget_routes.py` (prefix `/budget`).

Current code (budgets/page.tsx):
```python
fetch(`${API_URL}/budgets/status?month=${month}`)        // line 69
fetch(`${API_URL}/budgets`, { method: 'PUT', ... })       // line 133
fetch(`${API_URL}/budgets/${row.budget_id}`, { method:'DELETE' }) // line 159
// PUT body: { branch_code, category_code, period_month, amount_limit, alert_at_pct }
// expects rows: { amount_limit, spent, usage_pct, alert_at_pct, budget_id, status:'ok'|'warn'|'over'|'no_budget' }
```
vs the audited backend (`budget_routes.py`):
```python
@router.get("/targets")   # prefix /budget  ->  /budget/targets
@router.put("/targets")   # body: { month, category_code, amount, branch_code, notes }
@router.get("/status")    # rows: { budget_amount, actual_amount, variance, pct_used, status:'ok'|'warning'|'over' }
```

Issue:
- Two budget pages exist (`/budget` legacy and `/budgets` Phase 24b, per VEXONHQ CLAUDE.md layout). `app/budget/page.tsx` correctly targets `budget_routes.py` (`/budget/targets`, `/budget/status`, fields `amount`/`budget_amount`/`pct_used`). `app/budgets/page.tsx` targets a *different* router (`/budgets`, `/budgets/status`) with fields `amount_limit`/`alert_at_pct`/`period_month`/`spent`/`usage_pct` and status value `warn` (not `warning`).
- This means `/budgets*` is served by a router OUTSIDE the three audited backend files. If that router does not exist or drifted, the entire `/budgets` page silently shows empty (it does check `res.ok` and surfaces errors — better than C2 — but a contract drift on field names would render `(ไม่มีงบ)` / `-` everywhere while the data is actually present under different keys).
- Flagging as CRITICAL-for-verification: the audited budget backend cannot satisfy this page. The money risk is the same as C1/C2 — a budget screen that looks empty/broken erodes trust and the owner stops using budget control. Confirm `/budgets*` router still exists and its field names match before relying on this page.

Suggested fix: out of audited scope to edit, but verify the `/budgets` router (likely a separate `budgets_routes.py`) exists and its response keys match the `BudgetRow`/`StatusResponse` types at `budgets/page.tsx:28-41`. If only `budget_routes.py` survives, repoint `budgets/page.tsx` to `/budget/*` and remap field names, or retire one of the two pages.

Test plan:
1. `GET /budgets/status?month=2026-05` and `GET /budget/status?month=2026-05` — confirm which exist (200) and diff their JSON keys against each page's TypeScript types.
2. Load `/budgets` in the browser; confirm rows show real `งบ`/`ใช้แล้ว`/`%` not `(ไม่มีงบ)`/`-`.

---

## [M1] Cashflow forecast: aggregates do not use `v_daybook_pnl`; rely on a hand-maintained source exclusion list

File: `cashflow_routes.py:59-70, 80-92, 191-201`

Current code:
```python
FROM public.v_daybook
WHERE entry_date BETWEEN %s AND %s
  AND source NOT IN ('owner_capital', 'owner_advance', 'transfer_error',
                'bank_statement', 'vendor_payment',
                'grab_payout', 'lineman_payout',
                'pos_cash_deposit', 'cash_withdrawal')
```

Issue:
- Equity/transfer leak is correctly handled here (the three equity sources ARE excluded), so this is NOT a Session-6 equity-leak crash — downgraded to MEDIUM. But the exclusion is a hard-coded 9-item denylist repeated in 3 places. The schema now ships `v_daybook_pnl` (= `v_daybook` minus equity/transfer) as the P&L source of truth. Any new equity-like source added to `v_daybook` will silently leak into the average-daily income/expense and the cash position until someone remembers to edit all 3 lists.
- The list is also stricter than P&L (it additionally strips bank_statement, payouts, deposits, withdrawals) to approximate operating cash, so a naive swap to `v_daybook_pnl` would change numbers. This needs a deliberate decision, not a blind replace.

Suggested fix: either (a) source the operating-cash rows from `v_daybook_pnl` plus the extra cash-specific exclusions, or (b) centralize the denylist in one module constant and reference it from all 3 queries so it can't drift. Confirm with TUM which semantics (P&L vs operating-cash) the forecast should use.

Test plan: insert a test `owner_capital` row in the lookback window; confirm `avg_daily_income` is unchanged. Compare `/cashflow/summary.mtd_income` against the P&L page for the same month.

---

## [M2] Cashflow `active_days` divisor undercounts → average daily income/expense overstated

File: `cashflow_routes.py:63, 74-77`

Current code:
```python
COUNT(DISTINCT entry_date) AS active_days
...
active_days = int(row[2] or 1) or 1  # avoid div/0
avg_daily_income  = total_inc / active_days
avg_daily_expense = total_exp / active_days
```

Issue:
- The divisor is the count of DISTINCT days that had any non-excluded row, NOT the 30-day lookback window. If the shop only recorded entries on 20 of the last 30 days, the 30-day totals get divided by 20, inflating `avg_daily_income`/`avg_daily_expense` by ~50%. The forecast then projects every future day at that inflated rate, overstating both income and expense and distorting `cumulative_net` / `first_negative_date` — the exact number the owner uses to decide when to pay AP bills.
- div/0 itself is guarded (good).

Suggested fix: divide by the fixed lookback length (30) — or by the count of *calendar* days in the lookback that the business was open, not the count of days that happen to have data. Simplest correct: `avg = total / 30`. Document the choice.

Test plan: with data on only 15 of 30 days, confirm `avg_daily_income` ≈ total/30, not total/15. Eyeball forecast against last month's actual daily mean.

---

## [M3] Cashflow forecast double-counts AP bills that are also in the daily expense average

File: `cashflow_routes.py:96-135`

Current code:
```python
proj_expense = avg_daily_expense + ap_spike   # base average + known AP bills
```

Issue:
- `avg_daily_expense` is derived from `v_daybook` over the last 30 days. AP bills are tracked in `ar_ap_entries`. If a vendor bill flows into `v_daybook` as an expense (e.g. on confirmation/payment) AND is also still `pending`/`partial` in `ar_ap_entries`, the recurring spend is counted once in the rolling average and again as an `ap_spike`, overstating projected expense and pulling `first_negative_date` earlier than reality. For a 30-day Makro credit cycle this is plausible. Conversely, if AP bills never hit `v_daybook` until paid, there is no double count — depends on the daybook source mapping.

Suggested fix: confirm whether `ar_ap` accruals appear in `v_daybook`. If yes, exclude the AP-linked source from the rolling average (or subtract recurring AP from the base) so each baht is counted once. If no, add a code comment documenting that the two streams are disjoint, so a future dev does not "fix" it by removing the spike.

Test plan: pick a known recurring vendor; trace one bill through `v_daybook` and `ar_ap_entries` for the same period; confirm it contributes to exactly one of (avg_daily_expense, ap_spike).

---

## [M4] AR/AP overpayment guard uses float epsilon — can accept a 0.01 overpay; outstanding can be reported as a tiny negative elsewhere

File: `phase3_arap_routes.py:552-558, 603, 646`

Current code:
```python
if float(paid_before) + body.amount > float(total) + 0.01:
    raise HTTPException(400, "Payment would exceed total. ...")
...
"amount_outstanding": float(new_total) - float(new_paid),
```

Issue:
- The `+ 0.01` tolerance lets a payment overshoot the total by up to 1 satang, so `amount_outstanding` (line 603/646) can be returned as a small negative (`-0.01`), which the UI renders via `currency2.format(-0.01)` as `-฿0.01` on the AR/AP card. Cosmetic, but on a money screen a negative "ยอดค้าง" looks like a bug to the owner. The frontend mirror at `ar-ap/page.tsx:792` uses the same `+ 0.01` so the two agree, but neither clamps the result.
- Not a wrong-money-owed crash, hence MEDIUM.

Suggested fix: clamp `amount_outstanding = max(0.0, total - paid)` in the backend response, and/or tighten the epsilon to a true rounding guard. Keep client and server epsilons identical.

Test plan: create a 100.00 entry, pay 100.005 (rounded to 100.01 by the input); confirm outstanding shows `฿0.00`, not `-฿0.01`.

---

## [M5] Cashflow `thaiDate` indexes month array with no bounds/`NaN` guard

File: `app/cashflow/page.tsx:61-66`, used at lines 72, 205, 247, 297

Current code:
```python
function thaiDate(yyyymm: string) {
  const months = ['', 'ม.ค.', ...];           // index 0..12
  const [y, m, d] = yyyymm.split('-').map(Number);
  return `${d} ${months[m]}`;
}
```

Issue:
- If `date`/`first_negative_date` is ever malformed or undefined (e.g. backend shape drift, or the XAxis `tickFormatter` receiving an unexpected label), `m` is `NaN` and `months[NaN]` is `undefined`, rendering `"undefined undefined"` in the chart axis, tooltip header, and warning banner. `d` likewise can be `NaN`. The parameter is named `yyyymm` but actually receives full `YYYY-MM-DD` strings (the destructure takes `d`), so the naming is also misleading.
- Low blast radius (data comes from the same backend), hence MEDIUM not CRITICAL.

Suggested fix: guard — `if (!yyyymm) return '—'; const parts = yyyymm.split('-').map(Number); const m = parts[1]; if (!m || m < 1 || m > 12) return yyyymm;`.

Test plan: pass `''` and `'2026-13-40'`; confirm no `"undefined"` leaks to the DOM.

---

## [L1] Cashflow weekend heuristic scales income but not expense

File: `cashflow_routes.py:127-135`

`proj_income = avg_daily_income * (0.8 if is_weekend else 1.0)` but `proj_expense` has no weekday factor. For a restaurant, weekends are usually *higher* revenue, so an 80% weekend income haircut is likely backwards, and expense staying flat is an unmodeled assumption. Magic constant `0.8` is undocumented. Low because it is a rough forecast, not an accounting figure. Fix: make the factor a named constant with a comment, or drop it until validated against POS day-of-week data (the `/pos/dow` endpoint already has the real multipliers).

## [L2] Budget LINE-alert UI text says "≥90%" / "≥80%" inconsistently; backend warning threshold is ambiguous

File: `app/budget/page.tsx:277` ("ใกล้เกิน (≥90%)") vs `budget_routes.py:206` ("ใกล้เต็มงบ (≥80%)") and docstring `budget_status` line 132 ("warning (≥90%)").

The warning percentage shown to the owner differs across the LINE message (80%) and the on-screen summary card (90%), and the actual threshold lives in `v_budget_status` (not in these files, so unverifiable here). Whatever the real cutoff, two of the three labels are wrong. Fix: confirm the threshold in `v_budget_status` and make all three labels match.

## [L3] Budget `/suggest` rounds suggested amount in a way that can yield 0 for small spends

File: `budget_routes.py:357`

`suggested = round(avg * (1 + buffer_pct / 100) / 100) * 100` rounds to the nearest 100 baht. For a category averaging < 50 baht/month, this rounds to 0, producing a suggested budget of ฿0 which `applyAll` would then PUT as a real 0 budget (treated as "no cap" / instant-over depending on the view's divide-by-zero handling). Low frequency for this shop. Fix: floor at 100 (`max(100, ...)`) or skip categories rounding to 0.

## [L4] `app/budget/page.tsx` row tint uses malformed Tailwind classes

File: `app/budget/page.tsx:312` (`'bg-red-500/10/40'`, `'bg-amber-500/10/30'`)

`bg-red-500/10/40` is not a valid Tailwind opacity class (double slash) — the over/warning row tint is silently dropped, so over-budget rows are not visually highlighted in the table body. Pure cosmetic. Fix: `bg-red-500/10` and `bg-amber-500/10`.

---

## Notes / non-findings (checked, OK)

- AR/AP duplicate-payment guard (`phase3_arap_routes.py:560-575`) and cancelled-entry pay guard (550) are correct.
- `ar_ap_summary` (657-690) always returns both directions zero-filled — no silent-zero hiding a failed query; it reads a pre-aggregated view.
- AR/AP frontend (`ar-ap/page.tsx`) checks `res.ok`, has loading/error/empty states, guards the progress bar against `amount_total > 0` (line 542), and formats THB via `Intl`. Clean.
- `app/budgets/page.tsx` checks `res.ok` on all calls and surfaces errors (contrast with C2) — its only risk is the C3 contract mismatch.
- Cashflow div/0 on `active_days` is guarded; `budget` summary `pct` guards `totalLimit > 0` (budgets/page.tsx:100) and `formatPercent` guards non-finite.
- Equity sources (`owner_capital`, `owner_advance`, `transfer_error`) ARE excluded in every cashflow aggregate — no Session-6 equity leak in the audited cashflow code.
