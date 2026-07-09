"""
VEXONHQ — Monthly Close Risk Marking V1
========================================
Spec: docs/superpowers/specs/2026-07-09-monthly-close-risk-marking-v1-design.md

Read-only risk detection for the month-close workflow. Surfaces accounting risks
(unreviewed statement rows, stale rider-income classifications, missing platform
exports, ambiguous settlements, duplicate statement rows) so they are visible
DURING close instead of being discovered months later.

Hard scope boundaries (V1):
  - Read-only. NEVER mutates bank_statement_entries / pos_bills / pos_imports.
    The only table this module writes is public.monthly_close_risks.
  - No auto-reclassification, no month locking, no P&L v2, no payout reconciliation,
    no ignore/dismiss endpoint.
  - LINE push only for open `danger` risks, throttled to once per 24h via
    last_line_sent_at. Never for warning/info.

Endpoints (both admin-only via auth_routes._require_admin_role):
  POST /monthly-close/check   — run checks, upsert risks, resolve stale, maybe LINE
  GET  /monthly-close/risks   — list stored risks + counts

Design note: the risk RULES (build_*), the open/resolved/LINE reconciliation
(plan_risk_sync) and the LINE decision are pure functions so they can be unit
tested without a database. The route layer only runs SQL and applies the plan.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Query

from auth_routes import _require_admin_role

try:
    from main import get_db_conn  # type: ignore[import]
except ImportError:  # pragma: no cover - import fallback for standalone/tests
    def get_db_conn():  # type: ignore[misc]
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("monthly_close")

router = APIRouter(tags=["monthly-close"])

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_BRANCH = "thawi_watthana"
V1_LINK = "/alerts"                 # /alerts is the only frontend surface in V1
LINE_COOLDOWN = timedelta(hours=24)
EVIDENCE_LIST_CAP = 10              # spec: cap every list inside evidence at 10 items

# Exact risk_key literals allowed in V1 (spec).
RISK_BANK_NEEDS_REVIEW = "bank_needs_review"
RISK_BANK_RIDER_INCOME = "bank_rider_income"
RISK_MISSING_GRAB = "missing_platform_export_grab"
RISK_MISSING_LINEMAN = "missing_platform_export_lineman"
RISK_AMBIGUOUS = "ambiguous_settlement"
RISK_DUPLICATE = "duplicate_statement"

# R3 platform config: (platform, pos payment_type_raw literal, pos_imports report_type, risk_key, label)
# TUM explicitly confirmed this POS mapping for the bill-detail export. If the POS
# meaning of "K Plus shop" changes to generic storefront QR later, downgrade the
# Grab missing-export rule before enabling LINE for it.
R3_PLATFORMS = (
    ("grab", "K Plus shop", "grab_transaction", RISK_MISSING_GRAB, "Grab"),
    ("lineman", "Line Man - Rabbit Linepay", "lineman_daily", RISK_MISSING_LINEMAN, "LINE MAN"),
)

# R4 ambiguous-settlement description keywords (matched case-insensitively).
R4_KEYWORDS = ("LINE PAY", "Thai Line Pay", "บจก. แกร็บแท็กซี่")


# ── Month validation ─────────────────────────────────────────────────────────

def validate_month(month: str) -> tuple[date, date]:
    """Validate a 'YYYY-MM' string and return (month_start, month_end) dates.

    Raises HTTPException(400) on any malformed / out-of-range month.
    """
    if not month or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
    year, mon = int(month[:4]), int(month[5:7])
    if not (1 <= mon <= 12):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format (month 01-12)")
    mstart = date(year, mon, 1)
    if mon == 12:
        mend = date(year, 12, 31)
    else:
        mend = date(year, mon + 1, 1) - timedelta(days=1)
    return mstart, mend


# ── Evidence helpers ─────────────────────────────────────────────────────────

def _cap(items: list) -> list:
    """Limit an evidence list to at most EVIDENCE_LIST_CAP items (spec)."""
    return list(items[:EVIDENCE_LIST_CAP])


def _f(value: Any) -> float:
    """Coerce a numeric/Decimal/None to float."""
    return float(value or 0)


# ── V1 Risk rule builders (pure) ─────────────────────────────────────────────
# Each builder takes already-fetched SQL rows and returns a risk dict, or None
# when the rule does not fire. A risk dict maps onto monthly_close_risks columns.

def build_bank_needs_review_risk(rows: list) -> Optional[dict]:
    """R1 (danger): bank_statement_entries.match_status='needs_review' in month.

    rows: iterable of (id, description, abs_amount).
    """
    rows = list(rows)
    if not rows:
        return None
    count = len(rows)
    total = sum(_f(r[2]) for r in rows)
    examples = [{"id": str(r[0]), "description": r[1]} for r in _cap(rows)]
    return {
        "risk_key": RISK_BANK_NEEDS_REVIEW,
        "severity": "danger",
        "title": "Statement รอจัดหมวด",
        "message": f"Statement รอจัดหมวด {count} รายการ / ฿{total:,.0f}",
        "amount": total,
        "evidence": {"count": count, "sum_abs_amount": total, "examples": examples},
        "link": V1_LINK,
    }


def build_bank_rider_income_risk(rows: list) -> Optional[dict]:
    """R2 (danger): bank rows still classified as rider income in month.

    rows: iterable of (id, description, source_type, credit, debit).
    """
    rows = list(rows)
    if not rows:
        return None
    count = len(rows)
    sum_credit = sum(_f(r[3]) for r in rows)
    sum_debit = sum(_f(r[4]) for r in rows)
    total = sum_credit + sum_debit
    examples = [
        {"id": str(r[0]), "description": r[1], "source_type": r[2]}
        for r in _cap(rows)
    ]
    return {
        "risk_key": RISK_BANK_RIDER_INCOME,
        "severity": "danger",
        "title": "Statement ยังเป็นรายได้ไรเดอร์",
        "message": f"Bank rows ยัง classified เป็นรายได้ไรเดอร์ {count} รายการ / ฿{total:,.0f}",
        "amount": total,
        "evidence": {
            "count": count,
            "sum_credit": sum_credit,
            "sum_debit": sum_debit,
            "examples": examples,
        },
        "link": V1_LINK,
    }


def build_missing_platform_export_risk(
    platform: str,
    label: str,
    risk_key: str,
    pos_count: int,
    pos_sum: float,
    import_count: int,
) -> Optional[dict]:
    """R3 (danger): POS shows a delivery channel but the platform export is missing.

    Fires only when POS delivery evidence exists (pos_count > 0) AND there is zero
    successful platform import overlapping the month (import_count == 0). Month-level
    sanity check only — no daily completeness in V1.
    """
    if pos_count <= 0 or import_count > 0:
        return None
    return {
        "risk_key": risk_key,
        "severity": "danger",
        "title": f"POS มี {label} แต่ไม่มี {label} export",
        "message": f"POS มี {label} {pos_count} บิล / ฿{pos_sum:,.0f} แต่ยังไม่มี {label} export เดือนนี้",
        "amount": _f(pos_sum),
        "evidence": {
            "platform": platform,
            "pos_count": int(pos_count),
            "pos_sum": _f(pos_sum),
            "matching_import_count": int(import_count),
        },
        "link": V1_LINK,
    }


def build_ambiguous_settlement_risk(rows: list) -> Optional[dict]:
    """R4 (warning): statement text looks like a settlement keyword but the row is
    not already a known/reviewed payout. Web-only — never LINE.

    rows: iterable of (id, description, source_type, abs_amount).
    """
    rows = list(rows)
    if not rows:
        return None
    count = len(rows)
    total = sum(_f(r[3]) for r in rows)
    examples = [
        {"id": str(r[0]), "description": r[1], "source_type": r[2]}
        for r in _cap(rows)
    ]
    return {
        "risk_key": RISK_AMBIGUOUS,
        "severity": "warning",
        "title": "ข้อความ settlement กำกวม",
        "message": f"Statement มีข้อความคล้าย settlement {count} รายการ / ฿{total:,.0f} (ยังไม่ยืนยันเป็น payout)",
        "amount": total,
        "evidence": {"count": count, "sum_abs_amount": total, "examples": examples},
        "link": V1_LINK,
    }


def build_duplicate_statement_risk(groups: list) -> Optional[dict]:
    """R5 (warning): duplicate statement rows within the month.

    groups: iterable of (txn_date, description, debit, credit, balance, branch_code, cnt)
    where cnt > 1 (grouped duplicates).
    """
    groups = list(groups)
    if not groups:
        return None
    group_count = len(groups)
    dup_row_count = sum(int(g[6]) for g in groups)               # total rows in dup groups
    extra_copies = sum(int(g[6]) - 1 for g in groups)            # duplicate copies beyond the first
    total_dup_amount = sum((int(g[6]) - 1) * (abs(_f(g[2])) + abs(_f(g[3]))) for g in groups)
    examples = [
        {
            "txn_date": g[0].isoformat() if hasattr(g[0], "isoformat") else str(g[0]),
            "description": g[1],
            "debit": _f(g[2]),
            "credit": _f(g[3]),
            "balance": _f(g[4]),
            "count": int(g[6]),
        }
        for g in _cap(groups)
    ]
    return {
        "risk_key": RISK_DUPLICATE,
        "severity": "warning",
        "title": "Statement ซ้ำ",
        "message": f"พบ statement ซ้ำ {group_count} กลุ่ม ({extra_copies} รายการเกิน) / ฿{total_dup_amount:,.0f}",
        "amount": total_dup_amount,
        "evidence": {
            "duplicate_group_count": group_count,
            "duplicate_row_count": dup_row_count,
            "total_duplicate_amount": total_dup_amount,
            "examples": examples,
        },
        "link": V1_LINK,
    }


# ── Reconciliation (pure) ────────────────────────────────────────────────────

def plan_risk_sync(existing_rows: list, detected: list, now: datetime) -> dict:
    """Given the currently-stored risks and the freshly-detected risks, decide:
      - which risks to upsert (all detected -> status 'open'),
      - which previously-open risks to resolve (open in DB but no longer detected),
      - which detected danger risks should trigger a LINE push (respecting the
        24h per-risk cooldown from last_line_sent_at).

    existing_rows: list of dicts with keys: risk_key, status, last_line_sent_at.
    detected:      list of risk dicts from the build_* functions.
    now:           timezone-aware 'now'.
    """
    existing_by_key = {r["risk_key"]: r for r in existing_rows}
    detected_keys = {d["risk_key"] for d in detected}

    resolve_keys = [
        r["risk_key"]
        for r in existing_rows
        if r["status"] == "open" and r["risk_key"] not in detected_keys
    ]

    line_targets = []
    for d in detected:
        if d["severity"] != "danger":
            continue
        ex = existing_by_key.get(d["risk_key"])
        last = ex.get("last_line_sent_at") if ex else None
        if last is None or last < now - LINE_COOLDOWN:
            line_targets.append(d)

    return {
        "upserts": list(detected),
        "resolve_keys": resolve_keys,
        "line_targets": line_targets,
    }


# ── LINE (pure message + injectable send) ────────────────────────────────────

def format_line_message(risks: list, month: str, branch_code: str) -> str:
    """Build the short, actionable LINE body for one or more danger risks."""
    lines = [
        "Monthly Close Critical Risk",
        f"เดือน: {month}",
        f"สาขา: {branch_code}",
    ]
    for r in risks:
        lines.append(f"- {r['message']}")
    lines.append(f"เปิดดู: {V1_LINK}")
    return "\n".join(lines)


def send_danger_line(
    line_targets: list,
    month: str,
    branch_code: str,
    push_fn=None,
) -> bool:
    """Send a single LINE message covering all danger risks in line_targets.

    Returns True only when the push succeeded (so the caller updates
    last_line_sent_at). Any failure returns False and leaves timestamps untouched
    — a LINE failure must never break the risk check.
    """
    if not line_targets:
        return False
    if push_fn is None:
        from line_bot_routes import _push_text as push_fn  # lazy import (env-gated helper)
    message = format_line_message(line_targets, month, branch_code)
    try:
        push_fn(message)
        return True
    except Exception:
        log.exception("monthly-close: LINE push failed — last_line_sent_at NOT updated")
        return False


# ── SQL: detection (read-only) ───────────────────────────────────────────────

def run_all_checks(conn, mstart: date, mend: date, branch_code: str) -> list[dict]:
    """Run every V1 risk rule against the DB and return the list of detected risks.

    Strictly read-only — SELECT only, no writes to source tables.
    """
    detected: list[dict] = []
    with conn.cursor() as cur:
        # ── R1: bank statement needs review ──
        cur.execute(
            """
            SELECT id, description, ABS(COALESCE(amount, 0))
            FROM public.bank_statement_entries
            WHERE match_status = 'needs_review'
              AND txn_date BETWEEN %s AND %s
              AND branch_code = %s
            ORDER BY txn_date
            """,
            (mstart, mend, branch_code),
        )
        r = build_bank_needs_review_risk(cur.fetchall())
        if r:
            detected.append(r)

        # ── R2: bank rows still classified as rider income ──
        cur.execute(
            """
            SELECT id, description, source_type,
                   COALESCE(credit, 0), COALESCE(debit, 0)
            FROM public.bank_statement_entries
            WHERE source_type IN ('rider_income_grab', 'rider_income_lineman')
              AND txn_date BETWEEN %s AND %s
              AND branch_code = %s
            ORDER BY txn_date
            """,
            (mstart, mend, branch_code),
        )
        r = build_bank_rider_income_risk(cur.fetchall())
        if r:
            detected.append(r)

        # ── R3: POS shows delivery channel but platform export missing ──
        for platform, pt_value, report_type, risk_key, label in R3_PLATFORMS:
            # POS delivery evidence for the month (bill_net — verified real column;
            # spec said net_total "if available" but that column does not exist).
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(bill_net), 0)
                FROM public.pos_bills
                WHERE payment_type_raw = %s
                  AND sales_date BETWEEN %s AND %s
                  AND branch_code = %s
                """,
                (pt_value, mstart, mend, branch_code),
            )
            pos_count, pos_sum = cur.fetchone()
            # Successful platform imports whose period overlaps the month.
            cur.execute(
                """
                SELECT COUNT(*)
                FROM public.pos_imports
                WHERE report_type = %s
                  AND status = 'success'
                  AND branch_code = %s
                  AND period_start <= %s
                  AND period_end >= %s
                """,
                (report_type, branch_code, mend, mstart),
            )
            (import_count,) = cur.fetchone()
            r = build_missing_platform_export_risk(
                platform, label, risk_key,
                int(pos_count), _f(pos_sum), int(import_count),
            )
            if r:
                detected.append(r)

        # ── R4: ambiguous settlement keywords (web-only warning) ──
        cur.execute(
            """
            SELECT id, description, source_type, ABS(COALESCE(amount, 0))
            FROM public.bank_statement_entries
            WHERE txn_date BETWEEN %s AND %s
              AND branch_code = %s
              AND (description ILIKE %s OR description ILIKE %s OR description ILIKE %s)
              AND NOT (
                    match_status = 'manual'
                    AND source_type IN ('grab_payout', 'lineman_payout', 'payment_gateway_payout')
                    AND category_code IN ('delivery_grab', 'delivery_lineman', 'payment_gateway')
              )
              AND COALESCE(source_type, '') NOT IN ('grab_payout', 'lineman_payout', 'payment_gateway_payout')
            ORDER BY txn_date
            """,
            (mstart, mend, branch_code,
             f"%{R4_KEYWORDS[0]}%", f"%{R4_KEYWORDS[1]}%", f"%{R4_KEYWORDS[2]}%"),
        )
        r = build_ambiguous_settlement_risk(cur.fetchall())
        if r:
            detected.append(r)

        # ── R5: duplicate statement rows ──
        cur.execute(
            """
            SELECT txn_date, description,
                   COALESCE(debit, 0), COALESCE(credit, 0), COALESCE(balance, 0),
                   branch_code, COUNT(*)
            FROM public.bank_statement_entries
            WHERE txn_date BETWEEN %s AND %s
              AND branch_code = %s
            GROUP BY txn_date, description, debit, credit, balance, branch_code
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            """,
            (mstart, mend, branch_code),
        )
        r = build_duplicate_statement_risk(cur.fetchall())
        if r:
            detected.append(r)

    return detected


