"""OCR verification and invoice reconciliation domain helpers.

Pure functions/classes live here so route handlers can stay thin and tests can
run without Supabase, OpenAI, or uploaded files.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from time import perf_counter
from typing import Any, Protocol


MONEY = Decimal("0.01")
DEFAULT_TOLERANCE = Decimal("0.05")
LOW_CONFIDENCE_THRESHOLD = Decimal("0.60")


def _decimal(value: Any, default: Decimal | None = Decimal("0")) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value).replace(",", "")).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return default


def _money(value: Decimal | None) -> str:
    return str((value or Decimal("0")).quantize(MONEY, rounding=ROUND_HALF_UP))


def _rate(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(Decimal(str(value).replace(",", "")).quantize(Decimal("0.0001")))
    except (InvalidOperation, ValueError):
        return None


def normalize_ocr_extraction(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize monetary OCR fields without changing legacy field meanings."""
    out = dict(parsed or {})
    money_fields = [
        "subtotal",
        "vat",
        "amount",
        "bill_discount_total",
        "voucher_discount_total",
        "promotion_discount_total",
        "service_charge",
        "rounding_adjustment",
    ]
    for field in money_fields:
        if field in out:
            out[field] = _money(_decimal(out.get(field)))

    items = []
    for item in out.get("items") or []:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        for field in ("gross_amount", "line_discount_amount", "net_amount", "amount", "unit_price"):
            if field in normalized:
                normalized[field] = _money(_decimal(normalized.get(field)))
        if "line_discount_rate" in normalized:
            normalized["line_discount_rate"] = _rate(normalized.get("line_discount_rate"))
        items.append(normalized)
    out["items"] = items
    return out


