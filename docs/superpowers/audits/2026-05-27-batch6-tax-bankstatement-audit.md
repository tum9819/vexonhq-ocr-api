# Batch 6 Audit — Tax (WHT) + Bank Statement Parser

Date: 2026-05-27
Scope (READ-ONLY): `tax_routes.py`, `phase12_bank_statement_routes.py`, `app/tax/page.tsx`, `app/bank-statement/page.tsx`
Focus: money correctness (WHT math, bank sign/dedup, equity leak, P&L), crashes, frontend safety.

## Summary

| Severity | Count | IDs |
|---|---|---|
| CRITICAL | 4 | C1, C2, C3, C4 |
| MEDIUM | 6 | M1, M2, M3, M4, M5, M6 |
| LOW | 3 | L1, L2, L3 |

Headline: the WHT base amount is computed from the **net amount debited from the bank**, not the gross contract value — so both the WHT due and the "gross estimate" are wrong whenever the supplier was actually paid net-of-tax (the normal Thai WHT case). Two category codes the tax report queries (`rent`, `service_fee`) are never produced by the parser's auto-classifier, so the report silently under-reports. A musician-fee `int()` truncation can mis-classify, and the bank dedup key drops legitimate same-day same-amount transactions.

## 🛑 Closure status (Session 44, 2026-05-28)

**All 4 CRITICAL deferred** — tax filing risk (สรรพากร), MUST NOT touch without bookkeeper/accountant input:

- **C1** WHT base net-vs-gross treatment — 🛑 needs bookkeeper input (which suppliers are paid net? which gross?)
- **C2** WHT category codes parser never assigns — 🛑 needs config decision (which codes should the parser auto-tag, and where)
- **C3** musician-fee `int()` truncation — 🟡 mechanical fix once confirmed exact-amount tolerance is desired (not started)
- **C4** bank dedup key drops time+balance — 🛑 data-migration risk (changing the dedup key requires re-dedup of existing data)

No code changes from this batch in Session 44.

---

## [C1] WHT base uses net-paid amount as if it were the gross base — tax under/over-stated

File: `tax_routes.py:135-149`

Current code:
```python
amount = float(row["amount"] or 0)            # = debit (what left the bank)
wht = round(amount * rule["wht_pct"] / 100, 2)
net_paid = round(amount - wht, 2)
...
"amount_paid":    amount,   # amount actually debited from bank
"wht_amount":     wht,
"net_before_wht": round(amount / (1 - rule["wht_pct"] / 100), 2),  # gross estimate
```

Issue: `amount` is the `debit` value — the cash that actually left the bank. There are two mutually exclusive real-world cases and the code conflates them:

- **Supplier paid net of WHT (standard case):** the restaurant withholds tax and remits to Revenue Dept separately. Then the bank debit IS the net (gross − WHT), and the WHT base should be `debit / (1 - pct/100)`, i.e. `wht = base * pct`. The code instead does `wht = debit * pct`, understating the tax base by the WHT fraction. For rent at 5%, WHT should be `debit/0.95*0.05`; the code reports `debit*0.05`, i.e. ~5% too low on the tax due. This is the legal filing number (ภ.ง.ด.3) — a wrong remittance amount.
- **Supplier paid gross, WHT remitted on top:** then `debit` already equals gross and `wht = debit*pct` is right, but `net_before_wht = debit/(1-pct)` is then wrong (it inflates the base).

The code can't be right for both; it currently treats `debit` simultaneously as the base (line 135 `wht`) and as the net (line 149 `net_before_wht` divides as if `debit` were net). These two lines assume opposite things about the same number, so at least one of the three reported figures (`wht_amount`, `net_before_wht`, the remittance total) is always wrong.

Why it matters: the XLSX is filed with the accountant / e-Filing. A systematic 5% (rent) or 3% (musician/service) error on the remitted WHT is a direct legal/financial exposure.

Suggested fix: decide and document the convention with TUM. For Thai restaurant ops the bank debit is almost always the NET payment, so:
```python
base = round(amount / (1 - rule["wht_pct"] / 100), 2)   # gross contract value
wht  = round(base * rule["wht_pct"] / 100, 2)
# amount actually paid out = amount (the debit); base = gross; wht = remittance
```
Then report `base` (gross), `wht` (to remit), `amount` (paid to supplier). Remove the contradictory `net_before_wht` re-derivation. Confirm the convention with the accountant before shipping — this is a money/legal number.

Test plan: pick a known rent payment (e.g. debit ฿19,000 at 5% net convention → gross ฿20,000, WHT ฿1,000). Assert the report shows WHT ฿1,000, not ฿950. Add a unit test over each rate in `WHT_RULES`.

---

