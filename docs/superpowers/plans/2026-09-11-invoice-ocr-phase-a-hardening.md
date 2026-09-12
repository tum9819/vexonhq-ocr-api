# Invoice OCR Phase-A Hardening Implementation Plan

> **For agentic workers:** Execute inline with test-first checkpoints. Do not call OpenAI, commit, or push.

**Goal:** Correct the benchmark claims and remove the verified avoidable OCR-call risks without enabling local OCR in production.

**Architecture:** Keep the current upload/save API unchanged. Harden the offline Makro gate and scorer, make LINE classifier image detail explicitly low, and suppress page-level corrective retries only for multi-page PDFs. Preserve the existing single-image behavior and the frontend queue already shipped at `32eb068`.

**Tech Stack:** Python 3.10, pytest, FastAPI backend, offline Tesseract benchmark, Next.js frontend (verification only).

**Spec:** `docs/superpowers/audits/2026-09-10-invoice-ocr-cost-audit.md` plus the 2026-09-11 external review findings.

## Global Constraints

- No OpenAI calls during implementation or verification.
- No new dependency, database migration, API response change, commit, or push.
- Local OCR remains disabled/unwired in production.
- Each production behavior change gets a regression test that fails before implementation.

---

### Task 1: Correct and harden the offline benchmark

**Files:**
- Modify: `tools/ocr_local_bench/makro_extract.py`
- Modify: `tools/ocr_local_bench/bench_adversarial.py`
- Modify: `tools/ocr_local_bench/bench_run.py`
- Modify: `tools/ocr_local_bench/bench_pdfs.py`
- Create: `tests/test_makro_local_gate.py`

- [x] Confirm prior external-review hardening already covers strict dates, EACH-only inference, single-field pass-2 repair, VAT rows, payment labels, and repeated-SKU scoring.
- [x] Preserve the defensible benchmark headline and documented correlated-error limitation.

### Task 2: Make LINE classifier detail explicit and reversible

**Files:**
- Modify: `line_bot_routes.py`
- Create: `tests/test_line_image_classifier.py`

- [x] Add failing tests proving low detail is the default and high detail is the rollback value.
- [x] Implement `LINE_IMAGE_CLASSIFY_DETAIL`, default `low`, without making a real API call.
- [x] Run the focused test.

### Task 3: Stop invalid multi-page page-level retries

**Files:**
- Modify: `main.py`
- Modify: `tests/test_ocr_parallel_pages.py`

- [x] Add failing tests proving multi-page PDF calls suppress page-level corrective retry while single images retain it.
- [x] Pass retry permission into `_ocr_page` without changing the public upload response.
- [x] Add `OCR_PAGE_RETRY_MULTIPAGE=1` rollback and run focused regressions.

### Task 4: Correct audit language and rollback instructions

**Files:**
- Modify: `docs/superpowers/audits/2026-09-10-invoice-ocr-cost-audit.md`
- Modify: `docs/superpowers/audits/2026-09-10-makro-local-ocr-benchmark.md`

- [x] Confirm prior external-review sections replaced the unconditional/cache/561 wording and document correlated/scorer limitations.
- [x] Record Phase-A implementation status and per-change rollback controls.

### Task 5: Verification and handoff

- [x] Run focused pytest files, then `verify.ps1`.
- [x] Run frontend lint, TypeScript, and build against the already-shipped queue changes.
- [x] Inspect diffs and prepare the Antigravity review message.
