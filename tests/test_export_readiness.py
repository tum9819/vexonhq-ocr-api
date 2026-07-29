from datetime import date
from decimal import Decimal

import pytest

from export_readiness import build_readiness, canonical_fingerprint, package_status


def _facts(**overrides):
    base = {
        "today": date(2026, 8, 2),
        "month_start": date(2026, 7, 1),
        "month_end": date(2026, 7, 31),
        "statement": {
            "row_count": 81,
            "batch_count": 1,
            "first_observed_date": date(2026, 7, 1),
            "last_observed_date": date(2026, 7, 15),
            "declared_period_verified": False,
        },
        "statement_needs_review": {"count": 0, "amount": 0},
        "uncategorized": {"count": 0, "amount": 0},
        "reconciliation": {"ok": True, "drift": 0},
        "monthly_close_dangers": {"count": 0, "amount": 0},
        "transfer_evidence": {"missing_slip_count": 0, "missing_slip_amount": 0},
        "invoice_evidence": {"missing_page_count": 0, "missing_page_amount": 0},
        "credit_cards": {
            "confirmed_count": 2,
            "missing_invoice_page_count": 0,
            "missing_invoice_page_amount": 0,
        },
        "image_url_failures": {"count": 0},
    }
    base.update(overrides)
    return base


def test_observed_to_july_15_is_preview_only_not_ready():
    result = build_readiness("2026-07", "thawi_watthana", _facts())
    rules = result["packages"]["common_accounting"]["rules"]
    rule = next(r for r in rules if r["code"] == "STATEMENT_PERIOD_UNVERIFIED")
    assert rule["outcome"] == "preview_only"
    assert rule["evidence"]["last_observed_date"] == "2026-07-15"
    assert result["packages"]["common_accounting"]["status"] == "preview_only"


def test_missing_statement_requires_action():
    facts = _facts(statement={
        "row_count": 0,
        "batch_count": 0,
        "first_observed_date": None,
        "last_observed_date": None,
        "declared_period_verified": False,
    })
    result = build_readiness("2026-07", "thawi_watthana", facts)
    codes = [r["code"] for r in result["packages"]["common_accounting"]["rules"]]
    assert "STATEMENT_MISSING" in codes
    assert "STATEMENT_PERIOD_UNVERIFIED" not in codes
    assert result["packages"]["common_accounting"]["status"] == "action_required"


def test_declared_period_and_all_common_rules_can_be_ready():
    facts = _facts(statement={
        "row_count": 120,
        "batch_count": 1,
        "first_observed_date": date(2026, 7, 1),
        "last_observed_date": date(2026, 7, 31),
        "declared_period_verified": True,
    })
    result = build_readiness("2026-07", "thawi_watthana", facts)
    assert result["packages"]["common_accounting"]["status"] == "ready"


def test_current_month_is_preview_only_even_with_verified_coverage():
    facts = _facts(
        today=date(2026, 7, 29),
        statement={
            "row_count": 120,
            "batch_count": 1,
            "first_observed_date": date(2026, 7, 1),
            "last_observed_date": date(2026, 7, 29),
            "declared_period_verified": True,
        },
    )
    result = build_readiness("2026-07", "thawi_watthana", facts)
    assert result["packages"]["common_accounting"]["status"] == "preview_only"


def test_credit_card_missing_invoice_does_not_create_missing_transfer_slip():
    result = build_readiness("2026-07", "thawi_watthana", _facts(credit_cards={
        "confirmed_count": 2,
        "missing_invoice_page_count": 7,
        "missing_invoice_page_amount": 900,
    }))
    rule = next(r for r in result["packages"]["tax_evidence"]["rules"]
                if r["code"] == "TRANSFER_SLIP_EVIDENCE")
    assert rule["count"] == 0


def test_rollup_precedence():
    assert package_status(["pass", "preview_only"]) == "preview_only"
    assert package_status(["preview_only", "action_required"]) == "action_required"
    assert package_status(["pass", "pass"]) == "ready"


def test_fingerprint_is_order_independent_but_value_sensitive():
    a = canonical_fingerprint({"rows": [{"id": "b", "amount": 2}, {"id": "a", "amount": 1}]})
    b = canonical_fingerprint({"rows": [{"id": "a", "amount": 1}, {"id": "b", "amount": 2}]})
    c = canonical_fingerprint({"rows": [{"id": "a", "amount": 9}, {"id": "b", "amount": 2}]})
    assert a == b
    assert a != c


