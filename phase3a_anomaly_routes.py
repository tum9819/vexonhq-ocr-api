"""
VEXONHQ Phase 3A-2 — Anomaly Detection Routes + Worker
=======================================================
Flag bills with unusual amount vs category baseline.

Algorithm:
  1. For each confirmed bill, look up its category's baseline (mean, stddev, percentiles).
  2. If sample size n < 3, skip (not enough data).
  3. Compute zscore = (bill.amount - mean) / stddev.
  4. Severity:
       low    : abs(zscore) >= 1.5 AND < 2.0   (FYI warning)
       medium : abs(zscore) >= 2.0 AND < 3.0   (review)
       high   : abs(zscore) >= 3.0             (urgent)
     OR amount > p99 → high
     OR amount < 0.1 * p50 (and n>=5) → low (suspicious low)
  5. Insert into bill_anomalies (unique on bill_id + anomaly_type when user_action IS NULL).

Endpoints (6):
    POST  /ai/anomalies/scan            — scan confirmed bills, create alerts (admin; the
                                          scheduled scan runs in-process via line_bot_routes)
    GET   /ai/anomalies/list            — list with filters
    GET   /ai/anomalies/baselines       — per-category stats
    GET   /ai/anomalies/stats           — summary counts
    PATCH /ai/anomalies/{id}            — user action: false_positive | confirmed | ignored
    GET   /ai/anomalies/health          — smoke
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth_routes import _require_admin_role

try:
    from main import get_db_conn, _sign_uploads_url  # type: ignore
except ImportError:
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

    def _sign_uploads_url(url, *args, **kwargs):  # type: ignore
        return url


logger = logging.getLogger("phase3a_anomaly")
router = APIRouter(tags=["phase3a-anomaly"])

MIN_SAMPLE_FOR_BASELINE = 8   # raised from 3: stddev/spread is unreliable below ~8 samples (audit AI-2)
ZSCORE_LOW = 1.5
ZSCORE_MEDIUM = 2.0
ZSCORE_HIGH = 3.0


# ============================================================
# Helpers
# ============================================================

def _serialize_row(row: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _rows_to_dicts(cur) -> list[dict]:
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [_serialize_row(dict(zip(cols, r))) for r in cur.fetchall()]


def _parse_uuid(value: Any, field_name: str = "id") -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, f"Invalid UUID for {field_name}: {value!r}")


def _robust_zscore(amount: float, stddev: float, p50: float, p95: float) -> float:
    """Median-anchored z-score using a percentile-derived spread (audit AI-2).
    Expense amounts are right-skewed, so mean/stddev z-scores both miss real
    outliers and cry wolf. Anchor on the median (p50) and estimate the spread
    from the median->p95 gap (p95 ~= 1.645 sigma for a normal tail); fall back to
    stddev only if the percentile spread is unavailable."""
    spread = (p95 - p50) / 1.645 if (p95 and p95 > p50) else stddev
    if not spread:
        return 0.0
    return (amount - p50) / spread


def _classify_severity(robust_z: float, bill_amount: float,
                       p95: Optional[float], p99: Optional[float]) -> Optional[str]:
    """Severity from the robust z-score plus hard percentile gates. None if normal."""
    abs_z = abs(robust_z)
    if (p99 is not None and bill_amount > float(p99)) or abs_z >= ZSCORE_HIGH:
        return "high"
    if (p95 is not None and bill_amount > float(p95)) or abs_z >= ZSCORE_MEDIUM:
        return "medium"
    if abs_z >= ZSCORE_LOW:
        return "low"
    return None


def _build_message(severity: str, anomaly_type: str, bill_amount: float,
                    mean: float, p95: float, zscore: float, n: int) -> str:
    sev_th = {"low": "ผิดปกติเล็กน้อย", "medium": "ผิดปกติ", "high": "ผิดปกติมาก"}.get(severity, severity)
    if anomaly_type == "amount_high":
        return (
            f"{sev_th}: ยอด {bill_amount:,.2f} บาท สูงกว่าค่าเฉลี่ย {mean:,.2f} "
            f"(zscore={zscore:.2f}, p95={p95:,.2f}, ตัวอย่าง n={n})"
        )
    if anomaly_type == "amount_low":
        return (
            f"{sev_th}: ยอด {bill_amount:,.2f} บาท ต่ำกว่าค่าเฉลี่ย {mean:,.2f} "
            f"(zscore={zscore:.2f}, ตัวอย่าง n={n})"
        )
    return f"{sev_th}: {anomaly_type}"


# ============================================================
# Pydantic models
# ============================================================

class AnomalyUserAction(BaseModel):
    action: str                       # 'false_positive' | 'confirmed' | 'ignored'
    note: Optional[str] = None


# ============================================================
# Scanner logic
# ============================================================

def _fetch_baselines(cur) -> dict[str, dict]:
    """Map category_code → baseline dict."""
    cur.execute(
        """SELECT category_code, n, mean, stddev, p50, p95, p99
           FROM public.v_category_baselines
           WHERE n >= %s""",
        (MIN_SAMPLE_FOR_BASELINE,),
    )
    out: dict[str, dict] = {}
    for code, n, mean, stddev, p50, p95, p99 in cur.fetchall():
        out[code] = {
            "n": int(n),
            "mean": float(mean) if mean is not None else 0.0,
            "stddev": float(stddev) if stddev is not None else 0.0,
            "p50": float(p50) if p50 is not None else 0.0,
            "p95": float(p95) if p95 is not None else 0.0,
            "p99": float(p99) if p99 is not None else 0.0,
        }
    return out


def _scan_one_bill(cur, bill: dict, baselines: dict[str, dict]) -> Optional[dict]:
    """Returns an anomaly record to insert, or None if no anomaly."""
    cat = bill["category_code"]
    if not cat:
        return {
            "bill_id": bill["id"],
            "category_code": None,
            "anomaly_type": "missing_category",
            "severity": "low",
            "bill_amount": bill["amount"],
            "category_n": 0, "category_mean": None, "category_stddev": None,
            "category_p50": None, "category_p95": None, "category_p99": None,
            "zscore": 0.0,
            "message": f"ไม่มี category — กรุณาจัดหมวด (ยอด {float(bill['amount']):,.2f} บาท)",
        }

    b = baselines.get(cat)
    if not b:
        return None

    amount = float(bill["amount"])
    if amount <= 0:
        return None

    mean, stddev, p50, p95, p99 = b["mean"], b["stddev"], b["p50"], b["p95"], b["p99"]
    zscore = _robust_zscore(amount, stddev, p50, p95)

    anomaly_type = "amount_high" if amount >= p50 else "amount_low"
    severity = _classify_severity(zscore, amount, p95, p99)
    if severity is None:
        return None

    return {
        "bill_id": bill["id"],
        "category_code": cat,
        "anomaly_type": anomaly_type,
        "severity": severity,
        "bill_amount": amount,
        "category_n": b["n"],
        "category_mean": mean,
        "category_stddev": stddev,
        "category_p50": p50,
        "category_p95": p95,
        "category_p99": p99,
        "zscore": round(zscore, 2),
        "message": _build_message(severity, anomaly_type, amount, mean, p95, zscore, b["n"]),
    }


def _insert_anomaly(cur, rec: dict) -> Optional[str]:
    """Insert anomaly. Returns id if inserted, None if dedup blocked."""
    try:
        cur.execute(
            """INSERT INTO public.bill_anomalies
                 (bill_id, category_code, anomaly_type, severity,
                  bill_amount, category_n, category_mean, category_stddev,
                  category_p50, category_p95, category_p99,
                  zscore, message)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                rec["bill_id"], rec["category_code"], rec["anomaly_type"], rec["severity"],
                rec["bill_amount"], rec["category_n"], rec["category_mean"], rec["category_stddev"],
                rec["category_p50"], rec["category_p95"], rec["category_p99"],
                rec["zscore"], rec["message"],
            ),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
    except Exception as e:
        # uq_bill_anomalies_active blocks duplicate active alerts
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return None
        raise


