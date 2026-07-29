from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any, Literal

RuleOutcome = Literal["pass", "action_required", "preview_only"]
PackageStatus = Literal["ready", "action_required", "preview_only"]

RULE_HREFS = {
    "STATEMENT_MISSING": "/bank-statement",
    "STATEMENT_PERIOD_UNVERIFIED": "/bank-statement",
    "STATEMENT_REVIEW_CLEAR": "/bank-statement",
    "UNCATEGORIZED_CLEAR": "/ai-review",
    "MONTHLY_CLOSE_DANGER_CLEAR": "/alerts",
    "TRANSFER_SLIP_EVIDENCE": "/slips",
    "INVOICE_ATTACHMENT_EVIDENCE": "/invoices",
    "CREDIT_CARD_INVOICE_EVIDENCE": "/bills/payment",
}

COMMON_CODES = (
    "MONTH_ENDED",
    "STATEMENT_MISSING",
    "STATEMENT_PERIOD_UNVERIFIED",
    "STATEMENT_REVIEW_CLEAR",
    "UNCATEGORIZED_CLEAR",
    "DAYBOOK_RECONCILES",
    "MONTHLY_CLOSE_DANGER_CLEAR",
)
TAX_EVIDENCE_CODES = COMMON_CODES + (
    "TRANSFER_SLIP_EVIDENCE",
    "INVOICE_ATTACHMENT_EVIDENCE",
    "CREDIT_CARD_INVOICE_EVIDENCE",
    "IMAGE_URLS_PRESENT",
)
PND3_CODES = TAX_EVIDENCE_CODES + (
    "TAX_PROFILE_PHASE_B",
    "PND_RECIPIENT_ASSIGNMENTS_PHASE_B",
)
PND53_CODES = PND3_CODES
SHAREHOLDER_CODES = COMMON_CODES + (
    "INVOICE_ATTACHMENT_EVIDENCE",
    "CREDIT_CARD_INVOICE_EVIDENCE",
    "IMAGE_URLS_PRESENT",
    "SHAREHOLDER_PREVIEW_PHASE_C",
)


def package_status(outcomes: list[RuleOutcome]) -> PackageStatus:
    if "action_required" in outcomes:
        return "action_required"
    if "preview_only" in outcomes:
        return "preview_only"
    return "ready"


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        normalized = [_json_value(v) for v in value]
        return sorted(normalized, key=lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True))
    return value


