# Loan Tracking (เงินยืม) Implementation Plan — Phase 1 (core ledger)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let TUM record co-owner loans (เงินยืม) without polluting the P&L, and see outstanding balance per lender on the dashboard.

**Architecture:** Two new `source_type` values (`loan_in`, `loan_repayment`) on `bank_statement_entries`, both added to every P&L exclusion list so they never hit profit. The lender name is stored in the existing free-text `notes` column at tag time (via the existing `POST /classify/{entry_id}` endpoint, extended). A new read-only view `v_loan_balance` aggregates per lender straight from `bank_statement_entries`; a new `GET /loans` endpoint serves it to the web dashboard.

**Tech Stack:** FastAPI, psycopg2, Supabase Postgres (project `osneubnwghvbwyazaedo` / `mara-ai-prod`), pytest smoke tests, `verify.ps1`.

**Spec:** `docs/superpowers/specs/2026-05-31-loan-tracking-design.md` (read §3.5 for the live-DB facts that shaped this).

**Workflow guardrails (AGENTS.md):**
- Claude **never** runs `git push`. Each task commits locally; the final handoff gives TUM a PowerShell paste block.
- Migrations are idempotent and committed to the repo **before** being applied to prod.
- `bank_statement_entries.source_type` has **no CHECK constraint** (verified) — new values are safe.
- This plan is Phase 1 (manual tagging). **Auto-tag-from-memo is Phase 2, a separate plan** (it touches `slip_routes.py` + the scheduler — a coordination zone). Phase 1 ships and is testable on its own.

---

## File structure

- Create: `migrations/2026_05_31_loan_sources_pnl_exclude.sql` — extend `v_daybook_pnl` exclusion.
- Create: `migrations/2026_05_31_v_loan_balance.sql` — the per-lender balance view.
- Create: `loan_routes.py` — `GET /loans`, `GET /loans/{lender}`.
- Modify: `main.py` — import + `include_router(loan_router)`.
- Modify: `pnl_routes.py` — add loan sources to 2 inline exclusion lists.
- Modify: `cashflow_routes.py` — add loan sources to 3 inline exclusion lists.
- Modify: `phase12_bank_statement_routes.py` — `ClassifyRequest.lender` + write it to `notes`.
- Modify: `tests/test_smoke.py` — assert `GET /loans` returns 200.

---

## Task 1: Extend the P&L exclusion view (`v_daybook_pnl`)

**Files:**
- Create: `migrations/2026_05_31_loan_sources_pnl_exclude.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 2026_05_31_loan_sources_pnl_exclude.sql
-- Add loan_in / loan_repayment to the P&L exclusion list.
-- A loan is a financing/liability event, never income or expense — both legs
-- must be excluded from profit (same class as owner_capital / owner_advance).
-- Idempotent: CREATE OR REPLACE. Reversible by restoring the prior list.
-- NOTE: keep this list in sync with the inline lists in pnl_routes.py and
-- cashflow_routes.py (Tasks 4-5). See AGENTS.md #20.

CREATE OR REPLACE VIEW public.v_daybook_pnl AS
SELECT *
FROM public.v_daybook
WHERE source NOT IN (
    'owner_capital', 'owner_advance', 'transfer_error',
    'bank_statement', 'vendor_payment',
    'grab_payout', 'lineman_payout',
    'pos_cash_deposit', 'cash_withdrawal',
    'loan_in', 'loan_repayment'
);

COMMENT ON VIEW public.v_daybook_pnl IS
    'P&L source of truth: v_daybook with owner-equity, transfer, and loan sources excluded. Use this for all profit/expense/income aggregates. Use v_daybook (raw) only for the full ledger / daybook list.';
```

- [ ] **Step 2: Syntax-check the SQL by applying it to prod via migration**

This view is additive (CREATE OR REPLACE, no data change). Apply it now so later tasks can be A/B-verified.
Use the Supabase `apply_migration` tool with name `loan_sources_pnl_exclude` and the SQL above.
Expected: success, no error.

