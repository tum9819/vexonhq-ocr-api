# WHT/P.N.D. decommission — backend contract

Date: 2026-09-01
Status: implemented and verified locally; not committed, pushed, or deployed

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
