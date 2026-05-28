# Batch 8 Audit — LINE Bot + Quick Entry + Category + Store Context

Date: 2026-05-27
Scope (READ-ONLY): `line_bot_routes.py`, `phase3_quick_entry_routes.py`, `phase3_category_routes.py`, `store_context_routes.py`
Auditor focus: money-first preventive — wrong money in TUM-trusted LINE digests, equity leaks, text-parse corruption, scheduled-job math, suppressed errors.

## Summary

| Severity | Count | IDs |
|---|---|---|
| CRITICAL | 2 | C1, C2 |
| MEDIUM | 4 | M1, M2, M3, M4 |
| LOW | 3 | L1, L2, L3 |

Headline: **the DAILY LINE digest (`_build_digest`) reads raw `v_daybook` with NO equity exclusion** — it is the Session-6 bug, live in the digest TUM reads at 06:00 every day. The "กำไรสุทธิ" line and expense totals are wrong whenever owner_capital / owner_advance / transfer_error rows exist on that date. The WEEKLY digest correctly excludes equity, which makes the daily/weekly numbers disagree and erodes trust in both.

Heartbeat decorators are present on all scheduled jobs (good). Auth-header concerns deliberately not flagged (false positive per scope).

## ✅ Closure status (Session 44, 2026-05-28)

Both CRITICAL closed:
- **C1** Daily LINE digest equity exclusion — ✅ fixed in `766bdc0` (line_bot_routes.py:627 `FROM v_daybook_pnl`)
- **C2** Margin% same root — ✅ closed by C1 fix (denominator now equity-excluded)

MEDIUM closures (Session 45, 2026-05-28):
- **M3** `date.today()` host-TZ bug — ✅ fixed in `8ad1f51`. Added `_today_bkk()` helper (`ZoneInfo("Asia/Bangkok")`) and replaced `date.today()` in `_scheduled_daily_digest` (line 753), `_build_weekly_summary` (line 802), and `digest_today` (line 956). Container runs UTC, so 06:00 BKK = 23:00 UTC prev day — pre-fix all three would have computed for the wrong Bangkok business day. Quick-entry INSERT and display strftime intentionally left alone (out of audit scope, "now" semantics OK there).

Remaining: 3 MEDIUM + 3 LOW — quick-entry parser quirks, no money corruption.

---

## [C1] Daily digest does NOT exclude equity sources → wrong profit + expense totals (Session-6 bug, live)

- **File:line:** `line_bot_routes.py:619-724` (`_build_digest`), query at `625-631`
- **Current code:**
  ```python
  cur.execute("""
      SELECT direction, source, COALESCE(SUM(amount), 0) AS total
      FROM public.v_daybook
      WHERE entry_date = %s
      GROUP BY direction, source
      ORDER BY direction, source
  """, (target_date,))
  ...
  net = income_total - expense_total
  margin = (net / income_total * 100) if income_total > 0 else 0.0
  ...
  lines.append(f"{net_icon} กำไรสุทธิ: ฿{net:,.0f} ({margin:.1f}%)")
  ```
- **Issue:** Query selects from raw `v_daybook` with **no** `WHERE source NOT IN (...)` filter. Equity movements — `owner_capital` (owner injects cash), `owner_advance`, `transfer_error` — are summed straight into `income_total` / `expense_total`. The digest then prints "💚 รายรับ", "🔴 รายจ่าย" and "✅ กำไรสุทธิ" from those polluted totals. This is exactly the Session-6 incident the CLAUDE.md cheat sheet warns about ("Never subtract equity entries... leads to negative expense bug"). It runs in:
  - `_scheduled_daily_digest` (06:00 BKK, auto-pushed to TUM daily)
  - `POST /line/digest/today`, `POST /line/digest/{date}`
  On any day TUM tops up the till (owner_capital) or there's a transfer_error correction, the profit number TUM trusts is inflated/deflated by that amount. This is wrong money in the most-read digest.
- **Suggested fix:** Use the P&L source-of-truth view `v_daybook_pnl` (which excludes equity), or add the same exclusion the weekly digest uses:
  ```python
  FROM public.v_daybook_pnl     -- preferred: equity already excluded
  WHERE entry_date = %s
  GROUP BY direction, source
  ```
  Match the weekly digest exactly so daily and weekly reconcile. (Note: weekly at `798-801` also excludes `bank_statement, vendor_payment, grab_payout, lineman_payout, pos_cash_deposit, cash_withdrawal` — decide whether daily should mirror that fuller list or just the 3 equity sources; at minimum the 3 equity sources MUST be excluded.)