## [C2] Tax report queries category codes the parser never auto-assigns — silent under-reporting

File: `tax_routes.py:100,113-116` vs `phase12_bank_statement_routes.py:216-260,278-345`

Current code (tax):
```python
categories = list(WHT_RULES.keys())   # ['musician_fee','rent','service_fee']
... WHERE ... AND category_code = ANY(%s) AND debit > 0
```

Issue: of the three WHT category codes:
- `musician_fee` — produced by `_classify` (line 297) only when `int(amount) in {600,700,2100,2800}` and not a company. Many real performer payments at other amounts never get this code, so they never reach the WHT report at all.
- `rent` — NOT produced by any builtin pattern or by `_classify`. It can only appear if TUM manually picks `rent` in the bank-statement review UI (frontend `CATEGORIES` has `rent`). Auto-imported rent (the common case) lands under `utility_expense`/`needs_review`/`bank_statement`, never `rent`.
- `service_fee` — NOT produced anywhere. It is not even in the frontend `CATEGORIES` dropdown (`app/bank-statement/page.tsx:22-35`), so TUM cannot assign it manually either. The WHT report can never show a service-fee row.

Result: the ภ.ง.ด.3 report systematically omits rent and service withholding unless every entry is hand-classified, with no warning shown. Under-remitting WHT is a compliance risk.

Suggested fix: (a) add a builtin/DB rule path that classifies rent payments to `rent`; (b) add `service_fee` to the frontend `CATEGORIES` dropdown; (c) on the tax page, when `summary` is missing an expected category, show an info banner ("rent/service entries must be classified in Bank Statement review first"). Align the WHT category codes with what the classifier can actually emit.

Test plan: import a statement containing a rent transfer and a service-fee transfer; without manual classification, confirm they currently do NOT appear in `/tax/wht-summary`. After fix, confirm they do.

---

## [C3] Musician-fee match uses `int(amount)` truncation — misses ฿/satang and over-matches

File: `phase12_bank_statement_routes.py:292`

Current code:
```python
MUSICIAN_AMOUNTS = {600, 700, 2100, 2800}
...
if direction == "expense" and int(amount) in MUSICIAN_AMOUNTS:
```

Issue: `int(amount)` truncates toward zero. A transfer of ฿600.50 → `int` 600 → classified as `musician_fee` (taxed at 3%). More dangerously, the truncation means `2800.99` also matches, and any genuinely different ฿600-ish expense (a ฿600 supplier payment to an individual) is silently pulled into the WHT base. Because `musician_fee` feeds the WHT remittance (C1/C2), a false match injects a wrong line into a tax filing. Conversely an exact ฿600 fee paid as ฿600.00 works, but the equality should be exact, not truncated.

Suggested fix: match on exact (rounded-to-2dp) equality, mirroring the `amount_pattern` rule path:
```python
if direction == "expense" and any(abs(amount - a) < 0.01 for a in MUSICIAN_AMOUNTS):
```

Test plan: feed expense rows of 600.00, 600.50, 2800.99, 599.99. Assert only 600.00 and 2800.00 match; 600.50/2800.99/599.99 do not.

---

## [C4] Bank dedup key collapses distinct same-day, same-amount transactions

File: `phase12_bank_statement_routes.py:393`

Current code:
```python
ON CONFLICT (txn_date, description, debit, credit, branch_code) DO NOTHING
```

Issue: the KBank parser drops the time component (only `date(y,mo,d)` is stored, line 147) and discards balance (line 197 `"balance": 0.0`). The dedup uniqueness key is therefore `(date, description, amount)`. Two legitimately separate transactions on the same day, same amount, same counterparty description — e.g. two ฿600 musician payouts, or two identical ฿700 supplier transfers in one evening — collapse into one row. The second is silently `DO NOTHING`'d. That is real income/expense vanishing from P&L and (for musician fees) from the WHT base.

Note this is the inverse risk of normal re-import dedup: re-importing the same PDF is correctly de-duped, but so are genuine duplicates. Because time and balance are both thrown away, there is no field left to disambiguate.

Suggested fix: preserve the transaction time (parse the `HH:MM` already captured by the regex at line 142) into a `txn_time`/`txn_at` timestamp and include it in the conflict key, or capture the running balance (col[3], already documented at line 108) and include it — the balance differs between two otherwise-identical transactions. Re-import idempotency should key on `import_batch_id` + a per-row sequence, not on financial values.

Test plan: import a statement with two identical ฿600 expense lines at different times same day. Assert two rows are inserted, not one. Confirm P&L total reflects ฿1,200.

---

## [M1] Division-by-zero / negative if a 100%+ WHT rate is ever configured

File: `tax_routes.py:149`

