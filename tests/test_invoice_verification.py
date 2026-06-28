"""Offline tests for OCR verification + discount reconciliation.

These tests are pure domain checks: no Supabase, no network, no real AI key.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/d")
os.environ.setdefault("JWT_SECRET", "testsecret")
os.environ.setdefault("OPENAI_API_KEY", "x")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "x")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
os.environ.setdefault("SUPABASE_ANON_KEY", "x")

from invoice_verification import (  # noqa: E402
    MockInvoiceVerifier,
    build_approval_blockers,
    build_force_confirm_warning,
    calculate_reconciliation,
    decide_review_status,
    normalize_ocr_extraction,
    run_invoice_verification,
)


def _assert_money(result: dict, field: str, expected: str) -> None:
    assert result["components"][field] == expected


@pytest.mark.parametrize(
    ("name", "invoice", "items", "expected_components"),
    [
        (
            "no discount",
            {"amount": "107.00", "vat": "7.00"},
            [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
            {"gross_item_total": "100.00", "calculated_total": "107.00"},
        ),
        (
            "line item discount",
            {"amount": "96.30", "vat": "6.30"},
            [{
                "quantity": "1",
                "unit_price": "100.00",
                "gross_amount": "100.00",
                "line_discount_amount": "10.00",
                "net_amount": "90.00",
                "amount": "90.00",
            }],
            {"line_discount_total": "10.00", "calculated_total": "96.30"},
        ),
        (
            "bill discount",
            {"amount": "96.30", "vat": "6.30", "bill_discount_total": "10.00"},
            [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
            {"bill_discount_total": "10.00", "calculated_total": "96.30"},
        ),
        (
            "voucher and promotion",
            {
                "amount": "83.95",
                "vat": "5.95",
                "voucher_discount_total": "15.00",
                "promotion_discount_total": "7.00",
            },
            [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
            {"voucher_discount_total": "15.00", "promotion_discount_total": "7.00"},
        ),
        (
            "vat and service charge",
            {"amount": "117.70", "vat": "7.70", "service_charge": "10.00"},
            [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
            {"service_charge": "10.00", "calculated_total": "117.70"},
        ),
        (
            "rounding",
            {"amount": "107.00", "vat": "7.03", "rounding_adjustment": "-0.03"},
            [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
            {"rounding_adjustment": "-0.03", "calculated_total": "107.00"},
        ),
    ],
)
def test_reconciliation_matrix_success_cases(name, invoice, items, expected_components):
    result = calculate_reconciliation(invoice, items)

    assert result["status"] == "matched", name
    assert result["difference"] == "0.00"
    for field, expected in expected_components.items():
        _assert_money(result, field, expected)


def test_reconciliation_flags_ocr_ai_amount_mismatch():
    result = calculate_reconciliation(
        {"amount": "120.00", "vat": "7.00"},
        [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
    )

    assert result["status"] == "mismatch"
    assert result["blocking"] is True
    assert result["difference"] == "-13.00"
    assert any(w["code"] == "RECONCILIATION_MISMATCH" for w in result["warnings"])


def test_reconciliation_uses_decimal_tolerance_and_rounding():
    within = calculate_reconciliation(
        {"amount": "107.04", "vat": "7.00"},
        [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
        tolerance=Decimal("0.05"),
    )
    outside = calculate_reconciliation(
        {"amount": "107.06", "vat": "7.00"},
        [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
        tolerance=Decimal("0.05"),
    )

    assert within["status"] == "matched"
    assert outside["status"] == "mismatch"


def test_reconciliation_supports_old_invoice_without_new_fields():
    result = calculate_reconciliation(
        {"subtotal": "100.00", "vat": "7.00", "amount": "107.00"},
        [],
    )

    assert result["status"] == "matched"
    _assert_money(result, "gross_item_total", "100.00")


def test_normalize_ocr_extraction_preserves_legacy_and_discount_fields():
    normalized = normalize_ocr_extraction({
        "amount": "1,070.00",
        "subtotal": "1,000.00",
        "bill_discount_total": "10.00",
        "items": [{"product_name": "A", "line_discount_amount": "5.00"}],
    })

    assert normalized["amount"] == "1070.00"
    assert normalized["subtotal"] == "1000.00"
    assert normalized["bill_discount_total"] == "10.00"
    assert normalized["items"][0]["line_discount_amount"] == "5.00"


def test_mock_verifier_not_configured_is_explicit_and_not_real_ai():
    result = run_invoice_verification(
        MockInvoiceVerifier(mode="not_configured"),
        pages=[{"file_url": "https://example.invalid/invoice.png"}],
        raw_ocr_text="total 107",
        structured_ocr={"amount": "107.00"},
    )

    assert result["status"] == "not_configured"
    assert result["provider"] == "mock"
    assert result["is_real_ai"] is False
    assert result["warnings"][0]["code"] == "AI_VERIFIER_NOT_CONFIGURED"


@pytest.mark.parametrize("mode, expected", [("success", "verified"), ("failure", "failed"), ("timeout", "timeout")])
def test_mock_verifier_modes_are_deterministic_and_wrapped(mode, expected):
    result = run_invoice_verification(
        MockInvoiceVerifier(mode=mode),
        pages=[],
        raw_ocr_text="",
        structured_ocr={"amount": "107.00"},
    )

    assert result["status"] == expected
    assert result["provider"] == "mock"


def test_review_status_decision_never_auto_confirms_from_mock_verifier():
    clean_recon = calculate_reconciliation(
        {"amount": "107.00", "vat": "7.00"},
        [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
    )
    verifier = run_invoice_verification(
        MockInvoiceVerifier(mode="success"),
        pages=[],
        raw_ocr_text="",
        structured_ocr={"amount": "107.00"},
    )

    decision = decide_review_status(verifier, clean_recon, existing_review_status="pending")

    assert decision["verification_status"] == "verified"
    assert decision["review_status"] == "pending"
    assert decision["review_status"] != "confirmed"


def test_not_configured_verifier_never_auto_approves_invoice():
    clean_recon = calculate_reconciliation(
        {"amount": "107.00", "vat": "7.00"},
        [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
    )
    verifier = run_invoice_verification(
        MockInvoiceVerifier(mode="not_configured"),
        pages=[],
        raw_ocr_text="",
        structured_ocr={"amount": "107.00"},
    )

    decision = decide_review_status(verifier, clean_recon, existing_review_status="pending")

    assert decision["verification_status"] == "not_configured"
    assert decision["review_status"] == "needs_attention"
    assert decision["review_status"] != "confirmed"


def test_review_status_decision_sends_mismatch_to_needs_attention():
    mismatch = calculate_reconciliation(
        {"amount": "120.00", "vat": "7.00"},
        [{"quantity": "1", "unit_price": "100.00", "amount": "100.00"}],
    )
    verifier = {"status": "verified", "confidence": 1, "warnings": []}

    decision = decide_review_status(verifier, mismatch, existing_review_status="pending")

    assert decision["review_status"] == "needs_attention"
    assert decision["verification_status"] == "mismatch"


def test_confirmation_guard_blocks_mismatch_and_low_confidence_but_not_old_invoice():
    assert build_approval_blockers({}, None) == []

    mismatch = {"status": "mismatch", "blocking": True, "warnings": []}
    low_conf = {"status": "low_confidence", "confidence": "0.40", "warnings": []}

    assert build_approval_blockers(low_conf, None)[0]["code"] == "LOW_CONFIDENCE"
    assert build_approval_blockers({"status": "verified"}, mismatch)[0]["code"] == "RECONCILIATION_MISMATCH"


def test_force_confirm_warning_requires_reason_and_is_audit_ready():
    warning = build_force_confirm_warning(
        actor="admin@example.com",
        reason="checked physical receipt",
        blockers=[{"code": "RECONCILIATION_MISMATCH", "message": "total mismatch"}],
    )

    assert warning["severity"] == "warn"
    assert warning["code"] == "FORCE_CONFIRMED_VERIFICATION_BLOCK"
    assert "admin@example.com" in warning["message"]
    assert "checked physical receipt" in warning["message"]

    with pytest.raises(ValueError):
        build_force_confirm_warning(actor="admin@example.com", reason=" ", blockers=[])