# ============================================================
# Endpoints
# ============================================================

@router.post("/ai/anomalies/scan")
def scan_anomalies(limit: int = Query(500, ge=1, le=2000), _admin: dict = Depends(_require_admin_role)):
    """HTTP route: manual, admin-only re-scan (mutating + heavy = a DoS surface, so
    admin-gated). Delegates to the plain internal function below so in-process callers
    run a scan without tripping over FastAPI's Query/Depends parameter defaults."""
    return _scan_anomalies(limit)


def _scan_anomalies(limit: int = 500) -> dict:
    """Core anomaly scan — a plain function, safe to call in-process. The scheduled
    job in line_bot_routes calls THIS directly (not the HTTP route), so the admin gate
    never blocks scheduled scanning. Scans confirmed bills and flags anomalies vs
    category baselines. Returns: {scanned, alerts_created, by_severity, baselines_used}."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            baselines = _fetch_baselines(cur)

            cur.execute(
                """SELECT id, amount, category_code, vendor_name
                   FROM public.vendor_bills
                   WHERE review_status = 'confirmed'
                     AND amount IS NOT NULL AND amount > 0
                     AND id NOT IN (SELECT bill_id FROM public.bill_anomalies)
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            bills = [dict(zip(cols, r)) for r in cur.fetchall()]

            created = 0
            by_severity = {"low": 0, "medium": 0, "high": 0}
            for bill in bills:
                rec = _scan_one_bill(cur, bill, baselines)
                if not rec:
                    continue
                inserted_id = _insert_anomaly(cur, rec)
                if inserted_id:
                    created += 1
                    by_severity[rec["severity"]] += 1

            conn.commit()
            return {
                "scanned": len(bills),
                "alerts_created": created,
                "by_severity": by_severity,
                "baselines_used": len(baselines),
            }
    finally:
        conn.close()


