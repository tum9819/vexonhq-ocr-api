# REVIEW REQUEST — AUDIT ONLY (2026-10-04) — duplicate-bill guard (backend + frontend)

## Your role and hard rules
- You are an **independent auditor**. **Do NOT edit, patch, refactor, format, or "fix" any file. Do NOT commit, stage, stash, checkout, reset, install, or push.** Read-only.
- Report **only facts you verified in the code or in command output you actually ran**, with `file:line` citations for EVERY claim. If you cannot verify something, write `NOT VERIFIED` — do not guess, do not assume, do not infer behavior you did not read.
- A `PASS` without line-number evidence is invalid. Correctness is the only goal; 100% accuracy over speed.
- Do not run anything that calls OpenAI, LINE, Supabase, Coolify, or production. Offline reading + the local commands listed below only.
- **Pin check first:** confirm `git rev-parse --short HEAD` is `cab8fde` in Repo A and `6c6599f` in Repo B. If not, STOP and report the mismatch.

## Scope (all local, NOT pushed)

### Repo A — `C:\Users\rapee\vexonhq-ocr-api` (FastAPI backend), range `origin/main..main`
- `8785bc3`, `25f1fcb`, `6b5c7ae` — docs only. Skip.
- `0a64e6a` — `main.py` `invoice_suspected_duplicates()` (`GET /invoice/duplicates`, starts ~L1096): the grouping key changed from (exact lowercase vendor name, amount, bill_date) to (amount, bill_date) for dated bills; undated bills keep (amount, vendor name); bills without a vendor name can now be grouped when they have a date. `vendor_name` in each group is the distinct names joined with " / " (or `null`). Response shape unchanged. New test `tests/test_invoice_duplicates.py`.
- `cab8fde` — `line_bot_routes.py`: new `_process_invoice_from_line(image_bytes)` (~L267) hashes the image (SHA-256), calls `main._find_uploaded_file` (`main.py` ~L743, the web upload's existing check), returns an "already sent" reply with the existing bill id on a match (no OCR, no save), otherwise OCRs and calls `_save_invoice_from_line(..., file_sha256=...)` (~L234) which passes the hash to `main._save_invoice` (stored on the attachment row only when the storage upload succeeded, `main.py` ~L3875-3886, `file_sha256` at L3885). The webhook image branch (~L2153) now calls `_process_invoice_from_line`. New test `tests/test_line_invoice_dedup.py`.

### Repo B — `C:\Users\rapee\VEXONHQ` (Next.js), range `origin/main..main`
- `784dc7d`, `3a50dba`, `7eb7a66` — docs only. Skip.
- `6c6599f` — `components/invoice/InvoiceQueueGrid.tsx` ~L388-392: the duplicate banner's explanatory text only (no logic change).

Background (author's evidence; DB parts you cannot verify offline → NOT VERIFIED):
- 2026-10-03: two pending LINE bills, both 2,082.00, same date, vendor names `CP AXTRA PUBLIC COMPANY LIMITED (HEAD)` vs `CP AXTRA ศาลายามาร์เก็ต (3203)`, different image files (1,230,056 vs 360,398 bytes). Old key → no banner; new key → 1 group.
- Simulation of the new key on all 118 dated bills: 8 groups; 7 have differing vendor names and look like true duplicates (5 historical pairs where one was confirmed and the other rejected, the 2026-10-03 pair, and one pair where both were confirmed).

## What to audit — answer each with evidence

**A. Duplicates endpoint (`0a64e6a`)**
1. Trace every row through the new loop: can any combination of `amount` (None / "0" / negative / non-numeric string / numeric string) and `bill_date` (None / "") and `vendor_name` (None / "" / whitespace) raise, or be grouped wrongly? Can a dated key ever collide with an undated key?
2. Callers/consumers of `/invoice/duplicates` in both repos (`lib/invoice-api.ts`, `components/invoice/InvoiceQueueGrid.tsx`): does anything rely on `vendor_name` being a single real vendor, or on all bills in a group sharing a vendor (e.g. a React key, a link, a filter)? Is a `null` vendor_name handled?
3. Is the new behavior still advisory only (no endpoint rejects, merges, or deletes a bill because of it)?

**B. LINE dedup (`cab8fde`)**
4. Does `_process_invoice_from_line` preserve the old success behavior exactly (same OCR call, same save arguments plus the hash, same reply text via `_format_ocr_reply`)? Any exception path that changed (compare with the webhook's surrounding `try/except` ~L2125-2160)?
5. `_find_uploaded_file` semantics reused from the web path: fail-open on lookup errors? What happens when the matching bill was rejected, deleted (orphan attachment), or has fewer attachments than `expected_pages`? Confirm with line numbers.
6. Is the hash stored for LINE bills under the same `parent_type`/column the lookup queries? Any path where a LINE bill is saved without the hash (e.g. storage upload failure) — and is that a regression versus before (before: no hash at all)?
7. Thread/order safety: the webhook already sent the 30-second reply before this code runs — confirm nothing here needs the reply token.

**C. Tests and gates**
8. Do the two new test files exercise the real functions (not mocks of the code under test)? Would each key assertion fail on `origin/main`? (Author: the duplicates regression test fails at `tests/test_invoice_duplicates.py:66` and the LINE tests fail with `AttributeError`/`TypeError` on the old code.)
9. Frontend text: does the new banner sentence describe the backend rule accurately?

## Expected local evidence (offline)
- Repo A: `cd C:\Users\rapee\vexonhq-ocr-api; .\.venv\Scripts\python.exe -m pytest tests/test_invoice_duplicates.py tests/test_line_invoice_dedup.py tests/test_line_image_classifier.py -q` → 12 passed. Pre-push hook equivalent: `.\.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_smoke.py --ignore=tests/test_backup_prune.py` → 669 passed / 26 skipped (note: this includes `tests/test_workflow.py`, which makes live HTTP calls to production — skip it if your rules forbid network; then expect one fewer collected file).
- Repo B: `npm run lint` → 0 errors / 14 pre-existing warnings; `npx tsc --noEmit` → 0; `npm run build` → 70/70 + `ƒ Proxy (Middleware)`. Do NOT run `npm install`.

## Output format
Line 1: the two pinned SHAs you saw. Then for each item 1–9: `PASS` / `FAIL` / `NOT VERIFIED`, with `file:line` evidence (and the exact command + output where you ran one); for FAIL, the concrete failure scenario. Then any additional defects ranked by severity with `file:line`. No rewrite suggestions; no style comments; no edits.
