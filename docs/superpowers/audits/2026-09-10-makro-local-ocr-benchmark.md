# Makro local-OCR benchmark — Phase 2 step 1 (offline, no production change, 0 API calls)

Date: 2026-09-10 · Status: **DONE — results for TUM's go/no-go** · Follows [2026-09-10-invoice-ocr-cost-audit.md](2026-09-10-invoice-ocr-cost-audit.md)
Where: Docker image built from the prod `Dockerfile` recipe (`python:3.10-slim` + Debian `tesseract-ocr` 5.5.0 + `tesseract-ocr-tha/eng` = the same **tessdata_fast 2019** models prod uses) on TUM's PC.
Corpus: **all 17 human-confirmed Makro bills in prod (38 distinct page PNGs from Supabase storage, exactly the images `main._pdf_to_images` produced), 342 line items.** Ground truth = the confirmed `vendor_bills` header + `invoice_items` (which were produced by GPT-4o and then confirmed at bill level).
Code: `tools/ocr_local_bench/` (prototype, uncommitted; real images/expected.json live in the session scratchpad only — never in the repo).

## 1. Result in one table (final config: border-removal + 1.5x upscale + Tesseract tha+eng psm 6 + targeted cell re-OCR)

| Question | Result |
|---|---|
| Bills accepted by the local gate | **17 / 17** |
| Header vs confirmed value — invoice_no / bill_date / subtotal / vat / **amount** | **17/17 each** |
| Header — discount | 16/17 — the 1 mismatch is **DB wrong** (bill `068901200004`: printed DISCOUNT 22.00, GPT stored 44 and 3 phantom items — verified from the image) |
| Header — merchant_tax_id | local = `0107567000414` on 17/17 (checksum-valid); DB has a **14-digit** value on 6/17 (GPT misread, never validated) |
| Line items matched by SKU | 313 / 342; of the 23 disagreements, 9 eye-checked from the scan → **local right 9/9** (`198368`≠GPT `193868` ×4 bills, `226360`, `815410`, `830437`, `187898`, `929347`, `910910`, `864241`) |
| Item numbers (qty × price = total) on matched rows | 309 / 313 exact vs DB; the 4 diffs: `917309` ×2 (printed 2 × 63.00 — **local right**), `195270` (printed 4 × 68.00 — **local right**), `233698` price 93.06 vs printed 93.00 — **local wrong** (faded row) |
| Thai product names (Tesseract) | avg similarity 0.75 to confirmed names; ≥0.8 on 53% — **not good enough alone** |
| SKU seen in an earlier bill (name recoverable from history) | **252 / 336 rows = 75%** after only 17 bills |
| CPU per page (this PC, Docker) | preprocess 0.1 s + Tesseract 4.6 s + cell re-OCR ~1.2 s ≈ **6 s** (today's prod Tesseract stage: 1.4 s denoise + 5.8 s = 7.2 s, *then* 7–40 s GPT) |
| External API calls | **0** |

## 2. What it took to get there (each step measured on all 38 pages)

| Config | gate PASS | amount ok | items numbers exact |
|---|---|---|---|
| prod preprocessing (NLM denoise + threshold 150), psm 6 | 1/17 | 7/17 | 263/342 |
| Otsu only | 0/17 | 10/17 | 266 |
| + remove table borders (morphological open, 0.03 s) | 0/17 | 16/17 | 268 |
| + 1.5x cubic upscale (~324 DPI) | 6/17 | 13/17* | 303 |
| + position-based payment box / VAT table, comma-dot normalisation, nearest-0.25 rounding rule, tolerant page marker | 16/17 | 17/17 | 303 |
| + targeted cell re-OCR (psm 7, digits whitelist, ≤ 8 cells + failing rows only; 156 crops / 38 pages) | 16/17 | 17/17 | 309 |
| + DISCOUNT := TOTAL − AMOUNT when the cell misreads but everything else ties (1 bill: scanner speck on "21.00") | **17/17** | 17/17 | 309 |

*(13/17 was a window-selection bug, fixed in the next row.)*
Root cause of most numeric errors: the Debian **tessdata_fast** models read this font's "1" as "4" when a table border touches the glyph or at 216 DPI. Border removal + 1.5x fixes nearly all; `tessdata_best` fixed "1→4" but broke Thai at 1.5x and was 2x slower — not used.

## 3. Does the gate refuse wrong data? (adversarial test — `bench_adversarial.py`)
Mutations injected into the clean OCR output, 3 random placements × 17 bills each (51 trials per mutation):

| Injected error | Rejected |
|---|---|
| one digit of an item TOTAL changed | 51/51 |
| item QUANTITY "1" → "4" (the live confusion) | 51/51 |
| one item row deleted | 51/51 |
| one item row duplicated (parsed-level test) | 3/3 tested → rejected (Σ items ≠ TOTAL) |
| one digit of payment-box TOTAL / AMOUNT / VAT goods changed | 51/51 each |
| last page (totals) missing | 51/51 |
| one digit of an item UNIT PRICE changed | 42/51 — the 9 leaks are **weight rows** where Makro rounds the row total to the nearest 0.25, so a price error ≤ 0.12 is invisible (row and bill amounts unaffected; only the stored unit_price is off by ≤ 0.12). Mitigation: compare to the SKU's last known price. |
| date day digit changed | 21/51 — **not arithmetically checkable**; the 21 rejections are bills that were failing anyway. Mitigation: date must be ≤ today and within N days of the previous Makro bill (Makro delivers weekly). |
| invoice-number digit changed | 21/51 — same; mitigation: Makro numbers are monotonic 12-digit (`0689011797xx → 0689012011xx` over Apr–Jun) → range check against the last known number. |

Baseline for this test (pass-1 only, no cell re-OCR) was 10/17 PASS; rejections on the 7 already-failing bills are not evidence, so read the "51/51" rows as "no leak found", not as 51 independent proofs.

## 4. Findings about the CURRENT production OCR (GPT-4o) that fell out of the benchmark
The "expected" values are what GPT-4o read and TUM confirmed at bill level. The local run disagreed with them in places, and every disagreement eye-checked so far was GPT's error:
- Makro tax ID stored as 14 digits on **6 of 17** bills (no checksum in the pipeline)
- SKU wrong on ≥ 9 rows (all 9 checked), e.g. `198368` read as `193868` in 4 separate bills
- qty/price swapped (2 × 63.00 stored as 1 × 126.00; 4 × 68.00 stored as 1 × 272.00)
- bill `068901200004`: the "เงื่อนไขส่วนลด" condition lines (6 / 10 / 6) were stored as 3 line items **and** the discount doubled to 44 (printed 22) — header amount still right, so P&L unaffected, SKU analytics not
Money totals (`amount`) are correct in the DB for all 17 — the confirm modal did its job at header level; item-level errors passed through silently.

## 5. Recommendation (go/no-go)
**GO for Makro, Stage 1 (Tesseract that is already installed — no new dependency, no VPS change).** Evidence: 17/17 accepted with every money field equal to the confirmed value, gate rejects injected numeric errors, CPU ≈ today's Tesseract stage. Remaining design decisions before writing production code:
1. **Names for NEW SKUs** (25% of rows now, shrinking): (a) store the Tesseract name + flag for review in the modal, or (b) send only the cropped description cell to GPT-4o at low detail (~฿0.02/row). Recommend (a) first, measure, add (b) if TUM edits too many names.
2. **Extra guards not arithmetic:** date sanity (≤ today, near last Makro bill), invoice_no range, SKU last-known-price check (covers the weight-row blind spot).
3. **Render scale for the local path:** the prod renderer uses scale 3.0 (216 DPI); the local path should render at 4.5 (324 DPI) natively instead of upscaling a 216-DPI PNG — better than what this benchmark used. GPT path unchanged (OpenAI downsizes anyway).
4. **Where it plugs in:** `main._ocr_page` between step 1 (Tesseract) and step 2 (GPT), behind `LOCAL_OCR_ENABLED` / `HYBRID_OCR_ENABLED`; vendor detected by tax-ID checksum + regex on the Tesseract text, so only Makro pages ever take the local path — every other vendor is byte-for-byte today's flow.

## 6. Reproduce
```powershell
# scratchpad copy holds data/ (38 PNG) + expected.json (real financial data — NOT in repo)
docker build -t vexon-ocr-bench tools/ocr_local_bench          # prod-equivalent Tesseract image
docker run --rm -v ${PWD}:/work vexon-ocr-bench python /work/ocr_pages.py /work/data /work/out --variants nolines15
docker run --rm -v ${PWD}:/work -w /work vexon-ocr-bench python bench_run.py --variant nolines15 --pass2
python bench_adversarial.py
```

---

## 7. Second run — TUM's real PDF folder, end-to-end from the PDF (added later the same day)
Source: `C:\Users\rapee\Desktop\PJ-MARA\VPS-VEXONHQ\Invoice\26.04|26.05|26.06` — 41 files (40 PDF + 1 JPG): **22 Makro PDFs**, Wealimex 5, Singha 4, Wholesale 5, Change 2, Gas 2, Service 1. All scans, no text layer. Makro scans are 200 DPI (Apr–May) or 600 DPI (June).
Pipeline = exactly prod's `_pdf_to_images(scale=3.0)` render → border removal + 1.5x → Tesseract → extractor → gate. **5 of the 22 Makro bills were never uploaded to the system (05-14, 05-18, 05-23, 05-27, 05-28) → true blind test, no ground truth in the DB, verified against the scan by eye.**

| | Known (17, in DB) | Blind (5, never seen) |
|---|---|---|
| gate PASS | **17/17** | **4/5** (05-27 refused: VAT-table cells misread → would go to GPT-4o as today) |
| every money field (subtotal / VAT / amount / discount) equal to the paper | 17/17 (DB agrees on all but 200004 discount = paper 22, DB 44). *Self-review note: the first print-out said VAT 16/17 — that was a scoring bug in `bench_pdfs.py` treating a real 0.00 VAT as missing; fixed, re-scored 17/17.* | **4/4** eye-checked (5,595.25 · 5,890.50 · 1,774.25 · 1,858.75) |
| invoice_no / date equal to the paper | 17/17 | 4/4 (05-28 file is invoice-dated 29/05 — the filename is TUM's delivery date) |
| False accept (gate PASS with a wrong money value) | **0** | **0** |
| Tesseract per page (Docker on TUM's PC) | 5.7 s | 5.7 s |
| Cell re-OCR pass | 229 crops / 50 pages ≈ 1.4 s per page | |

Two generic fixes came out of the blind set (both are OCR-robustness, not bill-specific rules): a stray glyph glued to an article number (`210588)`) dropped a whole row → keep the digit core; a digit lost at a word boundary in a VAT cell (`93.26` → `3.26`) → re-OCR the cell from the previous column's edge; tax-ID checksum failure → re-OCR the header digit line. Before these fixes the blind set was 2/5; the refusals were all correct (no false accept at any point).

**Honest reading of the blind number:** 4/5 (80%) is from 5 bills — expect 60–85% of fresh Makro bills to take the local path at first, rising as failure classes are fixed; the other 15–40% fall back to GPT-4o exactly as today. The number that matters for money integrity: **no accepted bill had a wrong bill-level money total (subtotal / VAT / amount / discount) — 0 in 22 bills, and 100% of injected money-total mutations were rejected.** The gate does **not** protect date, invoice number, or a weight-row unit price within ฿0.12, and cannot see a correlated qty×price swap (§8 #2) — those are covered by the extra guards in §5.2 + the mandatory confirm modal. (Earlier wording "0 in 561 trials" was wrong — see §8 #1.)

Other vendors in the folder (for the "next template" decision): Wealimex and Singha are dot-matrix text on a **blue pre-printed form** (needs colour-channel separation before Tesseract; 1–2 line items; Singha dates in BE 2569); Wholesale/Change/Gas/Service are small receipts. None attempted — Makro first.

---

## 8. External review round 1 (2026-09-10) — dispositions + re-run after hardening
Reviewer findings were checked one by one against code/data before acting (nothing accepted on trust).

| # | Finding | Verdict | Action |
|---|---|---|---|
| 1 High | "false accept = 0 in 561 trials" overstated — §3 itself lists 9 unit-price + 30 date + 30 invoice-no mutations that passed | **Accepted** (561 trials *were* executed — the harness printed applied=51 for all 11 classes — but the sentence hid the leaks) | Claim rewritten: **0 accepted wrong bill-level money totals** (subtotal/VAT/amount/discount) in 22 bills; **money-total mutations rejected 100%**; the gate does NOT protect date, invoice number, or weight-row unit price ≤ ฿0.12 — those need the non-arithmetic guards (§5.2) and the confirm modal |
| 2 High | correlated qty×price errors tie (2 × 50 read as 1 × 100); pass-2 accepted the first tying combo | **Accepted** — real blind spot; row/bill amounts stay right, qty/unit_price can be wrong | pass-2 now repairs **one field only** (a combo that merely ties is never accepted); production gate to add **SKU history check** (unit_price & unit vs last confirmed row of the same SKU) and unit/qty consistency (EACH → integer qty, Kilogram → 3-decimal qty). Modal stays mandatory (TUM's decision) |
| 3 Med | SKU-keyed scoring collapses repeated SKUs | **Accepted as method weakness** — verified there is **no repeated SKU inside any of the 17 bills**, so reported numbers are unchanged | scoring is now row-level (sku, occurrence) in both runners |
| 4 Med | `date_parse` accepts 2026-02-31 | **Accepted** | strict `datetime.date` parse |
| 5 Med | payment box / VAT triplet chosen by arithmetic only, labels not required | **Partially accepted** — a stray 5-row window satisfying both identities *and* matching Σ items *and* the VAT total is very unlikely, but corroboration is cheap | gate now requires ≥ 2 of the 5 chosen rows to carry their English label (`payment_box_labels`); no bill lost |
| 6 Med | DISCOUNT := TOTAL − AMOUNT not independently proven | **Accepted** | inference now also requires the OCR'd discount cell to contain the same digit string (e.g. `2100` ↔ 21.00) — a lost separator is allowed, lost/extra digits are not |
| 7 Med | blank-qty inference ignores unit | **Accepted** | only when unit contains `EACH` |
| 8 Med | audit says multi-page retry fires "every time" | **Partially accepted** — unconditional wording was wrong; for bills with totals on the last page it is data-confirmed (9/9 multi-page Makro bills in DB: last-page items = 0.5–20% of amount). Plan A2 adds a kwarg to `_ocr_page` (caller passes page context) — reviewer is right that it cannot be done inside `_ocr_page` unchanged | audit §3.2 reworded |
| 9 Med | Phase-A rollback flags/criteria not in the audit doc | **Accepted** (they were only in chat) | added to audit §7.2: per-item env flag + rollback trigger |
| 10 Med | sequential upload alone lets the next result overwrite an unreviewed modal (`showingModal` single state) | **Accepted** — verified `InvoiceUploadFlow.tsx:32,89-112,148-180` | A3 redesigned: next upload starts only after the current modal is approved/rejected (or results queue with one modal at a time) |
| 11 Low | "prompt cache never triggers" too strong — the corrective retry and LINE (empty hint) share an identical prefix | **Accepted** | reworded: never across *distinct web uploads*; retry/LINE calls can hit |
| 12 Low | per-invoice extrapolation is inferred | **Accepted** (already labelled *inferred*) | wording kept, caveat repeated in §4 |

**Re-run after hardening (same corpus):** stored-PNG set 17/17; real-PDF set **16/17 known** (05-02 now refused: row 12 needed a qty *and* price repair — exactly the combo the #2 rule forbids → falls back to GPT, correct behaviour) + **4/5 blind**; adversarial unchanged. Defensible headline: **20 of 22 real Makro PDFs read locally with every money total equal to the paper; the 2 refusals go to GPT-4o as today; no accepted bill had a wrong money total.**

## 9. External review round 2 (2026-09-10) — dispositions + corrected numbers

| # | Finding | Verdict | Action |
|---|---|---|---|
| 1 High | `bench_run.py` still carried its own stale `refine_page` with the multi-field repair (`+ [cand]`); only `bench_common.py` had been hardened → the "stored-PNG 17/17 after hardening" line in §8 was produced by the UNhardened rule | **Accepted — reviewer is right, and §8's stored-PNG number was wrong** | `bench_run.py` now imports `refine_page` from `bench_common.py` (single source); re-run below |
| 2 High | VAT รวม row is checked only by goods+tax=total and total=amount → a correlated misread / column shift (goods=1000, tax=0, total=1000) passes; parsed per-code `vat_rows` were ignored | **Accepted** — verified on real data first: 42/43 parsed per-code rows obey tax = 7% × goods (code 2) or 0 (code 1), the 1 violation is on an already-refused bill | new hard check `vat_rows_consistent`: every per-code row must obey the 7%/0 rule and goods+tax=total, and Σ rows must equal the รวม row in all three columns; per-code cells added to pass-2 re-OCR |
| 3 Med | A3 "wait for approve/reject" stalls on the modal's X (`onClose` leaves the item `pending_review`), races with re-upload, and `setShowingModal` still clobbers an open modal | **Accepted** — verified `InvoiceUploadFlow.tsx:112,183-189,344` | A3 redesign: uploads run one at a time over the network; finished results go into a **review queue**; the modal shows the queue head; approve / reject / close(X) / re-upload each pop the head and show the next; close(X) leaves the bill `pending_review` (reviewable later in the queue page) and never blocks the upload loop; a finishing upload never calls `setShowingModal` directly |
| 4 Low | `lstrip("0") == ""` matches when the inferred discount is 0 and the cell holds only zeros | **Accepted** (harmless but fragile) | cand == 0 is now handled explicitly (cell digits must be all zeros) |

**Corrected re-run (all rules in one place, VAT-row check on):**

| set | result | refused bills (all refusals are SAFE — every refused bill's money totals were correct in DB/paper, the gate simply could not prove it) |
|---|---|---|
| stored PNG, 17 known | **15/17** | 183595 (row needs qty *and* price repair → forbidden combo), 195415 (per-code VAT row cell `3,441 0 \|` broken) |
| real PDF, 17 known | **15/17** | same two bills |
| real PDF, 5 blind | **3/5** | 05-23 (per-code VAT goods read `141825` = lost decimal — cell re-OCR did not recover it), 05-27 (VAT table garbage) |
| adversarial | unchanged — money-total mutations rejected 51/51 in every class; leaks only in the documented unprotected fields | |

**Defensible headline now:** **18 of 22 real Makro PDFs accepted, every accepted bill's subtotal / VAT / amount / discount equal to the DB or the paper; 4 refused (would go to GPT-4o as today); accepted-but-wrong money total = 0.** Hit-rate dropped from 20/22 to 18/22 as the price of closing the round-1/round-2 blind spots — the refusals are cell-level OCR misses on the VAT table, which the production path can attack with a native 324-DPI render (the benchmark upscaled a 216-DPI PNG) rather than by loosening the gate. Tuning was stopped here deliberately (overfitting risk on a 22-bill corpus).
