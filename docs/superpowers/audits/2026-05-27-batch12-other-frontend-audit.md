# Batch 12 — VEXONHQ Non-POS Frontend Audit (READ-ONLY)

Date: 2026-05-27
Scope: 24 non-POS pages under `C:\Users\rapee\VEXONHQ\app\` + shared libs (`lib/invoice-api.ts`, `lib/slip-api.ts`, `lib/store-context-api.ts`) and `components/invoice/*`.
Auth header is injected globally by `components/AuthProvider.tsx` — missing `Authorization` not flagged.

## Summary

- CRITICAL: 2
- MEDIUM: 5
- LOW: 4

Headline: The biggest money-screen risk is **`delivery/page.tsx`** and **`revenue/page.tsx`**, both of which call `.then(r => r.json())` with **no `res.ok` check** — on a 500/401 the page parses an error body (often `{detail:...}`) into the data shape, then either silently renders an empty/garbled state OR divides by it. Delivery also has a hard **division-by-zero on `gross_total`** that produces `NaN%` / `Infinity%` widths. All write paths (invoices, bills, slips, categories, quick-entry, rules, store-context, page-permissions) are SAFE — they go through `res.ok`-checking helpers, so there is **no silent-success-on-failed-save** anywhere in this batch. Good.

Pages with NO findings (clean): invoices/* (delegate to `lib/invoice-api.ts` `request()` which checks `res.ok`), slips/*, statement/by-category, rules, admin/store-context, ai-review, categories, quick-entry, search, menu, supplier/price-trend, admin/page-permissions, receipts.

## ✅ Closure status (Session 44, 2026-05-28)

Both CRITICAL closed via the `safeFetch + fmt` refactor (commit `295de44`):
- **C1** `/delivery` no `res.ok` + divide-by-zero NaN% — ✅ `safeFetch<T>` + rose error UI + `fmtPct/safeNum` for the percentage widths
- **C2** `/revenue` no `res.ok` — ✅ `safeFetch<T>` + rose error UI

Remaining: 5 MEDIUM + 4 LOW (alerts page silent fetch, search suggestion parse, supplier light-theme cosmetic). None money-at-risk; money-write paths were already safe per the original report.

---

## [C1] delivery — no `res.ok` check + division by zero on `gross_total`

- File: `app/delivery/page.tsx:74-81` (fetch), `:219` and `:224` (Net% width), `:146` (`total_commission_pct`)
- Pages: `/delivery`
- Issue:
  1. **No `res.ok` guard.** `fetch(...).then(r => r.json()).then(setData)` — a 500/401 backend error body is parsed straight into `SummaryData`. There is no `error` state at all on this page; on failure it falls through to the empty state (`!data || data.platforms.length === 0`) or renders with `undefined` fields. This is exactly the Session-18 silent-failure class, on a money screen (Grab/Lineman Net Payout).
  2. **Division by zero.** `Math.round((p.net_total / p.gross_total) * 100)` (line 219) and `width: ${(p.net_total / p.gross_total) * 100}%` (line 224). If a platform has `gross_total === 0` (e.g. a month with only refunds/adjustments, or a fresh upload), this yields `NaN%` text and an invalid CSS width. `total_commission_pct` (line 146) comes from backend so is less risky, but the per-platform bar is computed client-side.
- Suggested fix:
  - Add `if (!res.ok) throw new Error('โหลดข้อมูล Delivery ไม่ได้')` and an `error` state + red error card (copy the pattern from `bills/payment` / `menu`).
  - Guard the ratio: `const netPct = p.gross_total > 0 ? (p.net_total / p.gross_total) * 100 : 0;` and clamp width to `Math.min(100, Math.max(0, netPct))`.
- Test plan: Point `NEXT_PUBLIC_API_URL` at a 500-returning stub → page must show an error card, not an empty "ยังไม่มีข้อมูล". Feed one platform with `gross_total: 0, net_total: 0` → Net% shows `0%`, bar width 0, no `NaN`.

## [C2] revenue — no `res.ok` check on the main breakdown fetch

- File: `app/revenue/page.tsx:75-82`
- Pages: `/revenue`
- Issue: Same pattern as C1 — `.then(r => r.json()).then(setData).catch(console.error)`. No `res.ok`. On a 500 the JSON error body becomes `BreakdownData`; the page then either crashes on `data.sources.length` (if `sources` is undefined the `!data || data.sources.length === 0` short-circuits OK, so likely silent-empty) or shows "ยังไม่มีข้อมูลรายรับ" masking a real backend error. This is a money screen (POS + Delivery + Bank revenue totals). Group `pct` math at line 141 is already guarded (`grand_total ? ... : 0`) — good — so the only gap is the missing `res.ok` + missing error state.
- Suggested fix: Add `if (!res.ok) throw...`, add an `error` state, render a red error card instead of falling through to the empty state. Mirror `bills/payment`.
- Test plan: 500 stub → error card shown, not "ยังไม่มีข้อมูลรายรับ".

---

## [M1] alerts — no `res.ok` check; 500 silently shows "no alerts"

- File: `app/alerts/page.tsx:90-97`
- Pages: `/alerts`
- Issue: `fetch(...).then(r => r.json()).then(setData).catch(console.error)`. No `res.ok`. On a 500 the error body is set as `AlertSummary`; `data?.alerts.filter(...)` returns `[]`, so the page renders the green "ไม่มี alert ในขณะนี้ — ทุกอย่างปกติดี" success state. A down alerts endpoint therefore looks like "everything is fine" — dangerous for an alert center (AP-due / budget-over alerts would be hidden). Not CRITICAL because no money is displayed/written, but it actively misleads.
- Suggested fix: Add `if (!res.ok) throw`; add an error state distinct from the empty state.
- Test plan: 500 stub → distinct error card, NOT the green all-clear.

## [M2] search — suggestions fetch parses without `res.ok` (minor) + main search OK

- File: `app/search/page.tsx:117-122`
- Pages: `/search`
- Issue: The mount-time suggestions fetch does `.then(r => r.json()).then(...).catch(() => {})` with no `res.ok`. A 500 body like `{detail:'...'}` has no `.suggestions`, so the guard `if (d.suggestions?.length)` saves it — falls back to static `SUGGESTIONS`. Low impact (enhancement-only), but the parse of a non-OK body is sloppy. The main `handleSearch` (line 151-168) and `fetchEmptyHints` (line 131) both check `res.ok` correctly.
- Suggested fix: Add `if (!r.ok) return;` before `.json()` in the suggestions effect. Cosmetic.
- Test plan: 500 on `/search/suggestions` → static chips still shown, no console parse noise.

## [M3] delivery / revenue — Recharts `formatter`/`tickFormatter` pass raw values without coercion in one spot

- File: `app/revenue/page.tsx:238-241` (trend tooltip `formatter={(v) => [compact.format(v), ...]}`)
- Pages: `/revenue`
- Issue: The monthly-trend tooltip formatter calls `compact.format(v)` where `v` is `any` and could be a string from `Record<string, number | string>` trend rows. Other formatters on the same page defensively use `Number(v) || 0` (e.g. YAxis line 235). This one does not. If a trend cell is a string, `Intl.NumberFormat.format('abc')` yields `"NaN"`. Delivery's equivalents (lines 243, 271) already wrap in `Number(v) || 0` — so revenue is the lone inconsistent one.
- Suggested fix: `compact.format(Number(v) || 0)` to match the rest.
- Test plan: Hover trend bars — values render as numbers, never `NaN`.

## [M4] bills/payment — slip-match `patchBill(c as Bill, 'paid')` cast can carry a `diff` field, harmless but `candidates` typed loosely

- File: `app/bills/payment/page.tsx:566`
- Pages: `/bills/payment`
- Issue: Not a money-write bug (the PATCH goes through `res.ok` correctly at line 220), but `patchBill(c as Bill, 'paid')` casts a `SlipCandidate` (which has an extra `diff`) to `Bill`; the optimistic summary recompute (line 231-235) iterates `prev.bills`, not the candidate, so it's fine. Flagging only because the `as Bill` cast hides that `c.id` must exist in `data.bills` for the optimistic update to reflect — if the candidate is from a different month than the currently-loaded `data`, the toast says "อัปเดต...แล้ว" but the visible table doesn't change. Confusing, not incorrect.
- Suggested fix: After slip-match confirm, call `load(month, statusFilter, vendorSearch)` to re-sync rather than relying on optimistic update for an off-screen row.
- Test plan: Match a slip to a bill not in the current month filter → table refreshes / shows correct state.

## [M5] bank-statement — classify POST swallows errors silently

- File: `app/bank-statement/page.tsx:125-148` (`handleClassify` `catch { /* silent */ }`), `:69-82` (`fetchReview` returns silently on `!res.ok`)
- Pages: `/bank-statement`
- Issue: `handleClassify` checks `res.ok` (good — no silent success) BUT the `catch` is empty (`// silent`), so a network/500 failure on a *classify write* shows no feedback — the row simply stays. The user can't tell if it saved. `fetchReview` also `return`s silently on `!res.ok`, leaving the prior list. The upload path (line 97-109) is correctly surfaced. Not CRITICAL (it doesn't falsely claim success — the row just doesn't disappear), but the user gets zero signal on failure.
- Suggested fix: On classify failure, set a small inline error / toast instead of silent. At minimum `console.error`.
- Test plan: 500 on `/bank-statement/classify/{id}` → user sees an error, row stays.