- [ ] **Step 3: Verify the new exclusion is live**

Run (Supabase `execute_sql`):
```sql
SELECT pg_get_viewdef('public.v_daybook_pnl', true) AS def;
```
Expected: the definition now contains `'loan_in', 'loan_repayment'`.

- [ ] **Step 4: Commit**

```bash
git add migrations/2026_05_31_loan_sources_pnl_exclude.sql
git commit -m "feat(loan): exclude loan_in/loan_repayment from v_daybook_pnl"
```

---

## Task 2: Add loan sources to inline exclusion list in `pnl_routes.py`

**Files:**
- Modify: `pnl_routes.py` (two `source NOT IN (...)` blocks, ~lines 96 and 166)

- [ ] **Step 1: Confirm both occurrences and their exact text**

Run: `rg -n "pos_cash_deposit', 'cash_withdrawal'" pnl_routes.py`
Expected: 2 matches (the monthly query ~L96-99 and the yearly query ~L166-169). Each ends the `NOT IN` list with `'pos_cash_deposit', 'cash_withdrawal')`.

- [ ] **Step 2: Edit — add the two loan values to BOTH blocks**

In each block, change the closing line of the `NOT IN` list from:
```python
                            'pos_cash_deposit', 'cash_withdrawal')
```
to:
```python
                            'pos_cash_deposit', 'cash_withdrawal',
                            'loan_in', 'loan_repayment')
```
(Apply to both occurrences — match indentation exactly. `d.source NOT IN (...)`.)

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('pnl_routes.py', encoding='utf-8').read())"`
Expected: no output (parses clean).

- [ ] **Step 4: Verify no occurrence was missed**

Run: `rg -n "loan_in', 'loan_repayment'" pnl_routes.py`
Expected: 2 matches.

- [ ] **Step 5: Commit**

```bash
git add pnl_routes.py
git commit -m "feat(loan): exclude loan sources in pnl_routes inline lists"
```

---

## Task 3: Add loan sources to inline exclusion lists in `cashflow_routes.py`

**Files:**
- Modify: `cashflow_routes.py` (three `source NOT IN (...)` blocks, ~lines 66, 86, 197)

- [ ] **Step 1: Confirm all occurrences**

Run: `rg -n "pos_cash_deposit', 'cash_withdrawal'" cashflow_routes.py`
Expected: 3 matches.

- [ ] **Step 2: Edit — add the two loan values to ALL THREE blocks**

In each block, change the closing line from:
```python
                            'pos_cash_deposit', 'cash_withdrawal')
```
to:
```python
                            'pos_cash_deposit', 'cash_withdrawal',
                            'loan_in', 'loan_repayment')
```
(If a block's closing differs, just append `, 'loan_in', 'loan_repayment'` before the final `)` of that `NOT IN` list. `source NOT IN (...)`.)

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('cashflow_routes.py', encoding='utf-8').read())"`
Expected: no output.

- [ ] **Step 4: Verify count + sweep for any other raw-v_daybook readers**

Run: `rg -n "loan_in', 'loan_repayment'" cashflow_routes.py` → expected 3 matches.
Run: `rg -n "FROM public\.v_daybook\b" *.py` (word-boundary excludes `_pnl`). For EVERY file listed, open the query and confirm it is NOT a P&L/income/expense/export aggregate, OR that its `NOT IN` list now contains the loan values. If a P&L aggregate is found without the loan exclusion, add the two values there too and note it in the commit. Known clean targets already patched: `pnl_routes.py`, `cashflow_routes.py`.

- [ ] **Step 5: Commit**

```bash
git add cashflow_routes.py
git commit -m "feat(loan): exclude loan sources in cashflow_routes inline lists"
```

---

## Task 4: Prove the exclusion works (A/B P&L test)

**Files:** none (verification only, against prod via Supabase tools).

- [ ] **Step 1: Capture baseline P&L for a test month**

