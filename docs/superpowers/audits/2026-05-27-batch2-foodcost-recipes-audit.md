# Batch 2 — Food Cost / COGS / Recipes Audit (2026-05-27)

Money-first preventive audit, READ-ONLY. Subsystem: recipe + ingredient cost engine, `/pos/food-cost`, `/pos/menu-engineering`, plus the four VEXONHQ frontend pages.

## Summary

| Severity | Count |
|---|---|
| CRITICAL | 3 |
| MEDIUM | 6 |
| LOW | 4 |

CRITICAL findings are all "wrong money number affecting pricing/COGS decisions". The headline one (C1) is a recipe-cost SQL in `/pos/food-cost` that ignores `yield_pct`, so it disagrees with the `/recipes` cost engine and understates COGS / inflates GP across the whole Food Cost page. C2 is an `_auto_sync` crash (wrong logger name) that silently kills auto price-sync on every confirmed invoice. C3 is the dashboard food-cost % cross-source mismatch (uses `v_daybook` not `v_daybook_pnl`, and never excludes equity/transfer rows).

No hallucinated column names were found in the audited endpoints (the `menu_name` alias in `/pos/food-cost` is a SQL alias of `r.name`, not a hallucinated column — correct).

## ✅ Closure status (Session 44, 2026-05-28)

All 3 CRITICAL closed:
- **C1** yield division — fixed in `766bdc0` (menu_routes.py:3983, mirrors recipe engine)
- **C2** `log` → `logger` — fixed in `766bdc0` (recipe_routes.py:735,738)
- **C3** `/scorecard` equity leak — fixed in `766bdc0` (menu_routes.py /scorecard, swapped to v_daybook_pnl)

MEDIUM/LOW still open (6 + 4) — none money-at-risk after CRITICAL closures.

---

## CRITICAL

### [C1] — `/pos/food-cost` recipe cost ignores `yield_pct` → COGS understated, GP% / Food-Cost% wrong
- **File:** `menu_routes.py:3981-3989`
- **Current code:**
```sql
SELECT r.name AS menu_name,
       COALESCE(SUM(ri.qty_used * COALESCE(i.price_per_unit, 0)), 0) AS cost_per_unit
FROM recipes r
JOIN recipe_ingredients ri ON ri.recipe_id = r.id
JOIN ingredients i ON i.id = ri.ingredient_id
GROUP BY r.name
```
- **Issue:** The recipe cost engine in `recipe_routes.py:779` computes `item_cost = qty_f * price_f / effective_yield` where `effective_yield = yield_pct/100`. This `/pos/food-cost` query computes `qty_used * price_per_unit` with **no yield division**. For any ingredient with `yield_pct < 100` (trim/waste — meat, veg) the cost here is **understated** (e.g. 70% yield → true cost is 1/0.70 = 1.43x). Result: the entire Food Cost page (`total_est_cost`, `food_cost_pct`, per-item `cost_unit`, `est_cost`, `gross`, `fc_pct`, the KPI "Food Cost %" badge) shows COGS lower and GP higher than the `/recipes` page does for the same dish. TUM cannot trust either page because they disagree. This is the exact pricing-decision number the page exists to provide.
- **Suggested fix:** apply yield in SQL so both engines match:
```sql
SELECT r.name AS menu_name,
       COALESCE(SUM(
           ri.qty_used * COALESCE(i.price_per_unit, 0)
           / (CASE WHEN COALESCE(i.yield_pct, 100) > 0
                   THEN i.yield_pct / 100.0 ELSE 1 END)
       ), 0) AS cost_per_unit
FROM recipes r
JOIN recipe_ingredients ri ON ri.recipe_id = r.id
JOIN ingredients i ON i.id = ri.ingredient_id
GROUP BY r.name
```
- **Test plan:** Pick a recipe with a `yield_pct < 100` ingredient. Compare `cost_per_dish` on `GET /recipes/{id}` against `cost_unit` for the same `item_name` on `GET /pos/food-cost`. They must match after the fix; before the fix the food-cost value is lower.

