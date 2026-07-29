from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_routes
import export_routes
import monthly_close_routes
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
        "reconciliation": {
            "verified": False,
            "basis": "no_independent_comparator",
            "internal_partition": {"ok": True, "drift": 0},
        },
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


def test_readiness_requires_login():
    app = FastAPI()
    app.include_router(export_routes.router)
    response = TestClient(app, raise_server_exceptions=False).get(
        "/export/readiness?month=2026-07"
    )
    assert response.status_code == 401


def test_readiness_accepts_admin_and_returns_versioned_contract(monkeypatch):
    app = FastAPI()
    app.include_router(export_routes.router)
    app.dependency_overrides[export_routes._require_admin_role] = lambda: {
        "sub": "admin-uid", "_role": "admin",
    }
    monkeypatch.setattr(
        export_routes,
        "_query_readiness_facts",
        lambda *args: (_facts(), {"rows": []}),
    )
    response = TestClient(app, raise_server_exceptions=False).get(
        "/export/readiness?month=2026-07&branch_code=thawi_watthana"
    )
    assert response.status_code == 200
    assert response.json()["schema_version"] == 1
    assert len(response.json()["source_fingerprint"]["sha256"]) == 64


def test_readiness_rejects_staff_through_real_auth_dependency(monkeypatch):
    app = FastAPI()
    app.include_router(export_routes.router)
    monkeypatch.setattr(
        auth_routes,
        "verify_token",
        lambda _token: {"sub": "staff-uid", "_role": "staff"},
    )
    response = TestClient(app, raise_server_exceptions=False).get(
        "/export/readiness?month=2026-07",
        headers={"Authorization": "Bearer STAFF"},
    )
    assert response.status_code == 403


def test_readiness_rejects_unsupported_branch_without_querying(monkeypatch):
    app = FastAPI()
    app.include_router(export_routes.router)
    app.dependency_overrides[export_routes._require_admin_role] = lambda: {
        "sub": "admin-uid", "_role": "admin",
    }
    query_calls = []
    monkeypatch.setattr(
        export_routes,
        "_query_readiness_facts",
        lambda *args: query_calls.append(args) or (_facts(), {"rows": []}),
    )

    response = TestClient(app, raise_server_exceptions=False).get(
        "/export/readiness?month=2026-07&branch_code=future_branch"
    )

    assert response.status_code == 422
    assert query_calls == []


@pytest.mark.parametrize(
    "month",
    [
        "2026/07", "2026-7", "2026-07x", "2026-070", "2026-00", "2026-13",
        "0000-01", "\u0662\u0660\u0662\u0666-07",
    ],
)
def test_readiness_rejects_non_exact_or_invalid_month_without_querying(monkeypatch, month):
    app = FastAPI()
    app.include_router(export_routes.router)
    app.dependency_overrides[export_routes._require_admin_role] = lambda: {
        "sub": "admin-uid", "_role": "admin",
    }
    query_calls = []
    monkeypatch.setattr(
        export_routes,
        "_query_readiness_facts",
        lambda *args: query_calls.append(args) or (_facts(), {"rows": []}),
    )

    response = TestClient(app, raise_server_exceptions=False).get(
        f"/export/readiness?month={month}"
    )

    assert response.status_code == 400
    assert query_calls == []


def test_same_view_partition_keeps_reconciliation_preview_only():
    result = build_readiness("2026-07", "thawi_watthana", _facts())
    rule = next(
        rule
        for rule in result["packages"]["common_accounting"]["rules"]
        if rule["code"] == "DAYBOOK_RECONCILES"
    )

    assert rule["outcome"] == "preview_only"
    assert rule["amount"] is None
    assert rule["evidence"] == {
        "verified": False,
        "basis": "no_independent_comparator",
        "internal_partition": {"ok": True, "drift": 0},
    }


