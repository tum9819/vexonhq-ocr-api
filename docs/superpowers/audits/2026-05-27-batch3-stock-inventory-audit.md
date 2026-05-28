# Batch 3 Audit — Stock + Reorder + Inventory Forecast (Money-First, READ-ONLY)

Date: 2026-05-27
Scope: `stock_routes.py`, `inventory_forecast_routes.py` (backend) + `app/inventory/page.tsx`, `app/inventory/forecast/page.tsx`, `app/inventory/reorder/page.tsx` (frontend).
Also cross-referenced: `phase2_routes.py /inventory/current` (the endpoint the main inventory page actually calls).

## Summary

- CRITICAL: 2
- MEDIUM: 6
- LOW: 4

Headline money risks:
- **[C1]** The primary stock screen (`/inventory` page → `/inventory/current`) bypasses BOTH the partial-upload defense and the promo-SKU exclusion that every other stock/reorder endpoint applies. It can show a partial 1-row upload as the whole inventory and counts Pro/(pro) promo packs as real stock — directly driving wrong "what to buy" decisions.
- **[C2]** `/inventory/ai-order-advice` divides by `len(all_avg)` and renders advice off `EXTRACT(DOW)` rows; with sparse data the "stock multiplier" and "best day" advice can be built from a single day, producing confident but baseless purchasing guidance.

No SQL column hallucinations were found in the audited files — all column references match the verified schema. The AI-output-to-numeric-column class bug (Session 34) does NOT occur here: AI advice strings are returned in the JSON `advice[]` only and are never written back to any column.

## ✅ Closure status (Session 44, 2026-05-28)

- **C1** `/inventory/current` snapshot defense + promo filter — ✅ fixed in `10a788b` (mirrors stock_routes pattern; A/B verified 4 promo items removed)
- **C2** ai-order-advice min-sample guardrail — 🟡 **DEFERRED** (product decision: what to show on sparse data? TUM input needed)

---

## [C1] Main inventory screen ignores partial-upload defense and promo-SKU exclusion

**File:** `phase2_routes.py:714-789` (`/inventory/current`), consumed by `app/inventory/page.tsx:115`
**Current code (phase2_routes.py:720-755):**
```python
cur.execute(
    """SELECT id, snapshot_at, item_count, total_value
       FROM public.pos_inventory_snapshots
       WHERE branch_code = %s
       ORDER BY snapshot_at DESC
       LIMIT 1""",
    (branch,),
)
...
cur.execute(
    """SELECT item_name, material_code, tag, qty_in_stock, qty_max, ...
       FROM public.pos_inventory_items
       WHERE snapshot_id = %s
       ORDER BY CASE ...""",
    (snap_id,),
)
```

**Issue:**
Every other stock path (`stock_routes._get_latest_snapshot_id`, `_query_inventory`, `/stock/summary`, `/stock/alert`, and `inventory_forecast_routes._compute_reorder_list`) goes to deliberate lengths to:
1. Skip "partial upload" snapshots (item_count < 50% of the 30-day max) — the documented Session 15 defensive fix at `stock_routes.py:43-99`.
2. Exclude promo/bundle SKUs (`LOWER(item_name) NOT LIKE 'pro(%'` / `'(pro%'`) — Session 15 fix at `stock_routes.py:124-127`.

`/inventory/current` does NEITHER. It takes the literal most-recent snapshot (so a stray 1-row promo upload becomes the entire displayed inventory, exactly the breakage `_get_latest_snapshot_id` was written to prevent) and includes promo packs in `total_items`, `by_status` counts, `total_value`, and the item list. This is the screen the owner looks at first ("สต็อกสินค้า"), so the most-seen number is the least-defended one. The result is inconsistent with `/inventory/reorder` and `/stock/*`, which will show different counts for the same day.

**Suggested fix:**
Have `/inventory/current` reuse `stock_routes._get_latest_snapshot_id(branch)` for snapshot selection and add the same two `NOT LIKE` promo filters to the items query (pass `'pro(%'` and `'(pro%'` as parameters, not inlined — see the psycopg2 note at `stock_routes.py:121-123`). That makes the headline screen consistent with every other stock view.

