# Batch 5 — POS Analytics Audit (`menu_routes.py`)

Date: 2026-05-27
Scope: `C:\Users\rapee\vexonhq-ocr-api\menu_routes.py` (4476 lines, full read in chunks). Backend only, READ-ONLY. Money/decision math endpoints under `/menu/*`, `/pos/*`, `/delivery/*`, `/revenue/*`, `/alerts/*`, `/scorecard`.
Schema cross-checked against `pos_import.py` (writer of `pos_sales_daily`, `pos_inventory_items`), `phase3a_anomaly_routes.py` (writer of `bill_anomalies`), and the CLAUDE.md verified cheat sheet.

---

## Summary

| Severity | Count | Theme |
|---|---|---|
| CRITICAL | 4 | 1 hallucinated column (silently kills anomaly alerts); 3 void-leak classes that overstate revenue/qty/discount driving decisions |
| MEDIUM | 7 | inclusive `BETWEEN ... today` partial-day/range bugs; void leak on price history; suppressed `except: pass`; combo lift edge case |
| LOW | 3 | dead variable, label/bucket polish, redundant filter |

Headline:
- **[C1] `a.mean_amount` is a hallucinated column** in `/alerts/summary` (line 1347). It does NOT exist on `bill_anomalies` (real column is `category_mean`). It does not 500 because the block is wrapped in `except Exception: pass` — instead **the entire "บิลผิดปกติ" anomaly feed silently disappears from the Alert Center**. Worst kind: looks healthy, shows nothing.
- **[C2/C3/C4] Void leak in item-level analytics.** Many item/category/combo/price queries JOIN `pos_sales_items → pos_bills` but omit `bill_net > 0`. Voided bills (net=0, gross>0) still carry line items, so their qty + `net_amount`/`unit_price*qty` inflate "top sellers", category mix, food-cost revenue base, and discount totals. These numbers drive menu and pricing decisions.

NOTE on hallucinated columns: the historical offenders (`net_price`, `b.status`, `b.branch`, `staff`, `r.menu_name`, `ri.quantity`) were searched. `menu_routes.py` is clean of those EXCEPT it correctly uses `bill_net`, `branch_code`, `opened_by`, and aliases `r.name AS menu_name` (line 3982 — alias only, not a column reference, OK). The one genuine hallucinated column is `a.mean_amount` ([C1]). `pos_sales_daily.net_total`/`bill_count` and `pos_inventory_items.qty_in_stock` were verified REAL via `pos_import.py`.

---

## CRITICAL

### [C1] `/alerts/summary` — hallucinated column `a.mean_amount` silently kills anomaly alerts
- Endpoint: `GET /alerts/summary`
- File:line: `menu_routes.py:1345-1357` (SELECT), specifically `:1347`
- Current code:
```python
anom_sql = """
    SELECT a.id, a.severity, a.anomaly_type, a.message,
           a.bill_amount, a.mean_amount, a.created_at,        -- line 1347
           vb.vendor_name, vb.bill_date, vb.category_code
    FROM public.bill_anomalies a
    JOIN public.vendor_bills vb ON vb.id = a.bill_id
    ...
"""
anom_rows = _rows_to_dicts(conn, anom_sql, ())
...
except Exception:
    pass            # line 1371-1372
```
- Issue: `bill_anomalies` has **no `mean_amount` column**. The writer (`phase3a_anomaly_routes.py:204-208`) inserts `category_mean`, `category_stddev`, `category_n`, `category_p50/p95/p99`, `zscore`, `bill_amount` — there is no `mean_amount`. The query raises `psycopg2.errors.UndefinedColumn`, which is caught by the bare `except Exception: pass` at line 1371. Result: the anomaly section of the Alert Center is **always empty**, regardless of how many real anomalies exist. `mean_amount` is also never read in the loop body (lines 1358-1370), so it is pure dead-and-wrong SQL. This is the exact failure class flagged in CLAUDE.md pitfall #1 — and the swallow means it has likely been silently broken in production.
- Suggested fix: drop the column (it is unused), or rename to the real column:
```python
       a.bill_amount, a.category_mean, a.created_at,
```
  Independently, replace `except Exception: pass` with logging (see [M6]) so the next hallucinated column surfaces instead of hiding.