def test_query_readiness_facts_batches_six_selects_and_normalizes_evidence(monkeypatch):
    responses = [
        (
            [
                "row_count", "batch_count", "first_observed_date",
                "last_observed_date", "review_count", "review_amount",
            ],
            [(3, 1, date(2026, 7, 1), date(2026, 7, 31), 1, Decimal("12.00"))],
        ),
        (
            [
                "uncategorized_count", "uncategorized_amount", "income_total",
                "expense_total", "categorized_expense",
            ],
            [(1, Decimal("50.00"), Decimal("400.00"), Decimal("300.00"), Decimal("250.00"))],
        ),
        (
            [
                "id", "import_batch_id", "txn_date", "description", "debit",
                "credit", "amount", "category_code", "source_type",
                "match_status", "matched_invoice_id",
                "matched_invoice_is_credit_card", "has_slip",
            ],
            [
                (
                    "s1", "batch-1", date(2026, 7, 5), "transfer",
                    Decimal("100.00"), Decimal("0"), Decimal("100.00"),
                    "food_raw", "vendor_purchase", "manual", "b1", False, False,
                ),
                (
                    "s2", "batch-1", date(2026, 7, 6), "card settlement",
                    Decimal("200.00"), Decimal("0"), Decimal("200.00"),
                    "food_raw", "vendor_purchase", "manual", "b2", True, False,
                ),
                (
                    "s3", "batch-1", date(2026, 7, 7), "income",
                    Decimal("0"), Decimal("400.00"), Decimal("400.00"),
                    "sales", "sales", "manual", None, False, False,
                ),
            ],
        ),
        (
            [
                "id", "transfer_date", "amount", "raw_image_url",
                "matched_statement_id", "matched_invoice_id", "match_status",
                "is_branch_linked", "is_month_unattributed",
            ],
            [
                (
                    "sl1", date(2026, 6, 30), Decimal("100.00"), None,
                    None, "b3", "matched_full", True, False,
                ),
                (
                    "sl2", date(2026, 7, 10), Decimal("55.00"), None,
                    None, None, "unmatched", False, True,
                ),
            ],
        ),
        (
            [
                "id", "bill_date", "amount", "category_code", "review_status",
                "payment_type", "payment_status", "attachment_url",
                "attachment_id", "page_no", "file_url",
            ],
            [
                (
                    "b1", date(2026, 7, 5), Decimal("100.00"), "food_raw",
                    "confirmed", "transfer", "paid", None, None, None, None,
                ),
                (
                    "b2", date(2026, 7, 6), Decimal("200.00"), "food_raw",
                    "confirmed", "credit_card", "paid", "legacy-b2", None, None, None,
                ),
                (
                    "b3", date(2026, 7, 8), Decimal("50.00"), "food_raw",
                    "pending", "cash", "unpaid", None, "a3", 1, "page-b3",
                ),
            ],
        ),
        (
            [
                "entry_date", "direction", "amount", "category_code",
                "source", "ref_id", "label", "counterparty",
            ],
            [
                (
                    date(2026, 7, 5), "expense", Decimal("100.00"), "food_raw",
                    "bank_statement", "s1", "transfer", "Vendor One",
                ),
                (
                    date(2026, 7, 6), "expense", Decimal("200.00"), "food_raw",
                    "bank_statement", "s2", "card settlement", "Vendor Two",
                ),
                (
                    date(2026, 7, 7), "income", Decimal("400.00"), "sales",
                    "pos_sales", "s3", "income", None,
                ),
            ],
        ),
    ]

    class FakeCursor:
        description = None

        def __init__(self):
            self.executions = []
            self._rows = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            columns, rows = responses[len(self.executions)]
            self.executions.append((sql, params))
            self.description = [(column,) for column in columns]
            self._rows = rows

        def fetchone(self):
            return self._rows[0]

        def fetchall(self):
            return list(self._rows)

    class FakeConnection:
        def __init__(self):
            self.cur = FakeCursor()
            self.closed = False
            self.session_calls = []

        def set_session(self, **kwargs):
            self.session_calls.append(kwargs)

        def cursor(self):
            return self.cur

        def close(self):
            self.closed = True

    conn = FakeConnection()
    monkeypatch.setattr(export_routes, "get_db_conn", lambda: conn)
    monkeypatch.setattr(
        monthly_close_routes,
        "run_all_checks",
        lambda *_args: [
            {"risk_key": "danger-one", "severity": "danger", "amount": Decimal("9.00")},
            {"risk_key": "warning-one", "severity": "warning", "amount": Decimal("7.00")},
        ],
    )

    facts, fingerprint_inputs = export_routes._query_readiness_facts(
        date(2026, 7, 1), date(2026, 7, 31), "future_branch"
    )

    assert len(conn.cur.executions) == 6
    assert all(sql.lstrip().startswith("SELECT") for sql, _ in conn.cur.executions)
    assert conn.session_calls == [
        {"readonly": True, "isolation_level": "REPEATABLE READ"}
    ]
    assert conn.closed is True
    assert [params for _, params in conn.cur.executions] == [
        (date(2026, 7, 1), date(2026, 7, 31), "future_branch"),
        (date(2026, 7, 1), date(2026, 7, 31), "future_branch"),
        (date(2026, 7, 1), date(2026, 7, 31), "future_branch"),
        (
            date(2026, 7, 1), date(2026, 7, 31), "future_branch",
            date(2026, 7, 1), date(2026, 7, 31), "thawi_watthana",
            "future_branch", date(2026, 7, 1), date(2026, 7, 31),
        ),
        (
            date(2026, 7, 1), date(2026, 7, 31),
            "thawi_watthana", "future_branch",
        ),
        (date(2026, 7, 1), date(2026, 7, 31), "future_branch"),
    ]
    statement_sql = conn.cur.executions[2][0]
    assert "b.import_batch_id::text" in statement_sql
    assert "b.amount" in statement_sql
    assert "LEFT JOIN public.vendor_bills matched_vb" in statement_sql
    normalized_statement_sql = " ".join(statement_sql.split())
    assert (
        "matched_vb.review_status = 'confirmed' AND ( "
        "matched_vb.payment_type = 'credit_card' OR "
        "matched_vb.payment_status = 'credit_card' )"
    ) in normalized_statement_sql
    assert "matched_vb.bill_date" not in statement_sql
    assert "matched_invoice_is_credit_card" in statement_sql
    slip_sql = conn.cur.executions[3][0]
    assert "is_branch_linked" in slip_sql
    assert "is_month_unattributed" in slip_sql
    assert facts["statement"] == {
        "row_count": 3,
        "batch_count": 1,
        "first_observed_date": date(2026, 7, 1),
        "last_observed_date": date(2026, 7, 31),
        "declared_period_verified": False,
    }
    assert facts["statement_needs_review"] == {"count": 1, "amount": Decimal("12.00")}
    assert facts["reconciliation"] == {
        "verified": False,
        "basis": "no_independent_comparator",
        "internal_partition": {"ok": True, "drift": Decimal("0.00")},
    }
    assert facts["monthly_close_dangers"] == {"count": 1, "amount": Decimal("9.00")}
    assert facts["transfer_evidence"] == {
        "missing_slip_count": 1,
        "missing_slip_amount": Decimal("100.00"),
    }
    assert facts["invoice_evidence"] == {
        "missing_page_count": 1,
        "missing_page_amount": Decimal("100.00"),
    }
    assert facts["credit_cards"] == {
        "confirmed_count": 1,
        "missing_invoice_page_count": 0,
        "missing_invoice_page_amount": 0,
    }
    assert facts["image_url_failures"] == {"count": 2}
    assert fingerprint_inputs["month"] == "2026-07"
    assert fingerprint_inputs["branch_code"] == "future_branch"
    assert len(fingerprint_inputs["daybook_rows"]) == 3
    assert fingerprint_inputs["statement_rows"][0]["import_batch_id"] == "batch-1"
    assert fingerprint_inputs["statement_rows"][0]["amount"] == Decimal("100.00")
    assert fingerprint_inputs["statement_rows"][1]["matched_invoice_is_credit_card"] is True
    assert [row["id"] for row in fingerprint_inputs["branch_linked_slip_rows"]] == [
        "sl1"
    ]
    assert [
        row["id"] for row in fingerprint_inputs["month_unattributed_slip_rows"]
    ] == ["sl2"]
    assert fingerprint_inputs["monthly_close"] == [
        {"risk_key": "danger-one", "severity": "danger", "amount": Decimal("9.00")}
    ]

    base_hash = canonical_fingerprint(fingerprint_inputs)
    changed_batch = deepcopy(fingerprint_inputs)
    changed_batch["statement_rows"][0]["import_batch_id"] = "batch-2"
    changed_amount = deepcopy(fingerprint_inputs)
    changed_amount["statement_rows"][0]["amount"] = Decimal("101.00")
    assert canonical_fingerprint(changed_batch) != base_hash
    assert canonical_fingerprint(changed_amount) != base_hash


