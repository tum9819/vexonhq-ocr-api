"""
VEXONHQ Phase 11 — Smart Receipt Search (AI)
=============================================
Endpoints:
    POST /search/receipt      → Thai natural language → SQL → results
    GET  /search/suggestions  → ตัวอย่าง query สำหรับ UI hint

Flow:
    1. รับ query ภาษาไทย เช่น "หาบิล Makro เดือนเมษา" หรือ "รายจ่ายค่าแก๊สทั้งหมด"
    2. ส่งให้ Claude Haiku แปลงเป็น JSON filter
    3. Build parameterized SQL จาก filter (ปลอดภัย ไม่ใช้ raw SQL จาก AI)
    4. Query v_daybook แล้ว return ผลลัพธ์
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import date, datetime
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

# ─── Thai month mapping ────────────────────────────────────────────────────────

MONTHS_TH = {
    "มกราคม": 1, "มกรา": 1, "ม.ค.": 1,
    "กุมภาพันธ์": 2, "กุมภา": 2, "ก.พ.": 2,
    "มีนาคม": 3, "มีนา": 3, "มี.ค.": 3,
    "เมษายน": 4, "เมษา": 4, "เม.ย.": 4,
    "พฤษภาคม": 5, "พฤษภา": 5, "พ.ค.": 5,
    "มิถุนายน": 6, "มิถุนา": 6, "มิ.ย.": 6,
    "กรกฎาคม": 7, "กรกฎา": 7, "ก.ค.": 7,
    "สิงหาคม": 8, "สิงหา": 8, "ส.ค.": 8,
    "กันยายน": 9, "กันยา": 9, "ก.ย.": 9,
    "ตุลาคม": 10, "ตุลา": 10, "ต.ค.": 10,
    "พฤศจิกายน": 11, "พฤศจิกา": 11, "พ.ย.": 11,
    "ธันวาคม": 12, "ธันวา": 12, "ธ.ค.": 12,
}


# ─── Models ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str          # Thai natural language query
    limit: int = 50     # max rows to return


class SearchFilter(BaseModel):
    """Structured filter extracted by Claude — used to build safe SQL."""
    date_from: Optional[str] = None      # YYYY-MM-DD
    date_to: Optional[str] = None        # YYYY-MM-DD
    direction: Optional[str] = None      # 'income' | 'expense' | None (both)
    keyword: Optional[str] = None        # search in label/counterparty/source
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    source: Optional[str] = None         # pos_sale, rider_income_grab, etc.
    category_code: Optional[str] = None


# ─── Claude call ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """คุณเป็น AI ที่ช่วยแปลงคำค้นหาภาษาไทยเป็น JSON filter สำหรับระบบบัญชีร้านอาหาร

ฐานข้อมูลมีคอลัมน์เหล่านี้ใน v_daybook:
- entry_date: วันที่ (DATE)
- direction: 'income' หรือ 'expense'
- source: pos_sale, vendor_bill, rider_income_grab, rider_income_lineman,
          ar_payment, ap_payment, pos_cashflow, manual,
          salary, transfer, deposit, withdrawal
- label: รายละเอียด/ชื่อรายการ (text) — อาจเป็นข้อความดิบจาก bank statement
- counterparty: ชื่อคู่ค้า/supplier (text)
- amount: จำนวนเงิน (numeric)
- category_code: รหัสหมวดหมู่ค่าใช้จ่าย เช่น rent, staff_salary, food_raw,
                  beverage_raw, delivery_income, musician_fee, reimbursement

สำคัญ: รายการจาก bank statement มี label เป็นชื่อคนหรือตัวย่อ ไม่ใช่คำภาษาไทย
ดังนั้นให้ใช้ category_code แทน keyword สำหรับหมวดหมู่ต่อไปนี้:
- "ค่าเช่า" / "rent" → category_code: "rent"
- "เงินเดือน" / "salary" → category_code: "staff_salary"
- "ค่าดนตรี" / "นักดนตรี" → category_code: "musician_fee"
- "สำรองจ่าย" → category_code: "reimbursement"
- "เครื่องดื่ม" / "เบียร์" → category_code: "beverage_raw"