@router.get("/ai/anomalies/list")
def list_anomalies(
    severity: Optional[str] = Query(None, description="low | medium | high"),
    user_action: Optional[str] = Query(None, description="pending | false_positive | confirmed | ignored"),
    category_code: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: dict = Depends(_require_admin_role),
):
    """List anomaly alerts. Default = pending (user_action IS NULL) sorted by severity."""
    where: list[str] = []
    params: list[Any] = []

    if severity:
        if severity not in ("low", "medium", "high"):
            raise HTTPException(400, "severity must be low | medium | high")
        where.append("severity = %s"); params.append(severity)
    if category_code:
        where.append("category_code = %s"); params.append(category_code)

    if user_action == "pending" or user_action is None:
        where.append("user_action IS NULL")
    elif user_action in ("false_positive", "confirmed", "ignored"):
        where.append("user_action = %s"); params.append(user_action)
    elif user_action == "all":
        pass
    else:
        raise HTTPException(400, "user_action must be pending | false_positive | confirmed | ignored | all")

    sql_where = (" WHERE " + " AND ".join(where)) if where else ""

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT a.id, a.bill_id, vb.vendor_name, vb.invoice_no, vb.bill_date,
                           a.category_code, c.name_th AS category_name, c.color AS category_color,
                           a.anomaly_type, a.severity, a.bill_amount,
                           a.category_n, a.category_mean, a.category_p95,
                           a.zscore, a.message, a.user_action, a.user_action_at, a.scanned_at
                    FROM public.bill_anomalies a
                    JOIN public.vendor_bills vb ON vb.id = a.bill_id
                    LEFT JOIN public.expense_categories c ON c.code = a.category_code
                    {sql_where}
                    ORDER BY
                      CASE a.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                      a.scanned_at DESC
                    LIMIT %s OFFSET %s""",
                params + [limit, offset],
            )
            rows = _rows_to_dicts(cur)

            cur.execute(
                f"SELECT count(*) FROM public.bill_anomalies a{sql_where}",
                params,
            )
            total = cur.fetchone()[0]
        return {"rows": rows, "total": int(total), "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.get("/ai/anomalies/baselines")
def category_baselines(_admin: dict = Depends(_require_admin_role)):
    """Per-category statistics for context."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT category_code, name_th, parent_code, n, mean, stddev,
                          min_amount, max_amount, p50, p95, p99,
                          first_bill_date, last_bill_date
                   FROM public.v_category_baselines
                   ORDER BY n DESC, category_code"""
            )
            rows = _rows_to_dicts(cur)
        return {"rows": rows, "min_sample_threshold": MIN_SAMPLE_FOR_BASELINE}
    finally:
        conn.close()


@router.get("/ai/anomalies/stats")
def anomaly_stats(_admin: dict = Depends(_require_admin_role)):
    """Summary counts."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                     count(*) FILTER (WHERE user_action IS NULL)::int           AS pending,
                     count(*) FILTER (WHERE user_action = 'false_positive')::int AS false_positive,
                     count(*) FILTER (WHERE user_action = 'confirmed')::int      AS confirmed,
                     count(*) FILTER (WHERE user_action = 'ignored')::int        AS ignored,
                     count(*) FILTER (WHERE severity = 'high' AND user_action IS NULL)::int   AS pending_high,
                     count(*) FILTER (WHERE severity = 'medium' AND user_action IS NULL)::int AS pending_medium,
                     count(*) FILTER (WHERE severity = 'low' AND user_action IS NULL)::int    AS pending_low
                   FROM public.bill_anomalies"""
            )
            row = cur.fetchone()
            cols = [d[0] for d in cur.description]
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.patch("/ai/anomalies/{anomaly_id}")
def user_acknowledge(anomaly_id: str, body: AnomalyUserAction, _admin: dict = Depends(_require_admin_role)):
    """User marks the alert as false_positive / confirmed / ignored."""
    aid = _parse_uuid(anomaly_id, "anomaly_id")
    if body.action not in ("false_positive", "confirmed", "ignored"):
        raise HTTPException(400, "action must be false_positive | confirmed | ignored")

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE public.bill_anomalies
                   SET user_action = %s, user_action_at = now(), user_note = %s
                   WHERE id = %s
                   RETURNING id, severity, user_action""",
                (body.action, body.note, str(aid)),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Anomaly not found")
            conn.commit()
            cols = [d[0] for d in cur.description]
            return _serialize_row(dict(zip(cols, row)))
    finally:
        conn.close()


