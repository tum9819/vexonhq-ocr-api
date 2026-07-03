"""
supplier_routes.py — Phase 22: Supplier Analytics
===================================================
Endpoints:
  GET /supplier/summary?month=YYYY-MM   — spend per supplier for a month
  GET /supplier/trend?months=6          — top suppliers + 6-month spend trend
  GET /supplier/top?months=3&limit=10   — top N suppliers by total spend

Data source: vendor_bills (confirmed) + ar_ap_entries (payable) combined
"""

import os
from datetime import date, timedelta
from typing import Optional

import psycopg2
from fastapi import APIRouter, HTTPException, Query

from bkk import bkk_today

router = APIRouter(prefix="/supplier", tags=["supplier"])


def get_db_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def _rows_to_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _parse_month(month_str: str) -> tuple[date, date]:
    """Parse 'YYYY-MM' → (first_day, last_day)."""
    try:
        y, m = int(month_str[:4]), int(month_str[5:7])
    except (ValueError, IndexError):
        raise HTTPException(400, "month must be YYYY-MM (e.g. 2026-05)")
    first = date(y, m, 1)
    if m == 12:
        last = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(y, m + 1, 1) - timedelta(days=1)
    return first, last


# ─────────────────────────────────────────────────────────────
# GET /supplier/summary?month=YYYY-MM
# ─────────────────────────────────────────────────────────────

