# OCR Golden-Set Accuracy Harness

Audit Testing-phase remediation (2026-05-31). OCR is the money-data gateway but
its accuracy was never measured. This harness makes accuracy a **number you can
track**, including before/after a model change.

## What's here

| File | Purpose |
|---|---|
| `scorer.py` | Pure field-level scoring: text fields (exact after normalize), money fields (±0.01), line items (precision/recall/F1). No network/DB at import. |
| `cases/*.json` | **Synthetic** fixtures (fictional vendors/amounts). Each has `expected` (ground truth) + `sample_actual` (a recorded model output) + `expected_score`. |
| `../test_ocr_golden.py` | Offline pytest: scores each fixture, asserts the scorer produces the documented numbers. Runs in CI with **no API key**. |

**No real financial documents are committed to this repo.** The fixtures are
made up; they exist to prove the scorer works.

## Offline (CI / verify.ps1) — proves the scorer is correct

```powershell
pytest tests/test_ocr_golden.py -v
```

This needs no API key and no network.

## Live — measures the REAL production accuracy

Point the scorer at a real invoice image you keep **outside the repo**, plus a
hand-checked `expected.json` (same field shape as the fixtures):

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:DATABASE_URL   = "postgresql://..."   # _run_gpt_vision needs the app importable
python -m tests.ocr_golden.scorer --live C:\path\outside\repo\invoice1.jpg C:\path\outside\repo\invoice1.expected.json
```

It runs the real `main._run_gpt_vision` pipeline and prints field-level accuracy.

### Building a real golden set (recommended)
1. Pick ~20–50 invoices/slips that a human has already confirmed in the app.
2. For each, save the confirmed fields as `<name>.expected.json` in a folder
   **outside** this repo (e.g. `C:\Users\rapee\ocr-golden-private\`).
3. Run `--live` on each, record the accuracy, and re-run after any prompt/model
   change to see whether accuracy moved. That number is the real Testing-phase
   metric the audit asked for.

## Scoring rules
- **Text fields** (`vendor_name`, `invoice_no`, `bill_date`, `merchant_tax_id`):
  exact match after lowercase + whitespace-collapse.
- **Money fields** (`amount`, `subtotal`, `vat`): numeric, within ±0.01; commas
  and currency text are stripped first (`"1,070.00 บาท"` → `1070.0`).
- **Items**: matched on (normalized name + qty + total) → precision/recall/F1.
- **overall** = mean(scalar field accuracy, item F1).