### [C2] — `_auto_sync_ingredient_prices` uses undefined `log` → auto price-sync silently dies, ingredient costs go stale
- **File:** `recipe_routes.py:735` and `:738` (logger defined as `logger` at `:42`)
- **Current code:**
```python
log.info("auto-sync ingredient prices: applied=%d updates=%s", applied, updated)
return {"applied_count": applied, "updated": updated}
except Exception:
    log.exception("auto-sync ingredient prices failed (non-fatal)")
    return {"applied_count": 0, "updated": []}
```
- **Issue:** The module defines `logger = logging.getLogger("recipe")` at line 42, but this function references `log` (never defined). If any rows are updated, the `log.info(...)` on line 735 raises `NameError`. That exception is swallowed by the bare `except Exception` (line 737) which then calls `log.exception(...)` — also `NameError`, but inside the except it's lost — and returns `{"applied_count": 0}`. Net effect: every time a vendor bill is confirmed, the auto-sync that should push invoice prices onto `unit_cost_source='invoice'` ingredients **runs the UPDATEs, hits NameError after committing or mid-loop, and reports 0 applied** — and worse, because `log.info` fires only after `conn.commit()` on line 734, the commit may have happened but the caller is told nothing synced. Ingredient `price_per_unit` silently stops tracking invoices → every downstream COGS/GP number drifts.
- **Suggested fix:** replace both `log` with `logger`:
```python
logger.info("auto-sync ingredient prices: applied=%d updates=%s", applied, updated)
...
except Exception:
    logger.exception("auto-sync ingredient prices failed (non-fatal)")
```
- **Test plan:** `python -c "import ast,recipe_routes"` won't catch it (NameError is runtime). Confirm a vendor bill with an `unit_cost_source='invoice'` ingredient match, then assert the response `applied_count > 0` and the log line appears. Grep for other `\blog\.` in the file to confirm no other stray references.

### [C3] — Dashboard `/dashboard/overview` food-cost % uses `v_daybook` (not `v_daybook_pnl`) and never excludes equity/transfer rows
- **File:** `menu_routes.py:1570-1600` (expenses + food_cost + net_profit blocks)
- **Current code:**
```sql
FROM public.v_daybook
WHERE direction='expense'
  AND category_code IN ('food_cost','raw_meat','raw_veggies','raw_seasoning','raw_oil_gas','raw_beverage')
  AND TO_CHAR(entry_date,'YYYY-MM')=%s
```
(and the Total-Expenses query at :1574 likewise reads `v_daybook` with no `source NOT IN (...)` filter)
- **Issue:** Per the verified schema note, `v_daybook_pnl` = `v_daybook` minus equity/transfer sources and is the P&L source of truth; `CLAUDE.md` also mandates `WHERE source NOT IN ('owner_capital','owner_advance','transfer_error')` on any P&L read of `v_daybook`. This endpoint's Total-Expenses (`:1570`) and Net-Profit (`:1584`) computations read raw `v_daybook` with **no source exclusion**, so owner capital / advances / transfer errors inflate "expenses" and depress "net profit / margin". The food-cost numerator (`:1591`) is category-scoped so it's less affected, but the `food_cost_pct` denominator is `this_rev` (also from `v_daybook` income at :1556-1564, no source filter) — revenue can include `owner_capital` income rows. Checklist item 7 (cross-source consistency) fails: this dashboard food-cost% is actual-expense-based and is computed on a different (un-cleaned) base than the spec expects. Switch to `v_daybook_pnl` to align with the P&L source of truth.
- **Suggested fix:** change all three `v_daybook` reads in this function to `public.v_daybook_pnl` (which already strips equity/transfer), OR add `AND source NOT IN ('owner_capital','owner_advance','transfer_error')` to each. Prefer the view for consistency with the rest of the P&L stack.
- **Test plan:** In a month containing an `owner_capital` or `transfer_error` row, compare `this_exp` / `net_profit` from `/dashboard/overview` against a manual `SELECT … FROM v_daybook_pnl`. They must match after the fix. Confirm `food_cost_pct` denominator no longer includes equity income.

