# Batch 11 — POS Analytics Frontend Audit (READ-ONLY)

Date: 2026-05-27
Scope: 22 pages under `C:\Users\rapee\VEXONHQ\app\pos\` (overview, bill-analysis, payments, voids, staff, shifts, tables, dow, hourly, channels, discounts, combos, predict, flash, goals, calendar, items, prices, categories, menu-engineering, heatmap, prep-forecast).
Frontend display-correctness only. Backend bugs (e.g. void-leak `bill_net>0`) noted but not FE-fixable.
Auth: confirmed `components/AuthProvider.tsx` injects `Authorization` globally via `window.fetch` and handles 401→/login globally. Missing auth headers NOT flagged (correct per context).

## Summary

| Sev | Count | Headline |
|-----|-------|----------|
| CRITICAL | 5 | Missing `res.ok` checks (10 pages) swallow backend 500s and render misleading empty/zero UI as truth; hardcoded prod fallback URL on 6 pages; NaN-on-money risk on goals/calendar percent metrics |
| MEDIUM | 6 | No error state on the silent-fetch group; BKK timezone day-boundary on flash/calendar "today"; cross-page discount-rate scale inconsistency; pie label uses undefined field; duplicate Math.max recompute |
| LOW | 4 | `±` confidence interval can show negative, `connectNulls` visually bridges forecast gap, decimal-scale comments, redundant per-row Math.max |

Money-impact ranking (fix first): **C1** (silent 500 → blank page presented as "no data"), then **C3** (NaN on goal/forecast decision metrics), then **C2** (hardcoded URL — wrong-backend / stale-data risk).

## ✅ Closure status (Session 44, 2026-05-28)

All 3 frontend CRITICAL closed via the `safeFetch + fmt` refactor (commit `295de44`):
- **C1** Missing `res.ok` on 14 POS pages — ✅ replaced with `safeFetch<T>` (throws on non-2xx) + rose error UI branch on every page
- **C2** Hardcoded `api.vexonhq.com` fallback on 6 pages — ✅ removed (now `?? ''`, and helper prefixes `NEXT_PUBLIC_API_URL`)
- **C3** NaN money on `/pos/goals` + `/pos/predict` — ✅ local `fmt` wrappers rerouted through NaN-safe `fmtNum / safeNum`

Backend void-leak that some of these pages displayed (`/pos/voids`, `/pos/discounts`, etc.) — ✅ resolved by backend commit `5b785e9` (B5 void filter sweep).

New helpers introduced: `lib/safeFetch.ts`, `lib/fmt.ts`. See VEXONHQ `AGENTS.md` pitfall #7 for usage convention.

Remaining: 6 MEDIUM (BKK timezone day-boundary, pie label, cross-page rate scale) + 4 LOW. None money-at-risk.

---

## CRITICAL

### [C1] Missing `res.ok` check — backend 500/4xx parsed as success, UI shows false "no data"
The #1 recurring FE bug. Two fetch styles exist in the batch:
- **Safe (has `if (!r.ok) throw)`):** overview, staff, tables, dow, hourly, channels, prep-forecast. No action.
- **UNSAFE (calls `.json()` directly, `.catch(console.error)` only):**
  - `bill-analysis/page.tsx:47-49` — `setData(await res.json())`
  - `payments/page.tsx:109-110`
  - `voids/page.tsx:45-47`
  - `shifts/page.tsx:35-37`
  - `discounts/page.tsx:43-45`
  - `combos/page.tsx:46-47`
  - `predict/page.tsx:41-43`
  - `flash/page.tsx:78-80`
  - `goals/page.tsx:55-57`
  - `calendar/page.tsx:62-63`
  - `items/page.tsx:87-88`
  - `prices/page.tsx:50-52`
  - `categories/page.tsx:65-66`
  - `heatmap/page.tsx:59-61`
  - `menu-engineering/page.tsx:123-125` (has `catch{setData(null)}` but still no `res.ok` — a 500 returning an HTML/error body throws in `.json()` and is silently swallowed to the generic "ไม่สามารถโหลดข้อมูลได้").

**Issue:** On a backend 500 (the CLAUDE.md-documented SQL-error case), FastAPI returns a JSON error body like `{"detail": "..."}`. `await res.json()` succeeds, `setData(<error obj>)` runs, then the page renders its empty/zero branch (e.g. voids shows "ไno void 🎉", goals shows ฿0/0%, flash shows ฿0 revenue). The owner reads a *wrong money number or false "all clear" as truth*. This is exactly the Session 18 silent-404 class of bug called out in CLAUDE.md "Known pitfalls #1".

**Suggested fix:** Adopt the safe-group pattern on every unsafe page:
```ts
const res = await fetch(...);
if (!res.ok) throw new Error(`โหลดไม่ได้ (HTTP ${res.status})`);
setData(await res.json());
```
plus an `error` state + red banner (the safe group already has one; copy it). Replace `.catch(console.error)` with `.catch(e => setError(...))`.

**Test plan:** With backend stopped or a route forced to 500, load each page. Expect a visible red error banner, NOT a "no data 🎉" / ฿0 screen. Restart backend, confirm normal render.

### [C2] Hardcoded production fallback URL `https://api.vexonhq.com`
Files: `bill-analysis:10`, `combos:6`, `calendar:6`, `items:9`, `categories:10`, `heatmap:9` — all use `process.env.NEXT_PUBLIC_API_URL || 'https://api.vexonhq.com'`.
**Issue:** Violates CLAUDE.md "Known pitfalls #2" (never hardcode backend URL). `api.vexonhq.com` is the OLD domain — production migrated to `api.marastation.com` (memory + CLAUDE.md Session 32). If `NEXT_PUBLIC_API_URL` is ever empty at build (it currently isn't, masking this), these 6 pages silently hit a stale/wrong backend and display old or no data as truth; the other 16 pages use `?? ''` and would correctly fail-loud (relative URL → no data). Inconsistent.
**Suggested fix:** Change all six to `process.env.NEXT_PUBLIC_API_URL ?? ''` to match the rest of the codebase.
**Test plan:** `grep -r "api.vexonhq.com" app/pos` returns nothing. Build with env set; pages still load.

### [C3] Division-by-zero / NaN on decision metrics (goals, predict, calendar)
- `goals/page.tsx`: KPIs `daily_required`, `projected_eom`, `projected_pct`, `gap` come straight from backend (`fmt(data.daily_required)` etc., lines 169-186). If backend divides by `days_remaining=0` (end of month) or `days_elapsed=0` (1st of month before any sale) and returns `NaN`/`null`/`Infinity`, `fmt(NaN)` → `"NaN"` printed as the owner's "ต้องขาย/วัน เพื่อถึงเป้า" target. No client guard. Pace bar `(item.value / max) * 100` (line 218) where `max=Math.max(...,1)` is safe, but the *displayed number* is not guarded.
- `predict/page.tsx:66` `avgForecast = totalForecast7 / (forecast.length || 1)` is guarded — good. But `f.confidence`, `f.predicted`, `f.high` are rendered unguarded (`fmt(f.predicted)`, line 203); a null from backend → "฿NaN".
- `calendar/page.tsx:260` `pct = kpi.daily_avg_rev > 0 ? diff/.. : 0` is guarded — good. But `fmt(selected.avg_bill)` / `fmt(kpi.avg_bill)` unguarded if backend sends null.
**Issue:** These are money decision metrics; `NaN`/`Infinity` shown as a daily sales target is worse than a blank.
**Suggested fix:** Make `fmt`/`fmt1` null-safe: `function fmt(n:number){ return Number.isFinite(n) ? n.toLocaleString('th-TH',{maximumFractionDigits:0}) : '—'; }`. Apply across the batch (cheap, central, asymmetric benefit).
**Test plan:** Mock goals response with `daily_required:null` and `projected_pct:NaN`; assert UI shows "—" not "NaN". Same for predict `confidence:null`.

### [C4] Pages display backend void-leak / discount numbers that may be inflated (NO FE change needed — informational)
Per context, batch-5 found several POS endpoints miss `bill_net>0`, inflating gross/void/discount figures.
FE pages that *render those numbers as truth*: `voids` (void_amount, rev_loss_pct, avg_void), `payments` (discount_summary, total_gross/discount), `discounts` (total_discount, discount_rate_pct, by_order_type gross), `overview`/`channels`/`flash` (revenue totals), `goals`/`calendar` (actual revenue). No FE fix — fix is backend. Flagged so these pages are re-verified after the backend `bill_net>0` patch ships.
**Test plan:** After backend fix, eyeball that void amount / discount rate on these pages drop to expected values.

### [C5] `payments` and `discounts` show the SAME metric with DIFFERENT precision — owner sees two "discount rates"
`payments/page.tsx` `fmtPct` = `n.toFixed(1)+'%'` (1 decimal) for `discount_rate_pct`; `discounts/page.tsx` `fmt1` = `toLocaleString(...maximumFractionDigits:1)` for `discount_rate_pct`. Both also color-threshold at >5 / >2. Values come from different endpoints (`/pos/payments` vs `/pos/discounts`) that may compute the rate over different denominators (gross vs net+disc). 
**Issue:** Cross-page inconsistency (checklist #8) — the owner can open two pages and see e.g. "4.2%" vs "4.5%" for "อัตราส่วนลด" and lose trust. The `discounts` by_order_type row even recomputes rate client-side as `total_disc/(net_rev+total_disc)` (line 240-241), a third formula.
**Suggested fix:** Confirm both endpoints define discount rate identically (discount ÷ gross); document the canonical formula; use one shared `fmtPct`. If denominators differ by design, label them differently.
**Test plan:** Load both pages same month range; the headline อัตราส่วนลด% must match.

---

## MEDIUM

### [M1] No error state on the entire silent-fetch group
Same 14 pages as C1 render only `loading ? ... : data ? ... : "ไม่มีข้อมูล"`. With `.catch(console.error)`, a network failure leaves `data=null` → "ไม่มีข้อมูล" (looks like empty DB, not an error). Checklist #5. Fixed together with C1 by adding an `error` state + banner.
**Test plan:** Offline → expect error banner, not "ไม่มีข้อมูล".

### [M2] Timezone day-boundary on "today" (BKK 00:00–07:00)
- `flash/page.tsx:114-117` `new Date(d + 'T00:00:00')` then `toLocaleDateString('th-TH')` — parses as LOCAL time; on a UTC-deployed render path the displayed weekday could differ. Flash "today" date comes from backend `data.date`, mitigating, but the label formatting is local.
- `calendar/page.tsx:50-52` and `goals/page.tsx:44-45` derive the default month/year from client `new Date()`/`getMonth()` (local). `predict` divider uses `data.meta.today` from backend (good).
**Issue:** Checklist #7 — between 00:00–07:00 ICT a UTC clock is still "yesterday", so the default calendar month / goal month / flash header can show the wrong day at the day boundary.
**Suggested fix:** Prefer a backend-provided `as_of`/`today` (BKK) for default month selection rather than client clock; or compute with an explicit `Asia/Bangkok` offset. Low effort, prevents a confusing month-rollover edge.
**Test plan:** Set machine TZ to UTC, clock to 2026-06-01 00:30 ICT (= 2026-05-31 17:30 UTC); confirm calendar/goals default to June, not May.

### [M3] `categories` pie label references undefined `revenue_pct` field
`categories/page.tsx:172-174` label callback destructures `{ name, revenue_pct, percent }` but `pieData` objects only have `{name, value, fill}` — `revenue_pct` is `undefined`. It currently renders `percent*100` (the used var), so output is correct, but `revenue_pct` is dead/misleading and a future edit using it would print `NaN%`. Checklist #6.
**Suggested fix:** Drop `revenue_pct` from the destructure; keep `percent`.
**Test plan:** Pie slice labels show `xx.x%`; no `NaN%`.

### [M4] `bill-analysis` "บิล/วัน เฉลี่ย" uses fixed 30-day month
`bill-analysis/page.tsx:113` `fmt1(kpi.total_bills / (months * 30))`. Hardcodes 30 days/month and ignores the actual active-day count/date range in `data.period`. Mildly wrong average bills/day (e.g. 1-month = /30 even for Feb or a partial range). Not a crash, but a money-adjacent metric shown as truth.
**Suggested fix:** Divide by actual days in `period.start..end` (or backend `active_days`).
**Test plan:** Compare against calendar active_days for same range.

### [M5] `combos` / `items` search filters client-side over a `limit`-capped list
`combos` fetches `limit:50`, `items` fetches `limit:100`, then filter/sort client-side. A search for an item outside the top-N silently returns "ไม่พบ" even though it exists in the DB. `items` footer "จากทั้งหมด {data.items.length}" reinforces the cap as if it were the full menu.
**Suggested fix:** Pass the search term to the backend, or label the cap ("แสดง top 100 เมนู"). Low money impact, but can mislead a "why is my dish missing" check.
**Test plan:** Search a known low-volume item; confirm it's findable or the cap is labeled.

### [M6] `goals` target persisted only in component state (default 282000 hardcoded)
`goals/page.tsx:48-49` default target `282000` is a magic number reset on every reload; not stored. Owner re-types the target each visit, and the "projected vs target" money verdict silently uses the stale default until re-entered.
**Suggested fix:** Persist target per month (backend or at minimum localStorage) and surface where 282000 comes from.
**Test plan:** Set target, reload, confirm it sticks.

---

## LOW

### [L1] `predict` confidence interval `±` can display negative / asymmetric
`predict/page.tsx:204` `±{fmt(f.high - f.predicted)}`. If backend `high < predicted` (possible with a noisy model), shows `±-1,234`. Cosmetic but odd on a forecast card.
**Fix:** `±{fmt(Math.max(0, f.high - f.predicted))}` or show `low–high`.

### [L2] `predict` AreaChart `connectNulls` visually bridges actual↔forecast gap
`predict:167-168` both Areas use `connectNulls`; the seam at "today" can draw a misleading straight segment across the null between the last actual and first forecast.
**Fix:** Drop `connectNulls` on the boundary series, or add an overlapping anchor point.

### [L3] Redundant per-row / per-render `Math.max` recompute
- `bill-analysis:168` recomputes `maxAvg = Math.max(...dow_avg)` inside the `.map`, and `:201` likewise for order_types — O(n²). 
- `calendar:149,152` calls `Math.max(...days.map(...))` 3× for the same value already in `maxRev` (line 80).
Cosmetic perf; lists are small. Hoist out of the loop.

### [L4] Decimal/percent-scale assumption undocumented
Many pages assume backend `pct` is already 0–100 (e.g. `width:\`${o.pct}%\``, overview:237; channels SegmentBar). If any endpoint ever returns a 0–1 fraction, the segment bars collapse to ~1% width with no guard. Currently consistent (backend sends 0–100). Add a brief type comment so a future endpoint change is caught. Checklist #3.

---

## Notes for fixer
- C1 + M1 + M2 + C3 share one cheap refactor: a shared `safeFetch` helper + null-safe `fmt`. Highest asymmetric benefit (lean-bar approved) — one helper kills the recurring #1 FE bug across 14 pages.
- The "safe group" (overview/staff/tables/dow/hourly/channels/prep-forecast) is the reference pattern; copy its `error` state + `if(!res.ok) throw`.
- No page crashes were found on the happy path; all CRITICALs are failure-mode / wrong-truth issues, which is the money risk that matters here.