@router.get("/summary")
def supplier_summary(month: str = Query(..., description="YYYY-MM")):
    """
    Spend per supplier for a given month.
    Sources: confirmed vendor_bills (bill_date) + confirmed ar_ap_entries payable (doc_date).
    Returns suppliers sorted by total spend descending.
    """
    first, last = _parse_month(month)
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            WITH vb AS (
                SELECT
                    COALESCE(vendor_name, 'ไม่ระบุ') AS supplier,
                    SUM(amount)::numeric AS total,
                    COUNT(*)            AS bill_count
                FROM public.vendor_bills
                WHERE review_status = 'confirmed'
                  AND bill_date BETWEEN %s AND %s
                GROUP BY COALESCE(vendor_name, 'ไม่ระบุ')
            ),
            ap AS (
                SELECT
                    COALESCE(c.name, e.counterparty_name_snapshot, 'ไม่ระบุ') AS supplier,
                    SUM(e.amount_total)::numeric AS total,
                    COUNT(*)                     AS bill_count
                FROM public.ar_ap_entries e
                LEFT JOIN public.counterparties c ON c.id = e.counterparty_id
                WHERE e.direction = 'payable'
                  AND e.doc_date BETWEEN %s AND %s
                GROUP BY COALESCE(c.name, e.counterparty_name_snapshot, 'ไม่ระบุ')
            ),
            combined AS (
                SELECT supplier, total, bill_count FROM vb
                UNION ALL
                SELECT supplier, total, bill_count FROM ap
            )
            SELECT supplier,
                   SUM(total)      AS total_spend,
                   SUM(bill_count) AS bill_count
            FROM combined
            GROUP BY supplier
            ORDER BY total_spend DESC
        """, (first, last, first, last))

        rows = _rows_to_dicts(cur)
        grand_total = sum(float(r["total_spend"] or 0) for r in rows)

        result = []
        for r in rows:
            spend = float(r["total_spend"] or 0)
            result.append({
                "supplier":    r["supplier"],
                "total_spend": round(spend, 2),
                "bill_count":  int(r["bill_count"] or 0),
                "pct":         round(spend / grand_total * 100, 1) if grand_total > 0 else 0,
            })

        return {
            "month":       month,
            "grand_total": round(grand_total, 2),
            "count":       len(result),
            "suppliers":   result,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# GET /supplier/top?months=3&limit=10
# ─────────────────────────────────────────────────────────────

@router.get("/top")
def supplier_top(
    months: int = Query(3, ge=1, le=12, description="Look-back window in months"),
    limit:  int = Query(10, ge=1, le=50,  description="Max suppliers to return"),
):
    """Top suppliers by total spend over the last N months."""
    today = bkk_today()
    date_from = date(today.year, today.month, 1) - timedelta(days=months * 30)
    date_to   = today

    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            WITH vb AS (
                SELECT COALESCE(vendor_name, 'ไม่ระบุ') AS supplier,
                       SUM(amount)::numeric AS total, COUNT(*) AS bills
                FROM public.vendor_bills
                WHERE review_status='confirmed' AND bill_date BETWEEN %s AND %s
                GROUP BY 1
            ),
            ap AS (
                SELECT COALESCE(c.name, e.counterparty_name_snapshot, 'ไม่ระบุ') AS supplier,
                       SUM(e.amount_total)::numeric AS total, COUNT(*) AS bills
                FROM public.ar_ap_entries e
                LEFT JOIN public.counterparties c ON c.id = e.counterparty_id
                WHERE e.direction='payable' AND e.doc_date BETWEEN %s AND %s
                GROUP BY 1
            ),
            combined AS (SELECT supplier, total, bills FROM vb UNION ALL SELECT supplier, total, bills FROM ap)
            SELECT supplier, SUM(total) AS total_spend, SUM(bills) AS bill_count
            FROM combined
            GROUP BY supplier
            ORDER BY total_spend DESC
            LIMIT %s
        """, (date_from, date_to, date_from, date_to, limit))

        rows = _rows_to_dicts(cur)
        return {
            "months":    months,
            "date_from": str(date_from),
            "date_to":   str(date_to),
            "suppliers": [
                {
                    "supplier":    r["supplier"],
                    "total_spend": round(float(r["total_spend"] or 0), 2),
                    "bill_count":  int(r["bill_count"] or 0),
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# GET /supplier/trend?months=6
# ─────────────────────────────────────────────────────────────

@router.get("/trend")
def supplier_trend(
    months: int = Query(6, ge=2, le=24, description="Number of months for trend"),
    limit:  int = Query(8,  ge=1, le=20, description="Top N suppliers to show trend for"),
):
    """
    Month-by-month spend trend for the top N suppliers.
    Returns a list of months and per-supplier monthly amounts.
    """
    today = bkk_today()

    # Build list of months (YYYY-MM strings) from oldest to newest
    month_list = []
    for i in range(months - 1, -1, -1):
        m_date = date(today.year, today.month, 1) - timedelta(days=i * 30)
        month_list.append(f"{m_date.year}-{m_date.month:02d}")

    date_from = date(today.year, today.month, 1) - timedelta(days=(months - 1) * 30)
    date_to   = today

    conn = get_db_conn()
    try:
        cur = conn.cursor()

        # First get top suppliers by total over period
        cur.execute("""
            WITH vb AS (
                SELECT COALESCE(vendor_name, 'ไม่ระบุ') AS supplier,
                       SUM(amount)::numeric AS total
                FROM public.vendor_bills
                WHERE review_status='confirmed' AND bill_date BETWEEN %s AND %s
                GROUP BY 1
            ),
            ap AS (
                SELECT COALESCE(c.name, e.counterparty_name_snapshot, 'ไม่ระบุ') AS supplier,
                       SUM(e.amount_total)::numeric AS total
                FROM public.ar_ap_entries e
                LEFT JOIN public.counterparties c ON c.id = e.counterparty_id
                WHERE e.direction='payable' AND e.doc_date BETWEEN %s AND %s
                GROUP BY 1
            ),
            combined AS (SELECT supplier, total FROM vb UNION ALL SELECT supplier, total FROM ap)
            SELECT supplier FROM (
                SELECT supplier, SUM(total) AS t FROM combined GROUP BY supplier ORDER BY t DESC LIMIT %s
            ) sub
        """, (date_from, date_to, date_from, date_to, limit))
        top_suppliers = [r[0] for r in cur.fetchall()]

        if not top_suppliers:
            return {"months": month_list, "series": []}

        # Now get monthly spend per top supplier
        placeholders = ",".join(["%s"] * len(top_suppliers))
        cur.execute(f"""
            WITH vb AS (
                SELECT
                    to_char(bill_date, 'YYYY-MM') AS ym,
                    COALESCE(vendor_name, 'ไม่ระบุ') AS supplier,
                    SUM(amount)::numeric AS total
                FROM public.vendor_bills
                WHERE review_status='confirmed'
                  AND bill_date BETWEEN %s AND %s
                  AND COALESCE(vendor_name, 'ไม่ระบุ') IN ({placeholders})
                GROUP BY 1, 2
            ),
            ap AS (
                SELECT
                    to_char(e.doc_date, 'YYYY-MM') AS ym,
                    COALESCE(c.name, e.counterparty_name_snapshot, 'ไม่ระบุ') AS supplier,
                    SUM(e.amount_total)::numeric AS total
                FROM public.ar_ap_entries e
                LEFT JOIN public.counterparties c ON c.id = e.counterparty_id
                WHERE e.direction='payable'
                  AND e.doc_date BETWEEN %s AND %s
                  AND COALESCE(c.name, e.counterparty_name_snapshot, 'ไม่ระบุ') IN ({placeholders})
                GROUP BY 1, 2
            ),
            combined AS (SELECT ym, supplier, total FROM vb UNION ALL SELECT ym, supplier, total FROM ap)
            SELECT ym, supplier, SUM(total) AS spend
            FROM combined
            GROUP BY ym, supplier
            ORDER BY supplier, ym
        """, [date_from, date_to] + top_suppliers + [date_from, date_to] + top_suppliers)

        # Build pivot: {supplier → {month → spend}}
        pivot: dict[str, dict[str, float]] = {s: {m: 0.0 for m in month_list} for s in top_suppliers}
        for ym, supplier, spend in cur.fetchall():
            if supplier in pivot and ym in pivot[supplier]:
                pivot[supplier][ym] += float(spend or 0)

        series = [
            {
                "supplier": s,
                "data":     [round(pivot[s].get(m, 0.0), 2) for m in month_list],
            }
            for s in top_suppliers
        ]

        return {"months": month_list, "series": series}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# GET /supplier/products  — distinct product names from invoice_items
# ─────────────────────────────────────────────────────────────

@router.get("/products")
def supplier_products(q: Optional[str] = Query(None, description="Search keyword")):
    """
    Return distinct product_name values from confirmed invoice_items.
    Optionally filter by keyword (ILIKE).
    """
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        if q:
            cur.execute("""
                SELECT DISTINCT ii.product_name
                FROM public.invoice_items ii
                JOIN public.vendor_bills vb ON vb.id = ii.vendor_bill_id
                WHERE vb.review_status = 'confirmed'
                  AND ii.unit_price IS NOT NULL AND ii.unit_price > 0
                  AND ii.product_name ILIKE %s
                ORDER BY ii.product_name
                LIMIT 50
            """, (f"%{q}%",))
        else:
            cur.execute("""
                SELECT DISTINCT ii.product_name
                FROM public.invoice_items ii
                JOIN public.vendor_bills vb ON vb.id = ii.vendor_bill_id
                WHERE vb.review_status = 'confirmed'
                  AND ii.unit_price IS NOT NULL AND ii.unit_price > 0
                ORDER BY ii.product_name
                LIMIT 200
            """)
        products = [row[0] for row in cur.fetchall() if row[0]]
        return {"count": len(products), "products": products}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# GET /supplier/price-trend?product=&months=6
# ─────────────────────────────────────────────────────────────

@router.get("/price-trend")
def supplier_price_trend(
    product: str = Query(..., description="Product name to track (partial match)"),
    months: int  = Query(6, ge=2, le=24, description="Number of months look-back"),
):
    """
    Monthly avg unit_price for a given product name, broken down by supplier.
    Joins invoice_items -> vendor_bills (confirmed only).
    """
    today     = bkk_today()
    date_from = date(today.year, today.month, 1) - timedelta(days=(months - 1) * 30)
    date_to   = today

    month_list = []
    for i in range(months - 1, -1, -1):
        m_date = date(today.year, today.month, 1) - timedelta(days=i * 30)
        month_list.append(f"{m_date.year}-{m_date.month:02d}")

    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                to_char(vb.bill_date, 'YYYY-MM')       AS ym,
                COALESCE(vb.vendor_name, 'ไม่ระบุ')    AS supplier,
                ii.product_name,
                ROUND(AVG(ii.unit_price)::numeric, 2)  AS avg_unit_price,
                ROUND(SUM(ii.quantity)::numeric, 2)     AS total_qty,
                COUNT(*)                                AS records
            FROM public.invoice_items ii
            JOIN public.vendor_bills vb ON vb.id = ii.vendor_bill_id
            WHERE vb.review_status = 'confirmed'
              AND vb.bill_date BETWEEN %s AND %s
              AND ii.product_name ILIKE %s
              AND ii.unit_price IS NOT NULL
              AND ii.unit_price > 0
            GROUP BY to_char(vb.bill_date, 'YYYY-MM'), vb.vendor_name, ii.product_name
            ORDER BY ym, supplier
        """, (date_from, date_to, f"%{product}%"))
        rows = _rows_to_dicts(cur)
    finally:
        conn.close()

    if not rows:
        return {"months": month_list, "product": product, "series": [], "matched_names": []}

    suppliers     = list(dict.fromkeys(r["supplier"] for r in rows))
    matched_names = list(dict.fromkeys(r["product_name"] for r in rows))

    pivot = {s: {m: None for m in month_list} for s in suppliers}
    for r in rows:
        ym  = r["ym"]
        sup = r["supplier"]
        if sup in pivot and ym in pivot[sup]:
            pivot[sup][ym] = float(r["avg_unit_price"] or 0)

    series = [
        {"supplier": s, "data": [pivot[s].get(m) for m in month_list]}
        for s in suppliers
    ]

    return {
        "months":        month_list,
        "product":       product,
        "matched_names": matched_names,
        "series":        series,
    }


# ─────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────

@router.get("/health")
def supplier_health():
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM public.vendor_bills WHERE review_status='confirmed'")
            vb_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM public.ar_ap_entries WHERE direction='payable'")
            ap_count = cur.fetchone()[0]
        return {"db": "ok", "confirmed_vendor_bills": vb_count, "ap_payable_entries": ap_count}
    finally:
        conn.close()