- **Test plan:**
  1. Pick a date with an `owner_capital` row in `v_daybook`. Compare `POST /line/digest/{date}` net vs `v_daybook_pnl` SUM for same date — they must match after fix, differ before.
  2. SQL: `SELECT direction, SUM(amount) FROM v_daybook WHERE entry_date=DATE AND source IN ('owner_capital','owner_advance','transfer_error') GROUP BY direction;` — confirms leak magnitude.
  3. Confirm daily-digest net for a full week sums to the weekly-digest net.

## [C2] Daily-digest income denominator includes equity → margin% wrong (same root, separate symptom worth its own check)

- **File:line:** `line_bot_routes.py:677-678`
- **Current code:**
  ```python
  net = income_total - expense_total
  margin = (net / income_total * 100) if income_total > 0 else 0.0
  ```
- **Issue:** Because `income_total` (C1) includes `owner_capital` (cash the owner put in — NOT revenue), the margin% denominator is inflated. Example: real sales ฿10,000, owner adds ฿20,000 capital → income_total=฿30,000, margin shown as a fraction of ฿30k not ฿10k. TUM reads a margin that is materially understated on any capital-injection day. Division-by-zero itself is guarded (`if income_total > 0`), so this is purely the equity-contamination consequence — but it produces a *plausible-looking wrong percentage*, which is more dangerous than an obvious error. Resolved automatically once C1 switches to `v_daybook_pnl`.
- **Suggested fix:** Fixed by C1. After switching to `v_daybook_pnl`, verify margin uses the equity-free income.
- **Test plan:** Same date as C1 — assert displayed margin == net / (sales-only income).

---

## [M1] Quick-expense text parse can mis-capture amount from a multi-number message → wrong amount saved

- **File:line:** `line_bot_routes.py:544-576` (`_parse_quick_expense`)
- **Current code:**
  ```python
  for part in reversed(parts):
      cleaned = part.replace(",", "").replace("บาท", "").replace("฿", "").strip()
      try:
          val = float(cleaned)
          if val > 0:
              amount = val
              break
  ...
  return {"description": first, "amount": amount}
  ```
- **Issue:** Two related money risks:
  (a) **"last positive number wins"** — message "ค่าน้ำมัน 7-11 450" → parts are `["ค่าน้ำมัน","7-11","450"]`; `7-11` fails float, `450` taken — OK here. But "ค่าข้าว 2 จาน 120" → `120` taken (correct). However "ซื้อเบียร์ 6 ขวด" (qty but no price) → `6` saved as ฿6 expense silently. A quantity gets recorded as money with no warning to TUM.
  (b) **`description` = `parts[0]` only.** "จ่ายค่าไฟ 1200" stores label "จ่ายค่าไฟ" (fine) but "ซื้อ ผัก 80" (space after ซื้อ) stores label just "ซื้อ" — meaningless label, and `_save_quick_expense` hardcodes branch `thawi_watthana`, payment `cash`, no category. The entry lands in `manual_entries` → flows into `v_daybook` → into digests/P&L. A garbage label + wrong-magnitude amount silently enters the books.
- **Suggested fix:** (a) Require the amount token to look like money (contain a digit-group ≥ a threshold, or be the token adjacent to บาท/฿), and if the only number is a small bare integer with a trailing counter word (ขวด/จาน/กล่อง) treat as ambiguous → reply asking TUM to restate. (b) Use the full text minus the matched number as description (mirror `parse_quick_text` in `phase3_quick_entry_routes.py:88-96`, which is the better implementation). Reuse that function instead of the bespoke parser.
- **Test plan:** Unit-table: "ซื้อเบียร์ 6 ขวด"→expect reject/clarify; "ค่าน้ำมัน 1,250 บาท"→1250; "ซื้อ ผัก 80"→label "ผัก" amount 80; "ค่าข้าว 2 จาน 120"→120.

## [M2] `_parse_quick_expense` accepts any message starting with ค่า/จ่าย/ซื้อ before intent classification → can hijack legitimate queries

