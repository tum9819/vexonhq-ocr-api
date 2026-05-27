# P&L + Daybook Audit — Design Spec

**Date:** 2026-05-27 (Session ~43, Mara-W2)
**Status:** In progress — backend audit running
**Approach:** Money-first preventive audit, Audit-Then-Fix by severity

---

## Goal

ตรวจสูตรคำนวณ + display logic ของ P&L + Daybook (subsystem ที่กระทบเงิน/ตัดสินใจมากสุด) เพื่อหาบั๊ก/edge case ก่อนกระทบจริง

## Scope

### Backend (vexonhq-ocr-api)
- `pnl_routes.py` — daily/monthly P&L
- `yearly_routes.py` — yearly P&L
- `phase2_routes.py` — /phase2/pnl/*, /dashboard/overview
- `phase3_daybook_routes.py` — unified daybook (P&L source of truth)
- `phase10_narrative_routes.py` — AI narrative consumer

### Frontend (VEXONHQ)
- `/pnl/page.tsx` + `/pnl/compare/page.tsx`
- `/daybook/page.tsx`
- `/yearly/page.tsx`
- `/dashboard/page.tsx`
- `/scorecard/page.tsx` + `/revenue/page.tsx` + `/expense-trends/page.tsx`

### Out of scope (batch ต่อ ๆ ไป)
- POS analytics (`menu_routes.py`)
- Recipes / food cost
- Stock / inventory
- AR/AP / cashflow / budget
- Tax / bank statement

## Audit checklist

### Formula bugs
- Hallucinated SQL columns (อ้าง `net_price`, `b.status` ที่ไม่มีจริง — Session 18 pattern)
- Owner equity / transfer exclusion (`WHERE source NOT IN ('owner_capital','owner_advance','transfer_error')` — Session 6 incident)
- Date range / timezone (Asia/Bangkok vs UTC, off-by-one inclusive/exclusive)
- NULL handling (SUM without COALESCE, JOIN losing rows)
- Division by zero (margin % when revenue = 0)
- Aggregation correctness (DISTINCT vs COUNT, GROUP BY scope)
- Source-of-truth mismatch (P&L sum vs daybook sum should match)

### Frontend display
- Number formatting (THB, decimal places, thousands separator)
- Percent vs decimal (0.35 vs 35%)
- Loading / error / empty state
- FE-BE contract (FE expect fields BE doesn't return)
- Color coding (positive/negative/zero)

## Severity classification

- **CRITICAL** — เลขเงินผิด, กระทบ tax/decision/dashboard ทั้งหมด, หรือ endpoint return 500
- **MEDIUM** — Edge case, format ผิด, ดูแปลกแต่ไม่ทำให้ตัดสินใจผิด
- **LOW** — Display polish, label typo, alignment

## Workflow

1. **Round 1 — Audit (read-only):**
   - Backend audit via parallel subagent (~50 min)
   - Frontend audit (next session, ~30-45 min)
   - Findings written incrementally to `docs/superpowers/audits/2026-05-27-pnl-daybook-audit.md`

2. **TUM reviews report** → picks which to fix (can drop low-value items per lean-system bar)

3. **Round 2+ — Fix by severity batches** (per VEXONHQ 6-step):
   - Backup tag → edit all CRITICAL → test → commit handoff → TUM pushes
   - Repeat for MEDIUM, then LOW (if any survive lean-system filter)

## Time budget (time-boxed)

- This session: **1 hour** (TUM stop signal: "หยุด" / "พอ" / "พักก่อน")
- Output: backend audit report (5 files); frontend deferred to next session
- Resume: read existing audit report, continue from where stopped

## Output artifacts

- `docs/superpowers/specs/2026-05-27-pnl-daybook-audit-design.md` (this file)
- `docs/superpowers/audits/2026-05-27-pnl-daybook-audit.md` (findings, incremental)
