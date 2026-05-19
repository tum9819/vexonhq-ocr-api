"""
VEXONHQ Phase 11 — Smart Receipt Search (AI)
=============================================
Endpoints:
    POST /search/receipt      -> Thai natural language -> SQL -> results
    GET  /search/suggestions  -> query hints for UI
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

logger = logging.getLogger("search")
router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    limit: int = 50


class SearchFilter(BaseModel):
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    direction: Optional[str] = None
    keyword: Optional[str] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    source: Optional[str] = None
    category_code: Optional[str] = None


_SYSTEM_PROMPT = (
    "You are an AI that converts Thai accounting search queries to JSON filters.\n\n"
    "Database columns in v_daybook:\n"
    "- entry_date (DATE)\n"
    "- direction: income or expense\n"
    "- source: pos_sale, vendor_bill, rider_income_grab, rider_income_lineman,\n"
    "          ar_payment, ap_payment, pos_cashflow, manual\n"
    "- label: description text (may be bank statement raw text — raw person names, NOT brand names)\n"
    "- counterparty: supplier name\n"
    "- amount (numeric)\n"
    "- category_code: rent, staff_salary, food_raw, beverage_raw, delivery_income, musician_fee, reimbursement\n\n"
    "CRITICAL: Bank statement labels are raw person names (e.g. 'วัฒนา กิ่งธารา', 'มยุรฉัตร โส...').\n"
    "NEVER set keyword for named expense categories — use category_code ONLY. Do NOT set keyword.\n\n"
    "Category mappings (set category_code, set keyword=null):\n"
    "- ค่าเช่า/เช่า/rent -> category_code: rent\n"
    "- เงินเดือน/ค่าแรง/salary/พนักงาน -> category_code: staff_salary\n"
    "- ค่าดนตรี/นักดนตรี/musician -> category_code: musician_fee\n"
    "- สำรองจ่าย/คืนเงิน/reimbursement -> category_code: reimbursement\n"
    "- เบียร์/เบียร์ช้าง/เบียร์สิงห์/ช้าง/สิงห์/แกรนด์รอยัล/เหล้า/สุรา/เครื่องดื่ม/วัฒนา -> category_code: beverage_raw\n"
    "- วัตถุดิบ/อาหาร/ผัก/เนื้อ/หมู/ไก่/food_raw -> category_code: food_raw\n\n"
    "Special date keywords:\n"
    "- เดือนนี้ -> current month first/last day\n"
    "- เดือนที่แล้ว -> previous month first/last day\n"
    "- วันนี้ -> today for both date_from and date_to\n"
    "Thai months: ม.ค.=01 ก.พ.=02 มี.ค.=03 เม.ย.=04 พ.ค.=05 มิ.ย.=06\n"
    "             ก.ค.=07 ส.ค.=08 ก.ย.=09 ต.ค.=10 พ.ย.=11 ธ.ค.=12\n"
    "Thai month names: มกราคม=01 กุมภาพันธ์=02 มีนาคม=03 เมษายน=04 พฤษภาคม=05 มิถุนายน=06\n"
    "                  กรกฎาคม=07 สิงหาคม=08 กันยายน=09 ตุลาคม=10 พฤศจิกายน=11 ธันวาคม=12\n"
    "เมษา/เมษายน = April = month 04\n\n"
    "Year is AD (CE). If user gives BE year, subtract 543 (e.g. 2569 -> 2026).\n"
    "Current date: " + datetime.now().strftime("%Y-%m-%d") + "\n\n"
    "Reply JSON only, schema:\n"
    "{ date_from, date_to, direction, keyword, amount_min, amount_max, source, category_code }\n\n"
    "Examples:\n"
    '- "หาบิล Makro เดือนเมษา" -> {"date_from":"2026-04-01","date_to":"2026-04-30","direction":"expense","keyword":"Makro","amount_min":null,"amount_max":null,"source":"vendor_bill","category_code":null}\n'
    '- "รายรับจาก Grab ทั้งหมด" -> {"date_from":null,"date_to":null,"direction":"income","keyword":null,"amount_min":null,"amount_max":null,"source":"rider_income_grab","category_code":null}\n'
    '- "ค่าเช่าเดือนที่แล้ว" -> {"date_from":null,"date_to":null,"direction":"expense","keyword":null,"amount_min":null,"amount_max":null,"source":null,"category_code":"rent"}\n'
    '- "เบียร์ช้าง" -> {"date_from":null,"date_to":null,"direction":"expense","keyword":null,"amount_min":null,"amount_max":null,"source":null,"category_code":"beverage_raw"}\n'
    '- "เบียร์สิงห์เดือนเมษา" -> {"date_from":"2026-04-01","date_to":"2026-04-30","direction":"expense","keyword":null,"amount_min":null,"amount_max":null,"source":null,"category_code":"beverage_raw"}\n'
    '- "เงินเดือนเดือนนี้" -> {"date_from":"2026-05-01","date_to":"2026-05-31","direction":"expense","keyword":null,"amount_min":null,"amount_max":null,"source":null,"category_code":"staff_salary"}\n'
    '- "รายจ่ายเกิน 5000 บาท" -> {"date_from":null,"date_to":null,"direction":"expense","keyword":null,"amount_min":5000,"amount_max":null,"source":null,"category_code":null}\n'
    '- "วันไหนขายดีสุดเดือนเมษา" -> {"date_from":"2026-04-01","date_to":"2026-04-30","direction":"income","keyword":null,"amount_min":null,"amount_max":null,"source":"pos_sale","category_code":null}'
)


def _call_claude_filter(query: str) -> SearchFilter:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")

    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 256,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": query}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data["content"][0]["text"].strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw)
            return SearchFilter(**parsed)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise HTTPException(502, f"Claude API error {e.code}: {body}")
    except json.JSONDecodeError as e:
        raise HTTPException(502, f"Claude returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(502, f"Claude call failed: {e}")


def _build_and_run_query(f: SearchFilter, limit: int) -> list:
    conditions = ["1=1"]
    params = []

    if f.date_from:
        conditions.append("d.entry_date >= %s")
        params.append(f.date_from)
    if f.date_to:
        conditions.append("d.entry_date <= %s")
        params.append(f.date_to)
    if f.direction in ("income", "expense"):
        conditions.append("d.direction = %s")
        params.append(f.direction)
    if f.keyword:
        conditions.append(
            "(d.label ILIKE %s OR d.counterparty ILIKE %s "
            "OR d.source ILIKE %s OR d.category_code ILIKE %s)"
        )
        kw = f"%{f.keyword}%"
        params.extend([kw, kw, kw, kw])
    if f.amount_min is not None:
        conditions.append("d.amount >= %s")
        params.append(f.amount_min)
    if f.amount_max is not None:
        conditions.append("d.amount <= %s")
        params.append(f.amount_max)
    if f.source:
        conditions.append("d.source = %s")
        params.append(f.source)
    if f.category_code:
        conditions.append("d.category_code = %s")
        params.append(f.category_code)

    sql = """
        SELECT
            d.entry_date,
            d.direction,
            d.source,
            COALESCE(ec.name_th, d.category_code, '') AS category_name,
            COALESCE(d.label, d.counterparty, '') AS detail,
            d.amount,
            d.branch_code
        FROM public.v_daybook d
        LEFT JOIN public.expense_categories ec ON ec.code = d.category_code
        WHERE {where}
        ORDER BY d.entry_date DESC, d.amount DESC
        LIMIT %s
    """.format(where=" AND ".join(conditions))
    params.append(limit)

    try:
        conn = get_db_conn()
    except Exception as exc:
        logger.exception("DB connection failed: %s", exc)
        raise HTTPException(503, f"Database connection failed: {exc}")

    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = ["entry_date", "direction", "source", "category_name", "detail", "amount", "branch_code"]
            rows = []
            for row in cur.fetchall():
                r = dict(zip(cols, row))
                r["entry_date"] = str(r["entry_date"])
                r["amount"] = float(r["amount"] or 0)
                rows.append(r)
            return rows
    except Exception as exc:
        logger.exception("Search query failed: %s", exc)
        raise HTTPException(500, f"Database query failed: {exc}")
    finally:
        conn.close()


@router.post("/receipt")
def smart_search(body: SearchRequest):
    """Convert Thai natural language query to SQL and return results."""
    query = body.query.strip()
    if not query:
        raise HTTPException(400, "query must not be empty")
    if len(query) > 500:
        raise HTTPException(400, "query too long (max 500 chars)")

    logger.info("Smart search query: %r", query)

    try:
        search_filter = _call_claude_filter(query)
        logger.info("Parsed filter: %s", search_filter.model_dump())
        results = _build_and_run_query(search_filter, body.limit)
        total_income = sum(r["amount"] for r in results if r["direction"] == "income")
        total_expense = sum(r["amount"] for r in results if r["direction"] == "expense")
        return {
            "query": query,
            "filter": search_filter.model_dump(),
            "count": len(results),
            "total_income": total_income,
            "total_expense": total_expense,
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error in smart_search: %s", exc)
        raise HTTPException(500, f"Search error: {exc}")


@router.get("/suggestions")
def get_suggestions():
    return {
        "suggestions": [
            "หาบิล Makro เดือนเมษา",
            "รายรับจาก Grab ทั้งหมด",
            "ค่าแก๊สเดือนนี้",
            "รายจ่ายเกิน 5000 บาท",
            "ค่าเช่าเดือนที่แล้ว",
            "รายจ่ายทั้งหมดเดือนพฤษภา",
            "รายรับ POS วันนี้",
            "บิล Lineman เดือนเมษายน",
        ]
    }


@router.get("/empty-hints")
def get_empty_hints(q: str = ""):
    """
    Helper for `/search/receipt` 0-results UX (Issue 2, Session 25).

    Returns three lists so the frontend can show *actionable* hints when a
    user search returns nothing:

    - **top_vendors** – the 10 biggest confirmed vendors by total amount.
      Lets the user see what's actually in the database (e.g. their bills
      are labelled "ซีพี แอ็กซ์ตร้า", not "Makro").
    - **alias_hints** – rows from `vendor_aliases` whose `product_keyword`
      overlaps with the query. Surfaces TUM-curated mappings (e.g.
      "เบียร์ช้าง" → "วัฒนา") so the user can re-search with the
      bank-statement name instead of the brand name.
    - **fuzzy_matches** – up to 5 confirmed vendor_names whose full text
      contains the query as a substring (case-insensitive). Catches the
      "I searched 'ซีพี' but the vendor is 'บริษัท ซีพี แอ็กซ์ตร้า…'"
      case directly.

    Pure read-only; no side effects. JWT-protected by the global
    middleware (same as the rest of `/search/*`).
    """
    q_norm = (q or "").strip()

    try:
        conn = get_db_conn()
    except Exception as exc:
        logger.exception("DB connection failed: %s", exc)
        raise HTTPException(503, f"Database connection failed: {exc}")

    try:
        with conn.cursor() as cur:
            # 1. Top vendors by total — always returned so the user always
            #    has something concrete to click on.
            cur.execute(
                """
                SELECT vendor_name,
                       COUNT(*)::int AS bills,
                       SUM(amount)::numeric(12,2) AS total
                FROM public.vendor_bills
                WHERE review_status = 'confirmed'
                  AND vendor_name IS NOT NULL
                  AND vendor_name <> ''
                GROUP BY vendor_name
                ORDER BY total DESC NULLS LAST
                LIMIT 10
                """
            )
            top_vendors = [
                {
                    "vendor_name": r[0],
                    "bills": int(r[1] or 0),
                    "total": float(r[2] or 0),
                }
                for r in cur.fetchall()
            ]

            # 2. Alias hints — only if the user actually typed something.
            #    Match in both directions so short keywords ("makro") hit
            #    longer aliases and vice versa.
            alias_hints: list[dict] = []
            if q_norm:
                cur.execute(
                    """
                    SELECT product_keyword, vendor_name
                    FROM public.vendor_aliases
                    WHERE is_active = true
                      AND (
                            LOWER(product_keyword) LIKE LOWER(%s)
                         OR LOWER(%s) LIKE '%%' || LOWER(product_keyword) || '%%'
                          )
                    ORDER BY LENGTH(product_keyword) DESC
                    LIMIT 5
                    """,
                    (f"%{q_norm}%", q_norm),
                )
                alias_hints = [
                    {"product_keyword": r[0], "vendor_name": r[1]}
                    for r in cur.fetchall()
                ]

            # 3. Fuzzy substring match on real vendor_names. Skip for very
            #    short queries (≤ 2 chars) so we don't return half the
            #    database.
            fuzzy_matches: list[dict] = []
            if q_norm and len(q_norm) >= 3:
                cur.execute(
                    """
                    SELECT vendor_name,
                           COUNT(*)::int AS bills,
                           SUM(amount)::numeric(12,2) AS total
                    FROM public.vendor_bills
                    WHERE review_status = 'confirmed'
                      AND vendor_name IS NOT NULL
                      AND vendor_name ILIKE %s
                    GROUP BY vendor_name
                    ORDER BY total DESC NULLS LAST
                    LIMIT 5
                    """,
                    (f"%{q_norm}%",),
                )
                fuzzy_matches = [
                    {
                        "vendor_name": r[0],
                        "bills": int(r[1] or 0),
                        "total": float(r[2] or 0),
                    }
                    for r in cur.fetchall()
                ]
    except Exception as exc:
        logger.exception("empty-hints query failed: %s", exc)
        raise HTTPException(500, f"Database query failed: {exc}")
    finally:
        conn.close()

    return {
        "query": q_norm,
        "top_vendors": top_vendors,
        "alias_hints": alias_hints,
        "fuzzy_matches": fuzzy_matches,
    }