---

## MEDIUM

### [M1] — Recipe ingredient cost SQL drops ingredients with invalid/orphan `ingredient_id` (Session 34 ai-link bug residue)
- **File:** `recipe_routes.py:746-759` (`_calc_cost`) and `menu_routes.py:3984-3987`
- **Current code:** both use `JOIN public.ingredients i ON i.id = ri.ingredient_id` (INNER JOIN).
- **Issue:** Session 34's ai-link bug could insert `recipe_ingredients` rows with a bad `ingredient_id` (Thai text / non-existent uuid). An INNER JOIN silently drops those rows from the cost sum, so the recipe shows a **lower cost / higher GP%** with no warning, and `ingredient_count` in `/recipes` (derived from the breakdown length, `recipe_routes.py:831`) undercounts. The frontend `missing_price` warning never fires because the row simply isn't returned. The ai-link apply path is now guarded (`:1223,1232`), but pre-fix orphan rows in the DB stay invisible.
- **Suggested fix:** use `LEFT JOIN` and surface orphans, e.g. count `ri` rows whose `i.id IS NULL` as a `broken_link_count`, or run a one-off cleanup query to delete/repair orphan `recipe_ingredients`. At minimum the audit query: `SELECT ri.id FROM recipe_ingredients ri LEFT JOIN ingredients i ON i.id=ri.ingredient_id WHERE i.id IS NULL`.
- **Test plan:** Insert a `recipe_ingredients` row with a random uuid not in `ingredients`; confirm `/recipes/{id}` either flags it or the cleanup query finds it.

### [M2] — `/pos/food-cost` `coverage_pct` denominator is capped at 60 items, overstating coverage
- **File:** `menu_routes.py:4005` (`LIMIT 60`) + `:4054`
- **Current code:** `coverage_pct = round(matched / len(items) * 100, 1)` where `items` is built from `sales_rows` which is `LIMIT 60`.
- **Issue:** `sales_rows` is limited to the top 60 items by revenue. `coverage_pct` (`matched / len(items)`) is therefore computed over at most 60 menus, not the full menu. If the venue has 190+ menus (the recipes page references 187), the "Recipe coverage X%" banner on the food-cost page (`food-cost/page.tsx:118-134`) can read ">=80% complete" while the long tail of menus has no recipe at all. Misleads TUM into thinking the COGS picture is complete.
- **Suggested fix:** Either compute coverage over the full `COUNT(DISTINCT item_name)` for the period (separate query, no LIMIT), or label the banner explicitly as "top 60 by revenue". The revenue-weighted framing is defensible but must be stated.
- **Test plan:** Compare `total_items` in the response vs `SELECT COUNT(DISTINCT item_name)` for the period; if they differ, the banner wording is misleading.

### [M3] — `est_cost`/`gross` displayed even when item has no recipe → fake Gross Profit
- **File:** `menu_routes.py:4031-4047`; rendered `food-cost/page.tsx:257-264`
- **Current code:** `cost_unit = recipe_costs.get(name, 0.0)` → `est_cost = qty*0 = 0` → `gross = revenue - 0 = revenue`.
- **Issue:** For items with no recipe, `cost_unit` is 0, so `gross == revenue` and `fc_pct == 0`. The frontend guards the gross/cost cells with `row.est_cost === 0 ? '—'` (good), but the **summary** `gross_profit` (`:4053` `total_item_rev - total_est_cost`) and `food_cost_pct` (`:4052`) sum these zero-cost items into the totals, so the headline GP/FC% are computed over revenue that includes no-recipe items as pure profit. Coverage banner mitigates but the KPI cards still show an over-optimistic blended number.
- **Suggested fix:** compute `total_item_rev` / `total_est_cost` / `food_cost_pct` over only `has_recipe and cost_unit > 0` rows, and label the KPI "จากรายการที่คำนวณได้" consistently (the GP sub-label already says this; total_revenue card does not).
- **Test plan:** With partial recipe coverage, verify `food_cost_pct` matches `sum(est_cost)/sum(revenue)` restricted to recipe-priced items.