- Test plan: seed one row in `bill_anomalies` with `user_action IS NULL` (or trigger `/anomaly/scan`). `curl /alerts/summary` and assert an `"type":"anomaly"` entry appears in `alerts`. Before the fix it returns zero anomaly entries; after, it returns the row. Also run `psql -c "\d public.bill_anomalies"` and confirm no `mean_amount`.

---

### [C2] Item-level "top sellers" / trends count VOID bills — revenue & qty overstated
- Endpoints: `GET /menu/performance`, `GET /menu/trends`, `GET /pos/items`, `GET /pos/menu-engineering`
- File:line:
  - `/menu/performance` top/bottom/category/totals — `menu_routes.py:143-214` (4 queries, all JOIN `pos_bills pb` on `sales_date` window, **no `pb.bill_net > 0`**)
  - `/menu/trends` — `menu_routes.py:280-288` (no `pb.bill_net > 0`)
  - `/pos/items` cur/prev/sparkline/cat — `menu_routes.py:2250-2296` (JOIN `pos_bills b`, no `b.bill_net > 0`)
  - `/pos/menu-engineering` — `menu_routes.py:1808-1819` (no `pb.bill_net > 0`)
- Current code (representative, `/menu/performance` top items, 143-152):
```python
   FROM public.pos_sales_items si
   JOIN public.pos_bills pb ON pb.id = si.bill_id
   WHERE pb.branch_code = %s
     AND pb.sales_date >= %s
     AND pb.sales_date < %s
     AND si.item_name IS NOT NULL ...
```
- Issue: A voided bill has `bill_net = 0` but its `pos_sales_items` rows are NOT deleted (they retain `qty`, `net_amount`, `unit_price`). Because these queries filter only on the bill date window and never on `pb.bill_net > 0`, every voided bill's items are summed into `total_qty`, `total_revenue`, `order_count`, popularity index, and the Star/Plowhorse/Puzzle/Dog classification. This systematically overstates item revenue/popularity and skews menu-engineering quadrants — a direct money/decision distortion. (`/pos/voids` line 3915-3919 proves voids retain items by counting `si.qty * si.unit_price` on `bill_net = 0` bills.)
- Suggested fix: add `AND pb.bill_net > 0` (alias `b`/`pb` per query) to the WHERE of every `pos_sales_items JOIN pos_bills` aggregation that is meant to reflect real sales. For `/menu/performance` the `net_amount` of void items may also be non-zero; the bill-level filter is the correct guard since voids zero the bill, not the lines.
- Test plan: pick a known void bill (`SELECT id FROM pos_bills WHERE bill_net=0 AND bill_gross>0 LIMIT 1`) and its items. Sum `/menu/performance` `total_revenue` before/after adding the filter; the delta should equal the SUM of that void's `net_amount`. Confirm menu-engineering `avg_qty`/`avg_revenue` shift accordingly.

---

### [C3] Period comparison & daily reports count VOID line items in item/category panels
- Endpoints: `GET /pos/compare`, `GET /pos/calendar`, `GET /pos/flash`, `GET /pos/categories`, `GET /pos/combos`
- File:line:
  - `/pos/compare` top-10 items (2959-2968) and category mix (2972-2982) — JOIN `pos_bills b`, **no `b.bill_net > 0`** (note: the KPI/DOW/order-type panels in the same function DO have `b.bill_net > 0` at 2953/2992/3005, so item & category panels are inconsistent with the rest of the same response).
  - `/pos/calendar` top items (2731-2737) — no `b.bill_net > 0` (the daily/KPI panels at 2702/2720 DO filter).
  - `/pos/flash` top-10 items today (2165-2169 region, 3165-3169) — no `b.bill_net > 0` (hourly/order-type/MTD panels DO filter).
  - `/pos/categories` all 3 queries (2571-2611) — no `b.bill_net > 0`.
  - `/pos/combos` total_bills, item_counts, pairs, top items (2813-2867) — no `bill_net > 0` anywhere; support/lift/confidence denominators include voids.