- **File:line:** `line_bot_routes.py:1801-1820` (handler order) + `544-576`
- **Current code:** In `_process_one_event`, quick-expense parse runs BEFORE `_classify_intent`. Any text whose first token starts with `ค่า`/`จ่าย`/`ซื้อ` AND contains a number is written to `manual_entries` immediately.
- **Issue:** The financial-override keyword list includes `"ค่าเช่า"`, `"ค่าน้ำ"`, `"ค่าไฟ"` (lines 1039) intended to route to *search*. But a query like "ค่าเช่า 25000 เดือนนี้จ่ายยัง" starts with "ค่าเช่า" and contains 25000 → `_parse_quick_expense` matches first and **inserts a ฿25,000 expense** instead of searching. The user asked a question; the bot silently created an expense row that pollutes the digest. Money-impacting because it writes to the books from an ambiguous query.
- **Suggested fix:** Tighten the quick-expense trigger: require the message to be essentially `<label> <number> [บาท]` (e.g. ≤ 4 tokens, exactly one numeric token, no question/override keywords present). Or run `_classify_intent` first and only fall to quick-expense when intent is `other`/not financial. Echo a confirmation prompt for amounts above a threshold (e.g. ≥ ฿2,000) before committing.
- **Test plan:** "ค่าเช่า 25000 เดือนนี้จ่ายยัง" must NOT create a manual_entry (should search); "ค่าเช่า 25000" alone → still quick-entry (or confirm). Assert `manual_entries` row count unchanged for the query form.

## [M3] Weekly digest "yesterday/today" date window depends on `date.today()` host TZ, not Asia/Bangkok

- **File:line:** `line_bot_routes.py:737` (`_scheduled_daily_digest` uses `date.today()`), `783-787` (`_build_weekly_summary` uses `date.today()`), `938` (`digest_today`)
- **Current code:** `yesterday = date.today() - timedelta(days=1)` and weekly `today = date.today()`.
- **Issue:** APScheduler fires at the correct Bangkok wall-clock (scheduler is `timezone="Asia/Bangkok"`), but `date.today()` returns the **process/host local date**, which on a UTC container is 7 hours behind Bangkok. At the 06:00 BKK daily fire (= 23:00 UTC previous day), `date.today()` on a UTC host returns the day *before* Bangkok's "yesterday" — so the digest can report the wrong calendar day's numbers (off-by-one). Same risk for the Monday 08:00 weekly window and for `digest_today` near midnight. CLAUDE.md says jobs are Asia/Bangkok; relying on host TZ for the date is fragile.
- **Suggested fix:** Compute the date in Bangkok explicitly: `from zoneinfo import ZoneInfo; now_bkk = datetime.now(ZoneInfo("Asia/Bangkok")); today = now_bkk.date()`. Apply in all three spots and the stock digest.
- **Test plan:** Set container `TZ=UTC`, freeze time to 23:30 UTC, call `_scheduled_daily_digest`; assert it builds for Bangkok-yesterday (the date 7h ahead), not UTC-yesterday.

## [M4] Suppressed exceptions hide digest data gaps from TUM (silent partial digests)

- **File:line:** `line_bot_routes.py:656-665, 668-675, 828-840`; scheduled jobs `743-744, 764-765, 876-877, 1558-1559`
- **Current code:** Sub-queries (`bill_anomalies`, `bank_statement_entries`, `ar_ap_entries`) wrap in `try/except: log.exception(...)` and continue with a 0 default; scheduled jobs catch all and only `log.error(...)`.
- **Issue:** If `ar_ap_entries` query fails (schema drift), the weekly digest silently prints "AP ค้างจ่าย" as absent (count stays 0) — TUM reads "no bills due" when the truth is "query broke". Likewise a failed scheduled push only logs; TUM gets no digest and no failure notice (the LINE/Telegram fallback is inside `_push_text`, but if `_build_*` itself raises before push, nothing is sent and TUM is unaware). Money-relevant because absence of an alert reads as "all clear".
- **Suggested fix:** When a digest sub-section query fails, append a visible marker to the digest (e.g. "⚠️ AP data unavailable") rather than defaulting to 0/omitting. For scheduled jobs, on exception send a short LINE/Telegram "digest failed" notice so silence never means "nothing to report".
- **Test plan:** Force `ar_ap_entries` query to raise; assert weekly text contains an explicit "unavailable" marker, not a silent omission.

---

## [L1] `_verify_signature` returns True when `LINE_CHANNEL_SECRET` unset (open webhook)

