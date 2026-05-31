# Loan Auto-tag (Phase 2) Implementation Plan — vexonhq-ocr-api

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-tag loan **repayment** slips (memo "คืนยืม"/"คืนเงินยืม") as `loan_repayment` with the lender on the bank row, via the existing nightly slip-reconcile — plus a Thai how-to runbook for the mostly-manual loan workflow.

**Architecture:** Reuse the slip→statement reconcile (`slip_routes.reconcile_slips_to_statements`, expense-only). Seed two `statement_rules` keyword rows so a repayment memo resolves to category `loan_repayment`; map that category to `source_type='loan_repayment'`; and stamp the normalized lender into `notes` so the per-lender ledger (`v_loan_balance`) nets correctly. Borrows (incoming) stay manual.

**Tech Stack:** FastAPI, psycopg2, Supabase Postgres (`osneubnwghvbwyazaedo`), `verify.ps1`.

**Spec:** `docs/superpowers/specs/2026-05-31-loan-autotag-phase2-design.md`. **Depends on Phase 1** (loan sources, `v_loan_balance`, `/loans`).

**Verified facts:**
- `slip_routes._classify_slip_category` matches a slip memo against `statement_rules WHERE rule_type='keyword' AND direction='expense'` via `ILIKE '%'||match_value||'%'`, ordered `priority DESC, char_length(match_value) DESC` (slip_routes.py ~L368-389). Slips are expense/outgoing only.
- `statement_rules` has a UNIQUE on `(rule_type, match_value)` (used by `POST /add-rule`'s `ON CONFLICT`); columns: `rule_type, match_value, direction, category_code, source_type, priority`.
- `_CAT_TO_SOURCE` dict at slip_routes.py ~L627; `_source_for_category` at ~L639.
- Reconcile Pass-2 loop + UPDATE at slip_routes.py ~L701-728; it already SELECTs `recipient_name` and guards `WHERE … match_status <> 'manual'`.
- `slip_routes.py` already imports `Optional`. Confirm `re` is imported (add if missing).
- `bank_statement_entries`: `notes` (text), `source_type` (no CHECK), `direction`/`amount` GENERATED.

**Workflow:** commit locally; **TUM pushes**; no `Co-Authored-By:` trailer. `slip_routes.py` + the 02:00 scheduler are a **coordination zone** — every change here is additive (a loan branch) and idempotent.

---

## File structure

- Create: `migrations/2026_05_31_loan_repayment_keyword_rules.sql` — seed the two keyword rules.
- Modify: `slip_routes.py` — `_CAT_TO_SOURCE` (+1 entry), new `_normalize_lender()`, reconcile Pass-2 UPDATE (write `notes`).
- Create: `docs/HOWTO_loans.md` — Thai runbook.

---

## Task 1: Seed the repayment keyword rules

**Files:**
- Create: `migrations/2026_05_31_loan_repayment_keyword_rules.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 2026_05_31_loan_repayment_keyword_rules.sql
-- Phase 2: auto-tag loan REPAYMENT slips. A slip memo containing "คืนยืม" or
-- "คืนเงินยืม" classifies the matched OUTGOING bank row as loan_repayment via the
-- nightly slip-reconcile (_classify_slip_category → _CAT_TO_SOURCE). Borrow/incoming
-- stays manual (the slip pipeline is expense-only).
-- "คืนยืม" is NOT a substring of "คืนเงินยืม", so both keywords are needed.
-- priority=100 so they beat generic name rules in the keyword cascade.
-- Idempotent: ON CONFLICT (rule_type, match_value) DO UPDATE.

INSERT INTO public.statement_rules
    (rule_type, match_value, direction, category_code, source_type, priority)
VALUES
    ('keyword', 'คืนยืม',     'expense', 'loan_repayment', 'loan_repayment', 100),
    ('keyword', 'คืนเงินยืม', 'expense', 'loan_repayment', 'loan_repayment', 100)
ON CONFLICT (rule_type, match_value) DO UPDATE
    SET direction     = EXCLUDED.direction,
        category_code = EXCLUDED.category_code,
        source_type   = EXCLUDED.source_type,
        priority      = EXCLUDED.priority;
```

- [ ] **Step 2: Apply it** via the Supabase `apply_migration` tool (name `loan_repayment_keyword_rules`, the SQL above). Expected: success.

- [ ] **Step 3: Verify the rules exist + the classifier query resolves them**

Run (`execute_sql`):
```sql
SELECT match_value, direction, category_code, source_type, priority
FROM public.statement_rules
WHERE category_code = 'loan_repayment' ORDER BY match_value;
```
Expected: 2 rows.
Then verify the exact query `_classify_slip_category` runs returns `loan_repayment` for both memos:
```sql
SELECT 'คืนยืม' AS memo,
  (SELECT category_code FROM public.statement_rules
   WHERE rule_type='keyword' AND direction='expense'
     AND 'คืนยืม' ILIKE '%'||match_value||'%'
   ORDER BY priority DESC, char_length(match_value) DESC LIMIT 1) AS resolved
UNION ALL
SELECT 'คืนเงินยืม 5000',
  (SELECT category_code FROM public.statement_rules
   WHERE rule_type='keyword' AND direction='expense'
     AND 'คืนเงินยืม 5000' ILIKE '%'||match_value||'%'
   ORDER BY priority DESC, char_length(match_value) DESC LIMIT 1);
```
Expected: both `resolved = 'loan_repayment'`.

- [ ] **Step 4: Commit**

```bash
git add migrations/2026_05_31_loan_repayment_keyword_rules.sql
git commit -m "feat(loan): seed statement_rules keywords for repayment auto-tag"
```

---

## Task 2: Map the category + stamp the lender into notes (`slip_routes.py`)

**Files:**
- Modify: `slip_routes.py` (`_CAT_TO_SOURCE` ~L627; add `_normalize_lender`; reconcile Pass-2 UPDATE ~L701-728)

- [ ] **Step 1: Ensure `re` is imported**

At the top of `slip_routes.py`, confirm `import re` is present among the imports. If it is NOT, add `import re` with the other stdlib imports.

- [ ] **Step 2: Add `loan_repayment` to `_CAT_TO_SOURCE`**

Change the dict (ends around L636) from:
```python
    "bank_fee":     "bank_fee",
    "tax":          "tax_expense",
}
```
to:
```python
    "bank_fee":     "bank_fee",
    "tax":          "tax_expense",
    "loan_repayment": "loan_repayment",
}
```

- [ ] **Step 3: Add the `_normalize_lender` helper**

Immediately after the `_source_for_category` function (right after its `return` line ~L640), add:
```python


_LENDER_TITLE_RE = re.compile(r'^(?:น\.ส\.?|นางสาว|นาย|นาง)\s*')


def _normalize_lender(name: Optional[str]) -> Optional[str]:
    """Canonical short lender name for grouping loans by person.

    Strips a Thai title prefix, drops '+' padding / extra whitespace, and returns
    the FIRST name token so the slip's recipient ("น.ส. นุศรา ปรางม++") and TUM's
    hand-typed lender ("นุศรา") collapse to the same key in v_loan_balance.
    Returns None for blank input (so COALESCE leaves notes untouched).
    """
    if not name:
        return None
    s = name.replace('+', ' ').strip()
    s = _LENDER_TITLE_RE.sub('', s).strip()
    if not s:
        return None
    return s.split()[0]
```

- [ ] **Step 4: Write the lender into the reconcile Pass-2 UPDATE**

Replace the loop body block (currently ~L701-728) — from `for sid, recipient_name, memo, stmt_id in matched_slips:` down through its `except` — with:
```python
        for sid, recipient_name, memo, stmt_id in matched_slips:
            category_code, _src = _classify_slip_category(conn, recipient_name, memo)
            if not category_code:
                continue  # no memo/name signal — leave the bank row's default
            source_type = _source_for_category(category_code)
            # Loan repayments: also stamp the lender (normalized) onto notes so the
            # per-lender ledger (v_loan_balance groups by notes) nets borrow vs repay.
            lender_notes = (
                _normalize_lender(recipient_name)
                if category_code == "loan_repayment"
                else None
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE public.bank_statement_entries
                        SET category_code = %s,
                            source_type   = %s,
                            match_status  = 'auto',
                            notes         = COALESCE(%s, notes)
                        WHERE id = %s
                          AND match_status <> 'manual'
                          AND (category_code IS DISTINCT FROM %s
                               OR source_type IS DISTINCT FROM %s
                               OR (%s IS NOT NULL AND notes IS DISTINCT FROM %s))
                        """,
                        (category_code, source_type, lender_notes, str(stmt_id),
                         category_code, source_type, lender_notes, lender_notes),
                    )
                    if cur.rowcount:
                        categorized += 1
                conn.commit()
            except Exception:
                conn.rollback()
                log.exception("reconcile: push category failed slip=%s stmt=%s",
                              sid, stmt_id)
```
(8 `%s` placeholders ↔ 8-tuple: `category_code, source_type, lender_notes, stmt_id, category_code, source_type, lender_notes, lender_notes`. `notes` is only written for `loan_repayment` (else `lender_notes` is None → `COALESCE` no-op); the extra guard term lets a row that already has the right category/source but a stale/empty `notes` still get its lender stamped. `match_status <> 'manual'` still protects hand-tagged rows.)

- [ ] **Step 5: Syntax check**

Run: `python -c "import ast; ast.parse(open('slip_routes.py', encoding='utf-8').read())"`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add slip_routes.py
git commit -m "feat(loan): auto-tag repayment slips to loan_repayment + lender in notes"
```

---

## Task 3: End-to-end DB verification (orchestrator)

**Files:** none (verification only, against prod via Supabase tools).

- [ ] **Step 1: Capture baseline P&L (A/B)**

```sql
SELECT direction, COALESCE(SUM(amount),0) AS total
FROM public.v_daybook_pnl
WHERE entry_date BETWEEN '2026-04-01' AND '2026-04-30'
GROUP BY direction ORDER BY direction;
```
Record income/expense totals.

- [ ] **Step 2: Insert a test OUTGOING bank row + simulate the reconcile UPDATE**

The reconcile keyword resolution was already proven in Task 1 Step 3 (memo → `loan_repayment`). Here, verify the UPDATE the code runs lands in the ledger. `_normalize_lender('น.ส. นุศรา ป')` = `'นุศรา'` (computed by hand for the test). Run:
```sql
INSERT INTO public.bank_statement_entries
  (txn_date, description, debit, credit, balance, category_code, source_type, match_status, branch_code, notes)
VALUES ('2026-04-18','__TEST_REPAY__ คืนยืม นุศรา',5000,0,0,'other_expense','other_expense','auto','thawi_watthana',NULL);

UPDATE public.bank_statement_entries
SET category_code='loan_repayment', source_type='loan_repayment', match_status='auto',
    notes=COALESCE('นุศรา', notes)
WHERE description='__TEST_REPAY__ คืนยืม นุศรา' AND match_status <> 'manual';

SELECT lender, repaid, outstanding FROM public.v_loan_balance WHERE lender='นุศรา';
```
Expected: the SELECT shows `lender='นุศรา'` with `repaid` including 5,000 (outstanding decreases by 5,000 vs any existing นุศรา rows).

- [ ] **Step 3: Confirm P&L unchanged + cleanup**

Re-run the Step 1 query → income/expense **identical** (loan_repayment excluded). Then:
```sql
DELETE FROM public.bank_statement_entries WHERE description='__TEST_REPAY__ คืนยืม นุศรา';
SELECT count(*) AS leftover FROM public.bank_statement_entries WHERE description LIKE '__TEST_REPAY__%';
```
Expected: P&L identical to baseline; `leftover = 0`. No commit (verification only).

---

## Task 4: Thai how-to runbook

**Files:**
- Create: `docs/HOWTO_loans.md`

- [ ] **Step 1: Write the runbook**

Create `docs/HOWTO_loans.md` with EXACTLY this content:

```markdown
# วิธีใช้: เงินยืม (Loans) — กันลืม

> เงินยืม = รายการ **การเงิน ไม่ใช่กำไรขาดทุน**. เงินที่ยืมเข้า ไม่ใช่รายได้;
> เงินที่โอนคืน ไม่ใช่รายจ่าย. ทั้งสองขา **ไม่นับใน P&L**. ดูยอดที่หน้า `/loans`.

## 1. ยืมเข้า (มีคนให้ร้านยืมเงิน) — ทำมือครั้งเดียว
เงินเข้าจากผู้ให้ยืม (เช่น นุศรา โอนเข้าร้าน):
1. ไปหน้า **Bank Statement** (`/bank-statement`)
2. หาแถว **เงินเข้า** จากผู้ให้ยืม
3. ตั้งหมวด/source = **`loan_in`** และใส่ชื่อผู้ให้ยืมแบบสั้น เช่น **`นุศรา`**
   (พิมพ์ชื่อสั้นให้เหมือนกันทุกครั้ง — ระบบใช้ชื่อนี้รวมยอดต่อคน)

> ขายืมเข้าต้องทำมือเสมอ เพราะเงินเข้าไม่ผ่านระบบสลิป (สลิป = ขาออกเท่านั้น)

## 2. โอนคืน (ร้านจ่ายเงินคืนผู้ให้ยืม)
**วิธีที่ดีที่สุด (อัตโนมัติ):** ตอนโอนคืน ให้พิมพ์ใน **memo สลิป** ว่า
**`คืนยืม`** (หรือ `คืนเงินยืม`) แล้วส่งสลิปเข้า LINE ตามปกติ
→ ระบบจับคู่ตอนรอบกลางคืน (02:00) หรือกดปุ่ม **"Reconcile now"** (`POST /slip/reconcile`)
→ ติดป้าย `loan_repayment` + ชื่อผู้ให้ยืม (จากชื่อผู้รับในสลิป) ให้อัตโนมัติ

**ถ้าระบบไม่จับ (memo ไม่มีคำว่า "ยืม"):** ไปหน้า Bank Statement ตั้งแถวขาออกนั้นเป็น
**`loan_repayment`** เอง (ใส่ชื่อผู้ให้ยืมสั้น ๆ ให้ตรงกับขายืมเข้า)

## 3. ดูยอด / ไล่เช็ค
ไปหน้า **เงินยืม** (`/loans`):
- เห็นต่อผู้ให้ยืม: **ยืม / คืน / ค้าง** (ค้าง = ยืม − คืน)
- คลิกชื่อผู้ให้ยืม → กางดูรายการ ยืม/คืน รายครั้ง → เทียบกับสลิปใน LINE

## 4. กันพลาด
- ใช้ **ชื่อผู้ให้ยืมสั้นแบบเดียวกันทุกครั้ง** (เช่น `นุศรา`) ไม่งั้นยอดจะแยกเป็น 2 คน
  (แก้ได้ที่ Bank Statement → แก้ช่องชื่อ/notes ของแถวนั้น)
- memo "คืน" เฉย ๆ ไม่พอ — ต้องมีคำว่า **"ยืม"** (คืนยืม / คืนเงินยืม)
- ขายืมเข้า (เงินเข้า) ไม่มีทางจับอัตโนมัติ — ตั้งมือเสมอ
- อยากเพิ่มคำ trigger ใหม่ (เช่น "ใช้คืน"): เพิ่ม rule ผ่าน `POST /add-rule`
  (`rule_type=keyword, direction=expense, match_value=<คำ>, category_code=loan_repayment`)
  ไม่ต้อง deploy
- ห้ามนับเงินยืม/โอนคืนเป็นรายได้/รายจ่าย — ระบบกันออกจาก P&L ให้แล้วผ่าน source `loan_in`/`loan_repayment`
```

- [ ] **Step 2: Commit**

```bash
git add docs/HOWTO_loans.md
git commit -m "docs(loan): Thai how-to runbook for the manual loan workflow"
```

---

## Task 5: Verify + doc updates + handoff

**Files:** `AGENTS.md`, `docs/TOMORROW.md` (updates).

- [ ] **Step 1: Full local check**

Run: `.\verify.ps1`
Expected: compileall passes (includes `slip_routes.py`).

- [ ] **Step 2: Update AGENTS.md #36**

Append to pitfall #36 a one-line Phase-2 note: auto-tag repayments live (`statement_rules` คืนยืม/คืนเงินยืม → `loan_repayment`; `_CAT_TO_SOURCE` + `_normalize_lender` in `slip_routes.py`); borrow stays manual; how-to in `docs/HOWTO_loans.md`.

- [ ] **Step 3: Update docs/TOMORROW.md**

Under the Session 50 block, note Phase 2 (repayment auto-tag) shipped in code (awaiting push); how-to added.

- [ ] **Step 4: Commit docs**

```bash
git add AGENTS.md docs/TOMORROW.md
git commit -m "docs(loan): AGENTS #36 + TOMORROW — Phase 2 repayment auto-tag"
```

- [ ] **Step 5: Handoff paste block for TUM**

The keyword-rule migration was applied via `apply_migration` during Task 1 (data seed, idempotent). Only the code (`slip_routes.py`, `docs/HOWTO_loans.md`, doc updates) needs push + deploy. Produce:
```powershell
cd C:\Users\rapee\vexonhq-ocr-api
git fetch origin
git tag backup-pre-loan-autotag-2026-05-31 origin/main
git push origin backup-pre-loan-autotag-2026-05-31
git push origin main
```
State what was verified (verify.ps1, keyword resolution, simulated reconcile → v_loan_balance, A/B P&L unchanged). After deploy: forward a repayment slip with memo "คืนยืม" (or `POST /slip/reconcile`) and confirm the row turns `loan_repayment` on `/bank-statement` and shows on `/loans`. Then **stop — TUM pushes.**

---

## Self-review notes

- **Spec coverage:** §2.1 keywords + `_CAT_TO_SOURCE` → T1, T2 S2; §2.2 `_normalize_lender` + notes in reconcile → T2 S3-4; §3 edges (manual guard, idempotent) preserved by the unchanged `WHERE match_status<>'manual'` + `IS DISTINCT FROM` guard → T2 S4; §4 how-to → T4; §5 testing → T1 S3, T3, T5. Borrow-stays-manual (§1) = no code, documented in T4.
- **No placeholders:** all SQL/code/doc content is complete and literal.
- **Type consistency:** `loan_repayment` identical across migration, `_CAT_TO_SOURCE`, UPDATE, and the `category_code == "loan_repayment"` guard. `_normalize_lender` signature `(Optional[str]) -> Optional[str]` used once. The `notes`/lender key matches Phase 1's `v_loan_balance` grouping (the `notes` column).