- Current code (representative, `/pos/compare` top items 2959-2965):
```python
SELECT si.item_name, SUM(si.qty) AS qty, SUM(si.net_amount) AS revenue
FROM pos_sales_items si
JOIN pos_bills b ON b.id = si.bill_id
WHERE b.sales_date BETWEEN %(start)s AND %(end)s
  {branch_sql}
GROUP BY si.item_name
```
- Issue: same void-leak as [C2]. Especially damaging in `/pos/compare` and `/pos/flash` where the headline KPIs are void-filtered but the item/category breakdown is not, so the panels in one screen disagree. In `/pos/combos`, `total_bills` (denominator for support and lift, 2814-2818) counts void bills, deflating support % and distorting lift for every pair.
- Suggested fix: add `AND b.bill_net > 0` to each `pos_sales_items JOIN pos_bills` query and to the `/pos/combos` `total_bills` count (`WHERE sales_date ... AND bill_net > 0`). Keep it consistent with the KPI panels in the same endpoints.
- Test plan: for `/pos/compare`, assert `period.kpi.total_revenue` (filtered) vs `sum(top_items.revenue)` move toward consistency after the fix. For `/pos/combos`, confirm `total_bills` drops by the void count and `support_pct`/`lift` rise correspondingly.

---

### [C4] Discount summaries include VOID bills in gross/discount → discount-rate % wrong
- Endpoints: `GET /pos/payments` (discount block), `GET /pos/discounts`
- File:line:
  - `/pos/payments` discount summary (2037-2074) and monthly disc trend (2077-2099) — `FROM pos_bills b LEFT JOIN (...) si WHERE b.sales_date >= ... < ...` with **no `b.bill_net > 0`**. (The payment-method block above it at 1966-1973 correctly filters `bill_net > 0`, so within the same endpoint the discount section is inconsistent.)
  - `/pos/discounts` summary (3258-3272), by-staff (3290-3305), by-hour (3321-3332), monthly (3340-3353), by-order-type (3369-3381) — `WHERE b.sales_date BETWEEN ...` with **no `b.bill_net > 0`** in any query.
- Current code (`/pos/payments` discount summary 2049-2057):
```python
   FROM public.pos_bills b
   LEFT JOIN ( SELECT bill_id, SUM(discount) AS item_disc
               FROM public.pos_sales_items GROUP BY bill_id ) si
       ON si.bill_id = b.id
   WHERE b.branch_code = %s
     AND b.sales_date >= %s
     AND b.sales_date < %s
```
- Issue: void bills (net=0, gross>0) contribute their `bill_gross` and item-level `discount` into `total_gross` and `total_discount`. `discount_rate_pct = total_discount / total_gross` (2070) and `disc_rate` in the trend (2109, 3361-3363) are therefore computed over a base that includes cancelled sales. `/pos/discounts` additionally derives `gross_revenue = total_net + total_discount` (3276); since voids add 0 net but >0 discount, the discount rate is biased. This % is shown as a KPI and can trigger "staff over-discounting" decisions.
- Suggested fix: add `AND b.bill_net > 0` to all discount-summary/trend/by-staff/by-hour/by-order-type queries, matching the void convention used everywhere else. If the intent is to also see discounts on cancelled bills, that should be a separate, explicitly-labelled metric — not folded into the headline discount rate.
- Test plan: compute `discount_rate_pct` before/after on a month known to contain voids; confirm `total_gross`/`total_net` drop by the void totals and the rate changes. Cross-check `/pos/payments` discount_summary.total_net against `/pos/bill-analysis` total_revenue (which IS void-filtered) — they should match after the fix.

