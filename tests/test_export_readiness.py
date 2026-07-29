from datetime import date

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
    assert any(r["code"] == "STATEMENT_MISSING" for r in result["packages"]["common_accounting"]["rules"])
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


def test_credit_card_never_contributes_to_missing_slip():
    result = build_readiness("2026-07", "thawi_watthana", _facts())
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
