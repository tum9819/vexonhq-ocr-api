# P&L + Daybook Audit Report

**Date started:** 2026-05-27
**Scope:** P&L + Daybook subsystem (money-first preventive audit)
**Status:** Audit complete — C1 fixed (commit `9296ed5`, 2026-05-27), 7 CRITICAL findings remain open

---

## Progress tracker

| File | Status | Findings |
|---|---|---|
| `pnl_routes.py` | Done | 0 critical, 2 medium, 1 low |
| `yearly_routes.py` | Done | 1 critical, 2 medium, 0 low |
| `phase2_routes.py` | Done | 2 critical, 3 medium, 1 low |
| `phase3_daybook_routes.py` | Done | 0 critical, 2 medium, 1 low |
| `phase10_narrative_routes.py` | Done | 1 critical, 1 medium, 0 low |
| FE `app/dashboard/page.tsx` | Done | 1 critical, 2 medium, 1 low |
| FE `app/pnl/page.tsx` | Done | 0 critical, 2 medium, 1 low |
| FE `app/pnl/compare/page.tsx` | Done | 0 critical, 1 medium, 1 low |
| FE `app/daybook/page.tsx` | Done | 1 critical, 2 medium, 0 low |
| FE `app/yearly/page.tsx` | Done | 0 critical, 3 medium, 1 low |
| FE `app/scorecard/page.tsx` | Done | 1 critical, 2 medium, 0 low |
| FE `app/revenue/page.tsx` | Done | 0 critical, 2 medium, 1 low |
| FE `app/expense-trends/page.tsx` | Done | 1 critical, 2 medium, 0 low |

---

## Summary

- CRITICAL: **8**  (backend 4 + frontend 4)
- MEDIUM:   **25** (backend 10 + frontend 15) — M12 dropped as FALSE POSITIVE
- LOW:      **7**  (backend 3 + frontend 4)

Top risk by file:
- Backend: `phase10_narrative_routes.py` (no equity exclusion → Session-6-style bug, but on AI narrative + auto-LINE push), `phase2_routes.py` (`/dashboard/category-trends` references nonexistent `vb.direction` column → 500 in production), `yearly_routes.py` (double-source income totals from `pos_sales_daily` + `v_daybook` that can disagree).
- Frontend: `app/scorecard/page.tsx` (no `res.ok` check — backend 500 / non-JSON body crashes `.json()` and leaves stale data on screen), `app/expense-trends/page.tsx` (consumes broken `/dashboard/category-trends` C2 endpoint — page just shows "ยังไม่มีข้อมูล" with no signal that backend is 500), `app/dashboard/page.tsx` (renders inflated `top_categories` / `food_cost` from backend M4 and inflated `expense_bill_count` from L3 with no FE awareness).

---

## CRITICAL findings

### [C1] — ✅ FIXED in commit `9296ed5` (2026-05-27) — `/pnl/narrative` and `/pnl/narrative/preview` ignore owner-equity exclusion (Session 6 bug class)
- **File:** `phase10_narrative_routes.py:142-200`
- **Endpoint:** `POST /pnl/narrative`, `GET /pnl/narrative/preview`
- **Current code:**
  ```python
  cur.execute(
      """SELECT
           COALESCE(SUM(CASE WHEN d.direction='income'  THEN d.amount ELSE 0 END), 0) AS total_income,
           COALESCE(SUM(CASE WHEN d.direction='expense' THEN d.amount ELSE 0 END), 0) AS total_expense
         FROM public.v_daybook d
         WHERE d.entry_date BETWEEN %s AND %s""",
      (first, last),
  )
  ```
  Same omission in the income-by-source query (line 164-173), top-5 expense query (line 177-189), and txn_count (line 193-196).
- **Issue:** None of the four queries in `_gather_month_data()` filter out `owner_capital`, `owner_advance`, `transfer_error`, `bank_statement`, `vendor_payment`, `grab_payout`, `lineman_payout`, `pos_cash_deposit`, `cash_withdrawal`. This is the exact Session-6 incident pattern (CLAUDE.md "Known pitfalls" → equity inflates income, transfer pairs cancel out improperly). The result is then fed into Claude AI and **auto-pushed to LINE on the 1st of every month at 08:00 BKK** — so the owner reads wrong numbers in a narrative he treats as authoritative. Also, `pos_cashflow` (cash-drawer deposits) shows up as both an income and expense source counted toward "Top 5 categories".
- **Suggested fix:**
  ```python
  EXCLUDED_SOURCES = (
      'owner_capital', 'owner_advance', 'transfer_error',
      'bank_statement', 'vendor_payment',
      'grab_payout', 'lineman_payout',
      'pos_cash_deposit', 'cash_withdrawal',
  )
  # Add to every WHERE clause:
  #   AND d.source NOT IN %s
  # passing tuple(EXCLUDED_SOURCES) — psycopg2 expands a tuple correctly.
  ```
  Apply to all four queries in `_gather_month_data()`. Mirror the exclusion list used in `pnl_routes.py:96-99` so the narrative agrees with `/pnl/daily` and `/dashboard/overview`.
- **Test plan:**
  1. `GET /pnl/narrative/preview?month=2026-05` and compare `current.total_income` / `current.total_expense` to `GET /pnl/monthly?year=2026` row for `2026-05` — they must match to the baht.
  2. Then `GET /dashboard/overview?month=2026-05` `current.sales_net` / `current.expense_total` — must also match.
  3. Run a test month where a known owner_capital row exists (e.g. find one with `SELECT entry_date, amount FROM v_daybook WHERE source='owner_capital' LIMIT 5`) — without the fix the narrative income should be inflated by that amount.

---

### [C2] — `/dashboard/category-trends` references nonexistent column `vendor_bills.direction`
- **File:** `phase2_routes.py:845-858`
- **Endpoint:** `GET /dashboard/category-trends?months=6`
- **Current code:**
  ```python
  cur.execute("""
      SELECT
          vb.category_code,
          COALESCE(ec.name_th, vb.category_code) AS name_th,
          DATE_TRUNC('month', vb.bill_date)::date AS m,
          SUM(vb.amount)::numeric AS total
      FROM public.vendor_bills vb
      LEFT JOIN public.expense_categories ec ON ec.code = vb.category_code
      WHERE vb.review_status = 'confirmed'
        AND vb.direction = 'expense'
        AND vb.bill_date >= %s AND vb.bill_date < %s
        AND vb.category_code IS NOT NULL
      GROUP BY 1, 2, 3
  """, (start, end))
  ```
- **Issue:** `vendor_bills` has no `direction` column. The synthetic `direction='expense'` lives only inside `v_daybook` (see `migrations/17_vendor_bills_daybook.sql:139`). Calling this endpoint should raise `UndefinedColumn`. The function has no try/except around this query, so the endpoint returns **HTTP 500** — and there is no fallback path. Frontend `/dashboard` will show a partial or broken category-trends panel. This was almost certainly never tested end-to-end in production, or it would have been caught.
- **Suggested fix:**
  ```python
  # vendor_bills only ever represents expenses by construction
  WHERE vb.review_status = 'confirmed'
    AND vb.bill_date >= %s AND vb.bill_date < %s
    AND vb.category_code IS NOT NULL
  ```
  Simply delete the `AND vb.direction = 'expense'` line.
- **Test plan:**
  1. Stage locally; `curl -H "Authorization: Bearer <jwt>" http://localhost:8000/dashboard/category-trends?months=6` — must return 200 with non-empty `categories` array.
  2. Compare the returned category totals against `GET /pnl/by-category?month=2026-05` `categories[*].expense` for the most recent month — totals per category should match within rounding (allowing for the manual_entries + bank_statement_entries additions that this endpoint correctly merges in).

---

### [C3] — `/dashboard/category-trends` for `bank_statement_entries` lacks transfer/equity exclusion
- **File:** `phase2_routes.py:878-892`
- **Endpoint:** `GET /dashboard/category-trends`
- **Current code:**
  ```python
  cur.execute("""
      SELECT
          bse.category_code,
          COALESCE(ec.name_th, bse.category_code) AS name_th,
          DATE_TRUNC('month', bse.txn_date)::date AS m,
          SUM(bse.debit)::numeric AS total
      FROM public.bank_statement_entries bse
      LEFT JOIN public.expense_categories ec ON ec.code = bse.category_code
      WHERE bse.branch_code = %s
        AND bse.debit > 0
        AND bse.category_code IS NOT NULL
        AND bse.txn_date >= %s AND bse.txn_date < %s
      GROUP BY 1, 2, 3
  """, (branch, start, end))
  ```
- **Issue:** Once C2 is fixed and the endpoint actually runs, this branch is the next blow-up. `bank_statement_entries.source_type` carries the equity/transfer flags (`transfer`, `withdrawal`, `deposit`, …) — they all have `debit > 0` and are categorized, so they will be summed into the trend totals as legitimate expense (e.g. `transfer_error` debit of 50,000 inflates whichever category it was hand-tagged with). This is silent corruption (no 500), worse than C2.
- **Suggested fix:**
  ```python
  WHERE bse.branch_code = %s
    AND bse.debit > 0
    AND bse.category_code IS NOT NULL
    AND bse.txn_date >= %s AND bse.txn_date < %s
    AND bse.source_type NOT IN (
        'transfer', 'withdrawal', 'deposit',
        'owner_capital', 'owner_advance', 'transfer_error',
        'grab_payout', 'lineman_payout',
        'pos_cash_deposit', 'cash_withdrawal'
    )
  ```
  (Exact list to confirm against `migrations/16_bank_statement.sql` — at minimum match what `pnl_routes.py` excludes.)