**Test plan:**
- Seed a fresh snapshot with a single promo row `"Pro(3) เบียร์..."` dated after a full snapshot. Confirm `/inventory/current` returns the FULL snapshot (not the 1-row one) and that no `pro(`/`(pro` item appears in `items`, matching `/stock/all` and `/inventory/reorder` counts for the same branch.
- Assert `total_items` and `by_status` sums equal `len(items)` after the filter.

---

## [C2] AI order advice builds purchasing guidance from possibly 1 day of data

**File:** `inventory_forecast_routes.py:438-518`
**Current code:**
```python
all_avg = [float(r.get("avg_daily_sales") or 0) for r in dow_rows]
grand_avg = sum(all_avg) / len(all_avg) if all_avg else 1.0
...
index = round(avg_sales / grand_avg * 100, 1) if grand_avg > 0 else 100.0
...
if top2:
    top_index = top2[0]["sales_index"]
    multiplier = round(top_index / 100, 2)
    advice.append({... "detail": f"เตรียม {multiplier}x เทียบกับวันปกติ" ...})
```

**Issue:**
There is no minimum-sample guard. `dow_rows` aggregates by day-of-week, and `day_count` per DOW can be as low as 1 (a single Sunday in the window, or sparse imports). `grand_avg` is the mean of whatever DOW buckets exist — if only one or two DOWs have data, `sales_index` for the populated day is mechanically ~100-200 and "best day" / "stock multiplier (e.g. 1.8x)" advice is emitted as if statistically meaningful. The owner is told "เตรียม 1.8x เทียบกับวันปกติ" for the best day based on essentially one observation. This is a money decision (over-ordering perishables) driven by noise.
Note: the division itself is guarded (`if grand_avg > 0`), so this is not a crash — it is *confident-but-baseless output*, which is the more dangerous failure mode for purchasing.

**Suggested fix:**
- Require a minimum `day_count` per DOW (e.g. >= 3) before a DOW contributes to ranking / advice, and require >= ~2 distinct DOWs with data before emitting `best_sales_days` / `stock_level` advice. Below that, return advice with a "ข้อมูลยังน้อย — ยังไม่แนะนำปริมาณ" note instead of a multiplier.
- Optionally weight `grand_avg` by `day_count` rather than a flat mean of DOW means (current flat mean over-weights a DOW that has only 1 sample).

**Test plan:**
- Seed `v_daybook` income for a single date only; call `/inventory/ai-order-advice`. Assert no `stock_level` multiplier advice is produced (or it is flagged low-confidence) and `grand_avg_daily` is not used to manufacture a >110 index.
- Seed 12 weeks of balanced data; assert advice still emits normally.

---

## [M1] `_get_latest_snapshot_id` return type contradicts its signature

**File:** `stock_routes.py:43, 86, 97`
**Current code:**
```python
def _get_latest_snapshot_id(branch_code: str = "thawi_watthana") -> Optional[str]:
    ...
        if row:
            return (str(row[0]), str(row[1]))     # line 86
    ...
        return (str(row[0]), str(row[1])) if row else (None, None)   # line 97
```

**Issue:**
Signature says `-> Optional[str]` but the function always returns a 2-tuple. All current callers unpack `snapshot_id, snapshot_at = _get_latest_snapshot_id(...)`, so it works, but the lie invites a future caller to write `sid = _get_latest_snapshot_id()` and then use a `(id, at)` tuple as a snapshot_id in a SQL param — silent wrong query. Low blast radius today, real trap later.

**Suggested fix:**
Change annotation to `-> tuple[Optional[str], Optional[str]]` and update the docstring's "Return the most recent USABLE snapshot_id" wording to say it returns `(snapshot_id, snapshot_at)`.

**Test plan:** static — `mypy`/type check; confirm both call sites still unpack two values.

---