Pick a month with data, e.g. `2026-04`. Run (`execute_sql`):
```sql
SELECT direction, COALESCE(SUM(amount),0) AS total
FROM public.v_daybook_pnl
WHERE entry_date BETWEEN '2026-04-01' AND '2026-04-30'
GROUP BY direction ORDER BY direction;
```
Record the income and expense totals (baseline).

- [ ] **Step 2: Insert two TEMP loan rows (match_status='manual' so they enter v_daybook)**

NOTE: `direction` and `amount` on `bank_statement_entries` are GENERATED columns (derived from debit/credit) — do NOT insert them or you get `cannot insert a non-DEFAULT value into column "direction"`. Set debit/credit; the generated columns compute.
Run (`execute_sql`):
```sql
INSERT INTO public.bank_statement_entries
  (txn_date, description, debit, credit, balance,
   category_code, source_type, match_status, branch_code, notes)
VALUES
  ('2026-04-15','TEST loan_in นุศรา',0,33000,0,'loan','loan_in','manual','thawi_watthana','__TEST_LOAN__'),
  ('2026-04-16','TEST loan_repayment นุศรา',15000,0,0,'loan','loan_repayment','manual','thawi_watthana','__TEST_LOAN__');
```

- [ ] **Step 3: Re-run the baseline query — totals MUST be unchanged**

Re-run the Step 1 query. Expected: income and expense totals **identical** to baseline (the loan rows are excluded). If either changed, an exclusion list was missed — go back to Tasks 1-3.

- [ ] **Step 4: Confirm the rows DO appear in the raw daybook (sanity)**

```sql
SELECT source, direction, amount FROM public.v_daybook
WHERE entry_date IN ('2026-04-15','2026-04-16') AND amount IN (33000,15000)
  AND source IN ('loan_in','loan_repayment');
```
Expected: 2 rows (they exist in raw v_daybook but are filtered from v_daybook_pnl).

- [ ] **Step 5: Delete the TEMP rows**

```sql
DELETE FROM public.bank_statement_entries WHERE notes = '__TEST_LOAN__';
```
Expected: DELETE 2. (Leave Steps 1 totals as the truth.) No commit (verification only).

---

## Task 5: Create the `v_loan_balance` view

**Files:**
- Create: `migrations/2026_05_31_v_loan_balance.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 2026_05_31_v_loan_balance.sql
-- Per-lender loan ledger. Reads bank_statement_entries DIRECTLY (not v_daybook),
-- because v_daybook hard-codes counterparty=NULL for bank rows (spec §3.5).
-- Lender = the notes column (set at tag time via POST /classify). Rows with no
-- lender yet group under 'ไม่ระบุผู้ให้ยืม'.
-- outstanding = borrowed - repaid. Negative => lender now owes the shop.
-- Idempotent: CREATE OR REPLACE. Reversible with DROP VIEW.

CREATE OR REPLACE VIEW public.v_loan_balance AS
SELECT
  COALESCE(NULLIF(btrim(notes), ''), 'ไม่ระบุผู้ให้ยืม')                  AS lender,
  COALESCE(SUM(amount) FILTER (WHERE source_type = 'loan_in'), 0)        AS borrowed,
  COALESCE(SUM(amount) FILTER (WHERE source_type = 'loan_repayment'), 0) AS repaid,
  COALESCE(SUM(amount) FILTER (WHERE source_type = 'loan_in'), 0)
    - COALESCE(SUM(amount) FILTER (WHERE source_type = 'loan_repayment'), 0) AS outstanding,
  MAX(txn_date) AS last_activity,
  COUNT(*)      AS txn_count
FROM public.bank_statement_entries
WHERE source_type IN ('loan_in', 'loan_repayment')
GROUP BY 1;

COMMENT ON VIEW public.v_loan_balance IS
    'Per-lender loan ledger (เงินยืม): borrowed - repaid = outstanding, grouped by the notes column. Source: bank_statement_entries rows tagged loan_in / loan_repayment.';
```

- [ ] **Step 2: Apply via `apply_migration`** (name `v_loan_balance`). Expected: success.