# ── SQL: persistence (writes ONLY monthly_close_risks) ───────────────────────

def _fetch_existing(conn, month: str, branch_code: str) -> list[dict]:
    """Load current stored risks (all statuses) for the branch/month."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT risk_key, status, last_line_sent_at
            FROM public.monthly_close_risks
            WHERE branch_code = %s AND month = %s
            """,
            (branch_code, month),
        )
        return [
            {"risk_key": row[0], "status": row[1], "last_line_sent_at": row[2]}
            for row in cur.fetchall()
        ]


def _apply_upserts(conn, month: str, branch_code: str, upserts: list, now: datetime) -> None:
    """Upsert detected risks. On conflict re-open and refresh content but PRESERVE
    first_seen_at and last_line_sent_at (so the 24h LINE cooldown survives a
    resolve->reopen cycle)."""
    if not upserts:
        return
    with conn.cursor() as cur:
        for d in upserts:
            cur.execute(
                """
                INSERT INTO public.monthly_close_risks
                    (branch_code, month, risk_key, severity, status, title, message,
                     amount, evidence, link, first_seen_at, last_seen_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'open', %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                ON CONFLICT (branch_code, month, risk_key) DO UPDATE SET
                    severity     = EXCLUDED.severity,
                    status       = 'open',
                    title        = EXCLUDED.title,
                    message      = EXCLUDED.message,
                    amount       = EXCLUDED.amount,
                    evidence     = EXCLUDED.evidence,
                    link         = EXCLUDED.link,
                    last_seen_at = EXCLUDED.last_seen_at,
                    resolved_at  = NULL,
                    resolved_by  = NULL,
                    updated_at   = EXCLUDED.updated_at
                """,
                (
                    branch_code, month, d["risk_key"], d["severity"], d["title"],
                    d["message"], d["amount"],
                    json.dumps(d["evidence"], ensure_ascii=False), d["link"],
                    now, now, now, now,
                ),
            )