---

## [L1] delivery — table `<th>`/`<td>` use React fragments with `key` on children inside `.map` returning `<>...</>`

- File: `app/delivery/page.tsx:298-304` and `:311-323`
- Pages: `/delivery`
- Issue: `platforms_list.map((p) => (<>...<th key=.../>...</>))` — the fragment itself has no key, only its children do. React will warn "Each child in a list should have a unique key" for the fragment. Cosmetic (console warning), no visual bug.
- Suggested fix: Use `<Fragment key={p}>` from `react` instead of the shorthand `<>`.
- Test plan: Open `/delivery` with 2 platforms → no React key warning in console.

## [L2] revenue — donut `PieLabel` hides slices `< 3%` but legend still lists them (intended) — verify tiny-slice overlap

- File: `app/revenue/page.tsx:58-68`
- Pages: `/revenue`
- Issue: `if (pct < 3) return null` correctly suppresses cluttered labels; fine. Low note: `pct` here is the source `s.pct` (already a percent 0-100, confirmed by usage at line 191/210 `fmtDec.format(s.pct)%`), so the `< 3` threshold is in percent units — correct, not a decimal-vs-percent scale bug. No action needed; documented to confirm it was checked.
- Suggested fix: none.
- Test plan: n/a.

## [L3] quick-entry — `new Date().toISOString().slice(0,10)` for entry date (timezone)