def _item_gross_and_net(item: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    line_discount = _decimal(item.get("line_discount_amount")) or Decimal("0")
    gross = _decimal(item.get("gross_amount"), default=None)
    net = _decimal(item.get("net_amount"), default=None)
    amount = _decimal(item.get("amount"), default=None)

    if gross is None:
        qty = _decimal(item.get("quantity"), default=None)
        unit_price = _decimal(item.get("unit_price"), default=None)
        if qty is not None and unit_price is not None:
            gross = (qty * unit_price).quantize(MONEY, rounding=ROUND_HALF_UP)
        elif amount is not None:
            gross = (amount + line_discount).quantize(MONEY, rounding=ROUND_HALF_UP)
        else:
            gross = Decimal("0")

    if net is None:
        if amount is not None:
            net = amount
        else:
            net = (gross - line_discount).quantize(MONEY, rounding=ROUND_HALF_UP)

    return gross, line_discount, net


def calculate_reconciliation(
    invoice: dict[str, Any],
    items: list[dict[str, Any]] | None,
    *,
    tolerance: Decimal | str | float = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """Calculate discount-aware invoice totals using Decimal arithmetic."""
    tol = _decimal(tolerance) or DEFAULT_TOLERANCE
    rows = [it for it in (items or []) if isinstance(it, dict)]

    gross_item_total = Decimal("0")
    line_discount_total = Decimal("0")
    net_item_total = Decimal("0")
    for item in rows:
        gross, line_discount, net = _item_gross_and_net(item)
        gross_item_total += gross
        line_discount_total += line_discount
        net_item_total += net

    if not rows:
        subtotal = _decimal(invoice.get("subtotal"), default=None)
        if subtotal is not None:
            gross_item_total = subtotal
            net_item_total = subtotal

    bill_discount_total = _decimal(invoice.get("bill_discount_total")) or Decimal("0")
    voucher_discount_total = _decimal(invoice.get("voucher_discount_total")) or Decimal("0")
    promotion_discount_total = _decimal(invoice.get("promotion_discount_total")) or Decimal("0")
    service_charge = _decimal(invoice.get("service_charge")) or Decimal("0")
    vat = _decimal(invoice.get("vat")) or Decimal("0")
    rounding_adjustment = _decimal(invoice.get("rounding_adjustment")) or Decimal("0")
    stated_total = _decimal(invoice.get("amount"), default=None)

    calculated_total = (
        net_item_total
        - bill_discount_total
        - voucher_discount_total
        - promotion_discount_total
        + service_charge
        + vat
        + rounding_adjustment
    ).quantize(MONEY, rounding=ROUND_HALF_UP)

    warnings: list[dict[str, str]] = []
    if stated_total is None:
        status = "needs_review"
        difference = Decimal("0")
        blocking = False
        warnings.append({
            "severity": "error",
            "code": "MISSING_TOTAL",
            "message": "invoice total is missing",
            "field": "amount",
        })
    else:
        difference = (calculated_total - stated_total).quantize(MONEY, rounding=ROUND_HALF_UP)
        blocking = abs(difference) > tol
        status = "mismatch" if blocking else "matched"
        if blocking:
            warnings.append({
                "severity": "error",
                "code": "RECONCILIATION_MISMATCH",
                "message": f"calculated total {_money(calculated_total)} does not match stated total {_money(stated_total)}",
                "field": "amount",
            })

    components = {
        "gross_item_total": _money(gross_item_total),
        "line_discount_total": _money(line_discount_total),
        "net_item_total": _money(net_item_total),
        "bill_discount_total": _money(bill_discount_total),
        "voucher_discount_total": _money(voucher_discount_total),
        "promotion_discount_total": _money(promotion_discount_total),
        "service_charge": _money(service_charge),
        "vat": _money(vat),
        "rounding_adjustment": _money(rounding_adjustment),
        "calculated_total": _money(calculated_total),
        "stated_total": _money(stated_total),
    }
    return {
        "status": status,
        "blocking": blocking,
        "components": components,
        "calculated_total": components["calculated_total"],
        "stated_total": components["stated_total"],
        "difference": _money(difference),
        "tolerance": _money(tol),
        "warnings": warnings,
    }


class InvoiceVerifierProvider(Protocol):
    name: str
    model: str | None

    def verify(
        self,
        *,
        pages: list[dict[str, Any]],
        raw_ocr_text: str,
        structured_ocr: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class MockInvoiceVerifier:
    """Deterministic adapter used until a real provider is explicitly enabled."""

    name = "mock"
    model = "mock-not-real-ai"

    def __init__(self, mode: str = "not_configured") -> None:
        self.mode = mode

    def verify(
        self,
        *,
        pages: list[dict[str, Any]],
        raw_ocr_text: str,
        structured_ocr: dict[str, Any],
    ) -> dict[str, Any]:
        if self.mode == "failure":
            raise RuntimeError("mock verifier failure")
        if self.mode == "timeout":
            raise TimeoutError("mock verifier timeout")
        if self.mode == "success":
            return {
                "status": "verified",
                "provider": self.name,
                "model": self.model,
                "confidence": "1.0000",
                "is_real_ai": False,
                "field_results": [],
                "mismatches": [],
                "warnings": [],
            }
        return {
            "status": "not_configured",
            "provider": self.name,
            "model": self.model,
            "confidence": None,
            "is_real_ai": False,
            "field_results": [],
            "mismatches": [],
            "warnings": [{
                "severity": "warn",
                "code": "AI_VERIFIER_NOT_CONFIGURED",
                "message": "AI verifier provider is not configured; no real AI verification was performed",
                "field": "verification_status",
            }],
        }


def run_invoice_verification(
    provider: InvoiceVerifierProvider,
    *,
    pages: list[dict[str, Any]],
    raw_ocr_text: str,
    structured_ocr: dict[str, Any],
) -> dict[str, Any]:
    """Run a verifier adapter and convert failures into review-safe results."""
    started = perf_counter()
    try:
        result = provider.verify(
            pages=pages,
            raw_ocr_text=raw_ocr_text,
            structured_ocr=structured_ocr,
        )
        result = dict(result)
    except TimeoutError as exc:
        result = {
            "status": "timeout",
            "provider": getattr(provider, "name", "unknown"),
            "model": getattr(provider, "model", None),
            "confidence": None,
            "is_real_ai": False,
            "field_results": [],
            "mismatches": [],
            "error_code": "AI_VERIFIER_TIMEOUT",
            "error_message": str(exc),
            "warnings": [{
                "severity": "warn",
                "code": "AI_VERIFIER_TIMEOUT",
                "message": "AI verifier timed out; invoice requires human review",
                "field": "verification_status",
            }],
        }
    except Exception as exc:
        result = {
            "status": "failed",
            "provider": getattr(provider, "name", "unknown"),
            "model": getattr(provider, "model", None),
            "confidence": None,
            "is_real_ai": False,
            "field_results": [],
            "mismatches": [],
            "error_code": "AI_VERIFIER_FAILED",
            "error_message": str(exc),
            "warnings": [{
                "severity": "warn",
                "code": "AI_VERIFIER_FAILED",
                "message": "AI verifier failed; invoice requires human review",
                "field": "verification_status",
            }],
        }
    result["latency_ms"] = int((perf_counter() - started) * 1000)
    return result


def decide_review_status(
    verification: dict[str, Any],
    reconciliation: dict[str, Any],
    *,
    existing_review_status: str | None,
) -> dict[str, str]:
    """Choose additive verification status without auto-confirming invoices."""
    current = existing_review_status or "pending"
    if current in ("confirmed", "rejected"):
        review_status = current
    elif reconciliation.get("status") == "mismatch":
        review_status = "needs_attention"
    elif verification.get("status") in ("not_configured", "failed", "timeout"):
        review_status = "needs_attention"
    else:
        review_status = current

    if reconciliation.get("status") == "mismatch":
        verification_status = "mismatch"
    else:
        verification_status = str(verification.get("status") or "not_run")

    return {
        "review_status": review_status,
        "verification_status": verification_status,
    }


def build_approval_blockers(
    verification: dict[str, Any] | None,
    reconciliation: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Return blockers that require force-confirm; old invoices produce none."""
    blockers: list[dict[str, str]] = []
    verification = verification or {}
    reconciliation = reconciliation or {}

    status = str(verification.get("status") or "")
    confidence_raw = verification.get("confidence")
    confidence = _decimal(confidence_raw, default=None)
    if status == "low_confidence" or (
        confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD
    ):
        blockers.append({
            "severity": "error",
            "code": "LOW_CONFIDENCE",
            "message": "AI verifier confidence is below the approval threshold",
            "field": "verification_confidence",
        })

    if status == "mismatch":
        blockers.append({
            "severity": "error",
            "code": "AI_VERIFIER_MISMATCH",
            "message": "AI verifier found field-level mismatches",
            "field": "verification_status",
        })

    if reconciliation.get("status") == "mismatch" or reconciliation.get("blocking") is True:
        blockers.append({
            "severity": "error",
            "code": "RECONCILIATION_MISMATCH",
            "message": "Invoice reconciliation has a blocking mismatch",
            "field": "amount",
        })

    return blockers


def build_force_confirm_warning(
    *,
    actor: str | None,
    reason: str,
    blockers: list[dict[str, Any]],
) -> dict[str, str]:
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ValueError("force confirm reason is required")
    codes = ", ".join(str(b.get("code") or "UNKNOWN") for b in blockers) or "UNKNOWN"
    who = actor or "unknown"
    return {
        "severity": "warn",
        "code": "FORCE_CONFIRMED_VERIFICATION_BLOCK",
        "message": f"{who} force-confirmed despite {codes}: {clean_reason}",
        "field": "review_status",
    }