def _apply_resolves(conn, month: str, branch_code: str, resolve_keys: list, now: datetime) -> None:
    """Mark previously-open risks that are no longer detected as resolved."""
    if not resolve_keys:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.monthly_close_risks
            SET status = 'resolved', resolved_at = %s, resolved_by = 'system', updated_at = %s
            WHERE branch_code = %s AND month = %s AND status = 'open'
              AND risk_key = ANY(%s)
            """,
            (now, now, branch_code, month, list(resolve_keys)),
        )


def _mark_line_sent(conn, month: str, branch_code: str, risk_keys: list, now: datetime) -> None:
    """Stamp last_line_sent_at for the risks we just LINE-notified (call only on
    successful push)."""
    if not risk_keys:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.monthly_close_risks
            SET last_line_sent_at = %s, updated_at = %s
            WHERE branch_code = %s AND month = %s AND risk_key = ANY(%s)
            """,
            (now, now, branch_code, month, list(risk_keys)),
        )


def _fetch_risks(conn, month: str, branch_code: str, status: Optional[str]) -> list[dict]:
    """Return stored risk rows (JSON-serializable) for output, optionally filtered
    by status. status='all' (or None) returns every status."""
    query = """
        SELECT id, branch_code, month, risk_key, severity, status, title, message,
               amount, evidence, link, first_seen_at, last_seen_at,
               resolved_at, last_line_sent_at
        FROM public.monthly_close_risks
        WHERE branch_code = %s AND month = %s
    """
    params: list[Any] = [branch_code, month]
    if status and status != "all":
        query += " AND status = %s"
        params.append(status)
    query += """
        ORDER BY CASE severity WHEN 'danger' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, risk_key
    """
    with conn.cursor() as cur:
        cur.execute(query, params)
        cols = [c[0] for c in cur.description]
        out = []
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            d["id"] = str(d["id"])
            d["amount"] = _f(d["amount"])
            for k in ("first_seen_at", "last_seen_at", "resolved_at", "last_line_sent_at"):
                d[k] = d[k].isoformat() if d[k] else None
            out.append(d)
        return out