---

## MEDIUM

### [M1] Inclusive `BETWEEN sales_date AND today` truncates "today" and is inconsistent with the `< end` pattern
- Endpoints: `/pos/heatmap`, `/pos/items`, `/pos/bill-analysis`, `/pos/categories`, `/pos/combos`, `/pos/discounts`, `/pos/prices`, `/pos/predict`, `/pos/food-cost` (all the relativedelta endpoints)
- File:line: e.g. `menu_routes.py:2137` (`end = date.today()`) + `:2148` (`WHERE b.sales_date BETWEEN %(start)s AND %(end)s`); same shape at 2228-2229/2252, 2393-2394/2411, 2556-2557/2573, 2804-2805/2816, 3242-3243/3270, 3575-3576/3594.
- Issue: two problems. (1) `sales_date` is a DATE, and `BETWEEN start AND end` with `end = today` includes all of today — fine for DATE, but other endpoints (`/menu/performance`, `/pos/overview`, `/pos/staff-stats`, etc.) deliberately use a half-open `sales_date < end` exclusive window. The mix means two endpoints over "the same period" return different totals. (2) For the relativedelta endpoints, `start = end.replace(day=1) - relativedelta(months=months-1)` but `/pos/categories` and `/pos/combos` use `start = end - relativedelta(months=months)` (2557, 2805) — a different anchor (not start-of-month, full `months` back including a partial leading month). So "3 months" means different ranges across endpoints.
- Suggested fix: standardise on one helper that returns a half-open `[start_of_first_month, first_of_next_day_after_today)` window and use it everywhere. At minimum align `/pos/categories` and `/pos/combos` start computation with the day(1)-anchored pattern used by `/pos/items`, `/pos/heatmap`, `/pos/discounts`.
- Test plan: call `/pos/items?months=3` and `/pos/categories?months=3` for the same branch; confirm both cover identical date ranges (log `period.start`/`period.end`). Add an assertion comparing total revenue of overlapping months.

### [M2] `/pos/predict` — DOW labels mislabelled and window-over-aggregate `overall_avg_bills` is unused/misleading
- Endpoint: `GET /pos/predict`
- File:line: `menu_routes.py:3431` and `:3448-3449`, `:3493-3495`
- Current code:
```python
COALESCE(AVG(COUNT(*)) OVER(), 0)   AS overall_avg_bills   -- line 3431
...
DOW_NAMES_TH = {0:'อาทิตย์',1:'จันทร์',...,6:'เสาร์'}      -- Postgres DOW 0=Sun (correct)
...
pg_dow = fdate.weekday() % 7   # line 3493 — DEAD: computed, never used
pg_dow_conv = (fdate.weekday() + 1) % 7   # 0=Sun..6=Sat (correct mapping)
```
- Issue: (a) `overall_avg_bills` is selected (window over aggregate, valid SQL) but never read anywhere in Python — dead column. (b) `pg_dow` at line 3493 is computed and immediately shadowed/ignored (only `pg_dow_conv` is used) — dead and confusing; a future edit could grab the wrong one. The DOW→Thai mapping itself (`pg_dow_conv`) IS correct (Python Mon=0 → Postgres Sun=0 conversion is right), so forecasts land on the right weekday. Severity MEDIUM because output is correct today but the dead/duplicated DOW vars are a trap given the project's DOW-off-by-one history.
- Suggested fix: delete `overall_avg_bills` from the SELECT and delete line 3493 `pg_dow = ...`. Keep `pg_dow_conv`.
- Test plan: `curl /pos/predict?weeks=8`; assert each `forecast[i].dow_name` matches the actual Thai weekday of `forecast[i].date` (compute independently). No behavior change expected from removing dead code; re-run smoke.