- [ ] **Step 3: Verify the view returns rows with the trigger data shape**

Insert the two TEMP rows again (Task 4 Step 2), then run:
```sql
SELECT * FROM public.v_loan_balance WHERE lender LIKE '%TEST%' OR lender = '__TEST_LOAN__';
```
Expected: one row `lender='__TEST_LOAN__', borrowed=33000, repaid=15000, outstanding=18000, txn_count=2`.
Then delete: `DELETE FROM public.bank_statement_entries WHERE notes = '__TEST_LOAN__';`

- [ ] **Step 4: Commit**

```bash
git add migrations/2026_05_31_v_loan_balance.sql
git commit -m "feat(loan): add v_loan_balance per-lender ledger view"
```

---

## Task 6: New `loan_routes.py` endpoints

**Files:**
- Create: `loan_routes.py`

- [ ] **Step 1: Write the router**

```python
"""Loan (เงินยืม) ledger endpoints.

Read-only views over bank_statement_entries rows tagged source_type
loan_in / loan_repayment. A loan is a financing/liability item, excluded
from the P&L (see migrations/2026_05_31_loan_sources_pnl_exclude.sql).
Lender name comes from the `notes` column, set when the row is tagged via
POST /classify/{entry_id} (phase12_bank_statement_routes.py).
"""
import logging
import os

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("loan")
router = APIRouter(prefix="/loans", tags=["loans"])


def _get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


@router.get("")
def list_loans():
    """Per-lender outstanding balance (borrowed - repaid)."""
    conn = _get_db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT lender, borrowed, repaid, outstanding,
                       last_activity, txn_count
                FROM public.v_loan_balance
                ORDER BY outstanding DESC
                """
            )
            rows = cur.fetchall()
        for r in rows:
            for f in ("borrowed", "repaid", "outstanding"):
                r[f] = float(r[f] or 0)
            r["txn_count"] = int(r["txn_count"] or 0)
            r["last_activity"] = str(r["last_activity"]) if r["last_activity"] else None
        return {"lenders": rows}
    except Exception as e:
        logger.exception("list_loans failed")
        raise HTTPException(500, f"โหลดยอดเงินยืมไม่สำเร็จ: {e}")
    finally:
        conn.close()


@router.get("/{lender}")
def loan_detail(lender: str):
    """Per-lender transaction list (each loan_in / loan_repayment row)."""
    conn = _get_db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id::text, txn_date, direction, amount, source_type,
                       description
                FROM public.bank_statement_entries
                WHERE COALESCE(NULLIF(btrim(notes), ''), 'ไม่ระบุผู้ให้ยืม') = %s
                  AND source_type IN ('loan_in', 'loan_repayment')
                ORDER BY txn_date
                """,
                (lender,),
            )
            rows = cur.fetchall()
        for r in rows:
            r["amount"] = float(r["amount"] or 0)
            r["txn_date"] = str(r["txn_date"]) if r["txn_date"] else None
        return {"lender": lender, "entries": rows}
    except Exception as e:
        logger.exception("loan_detail failed lender=%s", lender)
        raise HTTPException(500, f"โหลดรายการเงินยืมไม่สำเร็จ: {e}")
    finally:
        conn.close()
```

- [ ] **Step 2: Syntax check**