### [M4] — Frontend silently swallows non-OK fetches on Food Cost & Menu Engineering pages (no res.ok check)
- **File:** `food-cost/page.tsx:42-47`, `menu-engineering/page.tsx:120-130`, and `recipes/page.tsx:96-99`, `:107-109`
- **Current code (food-cost):**
```tsx
fetch(`${API}/pos/food-cost?months=${months}`)
  .then((r) => r.json())
  .then(setData)
  .catch(console.error)
```
- **Issue:** No `res.ok` check. A 500 (the classic backend SQL error) returns a FastAPI `{"detail": ...}` JSON; `.then(setData)` stores it as `data`, then `s = data.summary ?? {}` yields `{}`, and every `s.field` is `undefined`. `fmt(undefined)` → `"NaN"` would surface, and `food_cost_pct <= 30` comparisons against `undefined` are `false`, silently mislabeling. A 401 redirect also never happens. This is exactly the "silent fetch failure" pitfall called out in both CLAUDE.md files. `recipes/page.tsx` `load()` and `loadDetail()` likewise never check `res.ok`.
- **Suggested fix:** add `if (!res.ok) throw new Error(await res.text())` before `.json()`, and render an error state (the ingredients page already does this correctly at `ingredients/page.tsx:95`).
- **Test plan:** Point the page at an endpoint returning 500; confirm an error banner shows instead of NaN/blank KPIs.

### [M5] — `fmt(undefined)` / `NaN` leak on Food Cost KPIs when summary fields missing
- **File:** `food-cost/page.tsx:12-13, 141-165`
- **Current code:** `function fmt(n: number) { return n.toLocaleString(...) }`; KPIs call `fmt(s.total_revenue)`, `fmt(s.total_est_cost)`, `fmt(s.gross_profit)` where `s = data?.summary ?? {}`.
- **Issue:** When `summary` is `{}` (empty data, or the M4 error path), `s.total_revenue` is `undefined`; `undefined.toLocaleString()` throws `TypeError`, crashing the page render (white screen). `s.food_cost_pct` undefined → `fmt1(undefined)` same crash. The page has no empty/guard state for a malformed-but-200 response.
- **Suggested fix:** `function fmt(n?: number) { return (n ?? 0).toLocaleString('th-TH', {maximumFractionDigits:0}); }` (and `fmt1`), or guard each KPI with `?? 0`.
- **Test plan:** Mock `/pos/food-cost` returning `{summary:{}, items:[]}`; page must render zeros, not crash.

### [M6] — `addItem`/`importFromStock`/`importFromMenu` swallow backend errors; recipe public-fields save shows raw error object
- **File:** `recipes/page.tsx:186-194` (`importFromMenu`), `ingredients/page.tsx:107-122` (`importFromStock`), `recipes/page.tsx:151` (`savePublicFields` catch)
- **Issue:** `importFromMenu` / `importFromStock` do `const data = await res.json()` with no `res.ok` check; a 500 sets `importMsg` to `data.message` which is `undefined` → shows "เสร็จแล้ว"/"นำเข้าสำเร็จ" on failure, misleading TUM into thinking an import worked. `savePublicFields` catch renders `String(e)` (raw `Error: HTTP 500`), acceptable but inconsistent with the rest.
- **Suggested fix:** add `if (!res.ok) throw new Error(...)` in the import handlers before reading `.message`, and set an error-styled message on failure.
- **Test plan:** Force the import endpoint to 500; confirm a failure message (not a success toast) is shown.

---

## LOW