def test_query_readiness_facts_closes_repeatable_read_connection_after_exception(
    monkeypatch,
):
    class RaisingCursor:
        description = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, _sql, _params):
            raise RuntimeError("database read failed")

    class RaisingConnection:
        def __init__(self):
            self.closed = False
            self.session_calls = []

        def set_session(self, **kwargs):
            self.session_calls.append(kwargs)

        def cursor(self):
            return RaisingCursor()

        def close(self):
            self.closed = True

    conn = RaisingConnection()
    monkeypatch.setattr(export_routes, "get_db_conn", lambda: conn)

    with pytest.raises(RuntimeError, match="database read failed"):
        export_routes._query_readiness_facts(
            date(2026, 7, 1), date(2026, 7, 31), "thawi_watthana"
        )

    assert conn.session_calls == [
        {"readonly": True, "isolation_level": "REPEATABLE READ"}
    ]
    assert conn.closed is True


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


def test_declared_period_still_preview_only_without_independent_reconciliation():
    facts = _facts(statement={
        "row_count": 120,
        "batch_count": 1,
        "first_observed_date": date(2026, 7, 1),
        "last_observed_date": date(2026, 7, 31),
        "declared_period_verified": True,
    })
    result = build_readiness("2026-07", "thawi_watthana", facts)
    assert result["packages"]["common_accounting"]["status"] == "preview_only"