## [M2] `next_order_est` / urgency wrong for single-order vendors (assumed bi-monthly)

**File:** `inventory_forecast_routes.py:168-177`
**Current code:**
```python
else:
    # Only 1 order — estimate from lookback
    avg_interval = lookback_months * 15.0  # assume bi-monthly
    min_interval = max_interval = int(avg_interval)
...
next_order_est = last_order + timedelta(days=int(avg_interval))
urgency = _urgency(days_since, avg_interval)
```

**Issue:**
For a vendor with a single confirmed bill in the window, `avg_interval` is fabricated as `lookback_months * 15` days (e.g. 90 days for a 6-month lookback). This is then used to compute `next_order_est`, `days_until_order`, and an `urgency` band shown to the owner as if it were data-derived. A one-off purchase (e.g. equipment, a single promo buy) gets a fake "next order" date and can surface as "soon"/"urgent", polluting the alert banner count (`overdue_count + urgent_count`). Note the route default is `min_orders=2`, which hides this — but `min_orders=1` is an allowed query param (`ge=1`), and the LINE/other callers may pass it.

**Suggested fix:**
When `len(dates) < 2`, mark `urgency="unknown"` and either omit `next_order_est` (null) or clearly flag it as an estimate; do not let single-order vendors count toward `overdue_count`/`urgent_count`.

**Test plan:** seed one vendor with exactly 1 confirmed bill; call `/inventory/forecast?min_orders=1`; assert that vendor's `urgency == "unknown"` and is excluded from the alert counts.

---

## [M3] Forecast cutoff uses 30-day months; `vendor_bills.amount` sign not guarded for spend

**File:** `inventory_forecast_routes.py:108, 119-120`
**Current code:**
```python
cutoff = today - timedelta(days=lookback_months * 30)
...
SUM(vb.amount)::numeric  AS total_spend,
AVG(vb.amount)::numeric  AS avg_amount,
...
AND vb.amount > 0
```

**Issue:**
Two minor money-accuracy points:
1. `lookback_months * 30` undercounts the real calendar window (6 "months" = 180 days, not ~182.6), so vendors purchased early in month 6 can silently fall outside the window and disappear from forecast — a vendor that should show "overdue" simply vanishes. Edge case but it is a *purchasing-visibility* gap, not just cosmetics.
2. `vb.amount > 0` is applied (good — filters credits/voids), and `total_spend`/`avg_amount` therefore exclude non-positive rows consistently. No bug here, noted as verified-OK so the `> 0` guard is not "fixed" away later.

**Suggested fix:** use `today - relativedelta(months=lookback_months)` (dateutil) or `date_trunc`-style month math so the window is calendar-correct. Leave the `amount > 0` guard as-is.

**Test plan:** seed a confirmed bill dated exactly `lookback_months` calendar-months ago; assert it is included with `relativedelta` and was being excluded under `*30`.

---

## [M4] Reorder "to_order" uses raw qty_max−qty_current with no pack rounding or floor on negatives

**File:** `inventory_forecast_routes.py:313-341`
**Current code:**
```python
q_cur = float(q_cur or 0)
q_max = float(q_max or 0)
to_order = max(q_max - q_cur, 0)
...
"qty_to_order": to_order,
"est_cost": round(to_order * price, 2),
```

**Issue:**
`to_order` is a raw float. Two practical problems for purchasing:
1. When `q_cur` is negative (POS oversold — the `/inventory` page note explicitly says "สต็อกติดลบ = POS ขายเกินที่บันทึก"), `to_order = q_max - q_cur` *inflates* the suggested order by the oversold amount (e.g. qty_max=10, qty_cur=−4 → order 14). For a real negative caused by an un-recorded receipt that is over-ordering; the SQL filter `qty_max > qty_in_stock` (line 289) deliberately includes negatives, so this is reachable and material on the reorder worksheet + its est_cost rollup.
2. `qty_to_order` is a fractional float (e.g. 3.5 ขวด) rendered raw in the UI (`reorder/page.tsx:354` `{i.qty_to_order}`) and in the LINE copy string — odd for discrete goods.

