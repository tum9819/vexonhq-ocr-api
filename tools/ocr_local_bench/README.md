# ocr_local_bench — Makro local-OCR benchmark prototype (2026-09-10)

Offline benchmark that proved Tesseract (the engine already in the prod Dockerfile) + a rule-based
Makro extractor + an arithmetic gate reads Makro tax invoices with 0 external API calls.
Results + reproduce steps: `docs/superpowers/audits/2026-09-10-makro-local-ocr-benchmark.md`.

NOT production code. `makro_extract.py` is the candidate for `main._ocr_page` (behind
`LOCAL_OCR_ENABLED` / `HYBRID_OCR_ENABLED`) once TUM approves Phase 2.

Real invoice images and `expected.json` (exported from confirmed prod bills) are NEVER committed —
`fetch_expected.py` / `fetch_corpus.py` regenerate them locally from `.env` (read-only DB/storage).