- File: `app/quick-entry/page.tsx:138` (`entryDate` initial) and `:287` (`todayKey`)
- Pages: `/quick-entry`
- Issue: `toISOString()` is UTC. For a user in `Asia/Bangkok` (UTC+7) recording an entry between 00:00–07:00 local, `toISOString().slice(0,10)` returns *yesterday's* date. The default `entry_date` on the form and the "วันนี้" grouping key both inherit this. A late-night (after midnight) quick entry would default to the previous day and not group under "วันนี้". Slips/statement pages correctly use `timeZone: 'Asia/Bangkok'` in their formatters; quick-entry does not for this default.
- Suggested fix: Build the local date key manually: `` `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}` `` (uses local time), matching the `currentMonthKey()` pattern used elsewhere.
- Test plan: Set machine clock to 01:00 Asia/Bangkok → form date defaults to today's local date, and a saved entry groups under "วันนี้".

## [L4] supplier — light-theme page (`bg-gray-50`, `text-gray-900`) inconsistent with dark app theme

- File: `app/supplier/page.tsx:102` (`bg-gray-50`) and throughout (`text-gray-900`, `bg-white`, `border-gray-100`)
- Pages: `/supplier`
- Issue: Pure cosmetic — this page is built on light Tailwind grays while every other page (and `CLAUDE.md` style guide) uses dark semantic tokens (`bg-background`, `text-foreground`). On the dark app it will render as a jarring white island. No correctness/money impact. Trend bars use `Math.max(...s.data, 1)` so division-by-zero is already guarded — good.
- Suggested fix: Re-skin to dark tokens to match the rest of the app.
- Test plan: Visual — `/supplier` matches dark theme.

---

## Notes / things explicitly verified safe

- **All money writes check `res.ok`:** `bills/payment` PATCH (line 220), `quick-entry` POST/DELETE (errorText helper), `categories` CRUD, `bank-statement` classify (line 140), invoice confirm/reject/update (`lib/invoice-api.ts request()` line 212), slip upload/match/manual-match/delete/category (`lib/slip-api.ts readJsonOrThrow` line 158), rules CRUD, store-context CRUD, page-permissions toggle (line 110). No silent-success-on-failed-save in this batch.
- **export/page.tsx** correctly checks `res.ok` on both summary (line 101) and download (line 130) and shows error state. Division-safe.
- **receipts/search/supplier-price-trend** all check `res.ok` on their data fetches and have error states.
- **bills/payment** optimistic summary recompute (line 231) uses `?? 0` defaults — NaN-safe.
- `toISOString().slice(0,10)` also appears in `quick-entry` only (flagged L3); other date handling uses explicit `Asia/Bangkok` formatters or local `getFullYear/getMonth` keys.