@router.get("/ai/anomalies/{anomaly_id}/context")
def anomaly_context(anomaly_id: str, _admin: dict = Depends(_require_admin_role)):
    """Evidence for reviewing one anomaly (read-only):
      - bill:            the actual bill behind it — signed image + OCR items + totals
      - vendor_history:  up to 8 other bills from the SAME vendor (match by tax id when
                         present, else exact vendor_name) so the reviewer can see the
                         vendor's normal range
      - category_baseline: n / mean / p50 / p95 / stddev / p99 for the bill's category
    """
    aid = _parse_uuid(anomaly_id, "anomaly_id")
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT vb.id, vb.vendor_name, vb.invoice_no, vb.bill_date, vb.amount,
                          vb.subtotal, vb.vat, vb.category_code, c.name_th AS category_name,
                          vb.review_status, vb.merchant_tax_id, vb.attachment_url, vb.ocr_json
                   FROM public.bill_anomalies a
                   JOIN public.vendor_bills vb ON vb.id = a.bill_id
                   LEFT JOIN public.expense_categories c ON c.code = vb.category_code
                   WHERE a.id = %s""",
                (str(aid),),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Anomaly not found")
            b = dict(zip([d[0] for d in cur.description], row))

            # OCR items — jsonb usually comes back as dict; guard str/None/non-dict.
            ocr = b.get("ocr_json")
            if isinstance(ocr, str):
                try:
                    ocr = json.loads(ocr)
                except Exception:
                    ocr = {}
            if not isinstance(ocr, dict):
                ocr = {}
            items = ocr.get("items")

            bill = {
                "bill_id":         str(b["id"]),
                "vendor_name":     b["vendor_name"],
                "invoice_no":      b["invoice_no"],
                "bill_date":       b["bill_date"].isoformat() if b["bill_date"] else None,
                "amount":          float(b["amount"] or 0),
                "subtotal":        float(b["subtotal"]) if b["subtotal"] is not None else None,
                "vat":             float(b["vat"]) if b["vat"] is not None else None,
                "category_code":   b["category_code"],
                "category_name":   b["category_name"],
                "review_status":   b["review_status"],
                "merchant_tax_id": b["merchant_tax_id"],
                # uploads bucket is private — sign so <img> can load it (raw URL 403s)
                "preview_url":     _sign_uploads_url(b["attachment_url"]) if b["attachment_url"] else None,
                "items":           items if isinstance(items, list) else [],
            }

            # Match the vendor by tax id when present (OCR reads it reliably), else by
            # exact vendor_name. If neither exists, skip — never match on NULL/'' which
            # would pull unrelated bills or return nothing useful.
            taxid = (b["merchant_tax_id"] or "").strip()
            vname = (b["vendor_name"] or "").strip()
            match_clause = None
            match_val = None
            if taxid:
                # TRIM both sides — the param is stripped, so the column must be too,
                # or a stored tax id with stray whitespace would miss (Codex #3).
                match_clause, match_val = "TRIM(COALESCE(merchant_tax_id, '')) = %s", taxid
            elif vname:
                match_clause, match_val = "vendor_name = %s", vname

            vendor_history: list[dict] = []
            if match_clause:
                cur.execute(
                    f"""SELECT bill_date, amount, invoice_no, category_code, review_status
                        FROM public.vendor_bills
                        WHERE id <> %s::uuid AND {match_clause}
                        ORDER BY bill_date DESC NULLS LAST
                        LIMIT 8""",
                    (str(b["id"]), match_val),
                )
                vendor_history = [
                    {
                        "bill_date":     r[0].isoformat() if r[0] else None,
                        "amount":        float(r[1] or 0),
                        "invoice_no":    r[2],
                        "category_code": r[3],
                        "review_status": r[4],
                    }
                    for r in cur.fetchall()
                ]

            baseline = None
            if b["category_code"]:
                cur.execute(
                    """SELECT n, mean, p50, p95, stddev, p99
                       FROM public.v_category_baselines
                       WHERE category_code = %s""",
                    (b["category_code"],),
                )
                br = cur.fetchone()
                if br:
                    baseline = {
                        "n":      int(br[0]) if br[0] is not None else None,
                        "mean":   float(br[1]) if br[1] is not None else None,
                        "p50":    float(br[2]) if br[2] is not None else None,
                        "p95":    float(br[3]) if br[3] is not None else None,
                        "stddev": float(br[4]) if br[4] is not None else None,
                        "p99":    float(br[5]) if br[5] is not None else None,
                    }
        return {"bill": bill, "vendor_history": vendor_history, "category_baseline": baseline}
    finally:
        conn.close()


@router.get("/ai/anomalies/health")
def anomaly_health():
    """Smoke: DB + counts."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.bill_anomalies WHERE user_action IS NULL")
            pending = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.v_category_baselines")
            baselines = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM public.v_category_baselines WHERE n >= %s",
                (MIN_SAMPLE_FOR_BASELINE,),
            )
            usable_baselines = cur.fetchone()[0]
        return {
            "db": "ok",
            "pending_anomalies": int(pending),
            "total_baselines": int(baselines),
            "usable_baselines": int(usable_baselines),
            "min_sample_threshold": MIN_SAMPLE_FOR_BASELINE,
            "zscore_thresholds": {
                "low": ZSCORE_LOW,
                "medium": ZSCORE_MEDIUM,
                "high": ZSCORE_HIGH,
            },
        }
    finally:
        conn.close()
