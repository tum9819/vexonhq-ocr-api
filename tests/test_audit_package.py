"""Pure regressions for the schema-v2 monthly audit-package contract."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export_routes as routes  # noqa: E402
from tax_routes import WHT_RULES  # noqa: E402


_DEFAULT_STATEMENT_ID = object()


def _row(
    date,
    amount,
    cat,
    ref,
    label="x",
    cp="y",
    name_th=None,
    *,
    source="vendor_purchase",
    statement_id=_DEFAULT_STATEMENT_ID,
):
    return {
        "entry_date": date, "amount": amount, "category_code": cat,
        "category_name_th": name_th or (cat or "ไม่ระบุ"),
        "counterparty": cp, "label": label, "ref_id": ref, "source": source,
        "statement_id": ref if statement_id is _DEFAULT_STATEMENT_ID else statement_id,
    }


def _assemble(transfer_rows, *, slips=None, invoices=None, card_rows=None, card_invoices=None):
    return routes._assemble_audit_vouchers(
        transfer_rows=transfer_rows,
        card_rows=card_rows or [],
        slips_by_stmt=slips or {},
        inv_by_stmt=invoices or {},
        card_invoices=card_invoices or {},
        wht_rules=WHT_RULES,
    )


def test_seq_follows_input_order_and_amounts_round():
    rows = [_row("2026-06-01", 100.005, "rent", "a"), _row("2026-06-02", 50, "food_raw", "b")]
    v = _assemble(rows)
    assert [x["seq"] for x in v] == [1, 2]
    assert v[0]["amount"] == 100.0 or v[0]["amount"] == 100.01  # round to 2dp


def test_wht_musician_3pct_and_rent_5pct_others_none():
    rows = [
        _row("2026-06-05", 2100, "musician_fee", "m1"),
        _row("2026-06-06", 8000, "rent", "r1"),
        _row("2026-06-07", 500, "food_raw", "f1"),
    ]
    v = _assemble(rows)
    assert v[0]["wht"] == {"rate": 3.0, "amount": 63.0}
    assert v[1]["wht"] == {"rate": 5.0, "amount": 400.0}
    assert v[2]["wht"] is None


def test_evidence_linked_by_ref_id_and_missing_is_none():
    rows = [_row("2026-06-01", 100, "rent", "s1"), _row("2026-06-02", 200, "rent", "s2")]
    slips = {"s1": {"image_url": "http://img/slip1", "ref_no": "R1",
                    "transfer_date": "2026-06-01", "transfer_time": "10:00"}}
    invs = {
        "s1": {
            "image_url": "http://img/inv1",
            "invoice_no": "IV-1",
            "vendor_name": "V",
            "pages": [{"page_no": 1, "image_url": "http://img/inv1"}],
        }
    }
    v = _assemble(rows, slips=slips, invoices=invs)
    assert v[0]["slip"]["ref_no"] == "R1" and v[0]["invoice"]["invoice_no"] == "IV-1"
    assert v[1]["slip"] is None and v[1]["invoice"] is None


def test_null_ref_id_never_matches_evidence():
    rows = [_row("2026-06-01", 100, None, None, statement_id=None)]
    v = _assemble(rows, slips={"None": {"image_url": "boom"}})
    assert v[0]["slip"] is None
    assert v[0]["category_name_th"] == "ไม่ระบุ"
    assert v[0]["wht"] is None


@pytest.mark.parametrize("source", ["manual", "ap_payment"])
def test_non_statement_cash_basis_rows_keep_truthful_other_evidence(source):
    voucher = _assemble([
        _row(
            "2026-07-10",
            500,
            "other_expense",
            f"{source}-id",
            source=source,
            statement_id=None,
        )
    ])[0]

    assert voucher["source_id"] == f"{source}-id"
    assert voucher["statement_id"] is None
    assert voucher["payment_method"] == "Other"
    assert voucher["requires_slip"] is False
    assert voucher["slip"] is None
    assert voucher["invoice"] is None
    assert routes._expenses_without_required_slip("2026-07", [voucher]) == []


def test_real_statement_cash_basis_row_preserves_transfer_evidence():
    voucher = _assemble(
        [
            _row(
                "2026-07-10",
                500,
                "food_raw",
                "statement-id",
                source="vendor_purchase",
                statement_id="statement-id",
            )
        ],
        slips={"statement-id": {"image_url": "signed-slip"}},
        invoices={"statement-id": {
            "invoice_id": "invoice-id",
            "payment_type": "transfer",
            "pages": [{"page_no": 1, "image_url": "signed-invoice"}],
        }},
    )[0]

    assert voucher["source_id"] == "statement-id"
    assert voucher["statement_id"] == "statement-id"
    assert voucher["payment_method"] == "Bank Transfer"
    assert voucher["requires_slip"] is True
    assert voucher["slip"]["image_url"] == "signed-slip"
    assert voucher["invoice"]["invoice_id"] == "invoice-id"


def test_audit_cash_basis_query_identifies_only_real_statement_ids():
    class FakeCursor:
        description = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            self.description = [
                (name,)
                for name in (
                    "entry_date", "amount", "category_code", "category_name_th",
                    "counterparty", "label", "source", "ref_id", "statement_id",
                )
            ]

        def fetchall(self):
            return [
                (
                    "2026-07-01", 100, "food_raw", "วัตถุดิบ", None,
                    "statement", "vendor_purchase", "statement-id", "statement-id",
                ),
                (
                    "2026-07-02", 200, "other_expense", "อื่นๆ", None,
                    "manual", "manual", "manual-id", None,
                ),
                (
                    "2026-07-03", 300, None, "ไม่ระบุ", "Supplier",
                    "ap", "ap_payment", "ap-id", None,
                ),
            ]

    cur = FakeCursor()
    rows, statement_ids = routes._query_audit_cash_basis_rows(
        cur, "2026-07-01", "2026-07-31"
    )

    assert [row["source"] for row in rows] == [
        "vendor_purchase", "manual", "ap_payment",
    ]
    assert [row["statement_id"] for row in rows] == [
        "statement-id", None, None,
    ]
    assert statement_ids == ["statement-id"]
    assert "LEFT JOIN public.bank_statement_entries" in cur.sql
    assert "b.id::text AS statement_id" in cur.sql
    assert cur.params == ("2026-07-01", "2026-07-31")


def test_audit_cash_basis_query_preserves_public_voucher_numbering_order():
    """Same-day PV numbering is the legacy (entry_date, ref_id) contract."""
    class FakeCursor:
        description = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params
            self.description = [
                (name,)
                for name in (
                    "entry_date", "amount", "category_code", "category_name_th",
                    "counterparty", "label", "source", "ref_id", "statement_id",
                )
            ]

        def fetchall(self):
            # ref_id order deliberately conflicts with source order:
            # source-first would return z-ref before a-ref and renumber both PVs.
            return [
                (
                    "2026-07-10", 100, "other_expense", "อื่นๆ", None,
                    "manual A", "manual", "a-ref", None,
                ),
                (
                    "2026-07-10", 200, "other_expense", "อื่นๆ", "Supplier",
                    "AP Z", "ap_payment", "z-ref", None,
                ),
            ]

    cur = FakeCursor()
    rows, statement_ids = routes._query_audit_cash_basis_rows(
        cur, "2026-07-01", "2026-07-31"
    )
    vouchers = _assemble(rows)

    assert "ORDER BY d.entry_date, d.ref_id" in cur.sql
    assert "ORDER BY d.entry_date, d.source, d.ref_id" not in cur.sql
    assert statement_ids == []
    assert [
        (voucher["seq"], voucher["source_id"])
        for voucher in vouchers
    ] == [(1, "a-ref"), (2, "z-ref")]


def test_counterparty_falls_back_to_label_when_null():
    """v_daybook_pnl.counterparty is NULL for bank-sourced rows (payroll/rent/
    vendor_purchase); the payee name lives in `label` instead — the printed
    voucher must not show a blank "จ่ายให้" for a real transaction."""
    r = _row("2026-06-01", 600, "musician_fee", "x", label="K PLUS โอนไป SCB X0060 นาย ศาตราวุธ", cp=None)
    v = _assemble([r])
    assert v[0]["counterparty"] == "K PLUS โอนไป SCB X0060 นาย ศาตราวุธ"


def test_counterparty_uses_real_value_when_present():
    r = _row("2026-06-01", 600, "rent", "x", label="some label", cp="Real Landlord Co.")
    v = _assemble([r])
    assert v[0]["counterparty"] == "Real Landlord Co."


def test_invoice_pages_sort_primary_then_appendix():
    rows = [
        {"stmt_id": "s1", "invoice_id": "i1", "attachment_id": "a3", "page_no": 3, "file_url": "p3"},
        {"stmt_id": "s1", "invoice_id": "i1", "attachment_id": "a1", "page_no": 1, "file_url": "p1"},
        {"stmt_id": "s1", "invoice_id": "i1", "attachment_id": "a2", "page_no": 2, "file_url": "p2"},
    ]
    grouped = routes._group_invoice_evidence(rows, signer=lambda u: f"signed:{u}")
    assert [p["page_no"] for p in grouped["s1"]["pages"]] == [1, 2, 3]
    assert grouped["s1"]["pages"][0]["image_url"] == "signed:p1"


def test_invoice_pages_deduplicate_urls_after_deterministic_sort():
    rows = [
        {"stmt_id": "s1", "invoice_id": "i1", "attachment_id": "a2", "page_no": 2, "file_url": "same"},
        {"stmt_id": "s1", "invoice_id": "i1", "attachment_id": "a1", "page_no": 1, "file_url": "same"},
        {"stmt_id": "s1", "invoice_id": "i1", "attachment_id": "a3", "page_no": 3, "file_url": "other"},
    ]
    grouped = routes._group_invoice_evidence(rows, signer=lambda u: f"signed:{u}")
    assert grouped["s1"]["pages"] == [
        {"page_no": 1, "image_url": "signed:same"},
        {"page_no": 3, "image_url": "signed:other"},
    ]


def test_invoice_fallback_is_used_only_when_no_attachment_row_exists():
    rows = [
        {
            "stmt_id": "has-row", "invoice_id": "i1", "attachment_url": "legacy-ignored",
            "attachment_id": "a1", "page_no": 1, "file_url": None,
        },
        {
            "stmt_id": "no-row", "invoice_id": "i2", "attachment_url": "legacy-used",
            "attachment_id": None, "page_no": None, "file_url": None,
        },
    ]
    grouped = routes._group_invoice_evidence(rows, signer=lambda u: f"signed:{u}")
    assert grouped["has-row"]["pages"] == []
    assert grouped["has-row"]["image_url"] is None
    assert grouped["no-row"]["pages"] == [
        {"page_no": 1, "image_url": "signed:legacy-used"}
    ]
    assert grouped["no-row"]["image_url"] == "signed:legacy-used"


def test_payment_method_precedence_is_evidence_derived():
    assert routes._payment_method(
        {"payment_type": "other", "payment_status": "credit_card"}, "s1"
    ) == "Credit Card"
    assert routes._payment_method(
        {"payment_type": "credit_card", "payment_status": "paid"}, "s1"
    ) == "Credit Card"
    assert routes._payment_method(None, "s1") == "Bank Transfer"
    assert routes._payment_method({"payment_type": "transfer"}, None) == "Bank Transfer"
    assert routes._payment_method({"payment_type": "cash"}, None) == "Cash"
    assert routes._payment_method({"payment_type": "other"}, None) == "Other"


def test_credit_card_never_requires_slip():
    vouchers = _assemble(
        [],
        card_rows=[{
            "source_id": "i1", "entry_date": "2026-07-10", "amount": 1070,
            "vendor_name": "Makro", "invoice_no": "M-1",
            "category_code": "food_raw", "category_name_th": "วัตถุดิบ",
        }],
        card_invoices={"i1": {
            "invoice_id": "i1",
            "pages": [{"page_no": 1, "image_url": "p1"}],
            "image_url": "p1",
        }},
    )
    assert vouchers[0]["payment_method"] == "Credit Card"
    assert vouchers[0]["requires_slip"] is False
    assert vouchers[0]["slip"] is None
    assert vouchers[0]["cash_basis_included"] is False


def test_linked_credit_card_keeps_cash_basis_but_never_requires_slip():
    voucher = _assemble(
        [_row("2026-07-10", 500, "food_raw", "s1")],
        invoices={"s1": {
            "invoice_id": "i1",
            "payment_status": "credit_card",
            "payment_type": "other",
            "pages": [{"page_no": 1, "image_url": "p1"}],
            "image_url": "p1",
        }},
    )[0]
    assert voucher["payment_method"] == "Credit Card"
    assert voucher["requires_slip"] is False
    assert voucher["slip"] is None
    assert voucher["cash_basis_included"] is True


def test_standalone_credit_card_has_no_wht_even_for_wht_category():
    voucher = _assemble(
        [],
        card_rows=[{
            "source_id": "i1", "entry_date": "2026-07-10", "amount": 1000,
            "vendor_name": "Landlord", "invoice_no": "R-1",
            "category_code": "rent", "category_name_th": "ค่าเช่า",
        }],
        card_invoices={"i1": {"invoice_id": "i1", "pages": []}},
    )[0]
    assert voucher["wht"] is None


def test_card_identity_fields_are_not_exposed():
    voucher = _assemble(
        [],
        card_rows=[{
            "source_id": "i1", "entry_date": "2026-07-10", "amount": 500,
            "vendor_name": "Makro", "invoice_no": "M-1",
            "category_code": "food_raw", "category_name_th": "วัตถุดิบ",
            "issuer": "Never expose", "account": "Never expose",
            "card_number": "4111111111111111", "last_four": "1111",
        }],
        card_invoices={"i1": {"invoice_id": "i1", "pages": []}},
    )[0]
    assert not {"issuer", "account", "card_number", "last_four"} & voucher.keys()
    assert "1111" not in repr(voucher)


def test_nested_invoice_evidence_is_limited_to_public_contract_fields():
    voucher = _assemble(
        [],
        card_rows=[{
            "source_id": "i1", "entry_date": "2026-07-10", "amount": 500,
            "vendor_name": "Makro", "invoice_no": "M-1",
            "category_code": "food_raw", "category_name_th": "วัตถุดิบ",
        }],
        card_invoices={"i1": {
            "invoice_id": "i1", "invoice_no": "M-1", "vendor_name": "Makro",
            "pages": [{"page_no": 1, "image_url": "p1"}], "image_url": "p1",
            "issuer": "Never expose", "account": "Never expose",
            "card_number": "4111111111111111", "last4": "1111",
        }},
    )[0]
    assert set(voucher["invoice"]) == {
        "invoice_id", "invoice_no", "vendor_name", "pages", "image_url",
    }
    assert "1111" not in repr(voucher["invoice"])


def test_every_v2_evidence_source_type_uses_the_two_value_contract():
    vouchers = _assemble(
        [_row("2026-07-09", 800, "food_raw", "s1")],
        card_rows=[{
            "source_id": "i1", "entry_date": "2026-07-10", "amount": 500,
            "vendor_name": "Makro", "invoice_no": "M-1",
            "category_code": "food_raw", "category_name_th": "วัตถุดิบ",
        }],
        card_invoices={"i1": {"invoice_id": "i1", "pages": []}},
    )
    assert [voucher["source_type"] for voucher in vouchers] == [
        "cash_basis_expense", "credit_card_invoice",
    ]
    assert {voucher["source_type"] for voucher in vouchers} <= {
        "cash_basis_expense", "credit_card_invoice",
    }


def test_missing_slip_excludes_credit_card():
    vouchers = [
        {
            "requires_slip": True, "slip": None, "seq": 1, "date": "2026-07-01",
            "counterparty": "A", "description": "x", "amount": 100,
        },
        {
            "requires_slip": False, "slip": None, "seq": 2, "date": "2026-07-02",
            "counterparty": "B", "description": "y", "amount": 200,
        },
    ]
    missing = routes._expenses_without_required_slip("2026-07", vouchers)
    assert [row["pv"] for row in missing] == ["PV-202607-001"]


def test_card_total_does_not_change_cash_basis_total():
    summary = routes._audit_summary(
        cash_basis_expense=1000,
        petty_total=200,
        vouchers=[
            {"amount": 800, "cash_basis_included": True, "payment_method": "Bank Transfer"},
            {"amount": 500, "cash_basis_included": False, "payment_method": "Credit Card"},
        ],
    )
    assert summary["cash_basis_voucher_total"] == 800
    assert summary["supplementary_credit_card_total"] == 500
    assert summary["cash_basis_voucher_total"] + 200 == 1000
    assert summary["cash_basis_reconciliation_drift"] == 0


def test_summary_returns_factual_cash_basis_drift_without_altering_expense():
    summary = routes._audit_summary(
        cash_basis_expense=1000,
        petty_total=200,
        vouchers=[
            {"amount": 799, "cash_basis_included": True, "payment_method": "Bank Transfer"},
        ],
    )
    assert summary["expense_pnl"] == 1000
    assert summary["cash_basis_reconciliation_drift"] == -1


def test_schema_v2_payload_keeps_current_frontend_compatibility_fields():
    vouchers = [
        {
            "seq": 1, "date": "2026-07-09", "counterparty": "Supplier",
            "description": "วัตถุดิบ", "category_code": "food_raw",
            "category_name_th": "วัตถุดิบ", "amount": 800, "wht": None,
            "slip": {"image_url": "s1"}, "invoice": None,
            "source_type": "cash_basis_expense", "source_id": "s1",
            "statement_id": "s1", "payment_method": "Bank Transfer",
            "requires_slip": True, "cash_basis_included": True,
        },
        {
            "seq": 2, "date": "2026-07-10", "counterparty": "Makro",
            "description": "วัตถุดิบ", "category_code": "food_raw",
            "category_name_th": "วัตถุดิบ", "amount": 500, "wht": None,
            "slip": None,
            "invoice": {
                "invoice_id": "i1", "invoice_no": "M-1", "vendor_name": "Makro",
                "image_url": "p1", "pages": [{"page_no": 1, "image_url": "p1"}],
            },
            "source_type": "credit_card_invoice", "source_id": "i1",
            "statement_id": None, "payment_method": "Credit Card",
            "requires_slip": False, "cash_basis_included": False,
        },
    ]
    payload = routes._build_audit_package_payload(
        month="2026-07",
        generated_at="2026-07-31T12:00:00+07:00",
        income_pnl=2000,
        expense_pnl=1000,
        vouchers=vouchers,
        petty_cash=[],
        bills_without_attachment=[],
        unmatched_slips=[],
    )
    assert payload["schema_version"] == 2
    assert {
        "month", "month_label_th", "generated_at", "summary", "vouchers",
        "evidence_vouchers", "petty_cash", "missing",
    } <= payload.keys()
    assert {
        "income_pnl", "expense_pnl", "voucher_count", "voucher_total",
        "petty_count", "petty_total", "missing_counts",
    } <= payload["summary"].keys()
    assert len(payload["vouchers"]) == 1
    assert payload["vouchers"][0]["source_type"] == "cash_basis_expense"
    assert payload["summary"]["voucher_count"] == 1
    assert payload["summary"]["voucher_total"] == 800
    assert len(payload["evidence_vouchers"]) == 2
    assert payload["summary"]["evidence_voucher_count"] == 2
    assert payload["evidence_vouchers"][1]["source_type"] == "credit_card_invoice"
    assert {"date", "counterparty", "description", "category_code",
            "category_name_th", "amount", "wht", "slip", "invoice"} <= payload["evidence_vouchers"][1].keys()
    assert {"image_url", "invoice_no", "vendor_name"} <= payload["evidence_vouchers"][1]["invoice"].keys()
    assert {
        "expenses_without_slip", "bills_without_attachment", "unmatched_slips",
    } <= payload["missing"].keys()