@pytest.mark.parametrize(
    ("code", "outcome", "count", "amount", "href"),
    [
        ("MONTH_ENDED", "pass", 0, None, None),
        ("STATEMENT_MISSING", "pass", 120, None, "/bank-statement"),
        ("STATEMENT_PERIOD_UNVERIFIED", "pass", 120, None, "/bank-statement"),
        ("STATEMENT_REVIEW_CLEAR", "pass", 0, 0, "/bank-statement"),
        ("UNCATEGORIZED_CLEAR", "pass", 0, 0, "/ai-review"),
        ("DAYBOOK_RECONCILES", "pass", 0, 0, None),
        ("MONTHLY_CLOSE_DANGER_CLEAR", "pass", 0, 0, "/alerts"),
        ("TRANSFER_SLIP_EVIDENCE", "pass", 0, 0, "/slips"),
        ("INVOICE_ATTACHMENT_EVIDENCE", "pass", 0, 0, "/invoices"),
        ("CREDIT_CARD_INVOICE_EVIDENCE", "pass", 0, 0, "/bills/payment"),
        ("IMAGE_URLS_PRESENT", "pass", 0, None, None),
        ("TAX_PROFILE_PHASE_B", "action_required", 1, None, None),
        ("PND_RECIPIENT_ASSIGNMENTS_PHASE_B", "action_required", 0, 0, None),
        ("SHAREHOLDER_PREVIEW_PHASE_C", "action_required", 1, None, None),
    ],
)
def test_each_stable_rule_exposes_its_pass_or_phase_blocker_details(
    code, outcome, count, amount, href
):
    result = build_readiness("2026-07", "thawi_watthana", _facts(statement={
        "row_count": 120,
        "batch_count": 1,
        "first_observed_date": date(2026, 7, 1),
        "last_observed_date": date(2026, 7, 31),
        "declared_period_verified": True,
    }))
    all_rules = [
        *result["packages"]["common_accounting"]["rules"],
        *result["packages"]["pnd3"]["rules"],
        *result["packages"]["shareholder"]["rules"],
    ]
    rule = next(rule for rule in all_rules if rule["code"] == code)

    assert rule["outcome"] == outcome
    assert rule["count"] == count
    assert rule["amount"] == amount
    assert rule["href"] == href


@pytest.mark.parametrize(
    ("name", "overrides", "code", "outcome", "count", "amount", "href"),
    [
        ("month not ended", {"today": date(2026, 7, 31)}, "MONTH_ENDED", "preview_only", 0, None, None),
        ("statement missing", {"statement": {
            "row_count": 0,
            "batch_count": 0,
            "first_observed_date": None,
            "last_observed_date": None,
            "declared_period_verified": False,
        }}, "STATEMENT_MISSING", "action_required", 0, None, "/bank-statement"),
        ("statement period unverified", {}, "STATEMENT_PERIOD_UNVERIFIED", "preview_only", 81, None, "/bank-statement"),
        ("statement review remains", {"statement_needs_review": {"count": 3, "amount": 450}}, "STATEMENT_REVIEW_CLEAR", "action_required", 3, 450, "/bank-statement"),
        ("uncategorized expense remains", {"uncategorized": {"count": 2, "amount": 80}}, "UNCATEGORIZED_CLEAR", "action_required", 2, 80, "/ai-review"),
        ("daybook drift exceeds tolerance", {"reconciliation": {"ok": False, "drift": -0.02}}, "DAYBOOK_RECONCILES", "action_required", 1, 0.02, None),
        ("monthly close danger remains", {"monthly_close_dangers": {"count": 4, "amount": 1200}}, "MONTHLY_CLOSE_DANGER_CLEAR", "action_required", 4, 1200, "/alerts"),
        ("transfer slip is missing", {"transfer_evidence": {"missing_slip_count": 5, "missing_slip_amount": 700}}, "TRANSFER_SLIP_EVIDENCE", "action_required", 5, 700, "/slips"),
        ("invoice page is missing", {"invoice_evidence": {"missing_page_count": 6, "missing_page_amount": 800}}, "INVOICE_ATTACHMENT_EVIDENCE", "action_required", 6, 800, "/invoices"),
        ("credit card invoice page is missing", {"credit_cards": {
            "confirmed_count": 2,
            "missing_invoice_page_count": 7,
            "missing_invoice_page_amount": 900,
        }}, "CREDIT_CARD_INVOICE_EVIDENCE", "action_required", 7, 900, "/bills/payment"),
        ("image URL is missing", {"image_url_failures": {"count": 8}}, "IMAGE_URLS_PRESENT", "action_required", 8, None, None),
        ("tax profile remains phase B", {}, "TAX_PROFILE_PHASE_B", "action_required", 1, None, None),
        ("recipient assignments remain phase B", {"wht_candidates": {"count": 9, "amount": 1000}}, "PND_RECIPIENT_ASSIGNMENTS_PHASE_B", "action_required", 9, 1000, None),
        ("shareholder preview remains phase C", {}, "SHAREHOLDER_PREVIEW_PHASE_C", "action_required", 1, None, None),
    ],
)
def test_each_blocking_rule_keeps_its_action_details(
    name, overrides, code, outcome, count, amount, href
):
    result = build_readiness("2026-07", "thawi_watthana", _facts(**overrides))
    all_rules = [
        *result["packages"]["common_accounting"]["rules"],
        *result["packages"]["pnd3"]["rules"],
        *result["packages"]["shareholder"]["rules"],
    ]
    rule = next(rule for rule in all_rules if rule["code"] == code)

    assert rule["outcome"] == outcome
    assert rule["count"] == count
    assert rule["amount"] == amount
    assert rule["href"] == href