Current code:
```python
"net_before_wht": round(amount / (1 - rule["wht_pct"] / 100), 2),
```

Issue: if any future `WHT_RULES` entry sets `wht_pct = 100`, `1 - 1 = 0` → ZeroDivisionError → 500 on `/tax/wht-summary` and `/tax/wht-export`. Rates >100 produce a negative "gross". Current configured rates (3/5) are safe, but the divide is unguarded.

Suggested fix: guard `den = 1 - pct/100; base = round(amount/den,2) if den > 0 else amount`. (Folds into the C1 rewrite.)

Test plan: add a temp 100% rule in a unit test; assert no crash.

---

## [M2] `_clean_number` mangles negatives and the EU/Thai decimal-comma edge; silent zero on parse failure

File: `phase12_bank_statement_routes.py:86-94`

Current code:
```python
cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
try:
    return float(cleaned)
except ValueError:
    return 0.0
```

Issue: strips everything but digits and `.`. A leading minus sign is removed, so a negative/refund amount becomes positive. `"1.234,56"` (comma decimal) → `raw.replace(",", "")` → `"1.234.56"` → `re.sub` leaves `"1.234.56"` → `float()` raises → silent `0.0`, dropping the row's amount entirely (becomes a ฿0 transaction). A genuinely unparseable amount also silently becomes 0 rather than surfacing. For KBank standard format this is usually fine, but the silent-zero path can hide a real money figure.

Suggested fix: log a warning when parse fails instead of returning 0 silently; for amounts, prefer skipping the row over inserting a ฿0 entry. KBank uses `1,234.56` so comma-as-thousands is correct, but document the assumption.

Test plan: `_clean_number("1,234.56")==1234.56`; `_clean_number("abc")` should log + the caller should skip, not insert 0.

---

## [M3] Misaligned date/amount/detail lists silently truncate real transactions

File: `phase12_bank_statement_routes.py:139-200`

Current code:
```python
n = min(len(date_entries), len(amount_entries), len(detail_entries))
for i in range(n):
```

Issue: dates, amounts and details are parsed into three independent lists from three different cell strings, then zipped by index up to the shortest. If any list has a different count (e.g. a detail line wraps and the prefix heuristic at line 171 mis-splits, or an amount line is blank and filtered by `val > 0` at line 156), the lists desync. `min(...)` then silently truncates the tail — real transactions are dropped — AND surviving rows can be paired with the wrong date/amount/detail, producing misclassified money. There is no assertion that the three counts match.

Note line 156 `if val > 0` also drops any legitimate ฿0.00 line and shifts alignment for everything after it.

Suggested fix: assert `len(date_entries) == len(amount_entries) == len(detail_entries)`; if not, raise a 422 ("ตารางใน PDF ไม่ตรงกัน กรุณาตรวจสอบ") rather than importing misaligned/truncated data. At minimum log a warning with the three counts on every import.

Test plan: craft a PDF cell set where details has one fewer entry; assert the import refuses or warns instead of silently importing N-1 rows shifted.

---

## [M4] Frontend silently swallows classify + review failures

File: `app/bank-statement/page.tsx:69-82, 125-148`

Current code:
```python
} catch {
  // silent
}
```
(both `fetchReview` and `handleClassify`; `fetchReview` also `if (!res.ok) return;` at line 73).

Issue: this is exactly the documented anti-pattern in `VEXONHQ/CLAUDE.md` pitfall #1. If `/bank-statement/classify/{id}` returns 500 (e.g. the entry_id isn't a valid uuid, or a rule-insert conflict), `handleClassify` removes the item from the local list (line 141) only inside the success path — but on failure it shows nothing and the operator assumes it failed silently with no feedback. `fetchReview` 500/network errors leave the review list stale with no error state. Misclassified or unclassified money goes unnoticed.

Note: not an auth-header finding (global AuthProvider handles that) — this is about suppressed non-2xx/500 responses.

Suggested fix: set an error state in both `catch` blocks and on `!res.ok`; surface a toast/inline error. Do not strip the row from the list unless the server confirms success.

Test plan: stub classify to 500; assert the row stays and an error message renders.

---

## [M5] `handleClassify` decrements `reviewTotal` optimistically; can drift below zero / desync

File: `app/bank-statement/page.tsx:141-142`

Current code:
```python
setReviewItems(prev => prev.filter(i => i.id !== itemId));
setReviewTotal(t => t - 1);
```