- **File:line:** `line_bot_routes.py:1645-1650`
- **Issue:** If the secret env var is missing, signature check is skipped (`return True`) — anyone can POST forged events to `/webhook` and trigger expense writes (via M2) or OCR spend. Not flagged CRITICAL because secret is set in prod, but it removes the only guard if env drifts. Note this is webhook signature (not the Authorization-header false-positive excluded from scope).
- **Suggested fix:** Fail closed in production (raise 500 if secret unset) or at minimum log a loud warning each request when skipping.
- **Test plan:** Unset secret, POST forged event → currently processed; after fix → rejected/loudly warned.

## [L2] `_handle_recipe_cost` GP/cost uses `NULLIF(yield_pct/100.0,0)` — recipes with NULL/0 yield silently drop ingredient cost

- **File:line:** `line_bot_routes.py:1445-1454, 1461-1464`
- **Issue:** `SUM(ri.qty_used * i.price_per_unit / NULLIF(i.yield_pct/100.0, 0))` — when `yield_pct` is NULL or 0, the division yields NULL and that ingredient contributes 0 to cost, inflating GP% shown on LINE. Owner-facing GP number can read artificially high (e.g. 80% when real is 55%). Display-only (no write), hence LOW, but it is a money figure TUM may act on.
- **Suggested fix:** `COALESCE(NULLIF(i.yield_pct,0),100)/100.0` so a missing yield defaults to 100% (no waste) rather than dropping the ingredient; or flag the menu as "ต้นทุนไม่ครบ".
- **Test plan:** Recipe with one ingredient `yield_pct=NULL`; assert cost includes that ingredient at full qty after fix.

## [L3] `parse_quick_text` regex misses Thai-digit numerals and treats "350.00" label digits ambiguously

- **File:line:** `phase3_quick_entry_routes.py:88-96`
- **Issue:** `re.finditer(r"(\d+(?:\.\d+)?)")` only matches ASCII digits. A message with Thai numerals ("๓๕๐") yields amount None and label = whole string. Minor: this is the web `/quick-entries/parse` helper (user can correct in UI), and Thai-numeral input is rare. No write happens directly from this (the create endpoint validates `amount > 0`). LOW.
- **Suggested fix:** Optionally normalize Thai digits before regex; or document ASCII-only.
- **Test plan:** POST `/quick-entries/parse {"text":"กาแฟ ๕๐"}` → currently amount null; decide expected.

---

## Notes / non-findings (verified clean)

- **Heartbeat decorators present** on all scheduled jobs: `daily_line_digest` (734), `daily_ap_due_reminder` (751), `weekly_summary` (868), `daily_budget_alert` (46), `daily_stock_digest` (1550), `weekly_do_snapshot` (1584). `vps_health_monitor` (1633) delegates to `health_monitor.health_check_job` (not in scope — verify its own heartbeat there). Per AGENTS.md P1.2 satisfied for in-file jobs.
- **No hallucinated columns found** in scope. `manual_entries.label` is real (used by `phase3_quick_entry_routes.py` create endpoint at line 273 and list at 158). `pos_inventory_items.qty` used in `_handle_recipe_suggest` (1359) — assumed valid as other stock code uses `qty_in_stock`; recommend a quick `information_schema` confirm that `pos_inventory_items.qty` exists (vs `qty_in_stock`) — possible column mismatch but the `_query_inventory_by_keywords` block uses `qty_in_stock`, so `qty` at 1359 is suspect. (Tracked under recipe_suggest; AI-suggest only, no money write — left as a note, verify before next touch.)
- **All DB connections** use try/finally close — no leaks observed. SQL params are bound (no injection) including the dynamic `like_clauses` in `_query_inventory_by_keywords` (parameterized).
- **`_push_text` retry + Telegram fallback** logic is sound (4xx fast-fail, 5xx backoff).
- **store_context_routes.py** clean: parameterized, cache thread-locked, admin gate present. No money math.
- **phase3_category_routes.py** clean: validation tight, soft-delete preserves FKs. No money math.

### Follow-up suspect to confirm before next edit
`line_bot_routes.py:1359` — `SELECT ii.item_name, ii.qty, ii.unit FROM pos_inventory_items` — column `qty` may not exist (rest of file uses `qty_in_stock`). Verify against `information_schema.columns`. If wrong, `_handle_recipe_suggest` throws and the LINE reply is "❌ ไม่สามารถแนะนำเมนูได้" — no money impact, but a broken feature.
