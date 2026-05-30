# TOMORROW.md — vexonhq-ocr-api backend

**Last updated**: 2026-05-30 (Session 47 — system-auditor round: export/analytics/WHT cash-basis fix)

> Frontend / cross-repo context → `C:\Users\rapee\VEXONHQ\docs\01_PROJECT\TOMORROW.md`
> Full re-audit detail → `docs/superpowers/audits/2026-05-29-reaudit-batch13-RUNBOOK.md`

---

## What's live + stable
- **Backend** `https://api.marastation.com` — FastAPI, Coolify auto-deploy ✅
- **P&L = CASH / bank-statement basis** — `vendor_bill` excluded from `v_daybook` (Branch 8 removed). AR sign bug fixed. Owner/inter-entity credits excluded from revenue. ✅
- **Session-47 audit FIXED a critical leak**: ~1.53M of categorised bank expense (beer/salary/food/utility) was tagged `source_type='bank_statement'` (excluded) → dashboard showed an impossible ~66% margin. Reclassified to counted sources by category; Nussara reimbursements counted; statement_rules + the food rules no longer emit `bank_statement` for expenses. **Real per-month margin now ~ -6%..+35% (avg ~15%).** ✅
- **Session-47 audit FIXED the accountant EXPORTS**: `export_routes` (daybook/category/pnd3/summary), `menu_routes` (/revenue, /scorecard KPI#5/#6/#8), `tax_routes` (WHT — was empty every month) all read RAW `v_daybook` → repointed to `v_daybook_pnl`. pnd3 payer = ร้านสถานีหม่าล่า, musician WHT = มาตรา 40(8). (Needs deploy + post-deploy verify.) ✅
- **Bank statements Jun 2025 – May 2026 reconciled 12/12 ZERO DRIFT** vs each statement's own `รวมฝาก/รวมถอน` checksum (line-based parser rewrite + balance dedup key). ✅
- **Slip-driven classification** — nightly `nightly_slip_reconcile` (02:00 BKK) pushes K+ slip memos → bank-row categories; manual `POST /slip/reconcile`; self-heals after re-import. ✅
- **food-cost% ~15%** (cash COGS categorised; rises toward ~30% as bank supplier purchases categorise via slips). Cash musician fees (76k) now feed ภ.ง.ด.3. ✅
- Tests / Uptime Robot / AI auto-diagnose / DO snapshot — unchanged from Session 42 ✅

---

## Next session

### A. [HIGH] After this push deploys — verify + run slip reconcile
1. Wait for Coolify (CPU<30%), then `GET /export/daybook?month=2026-04` must show profit +162k (not the old -675 loss).
2. The deploy registers `nightly_slip_reconcile` (auditor found it had never run). Run `POST /slip/reconcile` once to categorise the 26 waiting slips (rent 8k, salary 34k, beer ~28k → their own lines instead of the `other_expense` catch-all). Confirm `/cron/health` shows the job after 02:00 BKK 2026-05-31 (scheduled routine checks this at 02:30).

### B. [MED] B5 — ภ.ง.ด.3 / WHT (mostly resolved Session 47)
RESOLVED: all 3 generators (`/export/pnd3`, `/export/pnd3-annual`, `/tax/wht-export`) now agree — musician WHT = มาตรา 40(8) เงินได้อื่น @ 3%, payer = ร้านสถานีหม่าล่า (255/4 ถ.พุทธมณฑลสาย 2 เขตทวีวัฒนา). `/tax/wht-export` reads `v_daybook_pnl` (was reading bank_statement_entries → empty every month). REMAINING with accountant: confirm 3% is correct for live-music performers, and the per-payee เลขประจำตัวผู้เสียภาษี is still blank (กรอกเอง before filing).

### B. [MED] Let food-cost complete via slips
Bank supplier purchases (เบียร์/เนื้อ) sit in `other_expense` until a slip memo categorises them. As TUM backfills slips via LINE, the nightly reconcile lifts food-cost% toward ~30%. Seeded memo rules: ค่าเนื้อ→raw_meat, ค่าเหล้า→raw_beverage, etc. (add more in `statement_rules` as memos require).

### C. [MED] B8 / B9 / B13
- B8 Lineman 32.1% commission is a hardcoded estimate (no actual payout column).
- B9 delivery commission never shown as a cost line.
- B13 `/pos/food-cost` recipe-estimate vs FoodStory actual cost reconcile.

### D. [LOW] robustness
- `food_cost` query hardcodes 6 COGS codes — could sum by `parent_code='food_cost'` so any new sub-code counts automatically (would also catch `food_raw`).
- slip reconcile `_CAT_TO_SOURCE` doesn't list raw_meat/raw_veggies/etc. (defaults to `other_expense` source — harmless, both counted).

---

## Monitoring quick-ref
| URL | Purpose |
|-----|---------|
| `/health/deep` | Postgres + Supabase probe |
| `/cron/health` | Cron job status — confirm `nightly_slip_reconcile` `run_count ≥ 1` after its first 02:00 run |

## After ANY KBank statement (re-)import
```powershell
python scripts/verify_statement_parse.py "<each KBank PDF>"   # must print PASS (zero drift)
```
Then the nightly job (or `POST /slip/reconcile`) re-matches slips + pushes memo categories. A re-import orphans slips (FK SET NULL) but the reconcile self-heals them.

## Backups
- `bank_statement_entries_bak_20260530` (1033 rows) — pre-reimport snapshot, drop when confident.
- Backup tags pushed before each Session-46 commit (`backup-pre-*-2026-05-30`).
