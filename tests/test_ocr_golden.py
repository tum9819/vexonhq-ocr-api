"""
test_ocr_golden.py — offline verification of the OCR accuracy scorer.

Runs in CI / verify.ps1 with NO API key and NO network: it loads the synthetic
fixtures in ocr_golden/cases/, scores each `expected` against its recorded
`sample_actual`, and asserts the scorer produces the documented numbers. This
proves the measurement harness itself is correct; the REAL accuracy number is
produced by `python -m tests.ocr_golden.scorer --live ...` on real images kept
outside the repo (see ocr_golden/README.md).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.ocr_golden.scorer import score_case, aggregate, nums_match, normalize_text
from tests.ocr_golden.compare import compare_results, summarize

CASES_DIR = pathlib.Path(__file__).parent / "ocr_golden" / "cases"


def _load_cases():
    files = sorted(CASES_DIR.glob("*.json"))
    return [(f.name, json.loads(f.read_text(encoding="utf-8"))) for f in files]


def test_cases_exist():
    assert _load_cases(), "no synthetic OCR fixtures found in ocr_golden/cases/"


def test_normalizers():
    assert normalize_text("  A  B ") == "a b"
    assert normalize_text(None) == ""
    assert nums_match("1,234.56", 1234.56)
    assert nums_match("1,320.98 บาท", 1320.98)
    assert not nums_match(196.0, 0.0)
    assert nums_match(None, None)
    assert not nums_match(None, 5)


@pytest.mark.parametrize("name,case", _load_cases())
def test_synthetic_case_scores_as_documented(name, case):
    expected = case["expected"]
    actual = case["sample_actual"]
    result = score_case(expected, actual)
    doc = case.get("expected_score", {})

    if "field_accuracy" in doc:
        assert result["field_accuracy"] == pytest.approx(doc["field_accuracy"], abs=0.001), (
            f"{name}: field_accuracy {result['field_accuracy']} != {doc['field_accuracy']}"
        )
    if "item_f1" in doc:
        assert result["items"]["f1"] == pytest.approx(doc["item_f1"], abs=0.001), (
            f"{name}: item_f1 {result['items']['f1']} != {doc['item_f1']}"
        )
    if "item_matched" in doc:
        assert result["items"]["matched"] == doc["item_matched"]
    if "overall" in doc:
        assert result["overall"] == pytest.approx(doc["overall"], abs=0.001)
    # Per-field expectations, when the fixture spells them out.
    for field, exp_bool in (doc.get("fields") or {}).items():
        assert result["fields"][field] is exp_bool, (
            f"{name}: field {field} scored {result['fields'][field]}, expected {exp_bool}"
        )


def test_perfect_case_is_100():
    """A case whose actual == expected must score 1.0 across the board."""
    cases = dict(_load_cases())
    perfect = cases.get("synthetic_makro_perfect.json")
    assert perfect is not None
    r = score_case(perfect["expected"], perfect["expected"])
    assert r["field_accuracy"] == 1.0
    assert r["items"]["f1"] == 1.0
    assert r["overall"] == 1.0


def test_aggregate_runs():
    results = [score_case(c["expected"], c["sample_actual"]) for _, c in _load_cases()]
    agg = aggregate(results)
    assert agg["cases"] == len(results)
    assert 0.0 <= agg["mean_overall"] <= 1.0
    assert set(agg["per_field"]) >= {"vendor_name", "amount", "vat"}


# ── OCR model comparison harness (gpt-4o vs Claude) — pure-logic checks ──
# The API-calling parts (run_openai_ocr / run_claude_ocr) are NOT tested here
# (they need live keys); we test the scoring/aggregation that decides the winner.

def test_compare_results_picks_better_model():
    expected = {"vendor_name": "ก", "amount": 100.0, "items": []}
    openai_actual = {"vendor_name": "ก", "amount": 100.0, "items": []}   # perfect
    claude_actual = {"vendor_name": "ข", "amount": 999.0, "items": []}   # wrong
    res = compare_results(expected, openai_actual, claude_actual)
    assert res["winner"] == "openai"
    assert res["openai"]["overall"] > res["claude"]["overall"]


def test_compare_results_tie():
    expected = {"vendor_name": "ก", "amount": 100.0, "items": []}
    res = compare_results(expected, dict(expected), dict(expected))
    assert res["winner"] == "tie"


def test_summarize_counts_wins_and_recommends():
    expected = {"vendor_name": "ก", "amount": 100.0, "items": []}
    good, bad = {"vendor_name": "ก", "amount": 100.0, "items": []}, {"vendor_name": "ข", "amount": 1.0, "items": []}
    rows = [
        compare_results(expected, good, bad),   # openai wins
        compare_results(expected, good, bad),   # openai wins
        compare_results(expected, bad, good),   # claude wins
    ]
    s = summarize(rows)
    assert s["cases"] == 3
    assert s["wins"]["openai"] == 2 and s["wins"]["claude"] == 1
    assert s["recommendation"] == "openai"
    assert s["openai_mean_overall"] > s["claude_mean_overall"]


def test_summarize_empty():
    s = summarize([])
    assert s["cases"] == 0