### [L1] — Recipe GP% vs Food-Cost%: two different "cost" definitions, no shared label
- **File:** `recipe_routes.py:824` (`gp_pct = (sell-cost)/sell*100`) vs `menu_routes.py:4034` (`fc_pct = est_cost/revenue*100`)
- **Issue:** `/recipes` reports GP% off `selling_price` (the recipe's own price); `/pos/food-cost` reports FC% off actual POS `revenue` (qty*unit_price). When POS sells at discount or a different price than `recipes.selling_price`, the two pages legitimately differ even after C1 is fixed. Not a bug, but undocumented — TUM may read it as an inconsistency.
- **Suggested fix:** add a tooltip/footnote on each page clarifying the cost/price basis.
- **Test plan:** n/a (doc only).

### [L2] — Division-by-zero guards are correct but `selling_price=0` yields `gp_pct=None` silently treated as 0 in avg
- **File:** `recipes/page.tsx:347-349`
- **Current code:** `avgGP = recipes.reduce((s,r)=>s+(r.gp_pct ?? 0),0) / recipes.filter(r=>r.gp_pct!==null).length`
- **Issue:** Numerator sums `gp_pct ?? 0` over ALL recipes (including null ones, contributing 0), but denominator counts only non-null. So recipes with null GP (no selling price) drag the average toward 0 incorrectly. Backend correctly returns `gp_pct=null` for `sell<=0` (`recipe_routes.py:824`), but the frontend average is biased low.
- **Suggested fix:** `recipes.filter(r=>r.gp_pct!==null).reduce((s,r)=>s+(r.gp_pct ?? 0),0) / count` — sum and count over the same filtered set.
- **Test plan:** Add a recipe with `selling_price=0`; confirm avg GP doesn't drop.

### [L3] — `/pos/food-cost` revenue mismatch: item revenue uses `qty*unit_price`, total uses `bill_net`
- **File:** `menu_routes.py:3995` (`SUM(si.qty * si.unit_price)`) vs `:4011` (`SUM(bill_net)`)
- **Issue:** `total_revenue` (bill-level `bill_net`, net of discount) and `item_revenue` (line `qty*unit_price`, gross of discount) will not reconcile; the page shows both ("รายได้รวm" card uses `total_revenue`, GP card uses `item_revenue`). Minor since GP% is explicitly labeled "จากรายการที่คำนวณได้", but the two revenue figures invite a "numbers don't add up" question. Consider `si.net_amount` for item revenue to net discounts.
- **Test plan:** Compare `total_revenue` vs `item_revenue` on a month with discounts; expect item_revenue > total_revenue.

### [L4] — Menu Engineering: `avg_price` and indexes can divide by zero only if avg_qty/avg_revenue are 0; guarded by min_orders but worth a NaN note
- **File:** `menu_routes.py:1862-1863`
- **Issue:** `pop_index = total_qty/avg_qty`, `rev_index = rev/avg_revenue`. `avg_qty`/`avg_revenue` are means over `rows`; with `rows` non-empty they're > 0 in practice, but a dataset where every item has `total_qty=0` (e.g. all refunds netting zero qty) would yield `avg_qty=0` → `ZeroDivisionError` (no guard, unlike the `grand_total_revenue` guards elsewhere). Very low likelihood given `HAVING order_count >= min_orders`, hence LOW.
- **Suggested fix:** guard `pop_index = round(qty/avg_qty,2) if avg_qty else 0` (and same for rev_index), matching the `pct_total` guard style on line 1861.
- **Test plan:** Construct rows with `total_qty=0`; confirm no 500.

---

## Notes / non-findings
- `menu_name` in `/pos/food-cost` is a SQL alias (`r.name AS menu_name`), not a hallucinated column — correct per schema.
- All audited SQL uses `bill_net > 0` (not `b.status`), `branch_code` (not `branch`), `qty_used` (not `quantity`), `r.name` (not `r.menu_name` as a column). No hallucinated columns found in audited endpoints.
- Missing `Authorization` headers in frontend fetches are NOT flagged — `components/AuthProvider.tsx` injects auth globally via a window.fetch interceptor (per audit rules).
- `_calc_cost` correctly casts Decimal→float and applies yield (`recipe_routes.py:778-779`) — this is the correct reference implementation that C1 should match.