def test_packages_keep_the_approved_ordered_rule_composition_and_statuses():
    result = build_readiness("2026-07", "thawi_watthana", _facts(statement={
        "row_count": 120,
        "batch_count": 1,
        "first_observed_date": date(2026, 7, 1),
        "last_observed_date": date(2026, 7, 31),
        "declared_period_verified": True,
    }))

    assert [rule["code"] for rule in result["packages"]["common_accounting"]["rules"]] == [
        "MONTH_ENDED", "STATEMENT_MISSING", "STATEMENT_PERIOD_UNVERIFIED",
        "STATEMENT_REVIEW_CLEAR", "UNCATEGORIZED_CLEAR", "DAYBOOK_RECONCILES",
        "MONTHLY_CLOSE_DANGER_CLEAR",
    ]
    assert [rule["code"] for rule in result["packages"]["tax_evidence"]["rules"]] == [
        "MONTH_ENDED", "STATEMENT_MISSING", "STATEMENT_PERIOD_UNVERIFIED",
        "STATEMENT_REVIEW_CLEAR", "UNCATEGORIZED_CLEAR", "DAYBOOK_RECONCILES",
        "MONTHLY_CLOSE_DANGER_CLEAR", "TRANSFER_SLIP_EVIDENCE",
        "INVOICE_ATTACHMENT_EVIDENCE", "CREDIT_CARD_INVOICE_EVIDENCE",
        "IMAGE_URLS_PRESENT",
    ]
    assert [rule["code"] for rule in result["packages"]["pnd3"]["rules"]] == [
        "MONTH_ENDED", "STATEMENT_MISSING", "STATEMENT_PERIOD_UNVERIFIED",
        "STATEMENT_REVIEW_CLEAR", "UNCATEGORIZED_CLEAR", "DAYBOOK_RECONCILES",
        "MONTHLY_CLOSE_DANGER_CLEAR", "TRANSFER_SLIP_EVIDENCE",
        "INVOICE_ATTACHMENT_EVIDENCE", "CREDIT_CARD_INVOICE_EVIDENCE",
        "IMAGE_URLS_PRESENT", "TAX_PROFILE_PHASE_B",
        "PND_RECIPIENT_ASSIGNMENTS_PHASE_B",
    ]
    assert [rule["code"] for rule in result["packages"]["pnd53"]["rules"]] == [
        "MONTH_ENDED", "STATEMENT_MISSING", "STATEMENT_PERIOD_UNVERIFIED",
        "STATEMENT_REVIEW_CLEAR", "UNCATEGORIZED_CLEAR", "DAYBOOK_RECONCILES",
        "MONTHLY_CLOSE_DANGER_CLEAR", "TRANSFER_SLIP_EVIDENCE",
        "INVOICE_ATTACHMENT_EVIDENCE", "CREDIT_CARD_INVOICE_EVIDENCE",
        "IMAGE_URLS_PRESENT", "TAX_PROFILE_PHASE_B",
        "PND_RECIPIENT_ASSIGNMENTS_PHASE_B",
    ]
    assert [rule["code"] for rule in result["packages"]["shareholder"]["rules"]] == [
        "MONTH_ENDED", "STATEMENT_MISSING", "STATEMENT_PERIOD_UNVERIFIED",
        "STATEMENT_REVIEW_CLEAR", "UNCATEGORIZED_CLEAR", "DAYBOOK_RECONCILES",
        "MONTHLY_CLOSE_DANGER_CLEAR", "INVOICE_ATTACHMENT_EVIDENCE",
        "CREDIT_CARD_INVOICE_EVIDENCE", "IMAGE_URLS_PRESENT",
        "SHAREHOLDER_PREVIEW_PHASE_C",
    ]
    assert result["packages"]["common_accounting"]["status"] == "ready"
    assert result["packages"]["tax_evidence"]["status"] == "ready"
    assert result["packages"]["pnd3"]["status"] == "action_required"
    assert result["packages"]["pnd53"]["status"] == "action_required"
    assert result["packages"]["shareholder"]["status"] == "action_required"


def test_fingerprint_normalizes_date_and_decimal_values_before_hashing():
    typed = canonical_fingerprint({"month_end": date(2026, 7, 31), "amount": Decimal("123.40")})
    serialized = canonical_fingerprint({"month_end": "2026-07-31", "amount": "123.40"})

    assert typed == serialized
