# Makro hybrid OCR — Phase 0 (study + proposal, no code changed)

Date: 2026-10-04. Status: **TUM answered (§7). Next: duplicate guard task, then Phase 1.**
Builds on: `docs/superpowers/audits/2026-09-10-invoice-ocr-cost-audit.md`,
`docs/superpowers/audits/2026-09-10-makro-local-ocr-benchmark.md`,
`tools/ocr_local_bench/makro_extract.py`. TUM decisions already on record
(2026-09-10 / 2026-10-01): Makro first; web PDF upload; Tesseract only (no
PaddleOCR/torch on this VPS); new SKU names = Tesseract text + "check name"
flag; confirm modal stays mandatory; free external tiers rejected (they use
submitted documents to improve their models).

## 1. What the numbers say (re-checked 2026-10-04)

- Makro volume in `vendor_bills`: Apr 5, May 10, Jun 7 bills (avg ฿3–4k);
  none Jul–Sep (uploads paused by TUM); 2 new on 2026-10-03 (see §4).
- GPT-4o cost ≈ ฿1 per bill → Makro going local saves roughly **฿5–10 per
  month**. The reason to build this is **accuracy and determinism**, not
  money: production GPT-4o misread Makro tax IDs on 6/17 bills and SKUs on
  ≥9 rows (benchmark §4), silently.
- Benchmark (22 real PDFs): 18 accepted by the arithmetic gate, every
  accepted bill's subtotal/VAT/amount/discount equal to the paper;
  accepted-but-wrong money total = 0; 4 refused → GPT-4o as today.

## 2. Design corrections found in this study

1. **The gate is bill-level, so the hook must be bill-level.** The 09-10
   docs say "plug into `main._ocr_page`" — but `makro_extract.assemble_bill`
   needs every page (Σ items across pages = TOTAL, VAT table on the last
   page), while `_ocr_page` sees one page and runs pages in parallel. The
   local path belongs in `_process_upload` (`main.py:815`): after the PDF is
   rendered, before the parallel GPT stage. `_ocr_page` stays untouched.
2. **Shadow must not slow the upload.** Uploads already flirt with the
   Cloudflare 100 s edge timeout (524) on multi-page PDFs. Shadow mode runs
   the local extractor in a background thread *after* the bill is saved and
   only writes a log row; the HTTP response does not wait for it.
3. **LINE is a different path.** LINE images go straight to
   `_run_gpt_vision` (`line_bot_routes.py:227`) with no Tesseract hint and no
   file hash. Makro via LINE = phone photos, which the benchmark never
   tested (it used 600-DPI scans). Hybrid stays web-PDF only.
4. **Render scale:** production renders PDFs at scale 3.0 (216 DPI); the
   local path should render its own copy at 4.5 (324 DPI) — the 4 benchmark
   refusals were VAT-cell misreads at low resolution (benchmark §9).

## 3. Proposed phases (each: backup tag → edit → self-review → 2 external reviews → TUM confirm → push → verify → record)

| Phase | What | Production effect | Rollback |
|---|---|---|---|
| **1. Port + log, flag OFF** | Move `makro_extract` into a backend module as pure functions + unit tests on synthetic fixtures (never real bills in the repo); additive table `ocr_runs` (migration); background shadow runner behind `LOCAL_OCR_ENABLED` (default 0) | **None** (flag off = today's code path byte-for-byte) | revert commit; `DROP TABLE ocr_runs` |
| **2. Shadow ON** | `LOCAL_OCR_ENABLED=1`: each Makro web-PDF upload logs local result vs GPT result; when TUM confirms the bill, compare against the confirmed values. Also re-run the ported code offline on the 22-PDF corpus (parity with the benchmark) | Background CPU per Makro upload only; bills/UI unchanged | env back to 0 + restart (no redeploy) |
| **3. Hybrid ON (Makro only)** | `HYBRID_OCR_ENABLED=1`: gate-passed Makro bills skip GPT; extra guards outside arithmetic (date ≤ today and near the previous Makro bill, invoice_no 12 digits/increasing, SKU price vs last known price, EACH qty integer); modal still mandatory | Makro bills read locally; everything else unchanged | env back to 0 + restart |
| **4. Review** | Measure acceptance rate, edits in the modal, errors; decide on other vendors (Singha/Wealimex are blue dot-matrix — harder) | — | — |

Go/no-go into Phase 3 (proposed, TUM to approve): ≥ 15 Makro bills seen in
shadow, **0 accepted bills whose money totals differ from the confirmed
values**, acceptance ≥ 60%.

## 4. Live evidence found today (not part of the build, needs TUM)

Two Makro bills arrived on 2026-10-03 via LINE (JPEG, 20:12 and 20:21 BKK),
both `pending`, both ฿2,082, same date, different invoice numbers
(`016276201221BPM06236` vs `E032050002A2105`), 1 line item each, no file
hash. The first has tax ID `000002206541047` (15 digits — invalid); the
second has Makro's real ID `0107567000414` (checksum OK). Most likely the
same receipt sent twice (or receipt + tax invoice of one purchase). If both
are confirmed, the same purchase counts twice. This is exactly the
LINE-no-hash duplicate (cost audit §3.6) plus the silent tax-ID misread the
hybrid gate is designed to catch.

## 5. Risks and how the plan contains them

- Wrong money from local OCR → bill-level arithmetic gate + extra guards +
  mandatory modal; shadow first; per-vendor enable.
- CPU on the shared 2 vCPU / 4 GB VPS → Makro-only, background thread,
  Tesseract already installed (no new dependency).
- Cloudflare 524 → shadow is off the request path; hybrid replaces a slower
  GPT call, so it should shorten requests (to be measured in Phase 2).
- Prototype drift → port by importing one module (lesson from benchmark
  review round 2: a stale copy of `refine_page` produced wrong numbers).

## 6. Questions for TUM (answers decide Phase 1 scope)

1. Hybrid for **web PDF uploads only** (LINE photos stay on GPT-4o)?
2. Will you resume uploading bills now (Jul–Sep backlog included)? Shadow
   needs real Makro uploads to measure; at 5–10 bills/month, 15 bills takes
   2–3 months without the backlog.
3. Approve the Phase-3 go/no-go numbers in §3?
4. Separate small task: give LINE images a file hash so a re-sent photo
   cannot create a second bill (evidence: §4)?
5. The two 2026-10-03 bills — same purchase? (keep the one with the valid
   tax ID, reject the other in the review queue — your call, not changed by
   Claude).

## 7. TUM's decisions (2026-10-04)

1. Hybrid = **web PDF uploads only**; LINE photos stay on GPT-4o.
2. **Resume uploading bills now, backlog included** (feeds shadow).
3. Go/no-go into Phase 3: **≥ 50 Makro bills in shadow, acceptance ≥ 70%,
   0 accepted bills with a money total different from the confirmed value.**
4. Duplicate guard before Phase 1 — **both**: (a) widen the existing
   suspected-duplicates banner (`GET /invoice/duplicates`, `main.py:1096`) to
   match same amount + same bill_date even when vendor names differ, and (b)
   file-hash LINE invoice images.
   Why (a): checked in storage — the two 2026-10-03 images are different
   files (1,230,056 vs 360,398 bytes, different eTags), so a file hash alone
   could not have caught that pair; and the vendor names differ
   (`CP AXTRA PUBLIC COMPANY LIMITED (HEAD)` vs `CP AXTRA ศาลายามาร์เก็ต
   (3203)` — likely the full tax invoice + the store receipt of one
   purchase), so today's banner (exact vendor name + amount + date) stays
   silent. (b) still blocks an exact re-send of the same photo.
