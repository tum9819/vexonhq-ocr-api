# WHT/P.N.D. decommission — backend contract

Date: 2026-09-01
Status: shipped and production-verified on 2026-09-02

## Confirmed operational fact

TUM confirmed that musician fees and rent are paid in full, rent is THB 8,000,
VEXONHQ has no field proving an actual withholding event or remittance, and the
restaurant has not remitted WHT through this workflow. Expense categories are
accounting labels, not tax-event evidence. VAT is outside this decommission.

## Contract

- `GET /tax/wht-summary`, `GET /tax/wht-export`, `GET /export/pnd3`, and
  `GET /export/pnd3-annual` return HTTP 410 before DB or workbook work.
- `GET /export/zip-bundle` contains exactly `category_summary_YYYY-MM.xlsx`
  and `daybook_YYYY-MM.xlsx`.
- `GET /export/summary` keeps `pnd3` for compatibility but returns
  `{available:false,status:"decommissioned",rows:0,total_withholding:0}`;
  `zip_bundle.files` is 2.
- Readiness schema remains 1. `pnd3` and `pnd53` remain empty packages with
  `availability:"decommissioned"`. The fingerprint version is
  `phase-a-v2-wht-decommissioned` and excludes category-derived WHT inputs.
- Audit schema v2 keeps nullable `wht` but returns `null` for every voucher.
- P&L, VAT, categories, history, and stored amounts are unchanged. No DB
  migration or production data mutation is required.

## Rollback and verification

- Rollback tag: `backup-pre-wht-decommission-2026-09-01` at `b3c146a`.
- Affected suite: 92 passed.
- Full `verify.ps1` after external-review cleanup: 620 passed, 2 skipped,
  1 pre-existing warning.
- Deployment order after explicit TUM approval: backend first, verify contracts
  and health/settled CPU, then frontend.

## Production closure

- Backend commit `76160f2` and frontend commit `2298742` are on `origin/main`.
- The initial backend webhook rollout later entered `exited`/503. After TUM
  manually redeployed the same commit, Coolify reported Success/Running and
  live smoke passed 71/71; all retired-route and compatibility contracts above
  matched production.
- Frontend Coolify deployment `an2tkinii0kfxdpezp8bcois` succeeded from
  07:50:47 to 07:58:51 UTC (8m04s) and remained Running on exact commit
  `2298742`.
- Authenticated production checks showed no WHT/P.N.D. controls or printed WHT
  row, `/tax` redirected to `/export`, and the real August ZIP contained only
  Category Summary + Daybook. Those two XLSX files and the annual P&L XLSX had
  valid structures and no WHT/withholding/P.N.D. XML text.
- `/health/deep` remained healthy with PostgreSQL, Supabase, scheduler, RAM,
  and disk checks healthy; CPU reached 0% after the shared-VPS build.