### [M3] `/pos/predict` confidence divides by avg but guards only `avg > 0` after the fact
- Endpoint: `GET /pos/predict`
- File:line: `menu_routes.py:3460`
- Current code:
```python
"confidence": max(0, round(100 - (std / avg * 100) if avg > 0 else 0, 0)),
```
- Issue: the `if avg > 0` guard is correctly placed (the division only runs when `avg > 0`), so no ZeroDivision. But operator precedence: `round(100 - (std/avg*100) if avg>0 else 0, 0)` — the ternary binds the whole `100 - (...)` expression vs `0`, which is the intent. OK functionally. The MEDIUM concern: when `std > avg` (highly volatile DOW), `100 - std/avg*100` goes negative and is clamped to 0 by `max(0, ...)` — fine — but `confidence` can also exceed 100 is impossible here. No money impact; flag is low-risk. Downgrade candidate to LOW. Keeping as MEDIUM only because confidence drives a user-facing "trust this forecast" signal.
- Suggested fix: none required for correctness; optionally clamp upper bound and document that confidence is `0` for DOWs with no data.
- Test plan: feed a DOW with one data point (std=0) → confidence 100; a volatile DOW (std>avg) → confidence 0.

### [M4] `/pos/voids` — `rev_loss_pct` denominator can double count, and void definition may miss partial voids
- Endpoint: `GET /pos/voids`
- File:line: `menu_routes.py:3839-3845` (totals), `:3850-3858` (void def), `:3937`
- Current code:
```python
# totals: ALL bills incl voids, but voids have bill_net=0 so SUM(bill_net) excludes them
SELECT COUNT(*) AS total_bills, COALESCE(SUM(bill_net),0) AS total_rev ...   -- no bill_net>0
...
rev_loss_pct = round(void_amount / (total_rev + void_amount) * 100, 2) if (total_rev + void_amount) > 0 else 0.0
```
- Issue: (a) `total_bills` counts every row including voids and zero-net rows, so `void_rate_pct = void_count/total_bills` uses a denominator that mixes real + void + any net=0 non-void bills — acceptable as "share of all tickets" but should be documented. (b) The void definition `bill_net = 0 AND bill_gross > 0` will miss a fully-comped bill where `bill_gross` was also zeroed, and will misclassify a legitimately 0-net promo bill (100% discount) as a void. (c) `rev_loss_pct` denominator `total_rev + void_amount` is a reasonable "what we'd have made" base, but `total_rev` here is over the whole period with no `bill_net > 0` (it relies on voids summing to 0) — correct only as long as ALL voids are exactly net 0. A partially-refunded bill with `bill_net > 0` is not captured at all.
- Suggested fix: document the void heuristic in the response (`"void_definition": "bill_net=0 AND bill_gross>0"`). Confirm with TUM whether 100%-discount promos exist that would be false-positive voids. No crash risk.
- Test plan: count `SELECT COUNT(*) FROM pos_bills WHERE bill_net=0 AND bill_gross>0` vs the endpoint's `void_count`; spot-check a few to confirm they are true cancellations, not 100% promos.

### [M5] `/pos/combos` — lift/support denominators and `item_counts` default mask missing items
- Endpoint: `GET /pos/combos`
- File:line: `menu_routes.py:2820`, `:2874-2878`
- Current code:
```python
total_bills = int(total_row[0]["total_bills"]) if total_row else 1
...
cnt_a = item_counts.get(a, 1) or 1
lift = round((co / total_bills) / ((cnt_a / total_bills) * (cnt_b / total_bills)), 2)
```
- Issue: `item_counts.get(a, 1)` defaults a missing item to 1 occurrence. Because the pair self-join and the `item_counts` query use the same window, items in a pair should always be in `item_counts`, so the default rarely fires — but if it ever does (e.g. NULL item_name differences), lift is silently wrong rather than skipped. Also after fixing [C3], `total_bills` will exclude voids and these denominators must use the same filtered count for lift to be coherent. No ZeroDivision (defaults to 1).
- Suggested fix: when applying [C3], ensure `total_bills`, `item_counts`, and the pair query all share `bill_net > 0`. Replace the silent `, 1` default with a skip/log when an item is unexpectedly absent.
- Test plan: verify `sum over pairs` consistency; assert `lift` for a known pair against a hand computation on filtered data.