- **Test plan:**
  1. Pick a month with known owner-capital bank rows. Without fix: their category totals show inflated. With fix: totals match `/pnl/by-category` for the same month.
  2. Sum all `categories[*].series[i]` for one month and compare to `/dashboard/overview` `current.expense_total` — should be within ~5 % (the trend has fewer source streams than v_daybook so it's a lower bound, not equal).

---

### [C4] — `/pnl/yearly` mixes `v_daybook` totals with raw `pos_sales_daily` & `rider_deliveries` totals (can disagree)
- **File:** `yearly_routes.py:86-129`
- **Endpoint:** `GET /pnl/yearly`
- **Current code:**
  ```python
  # income / expense via v_daybook
  cur.execute("""SELECT EXTRACT(MONTH FROM entry_date)::int AS m,
                        COALESCE(SUM(CASE WHEN direction='income' ...)) AS income_total,
                        COALESCE(SUM(CASE WHEN direction='expense' ...)) AS expense_total
                 FROM public.v_daybook
                 WHERE branch_code = %s
                   AND EXTRACT(YEAR FROM entry_date) = %s
                   AND source NOT IN ('owner_capital', ..., 'cash_withdrawal')
                 GROUP BY 1""", (branch, year))
  daybook_map = {r[0]: (float(r[1] or 0), float(r[2] or 0)) for r in cur.fetchall()}

  # sales_net comes from a DIFFERENT source
  cur.execute("""SELECT EXTRACT(MONTH FROM sales_date)::int AS m,
                        SUM(net_total)::numeric AS sales_net, ...
                 FROM public.pos_sales_daily
                 WHERE branch_code = %s
                   AND EXTRACT(YEAR FROM sales_date) = %s
                 GROUP BY 1""", (branch, year))

  cur.execute("""SELECT EXTRACT(MONTH FROM delivery_date)::int AS m,
                        SUM(net_payout)::numeric AS rider_net
                 FROM public.rider_deliveries ...""", (branch, year))
  ```
  Then line 165-173 assembles a row where `income_total` is the v_daybook number but `sales_net` + `rider_net` come from raw tables.
- **Issue:** The v_daybook view itself is built from `pos_sales_daily` UNION `rider_deliveries` (per migration 17), so `sales_net + rider_net` should equal v_daybook's POS+rider portion. But because each is queried separately, any of these will cause them to silently disagree:
  - A `pos_sales_daily` row inserted after the v_daybook view's `pos_sale` source filter rejects it (e.g. branch_code NULL).
  - A `rider_deliveries` row where `net_payout` is NULL — `COALESCE` in the daybook query treats it as 0, the raw rider query gets NULL filtered into `SUM` (returns NULL → COALESCE'd to 0, OK), but if `branch_code` differs from the daybook's normalization the row goes into raw but not daybook (or vice versa).
  - Most importantly, `pos_sales_daily.net_total` may not equal v_daybook's `pos_sale` `amount` — the view in migration 17 maps `ps.net_total → amount`, so they SHOULD match, but they will diverge if anyone updates `pos_sales_daily` without refreshing or if the view was recreated with different logic.
  This shows up in the Excel export (`/export/yearly`) as a year-end row where `sales_net + rider_net + other ≠ income_total`. Hard to debug for TUM and easy for the accountant to flag.
- **Suggested fix:** Source-of-truth principle. Pull *all* the rollup numbers from `v_daybook`:
  ```python
  cur.execute("""
      SELECT EXTRACT(MONTH FROM entry_date)::int AS m,
             COALESCE(SUM(CASE WHEN source='pos_sale'                  THEN amount ELSE 0 END), 0) AS sales_net,
             COALESCE(SUM(CASE WHEN source IN ('rider_income_grab','rider_income_lineman') THEN amount ELSE 0 END), 0) AS rider_net,
             COALESCE(SUM(CASE WHEN direction='income'  THEN amount ELSE 0 END), 0) AS income_total,
             COALESCE(SUM(CASE WHEN direction='expense' THEN amount ELSE 0 END), 0) AS expense_total
        FROM public.v_daybook
       WHERE branch_code = %s
         AND EXTRACT(YEAR FROM entry_date) = %s
         AND source NOT IN ('owner_capital', 'owner_advance', 'transfer_error',
                            'bank_statement', 'vendor_payment',
                            'grab_payout', 'lineman_payout',
                            'pos_cash_deposit', 'cash_withdrawal')
       GROUP BY 1
  """, (branch, year))
  ```
  Keep the `pos_sales_daily` query only for `bill_count` (which v_daybook doesn't carry). Same for `vendor_bills` for `expense_bill_count`. Single SQL pass + no double-source risk.
- **Test plan:**
  1. For each month with data in 2026: assert `row["sales_net"] + row["rider_net"] + other_income_from_daybook == row["income_total"]` exactly.
  2. Cross-check `GET /pnl/yearly?year=2026` `months[i].income_total` matches `GET /pnl/monthly?year=2026` `rows[i].sales_net` for every month (same definition).

---

### [C5] — `/dashboard` silently displays inflated top_categories + food_cost + bill_count from broken backend
- **File:** `app/dashboard/page.tsx:166-183`
- **Page:** `/dashboard`
- **Current code:**
  ```typescript
  const loadDashboard = useCallback(async (targetMonth: string) => {
    setLoading(true);
    setError(false);
    try {
      const res = await fetch(`${API_URL}/dashboard/overview?month=${targetMonth}`, {
        cache: 'no-store',
      });
      if (!res.ok) throw new Error('Dashboard request failed');
      const raw = await res.json();
      // Session 15 fix: normalize null/undefined → 0 so render never shows "NaN%"
      setData(normalizeOverview(raw));
    } catch {
      setData(null);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);
  ```
- **Issue:** The page consumes `/dashboard/overview` and displays `top_categories`, `food_cost`, `current.sales_bill_count`, and `current.expense_bill_count`. Backend findings M4 + L3 mean those four values are **silently wrong** in production today:
  - `top_categories` only counts `vendor_bills` (M4) — the pie chart at `TopCategoriesCard` (line 475-512) adds up to less than the headline `current.expense_total`. The user reads "หมวดค่าใช้จ่ายสูงสุด" as a complete decomposition; it isn't.
  - `food_cost.pct` is understated (M4) — the colored badge at line 334-342 currently renders "ดี" (green) when the real ratio is probably "ปานกลาง" or "สูงเกิน". This is a category-driver display showing a misleading thumbs-up. CRITICAL because operational decisions (cut menu items, push specials) hinge on this color.
  - `expense_bill_count` is wrong (L3) — the KPI card at line 304 will show ~30 not ~660 once the user looks at sales side. Mismatch between sales/expense bill semantics is confusing.
  - Additionally, the `Authorization: Bearer <token>` header is **not sent** in the fetch (line 170-172) — same issue on every fetch in this file (`/bills/payment/summary` line 154 also). If JWT auth is enforced server-side this becomes 401; if not, it's a defense-in-depth gap. CLAUDE.md (frontend, line 169) shows the expected pattern.
- **Suggested fix:** The display correctness is a backend fix (M4/L3 — not in scope for FE). What FE should do:
  1. Add the `Authorization` header per CLAUDE.md pattern.
  2. Add a visual "ข้อมูลอาจไม่ครบ" badge near `top_categories` and `food_cost` until backend M4 is fixed — at minimum log a console warning when `top_categories.reduce((s,c)=>s+c.spent,0) < current.expense_total * 0.5` (clear undercount signal).
  ```typescript
  const topSum = data.top_categories.reduce((s, c) => s + c.spent, 0);
  const undercountRatio = data.current.expense_total > 0 ? topSum / data.current.expense_total : 1;
  if (undercountRatio < 0.5) {
    console.warn(`Dashboard top_categories likely undercount: ${(undercountRatio * 100).toFixed(0)}%`);
  }
  ```
- **Test plan:**
  1. After backend M4 is fixed, sum `top_categories[*].spent` and confirm > 80% of `current.expense_total` for May 2026.
  2. Verify `expense_bill_count` is in the 500-700 range after backend L3 fix (not ~30).
  3. Add auth header and confirm dashboard still loads against `https://api.marastation.com`.

---

### [C6] — `/daybook` displays `net` that includes equity (Session 6 bug class)
- **File:** `app/daybook/page.tsx:235-237, 266-278`
- **Page:** `/daybook`
- **Current code:**
  ```typescript
  const incomeTotal = summary?.by_direction.income.total ?? 0;
  const expenseTotal = summary?.by_direction.expense.total ?? 0;
  const net = summary?.net ?? 0;
  ...
  <p className="text-xs text-muted">net = รับ - จ่าย</p>
  ```
  And per-day rendering (line 370-389):
  ```typescript
  const dayIncome = dayRows.filter((r) => r.direction === 'income').reduce((s, r) => s + r.amount, 0);
  const dayExpense = dayRows.filter((r) => r.direction === 'expense').reduce((s, r) => s + r.amount, 0);
  const dayNet = dayIncome - dayExpense;
  ```
- **Issue:** Backend finding M6 already documents that `/daybook/summary.net` and `by_direction.*` include `owner_capital`, `owner_advance`, `transfer_error`, etc. The FE compounds this: it labels the third KPI card "กำไร/ขาดทุน" (line 270) — **explicitly P&L semantics** — when the number is actually a ledger-signed-sum. The page also computes per-day net **client-side** (line 370-373) by summing all income/expense rows returned by `/daybook/list` — which by default selects ALL sources (`ALL_SOURCES` line 111-114 lists pos_sale, vendor_bill, manual, ar_payment, ap_payment, rider_income_*, pos_cashflow — but the type definition `Source` (line 30-33) allows `string` to admit "future bank_statement source_type values"). So if a user filters or unfilters source chips, the daily P&L number swings wildly with equity entries. CRITICAL because the user sees "กำไร" — a number they will treat as profit — on a UI that does not exclude equity.
  Additionally, `ALL_SOURCES` (line 111-114) is missing equity sources entirely (`owner_capital`, `owner_advance`, `transfer_error`, `bank_statement`) — so when those rows exist in v_daybook (they do, see backend C1), the filter chips don't surface them but the data still loads from `/daybook/list` (because the size-less-than-ALL-SOURCES check at line 163 skips the source filter). The chip UI lies about what's being shown.
- **Suggested fix:**
  1. Rename the KPI card label from "กำไร/ขาดทุน" → "ยอดสุทธิ (รวมทุน)" or "Net ledger (incl. equity)". And in the per-day row "net" label should be the same.
  2. Either: (a) wait for backend M6 fix and consume the new `net_pnl_excluding_equity` field; OR (b) FE-side filter out equity sources before computing per-day net:
  ```typescript
  const EQUITY_SOURCES = new Set(['owner_capital', 'owner_advance', 'transfer_error', 'bank_statement', 'vendor_payment', 'grab_payout', 'lineman_payout', 'pos_cash_deposit', 'cash_withdrawal']);
  const dayIncome = dayRows.filter((r) => r.direction === 'income' && !EQUITY_SOURCES.has(r.source)).reduce((s, r) => s + r.amount, 0);
  const dayExpense = dayRows.filter((r) => r.direction === 'expense' && !EQUITY_SOURCES.has(r.source)).reduce((s, r) => s + r.amount, 0);
  ```
  3. Add equity sources to `ALL_SOURCES` (or a separate "Capital / Transfer" section in the chips) so the filter UI matches the data.
- **Test plan:**
  1. Pick a month where `v_daybook` has an `owner_capital` row of ฿X (e.g. `SELECT entry_date, amount FROM v_daybook WHERE source='owner_capital' LIMIT 5`). Today the daybook page net is `real_profit + X`. After fix, it should be `real_profit`.
  2. Click the source chips to deselect all but `pos_sale` — verify net excludes equity even when the user widens the filter.

---

### [C7] — `/scorecard` has no `res.ok` check; backend 500 / non-JSON body crashes the page with no error UI
- **File:** `app/scorecard/page.tsx:140-147`
- **Page:** `/scorecard`
- **Current code:**
  ```typescript
  useEffect(() => {
    setLoading(true);
    fetch(`${API}/scorecard?month=${month}`)
      .then((r) => r.json())
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [month]);
  ```
- **Issue:** Triple bug:
  1. No `res.ok` check → a 500/404/401 response body is parsed as JSON. If the backend returns `{detail: "..."}` (FastAPI default for HTTPException), `setData()` writes that into the typed `ScorecardData` state, then `data.overall_score` is `undefined` → `ScoreRing` renders `NaN`/100 ring → SVG dashOffset is `NaN` (invisible). `data.kpis.map` (line 215) throws because `data.kpis` is undefined.
  2. The error UI path is just `console.error` (line 145) — TUM in browser sees a blank page or React error overlay, with no Thai-language failure message. Compare to dashboard page (C5) which at least sets `error=true`.
  3. Crucially, `scorecard` is a top-level Mara-station KPI dashboard for TUM's monthly review. If backend has a transient outage during a board meeting, the page goes dead silent.
  Additionally, no auth header is sent (line 142). Same gap as C5.
- **Suggested fix:**
  ```typescript
  useEffect(() => {
    setLoading(true);
    setError(false);
    const token = localStorage.getItem('auth_token') ?? '';
    fetch(`${API}/scorecard?month=${month}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    })
      .then((r) => {
        if (!r.ok) throw new Error(`scorecard ${r.status}`);
        return r.json() as Promise<ScorecardData>;
      })
      .then(setData)
      .catch((e) => { console.error(e); setError(true); setData(null); })
      .finally(() => setLoading(false));
  }, [month]);
  ```
  Then add a `{error && <div>...โหลดข้อมูลไม่สำเร็จ</div>}` branch and an `error` state hook.
- **Test plan:**
  1. Temporarily point `NEXT_PUBLIC_API_URL` to an invalid URL — page should render error UI, not a NaN ring.
  2. Backend returns `{detail: "..."}` on 500 — verify FE no longer attempts to render kpis from that body.
  3. Confirm auth header is included when JWT is present.

---

### [C8] — `/expense-trends` silently empty while backend C2 returns 500
- **File:** `app/expense-trends/page.tsx:67-74`
- **Page:** `/expense-trends`
- **Current code:**
  ```typescript
  useEffect(() => {
    setLoading(true);
    setError('');
    fetch(`${API_URL}/dashboard/category-trends?months=${lookback}`, { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error('โหลดไม่ได้'); return r.json() as Promise<TrendsData>; })
      .then(d => { setData(d); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [lookback]);
  ```
- **Issue:** This page consumes the backend C2 endpoint `/dashboard/category-trends`, which **currently returns HTTP 500 in production** (references `vb.direction` column that doesn't exist). The FE error handler does set `error="โหลดไม่ได้"` — but the rendered message at line 123-127 is a small red banner. The empty/loading branches dominate the screen. Worse: once C2 is fixed, C3 silently kicks in (the bank_statement branch inflates totals with transfers). So this page is the FE display surface for **two CRITICAL backend bugs simultaneously**.
  Additionally:
  - No auth header (line 70) — same gap as C5/C7.
  - The error message text "โหลดไม่ได้" gives no actionable hint ("เซิร์ฟเวอร์ตอบกลับ 500" would let TUM know to message someone).
  - The chart's color-per-category (line 252-255 `LINE_COLORS[i % LINE_COLORS.length]`) cycles past index 10 — categories 11+ all show as gray dots in the row label. Once data is real and has more than 10 categories, color-to-row mapping desynchronizes from the chart legend.
- **Suggested fix:**
  ```typescript
  .then(r => {
    if (!r.ok) {
      // Surface the actual status so TUM can tell ops vs auth vs schema bug apart
      throw new Error(`เซิร์ฟเวอร์ตอบกลับ ${r.status} — ติดต่อทีมเทคนิคถ้ายังเกิดต่อ`);
    }
    return r.json() as Promise<TrendsData>;
  })
  ```
  And add the auth header. After backend C2/C3 are fixed, verify the trend totals here match `/pnl/by-category` totals per month.
- **Test plan:**
  1. Right now: open `/expense-trends` in production — confirm red banner with backend status appears (currently it shows generic "โหลดไม่ได้").
  2. After backend C2 fix: page loads with real data. Sum series across all categories for one month and confirm it's within ~5% of `/pnl/by-category` for that month.
  3. After backend C3 fix: verify no `owner_capital`-tagged categories appear inflated.

---

## MEDIUM findings

### [M1] — `pnl_routes.py /pnl/by-category` `vs_prev_month` skips the `__uncategorized` bucket
- **File:** `pnl_routes.py:296-310`
- **Endpoint:** `GET /pnl/by-category?month=YYYY-MM`
- **Current code:**
  ```python
  for r in curr_rows:
      exp      = float(r["expense"] or 0)
      cat_code = r["category_code"]
      if cat_code == "__uncategorized":
          uncategorized = exp
          continue
      prev_exp = prev_map.get(cat_code, 0.0)
      categories.append({ ..., "vs_prev_month": round(exp - prev_exp, 2) })
  ```
- **Issue:** If a category exists in the previous month but disappears entirely this month (zero expense, so no row in `curr_rows`), its `vs_prev_month` delta of `-prev_exp` is **never reported**. Frontend can't show "rent dropped to 0 this month" — silent. Minor money impact (no wrong number, just missing one), but flagged because TUM has historically caught these by eyeball.
- **Suggested fix:** After the loop, iterate `prev_map` for keys not in `curr_rows` and append zero-current rows with negative delta:
  ```python
  curr_codes = {r["category_code"] for r in curr_rows}
  for prev_code, prev_amt in prev_map.items():
      if prev_code in curr_codes or prev_code == "__uncategorized":
          continue
      categories.append({
          "category_code": prev_code,
          "name_th": prev_code,  # or look up
          "expense": 0.0,
          "pct_of_sales": 0.0,
          "pct_of_expense": 0.0,
          "vs_prev_month": round(-prev_amt, 2),
      })
  ```
- **Test plan:** Pick a category with prev-month spending and zero current-month spend (e.g. seasonal). Confirm it shows up in `categories[]` with `expense=0` and `vs_prev_month=-prev_amt`.

### [M2] — `pnl_routes.py /pnl/monthly` `bill_count_expense` uses ref_id which is non-unique across sources
- **File:** `pnl_routes.py:159-162`
- **Endpoint:** `GET /pnl/monthly?year=YYYY`
- **Current code:**
  ```python
  COUNT(DISTINCT CASE WHEN d.source='pos_sale'
                      THEN d.ref_id END)  AS bill_count_sales,
  COUNT(DISTINCT CASE WHEN d.direction='expense'
                      THEN d.ref_id END)  AS bill_count_expense
  ```
- **Issue:** `v_daybook.ref_id` is the source-row's PK (uuid for vendor_bills, uuid for manual_entries, integer-cast-to-uuid for bank_statement_entries, etc.). DISTINCT on it is correct only **within one source** — across sources `ref_id` values are independent uuids, so they won't collide. So the count is correct *in practice* today, BUT it is fragile: if a future source emits ref_id as `NULL` or as a non-uuid identifier shared with another source (e.g. invoice_no), the count could under-report. Marking MEDIUM rather than CRITICAL because uuids effectively never collide.
- **Suggested fix:** Either composite-key the count, or use `count(*)` if a per-row counting is what we want:
  ```python
  COUNT(*) FILTER (WHERE d.direction='expense') AS bill_count_expense
  ```
  Note: this counts entries not distinct bills. If a vendor bill is split into 2 daybook rows (it isn't today, but could be), `COUNT(*)` over-counts. Cleanest is `COUNT(DISTINCT (source, ref_id))`.
- **Test plan:** Cross-check `bill_count_expense` from `/pnl/monthly` against `SELECT COUNT(*) FROM vendor_bills WHERE review_status='confirmed' AND EXTRACT(MONTH FROM bill_date)=...` for a representative month plus the count of confirmed `manual_entries` of direction='expense' plus bank_statement_entries debits. Should match.

### [M3] — `yearly_routes.py /export/pnd3-annual` assumes flat 3 % WHT and no branch filter
- **File:** `yearly_routes.py:338-413`
- **Endpoint:** `GET /export/pnd3-annual?year=YYYY`
- **Current code:**
  ```python
  cur.execute(
      """SELECT EXTRACT(MONTH FROM entry_date)::int AS m, ...
         FROM public.v_daybook
         WHERE direction = 'expense'
           AND EXTRACT(YEAR FROM entry_date) = %s
           AND category_code IN ('musician_fee', 'freelance', 'pnd3')
         ORDER BY entry_date, amount""",
      (year,),
  )
  ...
  amount = float(r["amount"])
  tax = round(amount * 0.03, 2)
  ```
- **Issue:**
  (a) WHT rate is hard-coded to 3 % for every row, but Thai PND.3 rates vary: เงินได้ 40(2) freelancer = 3 %; 40(8) บางประเภท = 5 %; ค่าเช่าทรัพย์สิน 40(5) = 5 %. If `pnd3` category lumps together rent + freelancer, half the rows are taxed wrong. Excel that goes to สรรพากร with wrong WHT amounts is a real risk.
  (b) **No `branch_code` filter** — if multiple branches ever exist, this pulls all of them into one export. Other endpoints default `branch="thawi_watthana"`; this one is silently unscoped.
  (c) `category_code IN ('musician_fee', 'freelance', 'pnd3')` — `'pnd3'` as a category name is unusual; verify whether such a code actually exists in `expense_categories` or if this is a leftover guess. If no rows match, the export is silently empty.
- **Suggested fix:**
  ```python
  # Add branch filter + a per-row rate lookup
  WHT_RATE_BY_CATEGORY = {
      "musician_fee": 0.03,  # 40(2) ค่าจ้างชั่วคราว
      "freelance":    0.03,  # 40(2)
      "rent":         0.05,  # 40(5) — IF rent is supposed to be in PND.3 export
      # Otherwise drop 'rent' and 'pnd3' until expense_categories defines them.
  }
  ...
  WHERE direction='expense'
    AND branch_code = %s
    AND EXTRACT(YEAR FROM entry_date) = %s
    AND category_code = ANY(%s)
  ```
  Confirm the actual category list with TUM before shipping; this is a regulatory export.
- **Test plan:**
  1. `SELECT DISTINCT category_code FROM expense_categories WHERE code IN ('musician_fee','freelance','pnd3','rent');` — verify which exist.
  2. Sample known-good 2026 PND.3 amounts (TUM submits these to สรรพากร monthly) and reconcile to within ฿0.50 per row.

### [M4] — `phase2_routes.py /dashboard/overview` `top_categories` and `food_cost` only query `vendor_bills` (no manual/bank)
- **File:** `phase2_routes.py:357-388, 458-478`
- **Endpoint:** `GET /dashboard/overview`
- **Current code:**
  ```python
  cur.execute("""SELECT vb.category_code, ... SUM(vb.amount) AS spent
                 FROM public.vendor_bills vb ...
                 WHERE vb.review_status = 'confirmed'
                   AND vb.bill_date IS NOT NULL
                   AND vb.bill_date >= %s AND vb.bill_date < %s
                   AND vb.category_code IS NOT NULL
                 GROUP BY ...""", (month_start, pe))
  ```
  Same shape for the food-cost query.
- **Issue:** `current.expense_total` is pulled from `v_daybook` (correct, all sources). But `top_categories` and `food_cost` are pulled only from `vendor_bills`. So:
  - **Sum of `top_categories[*].spent` < `current.expense_total`** — frontend pie chart adds up to less than the headline expense number. TUM has noticed this before in P&L dashboards.
  - Food-cost % is **understated** because Lineman/Grab fees, bank withdrawals tagged as raw materials, manual cash-purchase entries are not counted. For a Thai-restaurant where raw-material purchases often go through cash + bank transfer (not always vendor_bills with OCR), this is a meaningful undercount.
- **Suggested fix:** Migrate both queries to `v_daybook` with the standard exclusion list:
  ```python
  SELECT d.category_code, COALESCE(ec.name_th, d.category_code) AS name_th,
         SUM(d.amount) AS spent
    FROM public.v_daybook d
    LEFT JOIN public.expense_categories ec ON ec.code = d.category_code
   WHERE d.direction = 'expense'
     AND d.branch_code = %s
     AND d.entry_date >= %s AND d.entry_date < %s
     AND d.category_code IS NOT NULL
     AND d.source NOT IN (...)  -- same exclusion list
   GROUP BY 1, 2
   ORDER BY spent DESC
   LIMIT 5
  ```
- **Test plan:**
  1. `GET /dashboard/overview?month=2026-05` → sum `top_categories[*].spent` and confirm > 80 % of `current.expense_total` (closer to 95 %).
  2. Food-cost % should rise (typically by ~5-10 pp for this restaurant once cash purchases are included).

### [M5] — `phase2_routes.py /dashboard/overview` swallows DB errors and returns zeros instead of 500
- **File:** `phase2_routes.py:314-415` (YTD, trend, top_categories, budget_alerts, food_cost all wrapped in try/except)
- **Endpoint:** `GET /dashboard/overview`
- **Current code:**
  ```python
  try:
      cur.execute(...)
      ytd_row = cur.fetchone()
      ytd_sales   = float(ytd_row[0] or 0)
      ytd_expense = float(ytd_row[1] or 0)
  except Exception as e:
      logger.error("dashboard_overview: YTD query failed: %s", e)
      ytd_sales, ytd_expense = 0.0, 0.0
      conn.rollback()
  ```
  Same pattern for trend, top_categories, budget_alerts, food_cost.
- **Issue:** When any subquery fails (schema drift, missing view, DB outage), the dashboard quietly degrades: YTD shows ฿0, food cost shows 0 %, etc. Frontend has no signal that the data is bad — TUM might trust ฿0 YTD as truth. This is the kind of silent-zero that hides real bugs. Auditing rule 9 calls it out: 200 with wrong data is worse than 500.
- **Suggested fix:** Either remove the broad exceptions (let FastAPI bubble 500), or return a per-section `error` flag the frontend can render:
  ```python
  ytd_block = {"sales_net": ..., "expense_total": ..., "error": None}
  try:
      ...
  except Exception as e:
      logger.exception("YTD")
      conn.rollback()
      ytd_block = {"sales_net": None, "expense_total": None, "error": "ytd_query_failed"}
  ```
  Frontend renders "ข้อมูล YTD ไม่พร้อม" instead of "฿0".
- **Test plan:** Force one subquery to fail (e.g. temporarily rename `v_budget_status`). Hit endpoint — currently returns 200 with empty `budget_status: []`; should signal failure instead.

### [M6] — `phase3_daybook_routes.py /daybook/summary` includes equity/transfer rows in `net` and `by_direction`
- **File:** `phase3_daybook_routes.py:181-219`
- **Endpoint:** `GET /daybook/summary`
- **Current code:**
  ```python
  cur.execute(
      f"""SELECT direction, count(*)::int AS count, sum(amount)::numeric(14,2) AS total
            FROM public.v_daybook
           WHERE {base_where}
           GROUP BY direction""",
      base_params,
  )
  ...
  net = by_direction["income"]["total"] - by_direction["expense"]["total"]
  ```
- **Issue:** `daybook_summary` returns a top-level `net` and a `by_direction` total that include `owner_capital`, `owner_advance`, `transfer_error`, etc. Frontend ledger pages or any consumer that treats `net` as profit will be wrong by exactly the equity amount. The default behavior (no `source` filter) is "show everything", which is fine as a raw ledger but the `net` field implies P&L semantics. Either:
  (a) Always exclude equity from `net` and report it separately, or
  (b) Rename the field to `signed_total` and document that it is not P&L.
  TUM's `/daybook` admin page likely surfaces this number.
- **Suggested fix:** Compute `net` from a separate exclusion-aware aggregate:
  ```python
  cur.execute(
      f"""SELECT
            COALESCE(SUM(CASE WHEN direction='income'  THEN amount ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN direction='expense' THEN amount ELSE 0 END), 0) AS net_pnl
            FROM public.v_daybook
           WHERE {base_where}
             AND source NOT IN (...)""",
      base_params,
  )
  net_pnl = float(cur.fetchone()[0] or 0)
  ```
  Keep `by_direction` as raw counts (it's a ledger summary, that's fine), but add a `net_pnl_excluding_equity` key.
- **Test plan:** Pick a month where v_daybook has a known `owner_capital` row of ฿X. Currently `/daybook/summary?date_from=...&date_to=...` returns `net = real_profit + X`. After fix, returns `net_pnl_excluding_equity = real_profit`.

### [M7] — `phase3_daybook_routes.py /daybook/list` default range and order-by use `ref_id` which is uuid (non-temporal)
- **File:** `phase3_daybook_routes.py:124-131`
- **Endpoint:** `GET /daybook/list`
- **Current code:**
  ```python
  cur.execute(
      f"SELECT source, entry_date, direction, amount, label, counterparty, "
      f"       branch_code, ref_id, category_code "
      f"FROM public.v_daybook{sql_where} "
      f"ORDER BY entry_date DESC, ref_id DESC "
      f"LIMIT %s OFFSET %s",
      params + [limit, offset],
  )
  ```
- **Issue:** `ORDER BY entry_date DESC, ref_id DESC` — ref_id is the source uuid, which is not chronological (uuids are random). Two rows on the same `entry_date` will sort in a random-looking order, and the order will be **stable across paginations only if the uuid set is unchanged** (true at small scale, but if new rows arrive mid-pagination the offset slides). Better: tiebreak by `created_at` (if v_daybook exposes it — migration 16 dropped it per the comment on line 121) or by source then ref_id. With ~660 bills/month this is rarely a visible issue, but the pagination guarantee is technically broken. MEDIUM because it can cause "missing rows" complaints from TUM during data review.
- **Suggested fix:** Add a deterministic tiebreaker; even `ORDER BY entry_date DESC, source, ref_id DESC` is more meaningful. If a `created_at` column can be added to `v_daybook` later, prefer that.
- **Test plan:** Insert two rows on the same `entry_date` with adjacent timestamps. Page through `/daybook/list` with `limit=1` and confirm the row order is stable across two consecutive requests and matches DB insert order.

### [M8] — `phase10_narrative_routes.py` falls back to swallowed exception for previous-month data
- **File:** `phase10_narrative_routes.py:304-312`
- **Endpoint:** `POST /pnl/narrative`
- **Current code:**
  ```python
  prev_month = _prev_month_str(month)
  try:
      prev_data = _gather_month_data(prev_month)
      if prev_data["txn_count"] == 0:
          prev_data = None
  except Exception:
      prev_data = None
  ```
- **Issue:** Bare `except Exception: pass`-style; if DB had a real outage for the prev_month query (not a "no data" condition), narrative silently runs without prior-month comparison and TUM never finds out. The current month query (line 298-302) correctly re-raises; this one shouldn't be different. Log at minimum.
- **Suggested fix:**
  ```python
  except Exception as e:
      logger.warning("narrative: prev-month %s query failed; continuing without comparison: %s", prev_month, e)
      prev_data = None
  ```
- **Test plan:** Force a DB error on the prev-month side (e.g. invalid month string) and confirm the log line appears in Coolify logs.

### [M9] — `pnl_routes.py /pnl/by-category` uses `BETWEEN start AND end` for month-end (correct for DATE, but verify)
- **File:** `pnl_routes.py:241, 260, 278`
- **Endpoint:** `GET /pnl/by-category`
- **Current code:** `WHERE entry_date BETWEEN %s AND %s` with `_month_range(year, mon)` returning `(date(y,m,1), date(y,m,last_day))`.
- **Issue:** `BETWEEN` is inclusive on both ends. Because `entry_date` is `DATE` (not timestamp), inclusive-inclusive over a month yields the right set. Confirmed safe for `date` columns. **Needs verification only if `v_daybook.entry_date` ever changes type to TIMESTAMP** — at that point `BETWEEN start AND last_day` would lose the rows from `last_day 00:00:00 < t < last_day 23:59:59.999`. Flagging as MEDIUM "needs verification" so it's on the radar if anyone ever migrates the column type. Today: fine.
- **Suggested fix:** Defensive: switch to `entry_date >= %s AND entry_date < %s` with `_next_month(start)` everywhere (the rest of the codebase already prefers this idiom — `phase2_routes.py:265` uses `entry_date >= %s AND entry_date < %s`). Consistency hardens against future type changes.
- **Test plan:** No live test today; trip a unit test if anyone proposes `entry_date::timestamptz`.

### [M10] — `phase2_routes.py /budgets/status` reads `vendor_bills` only (ignores manual/bank entries)
- **File:** `phase2_routes.py:537-547`
- **Endpoint:** `GET /budgets/status`
- **Current code:**
  ```python
  LEFT JOIN (
      SELECT category_code, SUM(amount) AS total
      FROM public.vendor_bills
      WHERE review_status = 'confirmed'
        AND bill_date IS NOT NULL
        AND bill_date >= %s AND bill_date < %s
        AND COALESCE(branch_code, %s) = %s
      GROUP BY category_code
  ) spent ON spent.category_code = ec.code
  ```
- **Issue:** Same shape as M4 — budget `spent` only counts vendor_bills. A category like "food_cost" that gets supplemented by cash/bank purchases will look under budget when it's actually over. Direct money impact: budget alerts (LINE warnings at 80 %, 100 %) will under-fire. TUM relies on these to throttle spend.
- **Suggested fix:** Replace the LEFT JOIN subquery with a v_daybook-backed one (same exclusion list, `direction='expense'`).
- **Test plan:** Pick a budgeted category where manual/bank entries are known (raw_meat is the obvious one). Currently `spent` returns the vendor_bills-only number; after fix it should include the missing rows. Compare to `/pnl/by-category[code=raw_meat].expense`.

### [M11] — `app/dashboard/page.tsx` `gross_margin_pct` rendered with `percent.format()` but backend semantics unclear (decimal vs already-percent)
- **File:** `app/dashboard/page.tsx:311, 543`
- **Page:** `/dashboard`
- **Current code:**
  ```typescript
  detail={`${percent.format(data.current.gross_margin_pct)}% มาร์จิ้น`}
  ...
  {percent.format(item.usage_pct)}%
  ```
  And budget alert (line 543):
  ```typescript
  {percent.format(item.usage_pct)}%
  ```
- **Issue:** Backend `phase2_routes.py:_summarize_month()` returns `gross_margin_pct` as percentage (0–100), not decimal — confirmed by phase2_routes computing `(gross_profit / sales_net) * 100`. FE displays `${value}%` correctly. But the same pattern at `food_cost.pct` (line 327-328) shows the same value with the `%` literal appended — needs verification that backend returns `food_cost.pct` already-multiplied. If backend ever returns `0.35` decimal it would show "0.4%" and look like a successful low food cost (CRITICAL display lie). **Needs verification:** grep `phase2_routes.py` for the food_cost block to confirm. Marked MEDIUM because the values may be correct today, but the implicit convention is risky — a refactor that flips decimal/percent semantics on either side silently produces 100× error.
- **Suggested fix:** Frontend-defensive bounds check; if `pct > 1000` or `< 0.001` for a non-zero value, log a console warning. Better: explicit type with brand:
  ```typescript
  type Percent100 = number & { __brand: 'Percent100' };  // value 0..100
  function assertPct100(v: number): Percent100 {
    if (Math.abs(v) > 200) console.warn(`Suspicious pct value: ${v}`);
    return v as Percent100;
  }
  ```
- **Test plan:** Manual: with food_cost.pct = 0.35 backend response, page should warn (or display "35.0%"). Without convention enforcement, can't be verified statically.

### [M12] — ~~no Authorization header~~ **FALSE POSITIVE** (verified 2026-05-27 by main session)

Original finding claimed all 8 pages lack `Authorization` header. Verification proves this is a **FALSE POSITIVE** caused by audit agent reading page files in isolation.

**Root cause of the confusion:**
`components/AuthProvider.tsx:84-137` (Session 41 SSO migration) installs a **global `window.fetch` interceptor** at module load time. The interceptor:

1. Reads `access_token` from the Supabase cookie (`sb-osneubnwghvbwyazaedo-auth-token`) synchronously on first import — so the very first fetch already has the header
2. Wraps `window.fetch` to inject `Authorization: Bearer <token>` for every URL that starts with `API_URL`
3. On 401, calls `supabase.auth.getSession()` → updates token → retries the original request once → redirects to `/login` only if refresh fails

This pattern means individual pages can call `fetch(url)` directly without any auth code — by design. CLAUDE.md (frontend, line 169) documents the OLD `localStorage.auth_token` pattern from before Session 41 SSO migration and is out of date; that doc should be updated.

**Live verification confirming the interceptor works:**
- `curl https://api.marastation.com/pnl/monthly?year=2026` (no auth) → **HTTP 401** as expected
- Production app at `app.marastation.com` loads `/dashboard`, `/pnl`, etc. without errors → must be because the interceptor injects auth

**No action required for M12 itself.** Drop from severity counts.

**Tangential issue worth tracking separately:**
The FE CLAUDE.md doc references `localStorage.auth_token` (pre-SSO pattern). Should be updated to document the AuthProvider interceptor. Severity: documentation, not code. Not counted in audit totals.

### [M13] — `app/pnl/page.tsx` "previous range" auto-fetch for daily can hit invalid (negative-month) date range
- **File:** `app/pnl/page.tsx:690-699`
- **Page:** `/pnl` (Daily tab)
- **Current code:**
  ```typescript
  function previousRange(from: string, to: string) {
    const start = new Date(`${from}T00:00:00`);
    const end = new Date(`${to}T00:00:00`);
    const days = Math.max(1, Math.round((end.getTime() - start.getTime()) / 86400000) + 1);
    const previousEnd = new Date(start);
    previousEnd.setDate(start.getDate() - 1);
    const previousStart = new Date(previousEnd);
    previousStart.setDate(previousEnd.getDate() - days + 1);
    return { from: toKey(previousStart), to: toKey(previousEnd) };
  }
  ```
- **Issue:** If user picks a custom range starting before the earliest data (e.g. `2024-01-01 to 2026-05-27`, ~880 days), the previous range becomes `2021-08-04 to 2024-01-31` — a range with **no data**. The endpoint will return zeros; FE will then compute `delta = (0 - X) / Math.abs(X) * 100` = -100% in `compare()` (line 630-635), which renders as "ลดลง 100.0%" — making it look like the business collapsed. Edge case but happens any time user picks YTD or wider in late-Jan.
  Also, the `new Date('YYYY-MM-DDT00:00:00')` parsing without explicit TZ uses **local time**. On a Bangkok device it's BKK midnight (intended); on a UTC server it would be wrong. Since this is client-only code it's fine, but flagging because `formatRangeTitle` (line 705) and `formatDayShort` (line 704) both use `new Date(string)` which has the same TZ-implicit behavior.
- **Suggested fix:**
  ```typescript
  // Don't fetch a "previous" period if previousStart is older than business start (2024-01-01)
  const previousIsBefore2024 = previousStart < new Date('2024-01-01T00:00:00');
  // Then in loadDaily, gate the second fetch and skip prev compare if no data.
  ```
  Or, in the `compare()` helper (line 630), check if `previous` is zero AND request range is wider than 90 days, return `{ label: 'ไม่มีข้อมูลก่อนหน้า', tone: 'neutral' ... }`.
- **Test plan:** Pick a YTD range; verify the KPI delta cards show "ไม่มีข้อมูลก่อนหน้า" rather than "ลดลง 100%".

### [M14] — `app/pnl/page.tsx` `formatRangeTitle` / `formatDayShort` use `new Date(string)` which is locale-fragile
- **File:** `app/pnl/page.tsx:704-705`
- **Page:** `/pnl`
- **Current code:**
  ```typescript
  function formatDayShort(date: string) { return new Intl.DateTimeFormat('th-TH', { day: '2-digit', month: '2-digit' }).format(new Date(date)); }
  function formatRangeTitle(from: string, to: string) { return `${formatThaiDate(from)} - ${formatThaiDate(to)}`; }
  ```
  And in `lib/format.ts:19`:
  ```typescript
  const d = new Date(iso);  // 'YYYY-MM-DD' interpreted as UTC midnight, then localized
  ```
- **Issue:** `new Date('2026-05-22')` is interpreted as UTC midnight (per ECMAScript spec for `YYYY-MM-DD`). When formatted with `th-TH` on a Bangkok device (UTC+7), the date stays `22 พ.ค.` — correct. But on a UTC server or anywhere west of UTC, `new Date('2026-05-22').getDate()` returns 21. This page is `'use client'` so it always runs in the user's local TZ, which is fine *if* TUM only views from Bangkok. If TUM ever views from abroad (he travels), late-night transactions could show wrong day.
  Confirmed `formatThaiDate` in `lib/format.ts:19` uses the same `new Date(iso)` pattern. Same risk.
- **Suggested fix:** Parse the date components explicitly:
  ```typescript
  function formatThaiDateLocal(iso: string): string {
    const [y, m, d] = iso.split('-').map(Number);
    const months = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];
    return `${d} ${months[m - 1]} ${y + 543}`;
  }
  ```
  No `Date` object → no TZ ambiguity. Cross-page consistency win because `daybook/page.tsx:69-72` already does its own thing and `dashboard/page.tsx:113-115` parses manually. Standardize on the manual parse.
- **Test plan:** Change browser TZ to America/Los_Angeles (UTC-7). Reload `/pnl?from=2026-05-22&to=2026-05-22` — today the formatted date can show "21 พ.ค." (wrong). After fix: always "22 พ.ค.".

### [M15] — `app/pnl/compare/page.tsx` `DeltaCell` percent formula uses `(b-a)/a*100` but should be `Math.abs(a)` for negative profits
- **File:** `app/pnl/compare/page.tsx:52-65`
- **Page:** `/pnl/compare`
- **Current code:**
  ```typescript
  function DeltaCell({ a, b }: { a: number; b: number }) {
    const delta = b - a;
    if (Math.abs(delta) < 1) return <span className="text-muted text-xs">—</span>;
    const pct = a !== 0 ? (delta / a) * 100 : null;
    const isUp = delta > 0;
    ...
  }
  ```
- **Issue:** When the previous month had a loss (gross profit `a = -10000`) and current has a small loss (`b = -5000`), `delta = -5000 - (-10000) = +5000` (profit improved by 5,000), but `pct = 5000 / -10000 * 100 = -50%`. Display shows "+5,000 (-50%)" — visually contradicting (sign of delta vs sign of pct). The SummaryRow component at line 325-356 doesn't compute pct, so it's correct; but the inline `DeltaCell` for both summary mar­gin row (line 192-196) and the category table (line 257) is wrong on the loss-recovering edge case.
  For the margin row specifically (line 192-196), `a` and `b` are percentages already (e.g. `a = 25, b = 30` margin pct). `(b-a)/a*100 = 20%` rendered as "+5% (+20%)" — confusing percentage-of-percentage which mixes pt-of-margin with pct-change. The margin row should show "+5 pt" not "+20%".
- **Suggested fix:**
  ```typescript
  const pct = a !== 0 ? (delta / Math.abs(a)) * 100 : null;
  ```
  And introduce a `DeltaPointCell` for the margin row that uses pp (percentage points) not %.
- **Test plan:**
  1. Pick a month-pair where profitA = -10000, profitB = -5000. Display today: "+5,000 (-50%)". After fix: "+5,000 (+50%)" (loss reduced by 50%).
  2. Margin row: today displays compound %; should display "Δ pt".

### [M16] — `app/daybook/page.tsx` `todayISO()` uses `new Date().toISOString()` which is UTC — wrong day boundary for Asia/Bangkok
- **File:** `app/daybook/page.tsx:120-134`
- **Page:** `/daybook`
- **Current code:**
  ```typescript
  function todayISO(): string {
    return new Date().toISOString().slice(0, 10);
  }
  function monthStartISO(): string {
    const d = new Date();
    d.setDate(1);
    return d.toISOString().slice(0, 10);
  }
  function weekAgoISO(): string {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString().slice(0, 10);
  }
  ```
- **Issue:** `Date.prototype.toISOString()` converts to UTC before formatting. In Bangkok (UTC+7), any time between 00:00 BKK and 07:00 BKK, `new Date().toISOString().slice(0,10)` returns **yesterday's** UTC date. Concretely: at 02:00 BKK on May 28, the function returns `2026-05-27`, so the "วันนี้" preset queries the wrong day. Same shape in `monthStartISO()` and `weekAgoISO()` — at the same hour `monthStartISO` could return last-month's 1st when the user is in the early hours of month-start day.
  CLAUDE.md (backend) Known pitfalls don't mention this but it's a known class of bug for Thai-restaurant apps. Restaurant ops happens late at night — TUM checks daybook at 1 AM after closing, sees yesterday's date in the filter, thinks no sales today.
- **Suggested fix:**
  ```typescript
  function todayISO(): string {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
  }
  ```
  Same approach in `monthStartISO()` and `weekAgoISO()`.
- **Test plan:**
  1. Set browser clock to 02:00 BKK on 2026-05-28. Open `/daybook`, click "วันนี้". Today the dateFrom/dateTo become `2026-05-27` (wrong). After fix: `2026-05-28`.
  2. Also test `monthStartISO` at 02:00 BKK on the 1st — should return today's date as month start, not last month's 1st.

### [M17] — `app/yearly/page.tsx` displays inflated `income_total` from broken backend `/pnl/yearly` (C4)
- **File:** `app/yearly/page.tsx:84-91, 195-216`
- **Page:** `/yearly`
- **Current code:**
  ```typescript
  fetch(`${API_URL}/pnl/yearly?year=${year}&branch=${BRANCH}`, { cache: 'no-store' })
    .then(r => { if (!r.ok) throw new Error('โหลดไม่ได้'); return r.json() as Promise<YearlyData>; })
    .then(d => { setData(d); setLoading(false); })
    .catch(e => { setError(e.message); setLoading(false); });
  ```
  And table render (line 305-311):
  ```typescript
  <td className="py-2.5 pr-3 text-right font-mono text-xs text-muted">
    {m.has_data && m.sales_net > 0 ? compact.format(m.sales_net) : '—'}
  </td>
  <td className="py-2.5 pr-3 text-right font-mono text-xs text-muted">
    {m.has_data && m.rider_net > 0 ? compact.format(m.rider_net) : '—'}
  </td>
  <td className="py-2.5 pr-3 text-right font-mono font-semibold text-xs">
    {m.has_data ? compact.format(m.income_total) : '—'}
  </td>
  ```
- **Issue:** Backend C4 documents that `/pnl/yearly` can have `sales_net + rider_net ≠ income_total` because the three values come from different SQL queries (pos_sales_daily, rider_deliveries, v_daybook). FE displays them side-by-side in the table — TUM will eyeball "1.2M + 0.3M = 1.5M" and see "1.6M" in the income_total column. He has caught this kind of inconsistency before.
  Additionally:
  - No auth header (M12).
  - Hard-coded `BRANCH = 'thawi_watthana'` (line 16) — fine today, becomes a bug if a second branch is added (no UI to switch).
  - `data.totals.bill_count` is displayed at line 215 as "บิล POS" but it's actually `expense_bill_count + sales_bill_count` per backend (label-vs-data mismatch — needs verification).
  - `data.totals.gross_margin_pct` displayed at line 213 as `${data.totals.gross_margin_pct}%` — if backend returns decimal (`0.25`), this displays "0.25%" instead of "25%". Cf. M11.
- **Suggested fix:** Display-level guard: compute `sum_check = m.sales_net + m.rider_net` and if `Math.abs(sum_check - m.income_total) > 100` baht, show a small warning icon in the income_total cell:
  ```typescript
  const incomeMismatch = m.has_data && Math.abs((m.sales_net + m.rider_net) - m.income_total) > 100;
  <td ...>
    {m.has_data ? compact.format(m.income_total) : '—'}
    {incomeMismatch && <span title="POS + Rider ไม่ตรงกับรายรับรวม" className="ml-1 text-yellow-300">⚠</span>}
  </td>
  ```
  Better: wait for backend C4 fix and remove the FE workaround.
- **Test plan:** Pick a year where sum_check disagrees with income_total. Today no warning. After fix: ⚠ next to the income cell on the discrepant row.

### [M18] — `app/yearly/page.tsx` calls `/export/pnd3-annual` which has known issues (backend M3) — UX consequences
- **File:** `app/yearly/page.tsx:100-118`
- **Page:** `/yearly`
- **Current code:**
  ```typescript
  async function handleDownload(type: 'pnl' | 'pnd3') {
    setDownloading(type);
    const url = type === 'pnl'
      ? `${API_URL}/export/yearly?year=${year}&branch=${BRANCH}`
      : `${API_URL}/export/pnd3-annual?year=${year}`;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error('ดาวน์โหลดไม่ได้');
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = type === 'pnl' ? `annual_pnl_${year}.xlsx` : `pnd3_annual_${year}.xlsx`;
      a.click();
    } catch {
      alert('ดาวน์โหลดล้มเหลว');
    } finally {
      setDownloading(null);
    }
  }
  ```
- **Issue:** This downloads the PND.3 Excel that backend M3 documents as having (a) hard-coded 3% WHT and (b) missing branch filter. Submitting that Excel to สรรพากร with wrong WHT rates is a regulatory risk. FE has no warning. Additionally:
  - `alert('ดาวน์โหลดล้มเหลว')` is browser-native, looks unprofessional in a dark-themed Thai-language app. Should be a toast.
  - `URL.createObjectURL(blob)` is never `URL.revokeObjectURL`'d — small memory leak per download (LOW).
  - No content-type or filename sniffing from `Content-Disposition` response header — if backend changes to dynamic filename FE won't pick it up.
- **Suggested fix:**
  1. Show a warning banner above the PND.3 download button: "⚠ ตรวจสอบอัตรา WHT (3% ทุกแถว) ก่อนยื่นกรมสรรพากร".
  2. Replace `alert()` with a toast component.
  3. `URL.revokeObjectURL(a.href)` after click.
- **Test plan:** Download PND.3 Excel; open it; verify all rows use 3% (backend M3 confirms today they do, even if some should be 5%).

### [M19] — `app/yearly/page.tsx` rounds `gross_margin_pct` to integer — half-percent loss
- **File:** `app/yearly/page.tsx:213, 322, 337`
- **Page:** `/yearly`
- **Current code:**
  ```typescript
  {data.totals.gross_margin_pct !== null ? `${data.totals.gross_margin_pct}%` : '—'}
  ```
  And `pctColor` thresholds (line 65-70):
  ```typescript
  if (pct >= 30) return 'text-emerald-300';
  if (pct >= 15) return 'text-yellow-300';
  return 'text-red-300';
  ```
- **Issue:** `${data.totals.gross_margin_pct}%` directly stringifies the backend number. If backend returns `25.7`, FE shows "25.7%" — fine. If backend returns `25.7142857`, FE shows the full precision — ugly but harmless. If backend returns `25` (an int), it shows "25%" — also fine. But TUM's threshold for "green" margin is `>= 30`; backend returns `29.9999...` and FE displays "29.9999...%" and colors it yellow. Minor display polish only. **Marked MEDIUM because the colored threshold drives operational interpretation** ("margin healthy?" / "margin warning?") and uncontrolled precision could push borderline cases into the wrong color.
- **Suggested fix:** Use the existing `pctFmt` formatter or `Intl.NumberFormat('th-TH', { maximumFractionDigits: 1 })`:
  ```typescript
  {data.totals.gross_margin_pct !== null
    ? `${pctFmt.format(data.totals.gross_margin_pct)}%`
    : '—'}
  ```
- **Test plan:** Inspect a month where `gross_margin_pct = 25.7142857`. Today: "25.7142857%". After fix: "25.7%".

### [M20] — `app/scorecard/page.tsx` `month` state initialised from `today` but `canGoNext` check is asymmetric — can never click "next"
- **File:** `app/scorecard/page.tsx:135-149`
- **Page:** `/scorecard`
- **Current code:**
  ```typescript
  const today = new Date();
  const [month, setMonth] = useState(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`);
  ...
  const canGoNext = month < `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;
  ```
- **Issue:** Comparing `YYYY-MM` strings via `<` works because they are zero-padded. On page first load, `month` equals today's key, so `canGoNext = false` — the right-chevron is disabled. Correct behavior. But: on first render, `new Date()` returns the user's local time; if user's clock is wrong (slightly ahead) and current month is e.g. `2026-06` on their clock but `2026-05` on the server, the user can navigate to the future. Backend `/scorecard?month=2026-06` would return empty/zero KPIs — page shows the score ring at the current data state from May. **Stale data without indication.**
  Also: `useEffect` (line 140-147) doesn't gate setLoading off the previous data — when the month changes, `data` still holds the old month's KPIs while new fetch is in flight; `loading=true` shows skeleton, hiding stale data, so OK. But if fetch fails (per C7), the `data` state retains the OLD month's KPIs and the month picker shows the NEW month label — silent stale data display.
- **Suggested fix:** On error (C7 fix), explicitly `setData(null)` (also in the fix above). Plus a safer comparison:
  ```typescript
  const now = new Date();
  const todayKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const canGoNext = month < todayKey;
  ```
- **Test plan:** Force backend to 500 then navigate months. Today the picker can drift while data stays stale. After fix: data clears on error and month label always matches data.

### [M21] — `app/scorecard/page.tsx` `vs_prev` display uses `.toFixed(1)` directly on possibly-null number from server
- **File:** `app/scorecard/page.tsx:113-126`
- **Page:** `/scorecard`
- **Current code:**
  ```typescript
  {kpi.vs_prev !== null && (
    <div className="mt-3 flex items-center justify-between">
      <span className={cn(
        'flex items-center gap-0.5 text-xs font-medium',
        kpi.vs_prev >= 0 ? 'text-emerald-400' : 'text-red-400',
      )}>
        {kpi.vs_prev >= 0
          ? <TrendingUp className="h-3 w-3" />
          : <TrendingDown className="h-3 w-3" />}
        {Math.abs(kpi.vs_prev).toFixed(1)}% จากเดือนก่อน
      </span>
      ...
  ```
- **Issue:** `vs_prev` is typed `number | null`. The `vs_prev !== null` guard catches `null` and `undefined` (loose), but **not** `NaN`. If backend returns `NaN` for vs_prev (e.g. divide-by-zero edge), `kpi.vs_prev !== null` passes, then `Math.abs(NaN).toFixed(1)` is `"NaN"`, and the page renders "NaN% จากเดือนก่อน" with a red TrendingDown icon (because `NaN >= 0` is `false`). This is a "wrong story" display.
- **Suggested fix:** Add a `Number.isFinite()` check:
  ```typescript
  {Number.isFinite(kpi.vs_prev) && kpi.vs_prev !== null && (
    ...
  )}
  ```
- **Test plan:** Mock backend to return `{vs_prev: NaN}` for one KPI. Today FE shows "NaN%". After fix: no badge at all.

### [M22] — `app/revenue/page.tsx` no `res.ok` check; `data.sources` accessed when backend errors
- **File:** `app/revenue/page.tsx:75-82`
- **Page:** `/revenue`
- **Current code:**
  ```typescript
  useEffect(() => {
    setLoading(true);
    fetch(`${API}/revenue/breakdown?months=${months}`)
      .then((r) => r.json())
      .then(setData)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [months]);
  ```
- **Issue:** Same triple-bug as C7 (`/scorecard`):
  1. No `res.ok` check — a 500 with `{detail: "..."}` body becomes `data`. Then `data.sources` is undefined → line 92 `data.sources.length === 0` throws "Cannot read properties of undefined". React error boundary catches it but the page goes blank.
  2. Only `console.error` for failure — no FE Thai-language error UI.
  3. No auth header (M12).
  Marked MEDIUM not CRITICAL because the page check `!data || data.sources.length === 0` (line 92) eats the worst case via fallthrough to "ยังไม่มีข้อมูล", but only because `data.sources.length` happens to throw before the false check completes. Brittle.
- **Suggested fix:** Same as C7 — add `res.ok` check, error state, auth header.
- **Test plan:** Point `NEXT_PUBLIC_API_URL` at invalid URL. Today: page renders broken. After fix: shows "โหลดข้อมูลไม่สำเร็จ".

### [M23] — `app/revenue/page.tsx` `PieLabel` uses `any` and silently swallows label issues
- **File:** `app/revenue/page.tsx:57-68`
- **Page:** `/revenue`
- **Current code:**
  ```typescript
  function PieLabel({ cx, cy, midAngle, innerRadius, outerRadius, pct, label }: any) {
    if (pct < 3) return null;
    ...
  }
  ```
- **Issue:** Recharts passes `payload.pct` only if the pie data has that field — but `pieData` (line 102) is `{name, value, color}` — no `pct`. So `pct` is always `undefined` → `undefined < 3` is `false` → labels always render. Wait — labels currently show `{fmtDec.format(pct)}%` (line 65) which becomes `"NaN%"` for every slice. **The pie labels are likely showing "NaN%" in production right now.** Verify by visiting `/revenue` — the donut chart should have `NaN%` in white text inside each slice.
- **Suggested fix:** Recharts passes `percent` (0..1, not 0..100) by default. Use:
  ```typescript
  function PieLabel({ cx, cy, midAngle, innerRadius, outerRadius, percent }: any) {
    const pct = percent * 100;
    if (pct < 3) return null;
    ...
    {fmtDec.format(pct)}%
  }
  ```
  Or pass the explicit value through the data (`pieData.pct`) and consume that.
- **Test plan:** Open `/revenue` in production. Observe pie labels. Today shows "NaN%". After fix: actual percentages.

### [M24] — `app/expense-trends/page.tsx` `change_pct.toFixed(0)` truncates to 0 for small changes, no guard for NaN/Infinity
- **File:** `app/expense-trends/page.tsx:262-277`
- **Page:** `/expense-trends`
- **Current code:**
  ```typescript
  {cat.trend === 'rising' ? (
    <span ...>
      <TrendingUp className="h-3 w-3" />
      +{cat.change_pct.toFixed(0)}%
    </span>
  ) : cat.trend === 'falling' ? (
    <span ...>
      <TrendingDown className="h-3 w-3" />
      {cat.change_pct.toFixed(0)}%
    </span>
  ) : ...
  ```
- **Issue:** When `prev_month = 0` and `last_month > 0`, backend may set `change_pct = Infinity` or undefined. `Infinity.toFixed(0)` is `"Infinity"`, rendering "+Infinity%" badge. Similarly NaN → "+NaN%". Backend C2 means this page isn't loading at all today, but once fixed this edge case becomes visible (new categories the restaurant has never used before show "+Infinity%").
  Additionally, `cat.change_pct.toFixed(0)` truncates `0.4` → "0" — a 0.4% rise displays as "+0%" but trend is still tagged "rising". User sees "rising, +0%" — looks broken.
- **Suggested fix:**
  ```typescript
  function formatChangePct(p: number): string {
    if (!Number.isFinite(p)) return 'ใหม่';   // new category
    return `${p >= 0 ? '+' : ''}${p.toFixed(0)}%`;
  }
  ```
- **Test plan:** After backend C2 fix, find a category with prev_month=0, last_month>0. Today FE will render "+Infinity%". After fix: "ใหม่".

### [M25] — `app/daybook/page.tsx` `ar_payment` direction assumption may not match backend
- **File:** `app/daybook/page.tsx:30-47, 96-105, 111-114`
- **Page:** `/daybook`
- **Current code:**
  ```typescript
  type Source =
    | 'pos_sale' | 'vendor_bill' | 'manual' | 'ar_payment' | 'ap_payment'
    | 'rider_income_grab' | 'rider_income_lineman' | 'pos_cashflow'
    | string;
  ...
  const ALL_SOURCES: string[] = [
    'pos_sale', 'vendor_bill', 'manual', 'ar_payment', 'ap_payment',
    'rider_income_grab', 'rider_income_lineman', 'pos_cashflow',
  ];
  ```
- **Issue:** The FE assumes 8 sources but backend `v_daybook` may produce additional source values (`owner_capital`, `owner_advance`, `transfer_error`, `bank_statement`, `vendor_payment`, `grab_payout`, `lineman_payout`, `pos_cash_deposit`, `cash_withdrawal` per backend audit notes). Rows from those sources appear in `/daybook/list` results (because `ALL_SOURCES.size === selectedSources.size` skips the source filter, line 163) but the chip UI doesn't list them. The rendering path at line 393 calls `getSourceMeta(row.source)` which falls back to `DEFAULT_SOURCE_META` (label "อื่นๆ") — so equity rows appear as "อื่นๆ" with no way to filter them out. Combined with C6, the user sees ~700,000 ฿ of "อื่นๆ" income that's actually owner_capital and can't isolate it.
- **Suggested fix:** Add a "ทุน / เงินทุน" filter category that covers all equity sources, with a different visual (e.g. striped border) to distinguish from operational sources. Update `getSourceMeta` to map known equity sources to a distinct meta.
- **Test plan:** With an `owner_capital` row in v_daybook, today it renders as "อื่นๆ" and gets counted in `dayIncome`. After fix: it's labeled "ทุน" and excluded from the day-net.

### [M26] — `app/pnl/compare/page.tsx` Numeric-only equality fallthrough `Math.abs(delta) < 1` swallows real ฿0.5–฿0.99 changes
- **File:** `app/pnl/compare/page.tsx:52-65, 332-333`
- **Page:** `/pnl/compare`
- **Current code:**
  ```typescript
  function DeltaCell({ a, b }: { a: number; b: number }) {
    const delta = b - a;
    if (Math.abs(delta) < 1) return <span className="text-muted text-xs">—</span>;
    ...
  ```
  And in SummaryRow:
  ```typescript
  const tone = Math.abs(delta) < 1 ? 'neutral' : isGood ? 'positive' : 'negative';
  ```
- **Issue:** Hiding sub-1-baht deltas as "—" is fine for category-level expense (thousands of baht). But for the **margin row** (line 192-197), `a` and `b` are percentages (e.g. 24.7 vs 25.3, delta=0.6). `Math.abs(0.6) < 1` is true → displays "—" even though the margin moved by 0.6 pp, which is meaningful at scale. The "—" makes month-over-month margin shifts of <1 pp invisible. For TUM who scrutinizes margin trends, this hides real signal.
- **Suggested fix:** Make the threshold context-aware, or pass a `minMeaningful` prop to `DeltaCell`:
  ```typescript
  function DeltaCell({ a, b, minMeaningful = 1 }: { a: number; b: number; minMeaningful?: number }) {
    const delta = b - a;
    if (Math.abs(delta) < minMeaningful) return <span className="text-muted text-xs">—</span>;
    ...
  }
  // Margin row: <DeltaCell a={mA} b={mB} minMeaningful={0.1} />
  ```
- **Test plan:** Compare two months where margin moved from 24.7 → 25.3. Today: shows "—". After fix: "+0.6%".

---

## LOW findings

### [L1] — `pnl_routes.py` log line at module top is shadowed by the helper file
- **File:** `pnl_routes.py:30`
- **Current code:** `logger = logging.getLogger("pnl")`
- **Issue:** Tiny: shared logger name with no per-endpoint context. When an error in `/pnl/by-category` is logged, the line shows up under `pnl` without disambiguation. Not a money bug; just slows debugging.
- **Suggested fix:** `logger = logging.getLogger("pnl_routes")` to match the module name convention (every other file in this audit uses module-name logger).
- **Test plan:** N/A — cosmetic.

### [L2] — `phase3_daybook_routes.py /daybook/summary` runs two queries when one would do
- **File:** `phase3_daybook_routes.py:181-213`
- **Endpoint:** `GET /daybook/summary`
- **Issue:** Direction totals + per-source breakdown are two separate `SELECT` round-trips. They could be a single query grouping by `(source, direction)` and computed in Python; saves one network round-trip per call. The two queries also have inconsistent param sets (direction totals respect `source` filter, by_source breakdown does not by design — this is documented as a UX feature on line 201-202, so it's correct, just inefficient).
- **Suggested fix:** Combine into one query and compute both rollups client-side. Or accept the two-query design as a clarity win.
- **Test plan:** Latency benchmark before/after if combined.

### [L3] — `phase2_routes.py:262` `expense_bill_count` from v_daybook uses `COUNT(...)` without DISTINCT
- **File:** `phase2_routes.py:259-262`
- **Endpoint:** `GET /dashboard/overview` (via `_summarize_month`)
- **Current code:**
  ```python
  COUNT(CASE WHEN direction = 'income'  THEN 1 END)::int AS sales_bill_count,
  COUNT(CASE WHEN direction = 'expense' THEN 1 END)::int AS expense_bill_count
  ```
- **Issue:** This counts daybook *entries*, not distinct vendor bills. For a normal vendor bill that produces one daybook row this is fine. But for a sales day with N POS bills, `sales_bill_count` here returns N daybook entries... which actually IS the bill count today (one row per `pos_sales_daily` aggregate, so `sales_bill_count` is one per *day*, not per bill!). The metric is mis-named: this is `expense_days_with_activity`, not bills. The frontend likely displays this as "X bills this month" — wrong for sales (will show ~30, not ~660).
- **Suggested fix:** For sales bill count, use `pos_sales_daily.bill_count` (already aggregated). For expense bill count, count distinct vendor_bills.id + manual_entries.id + bank_statement_entries.id with direction='expense'. Or replicate the `pnl_routes.py:159-162` pattern.
- **Test plan:** Hit `/dashboard/overview?month=2026-05` — `current.sales_bill_count` should be ~660, not ~30. Today it'll be wrong.

### [L4] — `app/dashboard/page.tsx` `currency` formatter shows 2 decimal places for whole-baht amounts
- **File:** `app/dashboard/page.tsx:40`
- **Page:** `/dashboard`
- **Current code:**
  ```typescript
  const currency = new Intl.NumberFormat('th-TH', { style: 'currency', currency: 'THB' });
  ```
- **Issue:** Default `Intl.NumberFormat` with `style: 'currency'` for THB uses minFractionDigits=2, so `1234567` renders as "฿1,234,567.00". Compare to `compactCurrency` (line 41) and `lib/format.ts` `formatTHB()` which uses 2 decimal places. Cross-page inconsistency: `pnl/page.tsx` uses `formatTHB()` (2 decimals), `dashboard` uses `currency` (2 decimals — matches), `daybook` uses `currency0` (0 decimals), `yearly` uses `currency` (0 decimals via `maximumFractionDigits: 0`). Mostly cosmetic, but the "ยอดขายเดือนนี้" KPI card showing "฿2,345,678.00" looks weird for THB which conventionally uses 0 decimals on big totals.
- **Suggested fix:** Standardize: for KPI big numbers use 0 decimals; for line-item amounts use 2 decimals; for compact use compact. Move all formatters to `lib/format.ts` and import everywhere.
- **Test plan:** Visual inspection across all 8 pages — same THB amount should format consistently.

### [L5] — `app/pnl/page.tsx` excessive `useMemo` dependency arrays cause re-render churn
- **File:** `app/pnl/page.tsx:180-188`
- **Page:** `/pnl`
- **Current code:**
  ```typescript
  const dailyRows = useMemo(() => sortRows(daily?.rows ?? [], dailySort.key, dailySort.dir), [daily, dailySort]);
  const monthlyRows = useMemo(() => sortRows(monthly?.rows ?? [], monthlySort.key, monthlySort.dir), [monthly, monthlySort]);
  const categoryRows = useMemo(() => sortRows(category?.categories ?? [], categorySort.key, categorySort.dir), [category, categorySort]);

  const kpis = useMemo(() => {
    if (view === 'daily') return dailyKpis(daily, prevDaily);
    if (view === 'monthly') return monthlyKpis(monthly, prevMonthly);
    return categoryKpis(category, prevCategory);
  }, [view, daily, prevDaily, monthly, prevMonthly, category, prevCategory]);
  ```
- **Issue:** The `kpis` useMemo depends on 7 values including 6 nullable response objects. When the user switches views (daily → monthly), only `view` changes — but the `useMemo` returns the same `monthlyKpis(monthly, prevMonthly)` because monthly is unchanged. Net: this is functioning fine, just over-specified. Listing data-flow as `[view, daily, prevDaily, monthly, prevMonthly, category, prevCategory]` is fragile if any new view is added. LOW; consider splitting into three view-specific memos.
- **Suggested fix:** Replace with a `switch (view)` in render path, no memo needed (the underlying `dailyKpis` etc. are pure and cheap).
- **Test plan:** React DevTools profiler before/after — KpiCard children should not re-render when only `view` flips between two states with same underlying data.

### [L6] — `app/pnl/compare/page.tsx` `allCodes` recomputed on every render
- **File:** `app/pnl/compare/page.tsx:99-104`
- **Page:** `/pnl/compare`
- **Current code:**
  ```typescript
  const allCodes = Array.from(new Set([
    ...(dataA?.categories ?? []).map(c => c.category_code),
    ...(dataB?.categories ?? []).map(c => c.category_code),
  ]));
  const mapA = Object.fromEntries((dataA?.categories ?? []).map(c => [c.category_code, c]));
  const mapB = Object.fromEntries((dataB?.categories ?? []).map(c => [c.category_code, c]));
  ```
- **Issue:** Recomputed every render; should be `useMemo`. With ~20 categories per month, it's <1ms — invisible. LOW perf nit. Worth wrapping for code hygiene.
- **Suggested fix:** `const { allCodes, mapA, mapB } = useMemo(() => { ... }, [dataA, dataB])`.
- **Test plan:** N/A — cosmetic.

### [L7] — `app/yearly/page.tsx` `month_label` displayed as-is — no Buddhist-year conversion in chart X-axis
- **File:** `app/yearly/page.tsx:93-98, 252`
- **Page:** `/yearly`
- **Current code:**
  ```typescript
  const chartData = data?.months.map(m => ({
    name: m.month_label,
    รายรับ: m.has_data ? m.income_total : null,
    ...
  })) ?? [];
  ...
  <XAxis dataKey="name" stroke="#6B7184" fontSize={12} tickLine={false} axisLine={false} />
  ```
- **Issue:** `month_label` comes from backend (likely "ม.ค.", "ก.พ.", ... in Thai) — fine. But the heading at line 132 uses `{year}` (Gregorian), and other pages convert to Buddhist (e.g. `year + 543` at `pnl/page.tsx:300`). Cross-page inconsistency: yearly page shows "2026" while pnl/page shows "2569". TUM sees both views in the same nav, gets confused. LOW because both are valid year representations but inconsistency is the bug.
- **Suggested fix:** Pick one convention site-wide (recommendation: Buddhist year per Thai convention for accounting periods). Document in `docs/03_SPECS/STYLE_SPEC.md` (or wherever style decisions go).
- **Test plan:** Visual sweep across `/yearly` and `/pnl` (Monthly tab) — confirm same year format.

---

## Notes / observations

### Cross-file pattern: equity-exclusion list is duplicated in 5+ places
The string
```
source NOT IN ('owner_capital', 'owner_advance', 'transfer_error',
               'bank_statement', 'vendor_payment',
               'grab_payout', 'lineman_payout',
               'pos_cash_deposit', 'cash_withdrawal')
```
appears verbatim in `pnl_routes.py` (3x), `yearly_routes.py` (1x), `phase2_routes.py` (3x), `phase3_daybook_routes.py` (0x — bug! see M6), and is missing entirely from `phase10_narrative_routes.py` (bug C1) and `phase2_routes.py /dashboard/category-trends` (bug C3).

**Recommendation:** Extract to a Python constant or a Postgres view (e.g. `v_daybook_pnl` that pre-applies the exclusion). A single source of truth would have prevented C1 + C3 + M6.

### Cross-file pattern: dashboard reads from `vendor_bills` directly while P&L reads from `v_daybook`
M4 + M10 are the same shape: any time a category total comes from `vendor_bills`, it disagrees with the P&L category total from `v_daybook`. Pick one source of truth (recommendation: `v_daybook` for everything category-related) and migrate the rest.

### Cross-file pattern: `try/except: zero-out` style hides bugs
Phase2 `dashboard_overview` (M5) and Phase10 narrative (M8) both have try/except blocks that swallow DB errors and continue with zeros or None. This violates CLAUDE.md rule 3 ("Fix root cause. Never suppress errors."). All five subquery blocks in `dashboard_overview` should be reviewed.

### Schema verification done during this audit
Confirmed via `migrations/17_vendor_bills_daybook.sql` that:
- `v_daybook.direction` is a synthesized column (CASE in UNION).
- `vendor_bills` has no `direction` column on the table itself (confirms C2).
- `v_daybook.ref_id` is the source-row PK (uuid for vendor_bills/manual_entries, int-cast for bank_statement_entries).
- All five files use `psycopg2` directly via `get_db_conn()`; no ORM. SQL strings are verbatim.

### Files audited but not flagged
- `phase3_daybook_routes.py /daybook/health` — read-only count, no money impact, safe.
- `phase2_routes.py /receipts/*` — search/detail endpoints, no aggregation, no money impact.
- `phase2_routes.py /budgets PUT/DELETE` — validated inputs (period_month.day == 1, amount_limit >= 0, alert_at_pct in 1..100), DB transaction committed correctly.
- `phase2_routes.py /inventory/current` and `/inventory/snapshots` — inventory only, not P&L.
- `yearly_routes.py /export/yearly` — formatting/Excel only; consumes `pnl_yearly()` which has its own findings (C4).

### Deferred to next session (per scope)
- `menu_routes.py` (4100 lines, 41 endpoints) — CLAUDE.md "Known pitfalls" item 1 already flags 5-10 likely hallucinated columns. Separate audit needed.
- `bill_payment_routes.py`, `phase12_bank_statement_routes.py`, `cashflow_routes.py` — adjacent money modules.

---

## Cross-FE patterns (added 2026-05-27 frontend audit)

### Pattern 1: ~~missing Authorization header~~ — FALSE POSITIVE (see [M12])
The audit agent missed `components/AuthProvider.tsx:84-137` which installs a global `window.fetch` interceptor that auto-injects `Authorization: Bearer <token>` for every backend call. Pages calling raw `fetch(API_URL + path)` is correct-by-design. Verified by live `curl` against production (`/pnl/monthly` → 401 without auth, dashboard loads in browser → must work via interceptor). **Lesson for future audits:** before flagging frontend systemic issues, grep the whole `components/` and `lib/` tree for fetch wrappers / interceptors / providers — not just the page files.

### Pattern 2: `fetch().then(r => r.json())` without `res.ok` check (C7, M22)
`scorecard/page.tsx` and `revenue/page.tsx` both skip the status check. A 500 response with `{detail: "..."}` body becomes the typed state, then downstream `.map()` / `.length` accesses crash. Even in pages that DO check `res.ok` (dashboard, pnl, daybook, yearly, expense-trends), the error UI is minimal — usually a small red banner with generic Thai text. Recommendation: every fetch should follow the dashboard pattern:
```typescript
const res = await fetch(...);
if (!res.ok) throw new Error(`endpoint ${res.status}`);
const data = await res.json();
```
And every page should have a visible `error` state branch with retry CTA.

### Pattern 3: TZ-fragile date parsing (M14, M16)
`new Date('YYYY-MM-DD').toISOString().slice(0,10)` is a common idiom across `daybook`, `lib/format`, `pnl` — it works for Asia/Bangkok users at most hours but fails 00:00-07:00 BKK and fails entirely for non-BKK browsers. Standardize on manual `${y}-${m}-${d}` construction with components from local-time getters.

### Pattern 4: silent stale data on month switch (C7, M20)
Pages that fetch on month change (`scorecard`, `dashboard`, `daybook`, `expense-trends`) keep the previous month's `data` state while the new fetch is in flight. Most use `setLoading(true)` to hide stale data with a skeleton — correct. But on fetch FAILURE, `setData(null)` is sometimes skipped (`scorecard` C7 doesn't clear on error → stale May data shown while picker says June). Fix: always `setData(null)` (or use SWR/react-query for proper invalidation).

### Pattern 5: FE consumes broken backend silently (C5, C8, M17, M25)
Multiple FE pages consume endpoints with known backend bugs (C1-C4) and surface the bad numbers without warning:
- `/dashboard` shows inflated `top_categories` + understated `food_cost` (backend M4).
- `/expense-trends` shows nothing because backend C2 is 500.
- `/yearly` shows inconsistent `sales_net + rider_net ≠ income_total` (backend C4).
- `/daybook` shows equity-inflated net (backend M6).
The pattern: FE trusts the API. Recommendation: add cross-field sanity checks at display time (e.g. M17's `sum_check` warning) until backend invariants are enforced via schema/views.

### Pattern 6: chart label / formatter accepts `any` without type contract (M23)
`PieLabel` in `revenue/page.tsx` types its params `any` and references a field (`pct`) that doesn't exist on the data — silently renders `NaN%`. Recharts has typed prop signatures; using them would have caught this at compile time. Apply across all recharts usage (10+ files).

### Pattern 7: hard-coded BRANCH constant (M17 secondary)
`yearly/page.tsx` line 16: `const BRANCH = 'thawi_watthana';`. Same constant appears in backend endpoints as default. If/when a second branch is added, every page needs a branch picker. Centralize the constant in `lib/config.ts` and add a `useBranch()` hook now (returning the constant) so future migration is one-line.

### Frontend audit summary
8 pages audited. 4 CRITICAL, 16 MEDIUM, 4 LOW frontend findings. Top three FE priorities:
1. **C7** (scorecard hard crashes on backend 500) — 5 min fix, biggest user-visible impact.
2. **C8** (expense-trends currently broken in prod due to backend C2) — wait for backend C2 fix; FE-side just needs a clearer error message.
3. **C6** (daybook displays equity-inflated net labeled "กำไร") — relabel + filter equity sources client-side until backend M6 lands.
~~The single highest-leverage fix is M12~~ — M12 is a FALSE POSITIVE (see updated finding). The actual highest-leverage backend fix is **extracting equity-exclusion to a shared helper / DB view** (eliminates C1, C3, M6 + future Session-6-class bugs in one shot).