def _count_by(risks: list) -> dict:
    """Counts by severity and status for a list of risk dicts."""
    by_severity = {"danger": 0, "warning": 0, "info": 0}
    by_status = {"open": 0, "resolved": 0, "ignored": 0}
    for r in risks:
        by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"by_severity": by_severity, "by_status": by_status, "total": len(risks)}


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/monthly-close/check")
def monthly_close_check(
    month: str = Query(..., description="YYYY-MM"),
    branch_code: str = Query(DEFAULT_BRANCH),
    _admin: dict = Depends(_require_admin_role),
):
    """Run V1 risk checks for a month, upsert current risks, resolve stale ones,
    and LINE open danger risks (24h throttled). Read-only against source data."""
    mstart, mend = validate_month(month)
    conn = get_db_conn()
    try:
        detected = run_all_checks(conn, mstart, mend, branch_code)
        existing = _fetch_existing(conn, month, branch_code)
        now = datetime.now(timezone.utc)
        plan = plan_risk_sync(existing, detected, now)

        _apply_upserts(conn, month, branch_code, plan["upserts"], now)
        _apply_resolves(conn, month, branch_code, plan["resolve_keys"], now)
        conn.commit()

        # LINE after the risk rows are committed, so a slow/failing push can never
        # roll back detection. Timestamps update only on a successful push.
        line_sent = send_danger_line(plan["line_targets"], month, branch_code)
        if line_sent:
            _mark_line_sent(
                conn, month, branch_code,
                [r["risk_key"] for r in plan["line_targets"]], now,
            )
            conn.commit()

        risks = _fetch_risks(conn, month, branch_code, status="open")
        return {
            "month": month,
            "branch_code": branch_code,
            "checked_at": now.isoformat(),
            "counts": _count_by(risks),
            "line_sent": line_sent,
            "risks": risks,
        }
    finally:
        conn.close()


@router.get("/monthly-close/risks")
def monthly_close_risks(
    month: str = Query(..., description="YYYY-MM"),
    branch_code: str = Query(DEFAULT_BRANCH),
    status: str = Query("open"),
    _admin: dict = Depends(_require_admin_role),
):
    """List stored monthly-close risks + counts by severity/status."""
    validate_month(month)
    conn = get_db_conn()
    try:
        risks = _fetch_risks(conn, month, branch_code, status=status)
        return {
            "month": month,
            "branch_code": branch_code,
            "status": status,
            "counts": _count_by(risks),
            "risks": risks,
        }
    finally:
        conn.close()