ปีในระบบเป็น AD (ค.ศ.) — ถ้าผู้ใช้บอกปี พ.ศ. ให้ลบ 543 (เช่น 2569 → 2026)
เดือนปัจจุบัน: """ + datetime.now().strftime("%Y-%m") + """

ตอบเป็น JSON เท่านั้น (ไม่มีข้อความอื่น) ตาม schema นี้:
{
  "date_from": "YYYY-MM-DD or null",
  "date_to": "YYYY-MM-DD or null",
  "direction": "income or expense or null",
  "keyword": "คำค้นหาหรือ null",
  "amount_min": number_or_null,
  "amount_max": number_or_null,
  "source": "source_code or null",
  "category_code": "code or null"
}

ตัวอย่าง:
- "หาบิล Makro เดือนเมษา" → {"date_from":"2026-04-01","date_to":"2026-04-30","direction":"expense","keyword":"Makro","amount_min":null,"amount_max":null,"source":"vendor_bill","category_code":null}
- "รายรับจาก Grab ทั้งหมด" → {"date_from":null,"date_to":null,"direction":"income","keyword":null,"amount_min":null,"amount_max":null,"source":"rider_income_grab","category_code":null}
- "ค่าเช่าเดือนที่แล้ว" → {"date_from":"PREV-MM-01","date_to":"PREV-MM-last","direction":"expense","keyword":null,"amount_min":null,"amount_max":null,"source":null,"category_code":"rent"}
- "เงินเดือนพนักงาน" → {"date_from":null,"date_to":null,"direction":"expense","keyword":null,"amount_min":null,"amount_max":null,"source":null,"category_code":"staff_salary"}
- "ค่าแก๊สเดือนนี้" → {"date_from":"YYYY-MM-01","date_to":"YYYY-MM-last","direction":"expense","keyword":"แก๊ส","amount_min":null,"amount_max":null,"source":null,"category_code":null}
- "รายจ่ายเกิน 5000 บาท" → {"date_from":null,"date_to":null,"direction":"expense","keyword":null,"amount_min":5000,"amount_max":null,"source":null,"category_code":null}"""


def _call_claude_filter(query: str) -> SearchFilter:
    """Ask Claude Haiku to parse Thai query into a structured SearchFilter."""
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
            # Extract JSON from response (Claude may wrap in ```)
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


# ─── SQL builder ──────────────────────────────────────────────────────────────

def _build_and_run_query(f: SearchFilter, limit: int) -> list[dict]:
    """Build safe parameterized SQL from SearchFilter and execute it."""
    conditions = ["1=1"]
    params: list = []

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

    sql = f"""
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
        WHERE {" AND ".join(conditions)}
        ORDER BY d.entry_date DESC, d.amount DESC
        LIMIT %s
    """
    params.append(limit)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = ["entry_date", "direction", "source", "category_name", "detail", "amount", "branch_code"]
            rows = []
            for row in cur.fetchall():
                r = dict(zip(cols, row))
                r["entry_date"] = str(r["entry_date"])
                r["amount"] = float(r["amount"])
                rows.append(r)
            return rows
    finally:
        conn.close()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/receipt")
def smart_search(body: SearchRequest):
    """
    แปลงคำค้นหาภาษาไทยเป็น SQL แล้ว return ผลลัพธ์จาก v_daybook

    ตัวอย่าง query:
    - "หาบิล Makro เดือนเมษา"
    - "รายรับจาก Grab ทั้งหมด"
    - "ค่าแก๊สเดือนนี้"
    - "รายจ่ายเกิน 5000 บาท"
    - "ค่าเช่าปีที่แล้ว"
    """
    query = body.query.strip()
    if not query:
        raise HTTPException(400, "query must not be empty")
    if len(query) > 500:
        raise HTTPException(400, "query too long (max 500 chars)")

    logger.i