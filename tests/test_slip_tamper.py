"""F11 anti-tamper: _slip_tamper_signal — same bank ref_no + different amount = edited slip.

A bank transfer's ref_no is globally unique per transaction. If a re-uploaded slip
carries the same ref_no but a different amount, the slip image was likely edited.
These tests pin the decision rule so a refactor can't silently weaken it.

Run: pytest tests/test_slip_tamper.py -v
"""
from slip_routes import _slip_tamper_signal


def test_same_ref_different_amount_flags_tamper():
    out = _slip_tamper_signal(6850.0, "REF123", "REF123", 685.0)  # dropped a zero
    assert out is not None
    assert out["existing_amount"] == 6850.0
    assert out["uploaded_amount"] == 685.0
    assert out["ref_no"] == "REF123"


def test_same_ref_same_amount_is_genuine_duplicate():
    assert _slip_tamper_signal(1200.0, "REF123", "REF123", 1200.0) is None


def test_amount_within_one_satang_is_not_tamper():
    # rounding noise (OCR float vs stored numeric) must not false-positive
    assert _slip_tamper_signal(1200.00, "REF123", "REF123", 1200.005) is None


def test_different_ref_is_not_tamper():
    # different transaction entirely — not a tamper signal
    assert _slip_tamper_signal(6850.0, "REF_A", "REF_B", 685.0) is None


def test_ref_whitespace_is_normalized():
    assert _slip_tamper_signal(500.0, "  REF9 ", "REF9", 900.0) is not None


def test_missing_ref_no_signal():
    assert _slip_tamper_signal(500.0, "", "", 900.0) is None
    assert _slip_tamper_signal(500.0, "REF1", None, 900.0) is None


def test_missing_existing_amount_no_signal():
    assert _slip_tamper_signal(None, "REF1", "REF1", 900.0) is None