def test_independently_verified_reconciliation_can_make_common_rules_ready():
    facts = _facts(
        statement={
            "row_count": 120,
            "batch_count": 1,
            "first_observed_date": date(2026, 7, 1),
            "last_observed_date": date(2026, 7, 31),
            "declared_period_verified": True,
        },
        reconciliation={
            "verified": True,
            "basis": "independent_comparator",
            "ok": True,
            "drift": 0,
            "internal_partition": {"ok": True, "drift": 0},
        },
    )
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


def test_missing_transfer_waives_only_confirmed_linked_card_evidence():
    daybook_rows = [
        {
            "direction": "expense",
            "source": "vendor_purchase",
            "ref_id": statement_id,
        }
        for statement_id in (
            "confirmed-cross-month-type",
            "confirmed-cross-month-status",
            "rejected-type",
            "rejected-status",
            "pending-type",
            "null-review-status",
            "non-card",
            "no-bill",
        )
    ]
    statement_rows = [
        {
            "id": "confirmed-cross-month-type",
            "debit": Decimal("100"), "has_slip": False,
            "matched_invoice_id": "out-of-month-type",
            "matched_invoice_is_credit_card": True,
        },
        {
            "id": "confirmed-cross-month-status",
            "debit": Decimal("200"), "has_slip": False,
            "matched_invoice_id": "out-of-month-status",
            "matched_invoice_is_credit_card": True,
        },
        {
            "id": "rejected-type", "debit": Decimal("300"), "has_slip": False,
            "matched_invoice_id": "rejected-type-bill",
            "matched_invoice_is_credit_card": False,
        },
        {
            "id": "rejected-status", "debit": Decimal("400"), "has_slip": False,
            "matched_invoice_id": "rejected-status-bill",
            "matched_invoice_is_credit_card": False,
        },
        {
            "id": "pending-type", "debit": Decimal("500"), "has_slip": False,
            "matched_invoice_id": "pending-bill",
            "matched_invoice_is_credit_card": False,
        },
        {
            "id": "null-review-status",
            "debit": Decimal("600"), "has_slip": False,
            "matched_invoice_id": "null-review-bill",
            "matched_invoice_is_credit_card": False,
        },
        {
            "id": "non-card", "debit": Decimal("700"), "has_slip": False,
            "matched_invoice_id": "out-of-month-transfer",
            "matched_invoice_is_credit_card": False,
        },
        {
            "id": "no-bill", "debit": Decimal("800"), "has_slip": False,
            "matched_invoice_id": None,
            "matched_invoice_is_credit_card": False,
        },
    ]

    missing = export_routes._missing_transfer_slips(statement_rows, daybook_rows)

    assert [row["id"] for row in missing] == [
        "rejected-type",
        "rejected-status",
        "pending-type",
        "null-review-status",
        "non-card",
        "no-bill",
    ]


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
        ("DAYBOOK_RECONCILES", "preview_only", 0, None, None),
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
        ("daybook drift exceeds tolerance", {"reconciliation": {
            "verified": True,
            "basis": "independent_comparator",
            "ok": False,
            "drift": -0.02,
            "internal_partition": {"ok": True, "drift": 0},
        }}, "DAYBOOK_RECONCILES", "action_required", 1, 0.02, None),
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
    result = build_readiness("2026-07", "thawi_watthana", _facts(
        statement={
            "row_count": 120,
            "batch_count": 1,
            "first_observed_date": date(2026, 7, 1),
            "last_observed_date": date(2026, 7, 31),
            "declared_period_verified": True,
        },
        reconciliation={
            "verified": True,
            "basis": "independent_comparator",
            "ok": True,
            "drift": 0,
            "internal_partition": {"ok": True, "drift": 0},
        },
    ))

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
