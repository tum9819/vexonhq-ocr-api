"""
VEXONHQ Phase 39 — AI Search (ค้นหาบิลด้วยภาษาไทย)
=====================================================

!!! UNUSED / DEAD CODE — superseded by phase11_search_routes.py !!!
This router is NOT registered in main.py (main.py imports phase11_search_routes
as `search_router`). The live search surface is /search/receipt, /search/suggestions,
/search/empty-hints from phase11_search_routes.py. The endpoints below
(/search/query, /search/health) do NOT exist in production. Do not "bugfix" here
expecting a prod effect — edit phase11_search_routes.py instead. (Security audit 2026-05-31, finding #13.)

GPT-4o-mini แปลง query ภาษาธรรมชาติ → filters → SQL บน v_daybook + vendor_bills

Endpoints (NOT registered):
  POST /search/query   {"q": "...", "limit": 20}
  GET  /search/health
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any, Optional

import psycopg2
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("search_routes")
router = APIRouter(tags=["search"])

LLM_MODEL = "gpt-4o-mini"

# ─────────────────────────────────────────────
# GPT intent parser
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """คุณช่วยแปลง query ภาษาไทยเป็น JSON filter สำหรับค้นหาบิลในระบบบัญชีร้านอาหาร

ให้ return JSON ที่มี field เหล่านี้ (ใส่ null หากไม่มีข้อมูล):
{
  "keyword": "คำค้นหา (ชื่อ vendor หรือ รายละเอียด)",
  "date_from": "YYYY-MM-DD",
  "date_to": "YYYY-MM-DD",
  "direction": "income" | "expense" | null,
  "category_code": "code หมวดหมู่ เช่น food_cost / rent / musician_fee / utilities / ingredient / beverage" | null,
  "amount_min": number | null,
  "amount_max": number | null,
  "source": "pos_sale" | "vendor_bill" | "bank_stmt" | "manual" | "rider" | null,
  "summary": "สรุปสิ่งที่ค้นหาสั้นๆ ภาษาไทย"
}

ตัวอย่าง:
- "ค่าแก๊สเดือนมีนาคม" → keyword:"แก๊ส", date_from:"YYYY-03-01", date_to:"YYYY-03-31", direction:"expense"
- "บิล Makro มีนา 2026" → keyword:"makro", date_from:"2026-03-01", date_to:"2026-03-31"
- "รายรับเดือนนี้" → direction:"income", date_from=วันที่1ของเดือนนี้
- "ค่าดนตรีทั้งหมด" → category_code:"musician_fee", direction:"expense"
- "ค่าเช่าปีที่แล้ว" → category_code:"rent", date_from:"YYYY-01-01", date_to:"YYYY-12-31"
- "บิลเกิน 5000 บาท" → amount_min:5000

ปีที่หมายถึง "ปีนี้" = 2026, "ปีที่แล้ว" = 2025.
เดือนที่หมายถึง "เดือนนี้" = พฤษภาคม 2026, "เดือนที่แล้ว" = เมษายน 2026.