Issue: `reviewTotal` is the server `COUNT(*)` of all needs_review rows (could be > 50, the page size). Decrementing it client-side on each classify drifts from the true count and, combined with M4, can decrement even when the server actually failed (no — it's inside the try after `if (!res.ok) throw`, so only on success; but it still drifts vs server when total > loaded items). After classifying the 50 loaded items, `reviewTotal` shows e.g. 70→20 while no items are listed (page only fetched 50). The empty-state condition `reviewTotal === 0` (line 305) then never shows and the list looks broken.

Suggested fix: re-call `fetchReview()` after a successful classify instead of optimistic local decrement, or paginate properly.

Test plan: seed 60 needs_review rows; classify all 50 loaded; assert UI refetches and shows the next page rather than an empty list with a non-zero badge.

---

## [M6] `save_rule` derives match_value from first whitespace token of a Thai description

File: `phase12_bank_statement_routes.py:486-498`

Current code:
```python
match_val = desc.split()[0] if desc.split() else desc[:20]
```

Issue: KBank Thai descriptions like `"จาก X4826 บจก.ไลน์ เพย์"` split on whitespace → first token `"จาก"` (the direction prefix), which is shared by every income row. Saving a `name` rule with `match_value="จาก"` will then auto-classify ALL future income into that one category (the `name` rule does `if mv in desc`, line 326). This silently misroutes large volumes of income — a P&L correctness problem the next import.

Suggested fix: strip the known direction prefixes (`จาก`/`โอนไป`/`เพื่อชำระ`) before picking the match token, and prefer the counterparty token (e.g. the `บจก.`/name segment). Validate the rule isn't a bare prefix before insert.

Test plan: classify a "จาก ..." row with save_rule=true; assert the saved `match_value` is the counterparty, not `"จาก"`; assert it doesn't match an unrelated "จาก ..." row.

---

## [L1] Percent formatting truncates fractional WHT rates

File: `app/tax/page.tsx:60`; `tax_routes.py:272,323`

Current code: `pct = (n) => ${n.toFixed(0)}%`; XLSX `f'{s["wht_pct"]:.0f}%'`.

Issue: `toFixed(0)` / `:.0f` round a 1.5% or 2.5% rate to "2%". All current rates are integers so no live impact, but a non-integer rate would display wrong on both the page and the filed XLSX.

Suggested fix: use `:.2f` trimmed, or display the raw rate.

Test plan: set a 1.5% rule; assert UI/XLSX show 1.5%.

---

## [L2] Tax page `total_net` computed by backend but never displayed; `net_before_wht` unused

File: `tax_routes.py:187`, `app/tax/page.tsx` (no reference)

Issue: backend returns `total_net` and per-txn `net_before_wht`, but the UI shows only `total_paid` and `total_wht`. Given C1's confusion over which figure is gross vs net, the unused fields are dead weight that could mislead a future dev. Low risk, but worth reconciling once C1 is resolved.

Suggested fix: after fixing C1's convention, drop or correctly surface these fields.

---

## [L3] `_format_month_th` re-parses month without the validation guard

File: `tax_routes.py:80-82` vs `67-77`

Current code:
```python
def _format_month_th(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{MONTH_NAMES_TH[m]} {y + 543}"
```

Issue: `_month_bounds` validates and raises HTTP 400 on a bad month, but `_format_month_th` (called at line 181 after bounds) re-parses with bare `int(...)` and indexes `MONTH_NAMES_TH[m]`. A month like `"2026-13"` passes `_month_bounds`'s try (int ok) but `monthrange(y,13)` raises `calendar.IllegalMonthError` (uncaught → 500, not 400). And `MONTH_NAMES_TH[m]` would IndexError for m>12. Minor since `_month_bounds` runs first and would surface the calendar error, but the error becomes a 500 rather than the intended 400.

Suggested fix: validate `1 <= m <= 12` inside `_month_bounds` and raise 400 there.

Test plan: `GET /tax/wht-summary?month=2026-13` → assert 400, not 500.

---

## Notes / non-findings

- Auth headers: NOT flagged (global AuthProvider interceptor) per audit rules.
- Equity/transfer leak into tax: tax query filters by explicit WHT `category_code`s only, so `owner_capital`/`owner_advance`/`transfer_error` cannot enter the WHT total — clean. (The risk there is the opposite: under-inclusion, see C2.)
- `bank_statement_entries` columns used (`txn_date, description, debit, credit, balance, direction, amount, category_code, source_type, match_status, branch_code, import_batch_id, created_at, id`) all match the verified schema — no hallucinated columns found.
- `/history` aggregates `direction='income'/'expense'` sums but does NOT exclude equity/transfer source_types; if this batch summary is ever shown as a P&L figure it would leak equity. It is labelled an import-batch summary, so MEDIUM-adjacent but acceptable as-is — flag if it feeds P&L later. (Per spec, `v_daybook_pnl` is the P&L source, not this endpoint.)
