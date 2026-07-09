# Historical Statement Cleanup/Reclass Progress

Date: 2026-07-07

Scope: remaining historical months before May 2026. Work month by month, reviewed IDs only.

Hard rules:
- Do not COMMIT production DB without TUM explicit Confirm.
- Do not push git without TUM explicit Confirm.
- Keep SQL drafts rollback-safe on disk.
- Use local evidence under `C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Report POS`.

Completed:
- 2026-06 reclass committed and verified.
- 2026-05 duplicate cleanup committed and verified.
- 2026-05 reclass committed and verified.
- 2026-04 reclass committed and verified.
- 2026-03 reclass committed and verified.
- 2026-02 reclass committed and verified.
- 2026-01 reclass committed and verified.
- 2025-12 reclass committed and verified.
- 2025-11 verified-subset reclass committed and verified.
- 2025-11 early-Grab manual reclass committed and verified.

Queue:
- No remaining month in this batch is queued for COMMIT.

Committed batch evidence:
- Local execution/log folder: `C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Audit Reports\historical_reclass_commit_20260707_231950`
- Draft SQL files remain rollback-safe on disk (`ROLLBACK;` active, `COMMIT;` commented).
- Persistent backup tables:
  - `audit.bank_statement_reclass_backup_20260707_april` (`91` rows / `207,763.87`)
  - `audit.bank_statement_reclass_backup_20260707_march` (`105` rows / `212,939.86`)
  - `audit.bank_statement_reclass_backup_20260707_february` (`98` rows / `179,513.36`)
  - `audit.bank_statement_reclass_backup_20260707_january` (`102` rows / `197,126.35`)
  - `audit.bank_statement_reclass_backup_20260707_december` (`102` rows / `181,553.15`)
  - `audit.bank_statement_reclass_backup_20260707_november_verified_subset` (`96` rows / `253,502.11`)

Pending evidence / TUM decision:
- None for this historical statement reclass batch. TUM confirmed on 2026-07-09 that there is no additional Grab export beyond the local 2025-11-17..2025-11-30 file, then explicitly confirmed `Confirm RUN NOVEMBER EARLY GRAB MANUAL COMMIT`. The separate manual-evidence draft `2026-07-09-november-early-grab-manual-reclass-draft.sql` was committed and verified: 11 rows / 3,168.81 now `grab_payout` / `delivery_grab` / `manual`, backup table `audit.bank_statement_reclass_backup_20260709_november_early_grab`.