### [M6] Suppressed exceptions hide schema/runtime errors in `/alerts/summary`
- Endpoint: `GET /alerts/summary`
- File:line: `menu_routes.py:1371-1372`, `:1417-1418`, `:1456-1457`, `:1491-1492` (four `except Exception: pass`)
- Current code:
```python
except Exception:
    pass
```
- Issue: each of the 4 alert sections (anomalies, budget, AP, stock) swallows all exceptions with no logging. This is exactly what let [C1]'s hallucinated column go unnoticed — a 500-class SQL error becomes "no alerts of that type". Violates CLAUDE.md rule #3 (never suppress errors). Any future bad column, type mismatch, or missing table silently drops a whole alert category with no trace in logs.
- Suggested fix: `except Exception: log.exception("alerts_summary: <section> failed")` (the module already has `log = logging.getLogger("menu_routes")`). Keep the section isolated (don't fail the whole endpoint) but record why.
- Test plan: temporarily point one sub-query at a bad column in a dev env; confirm a stack trace is logged while the endpoint still returns 200 with the other sections.

### [M7] ✅ CLOSED (2026-05-28, doc comment added) — `/scorecard` net_delta / expense ratio divide-by-zero edge + abs() sign flip

> **Outcome**: investigated — not a bug. abs(prev_net) deliberately makes the delta sign report direction-of-improvement (loss shrinking = positive %), divide-by-zero is already guarded by `if prev_net else 0`. Added explanatory comment in `menu_routes.py:1596` for future readers; no behavior change.

— original finding below —


- Endpoint: `GET /scorecard`
- File:line: `menu_routes.py:1587`, `:1711`
- Current code:
```python
net_delta = round((net_profit - prev_net) / abs(prev_net) * 100, 1) if prev_net else 0
...
"status": _score_status(this_exp/this_rev if this_rev else 1, 0.7, 0.85, higher_is_better=False),
```
- Issue: (a) `net_delta` uses `abs(prev_net)` in the denominator — if `prev_net` was negative (a loss) and this month is also a loss but smaller, the sign of the delta can be counter-intuitive (improving from -100k to -50k yields +50% which reads as "up", which is arguably correct, but worth a doc note). No crash (guarded by `if prev_net`). (b) The expense-ratio status passes `1` (i.e. 100% expense ratio → "danger") when `this_rev == 0`; reasonable fallback. Both are guarded against ZeroDivision. MEDIUM because the loss-to-loss delta sign can mislead the scorecard color.
- Suggested fix: document the delta semantics for negative bases, or special-case "loss→loss" to report improvement explicitly. No urgent code change.
- Test plan: feed prev_net = -100000, net_profit = -50000; confirm `net_delta` and the displayed arrow match the intended "improving" semantics.

---

## LOW

### [L1] `/pos/bill-analysis` histogram includes net=0 bills in the 0–99 bucket
- Endpoint: `GET /pos/bill-analysis`
- File:line: `menu_routes.py:2419-2423`
- Current code: `WHERE ... AND bill_net >= 0 AND bill_net < 10000` (histogram uses `>= 0`, while the KPI block above uses `bill_net > 0`).
- Issue: the histogram counts `bill_net = 0` bills (voids/comps) into the `0–99` bucket, while the KPI `total_bills`/`avg` exclude them. Minor visual inconsistency in the first bar.
- Suggested fix: change to `bill_net > 0` for parity with the KPI block.
- Test plan: compare histogram bucket-0 count vs `(KPI total_bills minus bills>=100)`.

### [L2] `_normalize_payment` substring matching can mislabel (`'line'` matches inside unrelated tokens)
- Endpoint: `GET /pos/payments`
- File:line: `menu_routes.py:1939-1946`
- Issue: `if k in key` substring match: key `'line'` → "LINE MAN" would also catch any raw payment string containing "line" (e.g. "online", "headline"); `'bank'`/`'transfer'` similar. Order of `_PAYMENT_LABEL` dict insertion determines first-match, which is fragile. Low risk given FoodStory's controlled `payment_type_raw` vocabulary, but a new payment string could be miscategorised and silently fold revenue into the wrong bucket.
- Suggested fix: match on normalized exact tokens or anchor with word boundaries; or map known raw values explicitly.
- Test plan: feed `payment_type_raw='online'` and confirm it doesn't land in "LINE MAN".

### [L3] `/pos/calendar` uses `cur.rowcount` to guard `_rows_to_dicts(cur)[0]`
- Endpoint: `GET /pos/calendar`
- File:line: `menu_routes.py:2723`
- Current code: `kpi = _rows_to_dicts(cur)[0] if cur.rowcount else {}`
- Issue: `cur.rowcount` after a SELECT is driver-dependent (psycopg2 usually returns the count, but it is not guaranteed for all cursor states). The aggregate `SELECT SUM(...)` always returns exactly one row even when empty, so `[0]` is safe regardless; the `cur.rowcount` guard is redundant and could theoretically be `-1`/`0` on some paths, falling back to `{}` unnecessarily (then all KPIs read 0 — harmless but masks data). Prefer `rows = _rows_to_dicts(cur); kpi = rows[0] if rows else {}`.
- Suggested fix: use `len(rows)` guard like the rest of the file.
- Test plan: `curl /pos/calendar` for a month with data; confirm KPI populated.

---

## Endpoints reviewed and found correct (no findings)
- `/pos/dow-stats` (1: uses `pos_sales_daily.net_total`/`bill_count` — verified real columns; `NULLIF(bill_count,0)` guards per-bill avg; DOW labels Sun=0 correct).
- `/pos/hourly-stats` (per-day and per-bill divisions all guarded `> 0`; `bill_net > 0`; `sales_time IS NOT NULL`).
- `/pos/channel-stats`, `/pos/staff-stats`, `/pos/table-stats`, `/pos/overview` — all use `bill_net > 0`, guard `grand_total > 0` / `days > 0` before dividing, `COALESCE(NULLIF(TRIM(...)))` on group keys. Shift hour buckets (06–14 / 15–23) leave 0–5 as 'other' intentionally.
- `/delivery/summary`, `/revenue/breakdown` — `rider_deliveries`/`v_daybook` columns match writers; `NULLIF(order_count,0)` and `if gross/orders` guards present.
- `/pos/food-cost` — correctly filters `b.bill_net > 0` (line 4001); `r.name AS menu_name` is an alias not a hallucinated column; all fc% divisions guarded `> 0`. (This is the Session-18-patched endpoint and it is clean.)
- `/pos/goals`, `/pos/shifts`, `/pos/prep-forecast` — `bill_net > 0` present; per-day averages guard `max(day_count,1)` / `days_elapsed > 0`; DOW→Thai conversion `(weekday()+1)%7` correct.

---

## Verification notes for TUM
- The single genuine hallucinated column is `a.mean_amount` ([C1], line 1347). Confirm with:
  `psql "$DATABASE_URL" -c "\\d public.bill_anomalies"` — you will see `category_mean`, not `mean_amount`.
- For every [C2]/[C3]/[C4] void-leak, the one-line fix is identical: add `AND <bills_alias>.bill_net > 0` to the WHERE. These are the highest-money items because they silently inflate the numbers TUM uses to pick which menu items to keep/cut and to judge discounting.
- No syntax changes were made (READ-ONLY). Recommend `python -c "import ast; ast.parse(open('menu_routes.py',encoding='utf-8').read())"` after any fix, then `.\verify.ps1 -Smoke`.