def canonical_fingerprint(facts: dict[str, Any]) -> str:
    payload = json.dumps(
        _json_value(facts),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rule(
    code: str,
    outcome: RuleOutcome,
    message_th: str,
    count: int,
    amount: Any,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "outcome": outcome,
        "message_th": message_th,
        "count": count,
        "amount": amount,
        "href": RULE_HREFS.get(code),
        "evidence": _json_value(evidence),
    }


def _rule_outcome(passed: bool, failure: RuleOutcome) -> RuleOutcome:
    return "pass" if passed else failure


def build_readiness(month: str, branch_code: str, facts: dict[str, Any]) -> dict[str, Any]:
    statement = facts["statement"]
    statement_rows = statement["row_count"]
    statement_exists = statement_rows > 0
    month_end = facts["month_end"]

    statement_review = facts["statement_needs_review"]
    uncategorized = facts["uncategorized"]
    reconciliation = facts["reconciliation"]
    monthly_close = facts["monthly_close_dangers"]
    transfer_evidence = facts["transfer_evidence"]
    invoice_evidence = facts["invoice_evidence"]
    credit_cards = facts["credit_cards"]
    image_url_failures = facts["image_url_failures"]
    wht_candidates = facts.get("wht_candidates", {"count": 0, "amount": 0})

    rules: dict[str, dict[str, Any]] = {
        "MONTH_ENDED": _rule(
            "MONTH_ENDED",
            _rule_outcome(facts["today"] > month_end, "preview_only"),
            "เดือนบัญชีสิ้นสุดแล้ว",
            0,
            None,
            {"today": facts["today"], "month_end": month_end},
        ),
        "STATEMENT_MISSING": _rule(
            "STATEMENT_MISSING",
            _rule_outcome(statement_exists, "action_required"),
            "พบ Statement แล้ว" if statement_exists else "ไม่พบ Statement ของเดือนนี้",
            statement_rows,
            None,
            {
                "batch_count": statement["batch_count"],
                "first_observed_date": statement["first_observed_date"],
                "last_observed_date": statement["last_observed_date"],
            },
        ),
        "STATEMENT_REVIEW_CLEAR": _rule(
            "STATEMENT_REVIEW_CLEAR",
            _rule_outcome(statement_review["count"] == 0, "action_required"),
            "ไม่มีรายการ Statement ที่ต้องตรวจสอบ"
            if statement_review["count"] == 0
            else "มีรายการ Statement ที่ต้องตรวจสอบ",
            statement_review["count"],
            statement_review["amount"],
            {},
        ),
        "UNCATEGORIZED_CLEAR": _rule(
            "UNCATEGORIZED_CLEAR",
            _rule_outcome(uncategorized["count"] == 0, "action_required"),
            "ไม่มีรายจ่ายที่ยังไม่จัดหมวด"
            if uncategorized["count"] == 0
            else "มีรายจ่ายที่ยังไม่จัดหมวด",
            uncategorized["count"],
            uncategorized["amount"],
            {},
        ),
        "DAYBOOK_RECONCILES": _rule(
            "DAYBOOK_RECONCILES",
            _rule_outcome(abs(reconciliation["drift"]) <= 0.01, "action_required"),
            "Daybook กระทบยอดแล้ว"
            if abs(reconciliation["drift"]) <= 0.01
            else "Daybook ยังไม่กระทบยอด",
            0 if abs(reconciliation["drift"]) <= 0.01 else 1,
            abs(reconciliation["drift"]),
            {"ok": reconciliation["ok"], "drift": reconciliation["drift"]},
        ),
        "MONTHLY_CLOSE_DANGER_CLEAR": _rule(
            "MONTHLY_CLOSE_DANGER_CLEAR",
            _rule_outcome(monthly_close["count"] == 0, "action_required"),
            "ไม่มีความเสี่ยงระดับ danger ก่อนปิดเดือน"
            if monthly_close["count"] == 0
            else "มีความเสี่ยงระดับ danger ก่อนปิดเดือน",
            monthly_close["count"],
            monthly_close["amount"],
            {},
        ),
        "TRANSFER_SLIP_EVIDENCE": _rule(
            "TRANSFER_SLIP_EVIDENCE",
            _rule_outcome(transfer_evidence["missing_slip_count"] == 0, "action_required"),
            "หลักฐานสลิปการโอนครบ"
            if transfer_evidence["missing_slip_count"] == 0
            else "ยังขาดหลักฐานสลิปการโอน",
            transfer_evidence["missing_slip_count"],
            transfer_evidence["missing_slip_amount"],
            {"scope": "transfer_only"},
        ),
        "INVOICE_ATTACHMENT_EVIDENCE": _rule(
            "INVOICE_ATTACHMENT_EVIDENCE",
            _rule_outcome(invoice_evidence["missing_page_count"] == 0, "action_required"),
            "ไฟล์แนบใบกำกับครบ"
            if invoice_evidence["missing_page_count"] == 0
            else "ยังขาดไฟล์แนบใบกำกับ",
            invoice_evidence["missing_page_count"],
            invoice_evidence["missing_page_amount"],
            {},
        ),
        "CREDIT_CARD_INVOICE_EVIDENCE": _rule(
            "CREDIT_CARD_INVOICE_EVIDENCE",
            _rule_outcome(credit_cards["missing_invoice_page_count"] == 0, "action_required"),
            "ใบกำกับของบัตรเครดิตครบ"
            if credit_cards["missing_invoice_page_count"] == 0
            else "ยังขาดใบกำกับของบัตรเครดิต",
            credit_cards["missing_invoice_page_count"],
            credit_cards["missing_invoice_page_amount"],
            {"confirmed_count": credit_cards["confirmed_count"]},
        ),
        "IMAGE_URLS_PRESENT": _rule(
            "IMAGE_URLS_PRESENT",
            _rule_outcome(image_url_failures["count"] == 0, "action_required"),
            "URL รูปภาพครบ" if image_url_failures["count"] == 0 else "มี URL รูปภาพที่ขาดหาย",
            image_url_failures["count"],
            None,
            {},
        ),
        "TAX_PROFILE_PHASE_B": _rule(
            "TAX_PROFILE_PHASE_B",
            "action_required",
            "Tax profile เริ่มใน Phase B",
            1,
            None,
            {"availability": "phase_b"},
        ),
        "PND_RECIPIENT_ASSIGNMENTS_PHASE_B": _rule(
            "PND_RECIPIENT_ASSIGNMENTS_PHASE_B",
            "action_required",
            "การกำหนดผู้รับ ภ.ง.ด. เริ่มใน Phase B",
            wht_candidates["count"],
            wht_candidates["amount"],
            {"availability": "phase_b"},
        ),
        "SHAREHOLDER_PREVIEW_PHASE_C": _rule(
            "SHAREHOLDER_PREVIEW_PHASE_C",
            "action_required",
            "รายงานผู้ถือหุ้นเริ่มใน Phase C",
            1,
            None,
            {"availability": "phase_c"},
        ),
    }

    if statement_exists:
        rules["STATEMENT_PERIOD_UNVERIFIED"] = _rule(
            "STATEMENT_PERIOD_UNVERIFIED",
            _rule_outcome(statement["declared_period_verified"] is True, "preview_only"),
            "ยืนยันช่วงวันที่จากหัว Statement แล้ว"
            if statement["declared_period_verified"] is True
            else "ยังไม่มีช่วงวันที่จากหัว Statement ที่ยืนยันได้",
            statement_rows,
            None,
            {
                "first_observed_date": statement["first_observed_date"],
                "last_observed_date": statement["last_observed_date"],
                "month_end": month_end,
                "declared_period_verified": statement["declared_period_verified"],
            },
        )

    def package(codes: tuple[str, ...], availability: str | None = None) -> dict[str, Any]:
        package_rules = [rules[code] for code in codes if code in rules]
        result: dict[str, Any] = {
            "status": package_status([rule["outcome"] for rule in package_rules]),
            "rules": package_rules,
        }
        if availability is not None:
            result["availability"] = availability
        return result

    return {
        "schema_version": 1,
        "month": month,
        "branch_code": branch_code,
        "coverage": {
            "statement": {
                "basis": "observed_transactions_only",
                "row_count": statement_rows,
                "batch_count": statement["batch_count"],
                "first_observed_date": _json_value(statement["first_observed_date"]),
                "last_observed_date": _json_value(statement["last_observed_date"]),
                "month_end": _json_value(month_end),
                "declared_period_verified": statement["declared_period_verified"],
            }
        },
        "packages": {
            "common_accounting": package(COMMON_CODES),
            "tax_evidence": package(TAX_EVIDENCE_CODES),
            "pnd3": package(PND3_CODES, "phase_b"),
            "pnd53": package(PND53_CODES, "phase_b"),
            "shareholder": package(SHAREHOLDER_CODES, "phase_c"),
        },
    }