Run: `python -c "import ast; ast.parse(open('loan_routes.py', encoding='utf-8').read())"`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add loan_routes.py
git commit -m "feat(loan): add GET /loans and GET /loans/{lender} endpoints"
```

---

## Task 7: Register the router in `main.py`

**Files:**
- Modify: `main.py` (import near line 59; `include_router` near line 290)

- [ ] **Step 1: Add the import**

After the line `from slip_routes import router as slip_router` (or alongside the other router imports near line 59), add:
```python
from loan_routes import router as loan_router
```

- [ ] **Step 2: Register the router**

After `app.include_router(slip_router)` (~line 290), add:
```python
app.include_router(loan_router)
```
Do NOT add `/loans` to `PUBLIC_PATHS` — it must stay behind JWT (financial data).

- [ ] **Step 3: Syntax check + import check**

Run: `python -c "import ast; ast.parse(open('main.py', encoding='utf-8').read())"`
Expected: no output.

- [ ] **Step 4: Local boot probe**

Run a local uvicorn (`uvicorn main:app --port 8000`) with `DATABASE_URL` set, then:
`Invoke-WebRequest http://localhost:8000/openapi.json | Select-String "/loans"`
Expected: `/loans` and `/loans/{lender}` appear in the path list. Stop uvicorn.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(loan): register loan_router in main"
```

---

## Task 8: Extend `POST /classify/{entry_id}` to capture the lender

**Files:**
- Modify: `phase12_bank_statement_routes.py` (`ClassifyRequest` ~line 49; `classify_entry` UPDATE ~line 482)

- [ ] **Step 1: Add `lender` to the request model**

In `ClassifyRequest` (line 49-53), add a field:
```python
class ClassifyRequest(BaseModel):
    category_code: str
    source_type: Optional[str] = "bank_statement"
    save_rule: bool = False        # ถ้า True → บันทึก rule สำหรับครั้งต่อไป
    rule_type: Optional[str] = "name"   # keyword / name / amount_pattern
    lender: Optional[str] = None   # written to notes when tagging a loan row
```

- [ ] **Step 2: Write `notes` in the UPDATE when a lender is provided**

Replace the UPDATE block in `classify_entry` (lines 482-489) with:
```python
            cur.execute("""
                UPDATE public.bank_statement_entries
                SET category_code = %s,
                    source_type   = %s,
                    match_status  = 'manual',
                    notes         = COALESCE(%s, notes)
                WHERE id = %s
                RETURNING id, description, amount, direction
            """, (body.category_code, body.source_type, body.lender, entry_id))
```
(`COALESCE(%s, notes)` leaves `notes` untouched when `lender` is null, so non-loan reclassifies are unaffected. `match_status='manual'` is already set here — confirmed.)

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('phase12_bank_statement_routes.py', encoding='utf-8').read())"`
Expected: no output.

- [ ] **Step 4: End-to-end DB test of the tag → ledger path**

Insert one unreviewed test row, classify it as a loan with a lender, confirm it lands in `v_loan_balance`, then clean up. Run (`execute_sql`) one statement at a time:
```sql
-- a) create an unreviewed test row (direction/amount are GENERATED — omit them)
INSERT INTO public.bank_statement_entries
  (txn_date, description, debit, credit, balance,
   source_type, match_status, branch_code)
VALUES ('2026-04-20','TEST classify นุศรา',0,33000,0,
        'bank_statement','needs_review','thawi_watthana')
RETURNING id;
```
Then simulate the endpoint's UPDATE (use the returned id):
```sql
UPDATE public.bank_statement_entries
SET category_code='loan', source_type='loan_in', match_status='manual',
    notes=COALESCE('นุศรา', notes)
WHERE id = '<id>';

SELECT lender, borrowed, outstanding FROM public.v_loan_balance WHERE lender='นุศรา';
-- Expected: borrowed=33000, outstanding=33000 (plus any real data)

DELETE FROM public.bank_statement_entries WHERE description='TEST classify นุศรา';
```
Expected: the SELECT shows นุศรา with borrowed including 33000; cleanup DELETE removes it.

- [ ] **Step 5: Commit**

```bash
git add phase12_bank_statement_routes.py
git commit -m "feat(loan): classify endpoint writes lender to notes for loan rows"
```

---

## Task 9: Add `/loans` to the smoke test

**Files:**
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Find the route list the smoke test iterates**

Run: `rg -n "/slip|CRITICAL_ROUTES|ROUTES|def test_" tests/test_smoke.py | head -40`
Identify the list/collection of GET routes the suite probes for non-404.

- [ ] **Step 2: Add `/loans` to that list**