ตอบเป็น JSON ล้วนๆ ไม่มีข้อความอื่น"""


def _gpt_parse(q: str) -> dict:
    try:
        from llm import openai_chat
        # Routed through llm.openai_chat for ai_call_log telemetry. Model unchanged.
        resp = openai_chat(
            "search_openai",
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": q},
            ],
            temperature=0,
            max_tokens=256,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        log.warning("GPT parse failed: %s — falling back to keyword only", e)
        return {"keyword": q, "summary": f"ค้นหา: {q}"}


# ─────────────────────────────────────────────
# DB query
# ─────────────────────────────────────────────

def _rows_to_dicts(cur) -> list[dict]:
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        row: dict[str, Any] = {}
        for k, v in zip(cols, r):
            if isinstance(v, (datetime, date)):
                row[k] = v.isoformat()
            elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                row[k] = float(v)
            else:
                row[k] = v
        out.append(row)
    return out


def _search_daybook(conn, filters: dict, limit: int) -> list[dict]:
    """Search v_daybook with parsed filters."""
    clauses = []
    params: list[Any] = []

    if filters.get("keyword"):
        kw = f"%{filters['keyword']}%"
        params.append(kw)
        params.append(kw)
        clauses.append(f"(LOWER(label) LIKE LOWER(%s) OR LOWER(counterparty) LIKE LOWER(%s))")

    if filters.get("date_from"):
        params.append(filters["date_from"])
        clauses.append("entry_date >= %s")

    if filters.get("date_to"):
        params.append(filters["date_to"])
        clauses.append("entry_date <= %s")

    if filters.get("direction"):
        params.append(filters["direction"])
        clauses.append("direction = %s")

    if filters.get("category_code"):
        params.append(filters["category_code"])
        clauses.append("category_code = %s")

    if filters.get("amount_min") is not None:
        params.append(float(filters["amount_min"]))
        clauses.append("amount >= %s")

    if filters.get("amount_max") is not None:
        params.append(float(filters["amount_max"]))
        clauses.append("amount <= %s")

    if filters.get("source"):
        src_map = {
            "pos_sale": "pos_sale",
            "vendor_bill": "vendor_bill",
            "bank_stmt": "bank_stmt",
            "manual": "manual_entry",
            "rider": ("rider_grab", "rider_lineman"),
        }
        src = src_map.get(filters["source"])
        if isinstance(src, tuple):
            params.extend(src)
            clauses.append(f"source IN (%s, %s)")
        elif src:
            params.append(src)
            clauses.append("source = %s")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    sql = f"""
        SELECT
            entry_date,
            label,
            counterparty,
            direction,
            amount::float,
            category_code,
            source,
            branch_code
        FROM v_daybook
        {where}
        ORDER BY entry_date DESC
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return _rows_to_dicts(cur)


def _search_vendor_bills(conn, filters: dict, limit: int) -> list[dict]:
    """Search vendor_bills for document-level details (invoice_no etc.)."""
    clauses = ["review_status = 'confirmed'"]
    params: list[Any] = []

    if filters.get("keyword"):
        kw = f"%{filters['keyword']}%"
        params.append(kw)
        params.append(kw)
        clauses.append("(LOWER(vendor_name) LIKE LOWER(%s) OR LOWER(description) LIKE LOWER(%s))")

    if filters.get("date_from"):
        params.append(filters["date_from"])
        clauses.append("bill_date >= %s")

    if filters.get("date_to"):
        params.append(filters["date_to"])
        clauses.append("bill_date <= %s")

    if filters.get("amount_min") is not None:
        params.append(float(filters["amount_min"]))
        clauses.append("total_amount >= %s")

    if filters.get("amount_max") is not None:
        params.append(float(filters["amount_max"]))
        clauses.append("total_amount <= %s")

    where = "WHERE " + " AND ".join(clauses)
    params.append(limit)

    sql = f"""
        SELECT
            id::text,
            bill_date,
            vendor_name,
            invoice_no,
            total_amount::float,
            payment_status,
            category_code,
            'vendor_bill' AS source
        FROM vendor_bills
        {where}
        ORDER BY bill_date DESC NULLS LAST
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return _rows_to_dicts(cur)


# ─────────────────────────────────────────────
# Request / Response
# ─────────────────────────────────────────────

class SearchRequest(BaseModel):
    q: str
    limit: int = 30


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.post("/search/query")
def search_query(body: SearchRequest):
    """Parse Thai query with GPT → search v_daybook + vendor_bills."""
    q = body.q.strip()
    if not q:
        raise HTTPException(400, "query is empty")

    # 1. GPT parse
    filters = _gpt_parse(q)
    summary = filters.pop("summary", q)

    # 2. DB search
    conn = get_db_conn()
    try:
        daybook_rows = _search_daybook(conn, filters, body.limit)
        # Only hit vendor_bills for bill-like queries (no direction or expense)
        direction = filters.get("direction")
        bill_rows: list[dict] = []
        if direction in (None, "expense") and not filters.get("source"):
            bill_rows = _search_vendor_bills(conn, filters, min(body.limit, 10))
    finally:
        conn.close()

    # 3. Merge + deduplicate (bill rows may overlap with daybook via vendor_bill source)
    seen_bills = {r.get("id") for r in bill_rows if r.get("id")}

    merged = daybook_rows  # primary results
    # Append bill extras (ones not already represented)
    bills_extra = [r for r in bill_rows]

    return {
        "query": q,
        "summary": summary,
        "filters": filters,
        "results": merged,
        "bills": bills_extra,
        "result_count": len(merged),
        "total_amount": round(sum(r.get("amount", 0) for r in merged), 0),
    }


@router.get("/search/health")
def search_health():
    return {"status": "ok", "router": "search"}