This is exactly the "wrong reorder qty driving purchasing" category, but it is bounded/explainable (negative stock is a known data condition the team understands), so MEDIUM not CRITICAL.

**Suggested fix:** decide policy with TUM: either clamp `q_cur` at 0 for the order calc (`to_order = max(q_max - max(q_cur,0), 0)`) so oversold items don't inflate orders, or keep current behavior but surface a "รวมยอดขายเกิน" note. Round `to_order` for discrete units.

**Test plan:** row with qty_max=10, qty_cur=−4, price=20 → assert `qty_to_order` and `est_cost` match the agreed policy; verify summary `est_total_cost` rollup.

---

## [M5] Frontend bar chart can divide by zero / produce NaN height when all sales are 0

**File:** `app/inventory/forecast/page.tsx:342-343`
**Current code:**
```tsx
const maxSales = Math.max(...aiData.dow_stats.map(x => x.avg_sales)) || 1;
const heightPct = Math.round((d.avg_sales / maxSales) * 100);
```

**Issue:**
The `|| 1` guard handles `maxSales === 0`, so no NaN — good. But `Math.max(...[])` on an empty array returns `-Infinity`; this is reached only inside the `aiData.dow_stats.length > 0` block (line 333), so the array is non-empty. Verified-OK on divide-by-zero. The residual concern: `avg_sales` arrives from the API as a number, but if a future API change sends `null`, `d.avg_sales / maxSales` → NaN and `Math.max(...,null)` coerces oddly. Low likelihood given current backend rounds to a number.

**Suggested fix:** coerce defensively: `const v = Number(d.avg_sales) || 0;` before the height math. Optional hardening.

**Test plan:** mock `dow_stats` with one `avg_sales: 0` row → bar renders at the `Math.max(heightPct,4)%` floor, no NaN in DOM.

---

## [M6] Reorder selection / dedup keyed on `item_name` only — collisions across tags

**File:** `app/inventory/reorder/page.tsx:122, 332, 156`
**Current code:**
```tsx
if (checked.has(i.item_name)) { ... }   // Set<string> of item_name
...
<tr key={i.item_name} ...>
...
const items = data.items.filter((i) => checked.has(i.item_name));
```

**Issue:**
The checkbox `Set` and the React row `key` use `item_name` alone. The backend reorder query does not dedupe by name and the same `item_name` can appear under two `tag`s (the `/inventory/current` and `/stock` paths key rows on `item_name + tag`, e.g. `page.tsx:331`). If two reorder rows share a name, ticking one ticks both visually (same key in the Set), the copied shopping list includes both/neither inconsistently, and React warns on duplicate keys. The estimated-cost rollup (`totalChecked`) then double-counts or under-counts. Money-relevant because the copied list is what gets purchased.

**Suggested fix:** key on `${i.item_name}__${i.tag}` for both the `Set` membership and the row `key`, consistent with the other inventory views.

**Test plan:** seed two reorder rows with same `item_name`, different `tag`; tick one; assert only that row checks and the copied list contains exactly one.

---

## [L1] `ai_order_advice` SQL counts rider income in "POS sales by day" but advice text says ยอดขาย POS

**File:** `inventory_forecast_routes.py:400-414`
**Current code:**
```python
WHERE direction = 'income'
  AND source IN ('pos_sale','rider_income_grab','rider_income_lineman')
```

**Issue:** The docstring/UI label says "ยอดขาย POS" but the query includes Grab/Lineman rider income. That is arguably correct (total sales by channel) but the label is misleading for someone reasoning about dine-in stock. Cosmetic / labeling.

**Suggested fix:** rename the UI/heading to "ยอดขายรวม (POS + เดลิเวอรี)" or split, per TUM preference.

**Test plan:** n/a (label).

---

## [L2] `formatDate` in forecast page swallows parse errors and returns raw ISO