Add the string `"/loans"` to the routes collection (matching the existing style — e.g. another entry in the `CRITICAL_ROUTES`/`GET_ROUTES` list). `/loans` requires auth, so if the suite sends an auth token it should expect 200; if it only checks "not 404", `/loans` returning 200/401 both pass the not-404 assertion. Follow whatever the existing entries do.

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('tests/test_smoke.py', encoding='utf-8').read())"`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add tests/test_smoke.py
git commit -m "test(loan): probe /loans in smoke suite"
```

---

## Task 10: Full local verification + handoff prep

**Files:** none (verification + handoff).

- [ ] **Step 1: Run the full local check**

Run: `.\verify.ps1`
Expected: compileall passes on every `.py` (no syntax errors).

- [ ] **Step 2: Confirm migrations are committed but note they're already applied**

The two view migrations were applied via `apply_migration` during Tasks 1 & 5 (additive, CREATE OR REPLACE — safe). They are also committed to the repo. Note this in the handoff so TUM knows the views are already live; only the code (`loan_routes.py`, `main.py`, the 3 edited files, test) needs the push + Coolify deploy.

- [ ] **Step 3: Prepare the backup tag + handoff paste block for TUM**

Produce a single PowerShell paste block (no `Co-Authored-By:` trailer) containing:
```powershell
cd C:\Users\rapee\vexonhq-ocr-api
git fetch origin
git tag backup-pre-loan-tracking-2026-05-31 origin/main
git push origin backup-pre-loan-tracking-2026-05-31
git push origin main
```
State plainly what was verified: "verify.ps1 passed; both views applied + A/B P&L proof identical; /loans appears in openapi; classify→ledger DB test = outstanding 18,000 on the นุศรา fixture." Then **stop — TUM pushes.** After deploy, run `.\verify.ps1 -Smoke` only once VPS CPU < 30% (memory `smoke_after_deploy_wait`).

- [ ] **Step 4: Post-task doc updates (AGENTS post-task routine)**

- `docs/TOMORROW.md` — note Phase 1 loan tracking shipped; Phase 2 (auto-tag-from-memo) pending.
- `C:\Users\rapee\VEXONHQ\docs\04_LOGS\DAILY_LOG_2026_05.md` — one session entry.
- Append AGENTS.md pitfall if a new rule emerged (e.g. "loan = financing, exclude both legs; lender stored in bank_statement_entries.notes").

---

## Out of scope → Phase 2 (separate plan)

**Auto-tag-from-memo.** Seed `statement_rules` keyword rows (`ยืม`→loan_in / income, `คืนยืม`→loan_repayment / expense) and wire the loan category→source mapping into the slip→statement reconcile so a slip memo auto-tags its matched bank row. This touches `slip_routes.py` and the APScheduler nightly job — a **coordination zone** (AGENTS Boundaries) — so it gets its own spec/plan and is done after Phase 1 is verified in prod. The trigger slip (`ร้านหม่าล่า 33000-15000`) has no "ยืม" keyword and will be tagged manually in Phase 1 regardless.

**Dashboard card.** Built in the `VEXONHQ` frontend repo (separate session) consuming `GET /loans`.

---

## Self-review notes

- **Spec coverage:** §3.1 sources → T1-3; §3.1 P&L exclusion correctness → T1-4; §3.2 manual lender capture + match_status='manual' → T8; §3.3 view + endpoints → T5-7; §3.4 over-repayment (negative outstanding) handled by plain subtraction in T5; testing §4 → T4, T8, T9, T10; §3.5 facts baked into T5/T8. §3.2 auto-from-memo → explicitly deferred to Phase 2.
- **No placeholders:** every code/SQL step is complete and runnable. The only `<id>` placeholder (T8 S4) is a runtime value returned by the prior statement, which is unavoidable and labeled.
- **Type consistency:** `lender` (str) flows ClassifyRequest → `notes` column → `v_loan_balance.lender` → `GET /loans` `lender` field → `GET /loans/{lender}` path param, all the same key. `source_type` values `loan_in`/`loan_repayment` identical across migration, exclusion lists, view, and endpoint.