**File:** `app/inventory/forecast/page.tsx:94-99`
**Current code:**
```tsx
function formatDate(iso: string | null) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString('th-TH', {...}); }
  catch { return iso; }
}
```

**Issue:** `new Date('garbage')` does not throw — it yields `Invalid Date`, and `toLocaleDateString` returns the string `"Invalid Date"` (not caught). The `catch` is effectively dead for bad-but-non-throwing input; the UI would show "Invalid Date". Minor.

**Suggested fix:** check `Number.isNaN(d.getTime())` and fall back to `'—'`, mirroring the more robust `formatDate` in `app/inventory/page.tsx:455-458`.

**Test plan:** pass `formatDate('not-a-date')` → expect `'—'`.

---

## [L3] `/stock/alert` LINE push: failure is returned but not logged

**File:** `stock_routes.py:547-555`
**Current code:**
```python
try:
    with _req.urlopen(req, timeout=10):
        push_result["sent"] = True
except _uerr.HTTPError as e:
    push_result["error"] = f"LINE {e.code}: {e.read().decode()[:100]}"
except Exception as e:
    push_result["error"] = str(e)[:100]
```

**Issue:** Not a silent `except: pass` (the error is captured into the response — good), but it is never `log.warning(...)`'d. Since this runs from a cron/scheduled job (`@router.get("/alert")`), the JSON response is usually discarded, so a persistently failing LINE push (expired token) leaves no server-side trace. CLAUDE.md rule 3 is "never suppress errors" — this is borderline.

**Suggested fix:** add `log.warning("stock alert LINE push failed: %s", push_result["error"])` in both except branches.

**Test plan:** force a bad token; confirm a warning line appears in logs.

---

## [L4] Forecast page renders `f.avg_interval_days` with no unit guard for the "1 order" estimate

**File:** `app/inventory/forecast/page.tsx:259, 269`
**Current code:**
```tsx
ยอดเฉลี่ย {currency.format(f.avg_amount)} · interval {f.avg_interval_days} วัน
...
<div className="hidden text-right text-sm text-muted sm:block">{f.avg_interval_days} วัน</div>
```

**Issue:** Tied to [M2] — for single-order vendors the backend sends the fabricated `lookback_months*15` interval and the UI prints it as a hard "X วัน" with no "ประมาณ"/estimate marker, so the owner can't tell a real cadence from a placeholder. Cosmetic surface of the [M2] data issue.

**Suggested fix:** if backend marks single-order vendors as `unknown` (per [M2]), render "—" or "ประมาณ" for their interval.

**Test plan:** covered by [M2].

---

## Verified-clean checklist (no finding)

- **SQL columns:** all references in scope match verified schema — `vendor_bills.amount/bill_date/vendor_name/category_code/branch_code/review_status/payment_status`, `pos_inventory_items.qty_in_stock/qty_max/qty_diff/unit_price/stock_value/tag/material_code`, `v_daybook.entry_date/direction/source/amount/branch_code`. No `net_price`, `b.status`, `b.branch`, `staff`, `ri.quantity`, `r.menu_name`.
- **Voids exclusion:** stock paths use POS snapshot tables (no bill voids concern); forecast uses `review_status='confirmed'` + `amount>0`. Correct.
- **AI-text-into-numeric-column (Session 34 class):** none. AI advice strings live only in the response `advice[]`; nothing is INSERTed/UPDATEd from advice output.
- **Division by zero (backend):** `_urgency` guards `avg_interval <= 0` (line 67); `grand_avg` guarded `if grand_avg > 0` (line 450); `multiplier` only inside `if top2`. No crash.
- **Snapshot freshness/timezone:** `_get_latest_snapshot_id` uses `NOW() - INTERVAL '30 days'` and orders by `snapshot_at DESC` (DB-side, no client tz math). Date display slices `[:10]`. No off-by-one found in the audited stock paths. (The freshness *defense* gap is [C1], in phase2 not stock_routes.)
- **Auth headers:** not flagged (global AuthProvider interceptor handles auth, per audit rules).
