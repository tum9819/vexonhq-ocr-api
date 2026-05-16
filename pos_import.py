"""
VEXONHQ Phase 2 — POS Excel Import (FoodStory)
================================================
Single-module FastAPI router for importing the 7 FoodStory POS reports.

Target: vexonhq-ocr-api repo. Drop this file in next to main.py and add:
    from pos_import import router as pos_router
    app.include_router(pos_router)

Endpoints exposed (all under /pos):
    POST /pos/import           — upload XLSX, auto-detect, parse, save
    GET  /pos/imports          — paginated history of imports
    GET  /pos/imports/{id}     — single import detail
    GET  /pos/detect-only      — dry-run: detect report type without saving

Dependencies (add to requirements.txt if missing):
    openpyxl>=3.1.0
    pandas>=2.0.0
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import date, datetime
from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Reuse the project's existing DB connection helper.
# Phase 1's main.py exposes `get_db_conn()` returning psycopg connection.
# If your helper has a different name, change the import below.
try:
    from main import get_db_conn  # type: ignore
except ImportError:
    # Fallback when imported before main.get_db_conn is defined
    # (happens during circular import — main.py imports pos_import before
    # the function is defined at the line below). psycopg2 is installed
    # in production via requirements.txt.
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])


logger = logging.getLogger("pos_import")
router = APIRouter(prefix="/pos", tags=["pos"])


# ============================================================
# 1. Header signatures for auto-detection
# ============================================================
# Each entry: report_type → list of column headers that MUST appear (in any order)
# in row index 1 (the header row) of the XLSX. The MOST SPECIFIC signature wins.

SIGNATURES: dict[str, list[str]] = {
    # Most specific first (drawer must be checked before daily_summary
    # because both share "วันที่" + "ยอดก่อนลด")
    #
    # Type 8: cashflow_detail — "รายละเอียดการจ่ายเข้า/ออก"
    # Unique columns: เวลา (full datetime) + รายละเอียด + ประเภท
    # Must be checked BEFORE daily_drawer (both have รหัสถาดเก็บเงิน)
    "cashflow_detail": [
        "เวลา", "รายละเอียด", "ประเภท", "รหัสถาดเก็บเงิน",
    ],
    "daily_drawer": [
        "วันที่", "รหัสถาดเก็บเงิน", "ยอดก่อนลด", "จำนวนบิล",
    ],
    "bill_detail": [
        "วันที่ชำระเงิน", "เวลาที่ชำระเงิน", "หมายเลขใบเสร็จ / ID",
        "รหัสเมนู", "ชื่อเมนู",
    ],
    "inventory": [
        "ชื่อ", "รหัสวัตถุดิบ", "ป้ายกำกับ", "จำนวนของในสต็อก",
        "จำนวนสูงสุดของสต็อก",
    ],
    "sales_by_product": [
        "รหัสสินค้า", "ชื่อสินค้า", "หมวดสินค้า", "ต้นทุนเฉลี่ย",
        "จำนวนการขาย",
    ],
    "monthly_summary": [
        "เดือน", "ยอดก่อนลด", "ส่วนลดบิล", "จำนวนบิล",
    ],
    "payment_type_summary": [
        "ประเภทการชำระเงิน", "วิธีบันทึกรายการชำระ", "ยอดก่อนลด",
    ],
    # Rider delivery platforms — detected BEFORE normalise_columns (raw headers)
    # Lineman daily summary (XLSX, header row 0, English col names)
    "lineman_daily": [
        "time", "sales", "orders", "avgBasketSize",
    ],
    # Least specific last
    "daily_summary": [
        "วันที่", "ยอดก่อนลด", "ส่วนลดบิล", "จำนวนบิล",
    ],
}


def detect_report_type(headers: list[str]) -> Optional[str]:
    """Pick the most specific matching report_type. Returns None if no match."""
    hset = {str(h).strip() for h in headers if h is not None}
    for rtype, required in SIGNATURES.items():
        if all(col in hset for col in required):
            return rtype
    return None


# ============================================================
# 1b. File reader + auto-detector (XLSX / CSV unified)
# ============================================================
# Handles:
#   • FoodStory XLSX  (header on row index 1)
#   • Lineman XLSX    (header on row index 0, English cols)
#   • Grab CSV        (UTF-8 BOM, header on row index 0)
#
# Returns (df, report_type) or raises HTTPException 400.

def read_and_detect(content: bytes, filename: str):
    """Parse uploaded file and detect its report type.

    Returns (df, rtype) on success.
    Raises HTTPException(400) if the type cannot be determined.
    """
    import io as _io
    fname = (filename or "").lower()

    # ── CSV path (Grab Transaction) ────────────────────────────────
    if fname.endswith(".csv"):
        try:
            df = pd.read_csv(_io.BytesIO(content), encoding="utf-8-sig")
        except Exception as e:
            raise HTTPException(400, f"Cannot read CSV: {e}")
        hset = {str(h).strip() for h in df.columns if h is not None}
        # Grab Transaction: has unique combo of Thai + English headers
        if "Transaction ID" in hset and "ค่าคอมมิชชันแพลตฟอร์ม" in hset:
            return df, "grab_transaction"
        raise HTTPException(
            400,
            f"Cannot detect CSV type. Headers seen: {list(df.columns)[:10]}"
        )

    # ── XLSX path ─────────────────────────────────────────────────
    try:
        # Try header=0 first (Lineman + future non-FoodStory XLSX)
        df0 = pd.read_excel(_io.BytesIO(content), header=0)
        hset0 = {str(h).strip() for h in df0.columns if h is not None}
        if {"time", "sales", "orders", "avgBasketSize"}.issubset(hset0):
            return df0, "lineman_daily"

        # Fall back to FoodStory XLSX (header on row 1)
        df1 = pd.read_excel(_io.BytesIO(content), header=1)
        df1 = normalize_columns(df1)
        rtype = detect_report_type(list(df1.columns))
        if rtype:
            return df1, rtype

        raise HTTPException(
            400,
            f"Cannot detect report type. Headers seen: {list(df1.columns)[:10]}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Cannot read Excel: {e}")


# ============================================================
# 2. Helpers
# ============================================================

HTML_TAG_RE = re.compile(r"<[^>]+>")

def strip_html(v: Any) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return HTML_TAG_RE.sub("", str(v)).strip() or None


def to_num(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v: Any) -> Optional[int]:
    n = to_num(v)
    return int(n) if n is not None else None


def to_date(v: Any) -> Optional[date]:
    """Parse FoodStory date strings (DD/MM/YYYY) or pandas Timestamps."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    # DD/MM/YYYY
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        pass
    # YYYY-MM-DD
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def to_thtime(v: Any):
    """Parse HH:MM time strings."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "hour"):  # already datetime.time
        return v
    s = str(v).strip()
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        try:
            return datetime.strptime(s, "%H:%M:%S").time()
        except ValueError:
            return None


THAI_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}

def thai_month_to_date(m: str, year: int) -> Optional[date]:
    n = THAI_MONTHS.get(str(m).strip())
    if not n:
        return None
    return date(year, n, 1)


def is_total_row(row: pd.Series) -> bool:
    """Detect the trailing 'Total' aggregate row that FoodStory appends."""
    first = str(row.iloc[0]).strip().lower() if len(row) else ""
    return first == "total"


# Canonical short names sorted by length DESC so longer names match first
# (e.g. "ส่วนลดสินค้า %" matches before "ส่วนลดสินค้า")
_CANONICAL_COLS = sorted([
    "วันที่ชำระเงิน", "เวลาที่ชำระเงิน", "หมายเลขใบเสร็จ / ID",
    "INV. No", "รหัสถาดเก็บเงิน", "รหัสเมนู", "ชื่อเมนู",
    "ประเภทการสั่ง", "จำนวน", "ราคาต่อหน่วย",
    "ส่วนลดสินค้า %",         # MUST come before "ส่วนลดสินค้า"
    "ส่วนลดสินค้า", "ส่วนลดบิล",
    "ราคาสุทธิ", "ประเภทภาษีของรายการ", "ช่องทาง", "โต๊ะ",
    "ชื่อลูกค้า", "เบอร์โทรศัพท์",
    "ประเภทการชำระเงิน", "วิธีบันทึกรายการชำระ",
    "รหัสชำระเงินแบบกำหนดเอง", "หมายเหตุ",
    "ประเภทโปรโมชั่น", "กลุ่ม", "หมวดสินค้า",
    "เปิดบิลโดย", "ปิดบิลโดย", "สาขา",
    "วันที่", "เดือน",
    "รหัสสินค้า", "ชื่อสินค้า",
    "ต้นทุนเฉลี่ย", "ราคาขายเฉลี่ย", "จำนวนการขาย",
    "ยอดก่อนลด", "ยอดรวม",
    "ค่าบริการ", "ยอดขายสินค้าไม่มีภาษี",
    "ยอดก่อนภาษี", "ภาษี",
    "มูลค่า Voucher", "ส่วนลด Voucher",
    "ยอดปัดเศษ", "ค่าจัดส่ง",
    "รวมสุทธิ", "ทิป", "คืนเงิน",
    "ส่วนลด",                       # generic fallback (payment_type report uses bare "ส่วนลด")
    "ต้นทุน", "กำไรเฉลี่ย", "กำไร",
    "จำนวนบิล",
    "LINE MAN ยอดปรับยอด",
    "ชื่อ", "รหัสวัตถุดิบ", "ป้ายกำกับ",
    "จำนวนของในสต็อก", "จำนวนสูงสุดของสต็อก",
    "ส่วนต่าง", "หน่วย",
    "ราคาต่อหน่วย",
    "มูลค่าสินค้าในสต๊อก",
], key=len, reverse=True)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename FoodStory's verbose column names to their short canonical form.

    FoodStory exports headers like:
        'ยอดรวม ยอดก่อนลด - ส่วนลดสินค้า'
        'รวมสุทธิ (ยอดก่อนภาษี + ภาษี + ยอดปัดเศษ) -  ยอดขายสินค้าไม่มีภาษี'

    We rewrite these to the short head ('ยอดรวม', 'รวมสุทธิ', ...) so the
    parsers can use r.get('ยอดรวม') uniformly across all 7 report types.
    """
    rename: dict = {}
    seen: set = set()
    for raw in df.columns:
        if raw is None:
            continue
        s = str(raw).strip()
        for canon in _CANONICAL_COLS:
            if s == canon or s.startswith(canon + " ") or s.startswith(canon + "(") \
                    or s.startswith(canon + " ("):
                if canon not in seen:
                    rename[raw] = canon
                    seen.add(canon)
                break
    if rename:
        df = df.rename(columns=rename)
    return df


def map_branch(name: Any) -> str:
    """Map Thai branch name → branch_code. Auto-falls back to single default."""
    if not name or (isinstance(name, float) and pd.isna(name)):
        return "thawi_watthana"
    s = str(name).strip()
    return {
        "ทวีวัฒนา": "thawi_watthana",
    }.get(s, "thawi_watthana")  # default for now; expand when multi-branch


# ============================================================
# 3. Parsers — one per report_type
# ============================================================
# Each parser returns (period_start, period_end, rows_to_insert_per_table)
# where rows_to_insert_per_table is dict[table_name] -> list[dict]

def parse_monthly_summary(df: pd.DataFrame, year_hint: int = 2026, **_) -> dict:
    rows = []
    for _, r in df.iterrows():
        if is_total_row(r):
            continue
        period = thai_month_to_date(r["เดือน"], year_hint)
        if not period:
            continue
        rows.append({
            "branch_code":      map_branch(r.get("สาขา")),
            "period_month":     period,
            "gross":            to_num(r.get("ยอดก่อนลด"))      or 0,
            "item_discount":    to_num(r.get("ส่วนลดสินค้า"))     or 0,
            "bill_discount":    to_num(r.get("ส่วนลดบิล"))        or 0,
            "total":            to_num(r.get("ยอดรวม"))          or 0,
            "service_charge":   to_num(r.get("ค่าบริการ"))        or 0,
            "non_vat_sales":    to_num(r.get("ยอดขายสินค้าไม่มีภาษี")) or 0,
            "pre_tax":          to_num(r.get("ยอดก่อนภาษี"))     or 0,
            "vat":              to_num(r.get("ภาษี"))            or 0,
            "voucher_value":    to_num(r.get("มูลค่า Voucher"))   or 0,
            "voucher_discount": to_num(r.get("ส่วนลด Voucher"))  or 0,
            "rounding":         to_num(r.get("ยอดปัดเศษ"))       or 0,
            "delivery_fee":     to_num(r.get("ค่าจัดส่ง"))       or 0,
            "net_total":        to_num(r.get("รวมสุทธิ"))        or 0,
            "tip":              to_num(r.get("ทิป"))             or 0,
            "refund":           to_num(r.get("คืนเงิน"))         or 0,
            "bill_count":       to_int(r.get("จำนวนบิล"))         or 0,
        })
    ps = min(r["period_month"] for r in rows) if rows else None
    pe = max(r["period_month"] for r in rows) if rows else None
    return {"period_start": ps, "period_end": pe,
            "tables": {"pos_sales_monthly": rows}}


def parse_daily_summary(df: pd.DataFrame, **_) -> dict:
    rows = []
    for _, r in df.iterrows():
        if is_total_row(r):
            continue
        d = to_date(r["วันที่"])
        if not d:
            continue
        rows.append({
            "branch_code":      map_branch(r.get("สาขา")),
            "sales_date":       d,
            "gross":            to_num(r.get("ยอดก่อนลด"))    or 0,
            "item_discount":    to_num(r.get("ส่วนลดสินค้า"))  or 0,
            "bill_discount":    to_num(r.get("ส่วนลดบิล"))    or 0,
            "total":            to_num(r.get("ยอดรวม"))      or 0,
            "service_charge":   to_num(r.get("ค่าบริการ"))    or 0,
            "pre_tax":          to_num(r.get("ยอดก่อนภาษี"))  or 0,
            "vat":              to_num(r.get("ภาษี"))         or 0,
            "voucher_value":    to_num(r.get("มูลค่า Voucher"))  or 0,
            "voucher_discount": to_num(r.get("ส่วนลด Voucher")) or 0,
            "rounding":         to_num(r.get("ยอดปัดเศษ"))    or 0,
            "delivery_fee":     to_num(r.get("ค่าจัดส่ง"))    or 0,
            "net_total":        to_num(r.get("รวมสุทธิ"))     or 0,
            "tip":              to_num(r.get("ทิป"))          or 0,
            "refund":           to_num(r.get("คืนเงิน"))      or 0,
            "bill_count":       to_int(r.get("จำนวนบิล"))     or 0,
        })
    ps = min(r["sales_date"] for r in rows) if rows else None
    pe = max(r["sales_date"] for r in rows) if rows else None
    return {"period_start": ps, "period_end": pe,
            "tables": {"pos_sales_daily": rows}}


def parse_daily_drawer(df: pd.DataFrame, **_) -> dict:
    rows = []
    drawers = set()
    for _, r in df.iterrows():
        if is_total_row(r):
            continue
        d = to_date(r["วันที่"])
        drawer = str(r.get("รหัสถาดเก็บเงิน") or "").strip() or None
        if not d or not drawer:
            continue
        drawers.add((drawer, d))
        rows.append({
            "branch_code":      map_branch(r.get("สาขา")),
            "sales_date":       d,
            "drawer_code":      drawer,
            "gross":            to_num(r.get("ยอดก่อนลด"))    or 0,
            "item_discount":    to_num(r.get("ส่วนลดสินค้า"))  or 0,
            "bill_discount":    to_num(r.get("ส่วนลดบิล"))    or 0,
            "total":            to_num(r.get("ยอดรวม"))      or 0,
            "service_charge":   to_num(r.get("ค่าบริการ"))    or 0,
            "pre_tax":          to_num(r.get("ยอดก่อนภาษี"))  or 0,
            "vat":              to_num(r.get("ภาษี"))         or 0,
            "non_vat_sales":    to_num(r.get("ยอดขายสินค้าไม่มีภาษี")) or 0,
            "voucher_value":    to_num(r.get("มูลค่า Voucher"))  or 0,
            "voucher_discount": to_num(r.get("ส่วนลด Voucher")) or 0,
            "rounding":         to_num(r.get("ยอดปัดเศษ"))    or 0,
            "delivery_fee":     to_num(r.get("ค่าจัดส่ง"))    or 0,
            "net_total":        to_num(r.get("รวมสุทธิ"))     or 0,
            "tip":              to_num(r.get("ทิป"))          or 0,
            "refund":           to_num(r.get("คืนเงิน"))      or 0,
            "lineman_adjust":   to_num(r.get("LINE MAN ยอดปรับยอด")) or 0,
            "bill_count":       to_int(r.get("จำนวนบิล"))     or 0,
        })
    ps = min(r["sales_date"] for r in rows) if rows else None
    pe = max(r["sales_date"] for r in rows) if rows else None
    # Also seed pos_cash_drawers
    drawer_rows = [
        {"code": code, "branch_code": "thawi_watthana",
         "first_seen": d, "last_seen": d}
        for code, d in drawers
    ]
    return {"period_start": ps, "period_end": pe,
            "tables": {
                "pos_sales_drawer_daily": rows,
                "pos_cash_drawers":       drawer_rows,
            }}


def parse_payment_type_summary(df: pd.DataFrame, period_start: date,
                                period_end: date, **_) -> dict:
    rows = []
    for _, r in df.iterrows():
        if is_total_row(r):
            continue
        ptype = strip_html(r.get("ประเภทการชำระเงิน"))
        if not ptype:
            continue
        rows.append({
            "branch_code":      map_branch(r.get("สาขา")),
            "period_start":     period_start,
            "period_end":       period_end,
            "payment_type_raw": ptype,
            "payment_method":   strip_html(r.get("วิธีบันทึกรายการชำระ")),
            "custom_code":      strip_html(r.get("รหัสชำระเงินแบบกำหนดเอง")),
            "gross":            to_num(r.get("ยอดก่อนลด"))    or 0,
            "total_discount":   to_num(r.iloc[4])             or 0,  # 'ส่วนลด' merged col
            "total":            to_num(r.get("ยอดรวม"))      or 0,
            "service_charge":   to_num(r.get("ค่าบริการ"))    or 0,
            "pre_tax":          to_num(r.get("ยอดก่อนภาษี"))  or 0,
            "vat":              to_num(r.get("ภาษี"))         or 0,
            "non_vat_sales":    to_num(r.get("ยอดขายสินค้าไม่มีภาษี")) or 0,
            "voucher_value":    to_num(r.get("มูลค่า Voucher"))  or 0,
            "voucher_discount": to_num(r.get("ส่วนลด Voucher")) or 0,
            "rounding":         to_num(r.get("ยอดปัดเศษ"))    or 0,
            "net_total":        to_num(r.get("รวมสุทธิ"))     or 0,
            "tip":              to_num(r.get("ทิป"))          or 0,
            "refund":           to_num(r.get("คืนเงิน"))      or 0,
        })
    return {"period_start": period_start, "period_end": period_end,
            "tables": {"pos_sales_payment_summary": rows}}


def parse_sales_by_product(df: pd.DataFrame, period_start: date,
                            period_end: date, **_) -> dict:
    rows = []
    products = []
    for _, r in df.iterrows():
        if is_total_row(r):
            continue
        name = str(r.get("ชื่อสินค้า") or "").strip()
        if not name:
            continue
        sku = str(r.get("รหัสสินค้า") or "").strip() or None
        avg_cost  = to_num(r.get("ต้นทุนเฉลี่ย"))
        avg_price = to_num(r.get("ราคาขายเฉลี่ย"))
        category  = str(r.get("หมวดสินค้า") or "").strip() or None
        group     = str(r.get("กลุ่ม") or "").strip() or None
        rows.append({
            "branch_code":   map_branch(r.get("สาขา")),
            "period_start":  period_start,
            "period_end":    period_end,
            "sku":           sku,
            "product_name":  name,
            "product_group": group,
            "category":      category,
            "avg_cost":      avg_cost,
            "avg_price":     avg_price,
            "qty_sold":      to_int(r.get("จำนวนการขาย")) or 0,
            "gross":         to_num(r.get("ยอดก่อนลด"))   or 0,
            "cost_total":    to_num(r.get("ต้นทุน"))      or 0,
            "item_discount": to_num(r.get("ส่วนลดสินค้า")) or 0,
            "net_amount":    to_num(r.get("ราคาสุทธิ"))   or 0,
            "profit":        to_num(r.get("กำไร"))        or 0,
            "avg_profit":    to_num(r.get("กำไรเฉลี่ย"))  or 0,
        })
        # Seed pos_products master
        if sku:
            products.append({
                "sku": sku, "name": name,
                "product_group": group, "category": category,
                "avg_cost": avg_cost, "avg_price": avg_price,
                "first_seen": period_start, "last_seen": period_end,
            })
    return {"period_start": period_start, "period_end": period_end,
            "tables": {
                "pos_sales_by_product": rows,
                "pos_products":         products,
            }}


def parse_inventory(df: pd.DataFrame, snapshot_at: datetime, **_) -> dict:
    items = []
    total_value = 0.0
    for _, r in df.iterrows():
        if is_total_row(r):
            continue
        name = str(r.get("ชื่อ") or "").strip()
        if not name:
            continue
        value = to_num(r.get("มูลค่าสินค้าในสต๊อก")) or 0
        total_value += value
        items.append({
            "item_name":     name,
            "material_code": str(r.get("รหัสวัตถุดิบ") or "").strip() or None,
            "tag":           str(r.get("ป้ายกำกับ") or "").strip() or None,
            "qty_in_stock":  to_num(r.get("จำนวนของในสต็อก")),
            "qty_max":       to_num(r.get("จำนวนสูงสุดของสต็อก")),
            "qty_diff":      to_num(r.get("ส่วนต่าง")),
            "unit":          str(r.get("หน่วย") or "").strip() or None,
            "unit_price":    to_num(r.get("ราคาต่อหน่วย")),
            "stock_value":   value,
        })
    snapshot_row = {
        "branch_code":  "thawi_watthana",
        "snapshot_at":  snapshot_at,
        "item_count":   len(items),
        "total_value":  total_value,
    }
    return {"period_start": snapshot_at.date(),
            "period_end":   snapshot_at.date(),
            "tables": {
                "pos_inventory_snapshots": [snapshot_row],
                "_inventory_items":        items,  # special — linked after snapshot insert
            }}


def parse_bill_detail(df: pd.DataFrame, **_) -> dict:
    """
    The largest parser. Builds bill headers (one per receipt_code)
    AND line items (one per row).
    """
    bills: dict[tuple, dict] = {}  # (branch, receipt_code, date) → header
    items: list[dict] = []
    for line_no, (_, r) in enumerate(df.iterrows(), start=1):
        if is_total_row(r):
            continue
        d = to_date(r.get("วันที่ชำระเงิน"))
        receipt = str(r.get("หมายเลขใบเสร็จ / ID") or "").strip()
        if not d or not receipt:
            continue
        branch = map_branch(r.get("สาขา"))
        key = (branch, receipt, d)
        if key not in bills:
            bills[key] = {
                "branch_code":      branch,
                "receipt_code":     receipt,
                "invoice_no":       strip_html(r.get("INV. No")),
                "sales_date":       d,
                "sales_time":       to_thtime(r.get("เวลาที่ชำระเงิน")),
                "drawer_code":      str(r.get("รหัสถาดเก็บเงิน") or "").strip() or None,
                "order_type":       strip_html(r.get("ประเภทการสั่ง")),
                "channel":          strip_html(r.get("ช่องทาง")),
                "table_label":      str(r.get("โต๊ะ") or "").strip() or None,
                "customer_name":    strip_html(r.get("ชื่อลูกค้า")),
                "customer_phone":   strip_html(r.get("เบอร์โทรศัพท์")),
                "payment_type_raw": strip_html(r.get("ประเภทการชำระเงิน")),
                "payment_method":   strip_html(r.get("วิธีบันทึกรายการชำระ")),
                "custom_code":      strip_html(r.get("รหัสชำระเงินแบบกำหนดเอง")),
                "promo_type":       strip_html(r.get("ประเภทโปรโมชั่น")),
                "opened_by":        strip_html(r.get("เปิดบิลโดย")),
                "closed_by":        strip_html(r.get("ปิดบิลโดย")),
                "bill_gross":       0.0,
                "bill_discount":    0.0,
                "bill_net":         0.0,
            }
        # accumulate bill totals
        gross = to_num(r.get("ยอดก่อนลด")) or 0
        disc  = to_num(r.get("ส่วนลดสินค้า")) or 0
        net   = to_num(r.get("ราคาสุทธิ")) or 0
        bills[key]["bill_gross"]    += gross
        bills[key]["bill_discount"] += disc
        bills[key]["bill_net"]      += net
        # line item
        items.append({
            "_bill_key":    key,    # resolved to bill_id after bill insert
            "line_no":      line_no,
            "sku":          str(r.get("รหัสเมนู") or "").strip() or None,
            "item_name":    str(r.get("ชื่อเมนู") or "").strip(),
            "product_group": strip_html(r.get("กลุ่ม")),
            "category":     strip_html(r.get("หมวดสินค้า")),
            "qty":          to_num(r.get("จำนวน")) or 1,
            "unit_price":   to_num(r.get("ราคาต่อหน่วย")),
            "gross":        gross,
            "discount":     disc,
            "discount_pct": to_num(r.get("ส่วนลดสินค้า %")),
            "net_amount":   net,
            "vat_type":     strip_html(r.get("ประเภทภาษีของรายการ")),
            "note":         strip_html(r.get("หมายเหตุ")),
        })
    bill_rows = list(bills.values())
    ps = min(b["sales_date"] for b in bill_rows) if bill_rows else None
    pe = max(b["sales_date"] for b in bill_rows) if bill_rows else None
    return {"period_start": ps, "period_end": pe,
            "tables": {
                "pos_bills":       bill_rows,
                "_sales_items":    items,   # special — linked after bill insert
            }}


# ============================================================
# Type 8 — cashflow_detail  ("รายละเอียดการจ่ายเข้า/ออก")
# ============================================================
# FoodStory records every manual cash-in/out from the physical
# tray here.  In practice the current data is 100% เงินออก
# (cash taken OUT).  Amounts in Excel are negative; we store
# them as positive and direction is always 'expense'.
#
# Special rule — "คืนเงิน" rows:
#   is_refund = True  → category_code set to 'customer_refund'
#   These rows appear in v_daybook as source='pos_cashflow_refund'
#   so P&L can deduct them from revenue rather than add to opex.
# ============================================================

def _parse_cashflow_datetime(v: Any) -> Optional[datetime]:
    """Parse FoodStory cashflow datetime: 'DD/MM/YYYY HH:MM' or Timestamp."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    # fallback: try date-only
    d = to_date(v)
    return datetime(d.year, d.month, d.day) if d else None


def parse_cashflow_detail(df: pd.DataFrame, **_) -> dict:
    """
    Parse FoodStory Type 8: รายละเอียดการจ่ายเข้า/ออก.

    Returns rows for table: pos_cashflow_entries
    Columns used from df:
        เวลา              → txn_at  (DD/MM/YYYY HH:MM)
        รหัสถาดเก็บเงิน  → drawer_code
        รายละเอียด       → description
        ประเภท            → txn_type  (always "เงินออก" in current data)
        จำนวน             → amount  (negative in Excel → stored positive)
        สาขา              → branch_code
        หมวดหมู่         → ignored (always NaN in FoodStory export)
    """
    rows: list[dict] = []
    for _, r in df.iterrows():
        if is_total_row(r):
            continue

        txn_at = _parse_cashflow_datetime(r.get("เวลา"))
        if txn_at is None:
            continue  # skip malformed rows

        drawer = str(r.get("รหัสถาดเก็บเงิน") or "").strip() or None
        if not drawer:
            continue

        raw_desc = strip_html(r.get("รายละเอียด")) or ""
        if not raw_desc:
            continue

        raw_amount = to_num(r.get("จำนวน"))
        if raw_amount is None:
            continue
        amount = abs(raw_amount)       # store as positive

        txn_type  = strip_html(r.get("ประเภท")) or "เงินออก"
        direction = "income" if txn_type == "เงินเข้า" else "expense"

        # Detect informal customer refunds (only in เงินออก rows)
        is_refund = direction == "expense" and "คืนเงิน" in raw_desc.lower()

        rows.append({
            "txn_at":        txn_at.isoformat(),
            "txn_date":      txn_at.date().isoformat(),   # explicit — not GENERATED
            "drawer_code":   drawer,
            "description":   raw_desc,
            "txn_type":      txn_type,
            "direction":     direction,
            "amount":        amount,
            "branch_code":   map_branch(r.get("สาขา")),
            "is_refund":     is_refund,
            # Pre-seed category for refunds; everything else → pending AI cat.
            "category_code": "misc" if is_refund else None,  # misc = closest valid code; no customer_refund in expense_categories
            "ai_cat_status": "skipped" if is_refund else "pending",
        })

    ps = min(datetime.fromisoformat(r["txn_at"]).date() for r in rows) if rows else None
    pe = max(datetime.fromisoformat(r["txn_at"]).date() for r in rows) if rows else None

    return {
        "period_start": ps,
        "period_end":   pe,
        "tables": {
            "pos_cashflow_entries": rows,
        },
    }



# ============================================================
# Type grab_transaction — Grab Food daily CSV
# ============================================================
# Grab exports one CSV row per order. We aggregate to daily totals.
# Key columns (Thai):
#   วันที่สร้าง          — order creation datetime e.g. "29 Apr 2026 6:22 PM"
#   ยอด                  — gross order value (positive)
#   ค่าคอมมิชชันแพลตฟอร์ม — platform commission (negative = cost)
#   ส่วนลด (ออกโดยร้าน)  — store-funded promo (negative or 0)
#   ทั้งหมด               — net payout to store (positive)
# Filter: only rows with หมวดหมู่ == 'ชำระเงิน' (payment rows only)

_GRAB_DATE_FMTS = ["%d %b %Y %I:%M %p", "%d %b %Y %H:%M"]

def _parse_grab_date(v: Any) -> Optional[date]:
    """Parse Grab datetime string to date. e.g. '29 Apr 2026 6:22 PM'"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (datetime, date)):
        return v.date() if isinstance(v, datetime) else v
    s = str(v).strip()
    # Normalise: single-digit hour may be missing leading zero
    for fmt in _GRAB_DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # Last resort: try dateutil
    try:
        from dateutil import parser as _dp
        return _dp.parse(s).date()
    except Exception:
        return None


def parse_grab_transaction(df: pd.DataFrame, **_) -> dict:
    """Aggregate Grab CSV to daily rider_deliveries rows."""
    # Filter to payment rows only (safety — Grab only exports ชำระเงิน)
    if "หมวดหมู่" in df.columns:
        df = df[df["หมวดหมู่"].astype(str).str.strip() == "ชำระเงิน"].copy()

    daily: dict = {}  # date → aggregated values
    for _, r in df.iterrows():
        d = _parse_grab_date(r.get("วันที่สร้าง"))
        if d is None:
            continue
        gross   = to_num(r.get("ยอด")) or 0
        gp      = to_num(r.get("ค่าคอมมิชชันแพลตฟอร์ม")) or 0
        promo_s = to_num(r.get("ส่วนลด (ออกโดยร้าน)")) or 0
        payout  = to_num(r.get("ทั้งหมด")) or 0
        if d not in daily:
            daily[d] = {"gross_sales": 0.0, "gp_amount": 0.0,
                        "promo_store": 0.0, "net_payout": 0.0, "order_count": 0}
        daily[d]["gross_sales"]  += gross
        daily[d]["gp_amount"]    += gp
        daily[d]["promo_store"]  += promo_s
        daily[d]["net_payout"]   += payout
        daily[d]["order_count"]  += 1

    if not daily:
        raise HTTPException(400, "No valid Grab payment rows found")

    rows = []
    for d, agg in sorted(daily.items()):
        rows.append({
            "platform":        "grab",
            "delivery_date":   d,
            "gross_sales":     round(agg["gross_sales"], 2),
            "gp_amount":       round(agg["gp_amount"], 2),
            "gp_is_estimated": False,
            "promo_store":     round(agg["promo_store"], 2),
            "net_payout":      round(agg["net_payout"], 2),
            "order_count":     agg["order_count"],
            "branch_code":     "thawi_watthana",
        })

    dates = [r["delivery_date"] for r in rows]
    return {
        "period_start": min(dates),
        "period_end":   max(dates),
        "tables": {"rider_deliveries": rows},
    }


# ============================================================
# Type lineman_daily — Lineman daily summary XLSX
# ============================================================
# Lineman XLSX (sheet name LINEMAN) columns (English):
#   time           — YYYY-MM-DD date string or datetime
#   sales          — gross sales (baht, can be decimal)
#   orders         — order count (int)
#   avgBasketSize  — average basket (not stored, just for reference)
#
# GP is estimated at 32.1% of gross:
#   30% platform commission + 7% VAT on the commission
#   (30% × 1.07 = 32.1%)

_LINEMAN_GP_RATE = 0.321


def parse_lineman_daily(df: pd.DataFrame, **_) -> dict:
    """Parse Lineman daily XLSX to rider_deliveries rows."""
    rows = []
    for _, r in df.iterrows():
        d = to_date(r.get("time"))
        if d is None:
            continue
        gross = to_num(r.get("sales")) or 0
        if gross <= 0:
            continue
        orders = to_int(r.get("orders")) or 0
        gp_est = round(-gross * _LINEMAN_GP_RATE, 2)   # negative = expense
        payout = round(gross + gp_est, 2)               # gross - GP

        rows.append({
            "platform":        "lineman",
            "delivery_date":   d,
            "gross_sales":     round(gross, 2),
            "gp_amount":       gp_est,
            "gp_is_estimated": True,
            "promo_store":     0.0,
            "net_payout":      payout,
            "order_count":     orders,
            "branch_code":     "thawi_watthana",
        })

    if not rows:
        raise HTTPException(400, "No valid Lineman rows found")

    dates = [r["delivery_date"] for r in rows]
    return {
        "period_start": min(dates),
        "period_end":   max(dates),
        "tables": {"rider_deliveries": rows},
    }


PARSERS = {
    "monthly_summary":      parse_monthly_summary,
    "daily_summary":        parse_daily_summary,
    "daily_drawer":         parse_daily_drawer,
    "payment_type_summary": parse_payment_type_summary,
    "sales_by_product":     parse_sales_by_product,
    "inventory":            parse_inventory,
    "bill_detail":          parse_bill_detail,
    "cashflow_detail":      parse_cashflow_detail,   # Type 8 — petty cash tray
    # Rider delivery platforms
    "grab_transaction":     parse_grab_transaction,  # Grab Food CSV
    "lineman_daily":        parse_lineman_daily,     # Lineman daily XLSX
}


# ============================================================
# 4. DB writers — UPSERT per table
# ============================================================
# Re-import is idempotent: existing rows for the same (period, branch) are
# replaced. For most tables this works via UNIQUE constraint + ON CONFLICT.

def _values_clause(rows, cols):
    """Build (%(col1)s, %(col2)s, ...) tuple-list-string for executemany."""
    return ",".join(f"%({c})s" for c in cols)


def _upsert(cur, table: str, rows: list[dict], conflict_cols: list[str],
            update_cols: list[str]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"""
      INSERT INTO public.{table} ({", ".join(cols)})
      VALUES ({_values_clause(rows, cols)})
      ON CONFLICT ({", ".join(conflict_cols)}) DO UPDATE SET
      {", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)}
    """
    cur.executemany(sql, rows)
    return cur.rowcount  # may be approximate


WRITER_CONFIG = {
    "pos_sales_monthly": dict(
        conflict_cols=["branch_code","period_month"],
        update_cols=["gross","item_discount","bill_discount","total",
                     "service_charge","non_vat_sales","pre_tax","vat",
                     "voucher_value","voucher_discount","rounding",
                     "delivery_fee","net_total","tip","refund","bill_count",
                     "source_import_id"]),
    "pos_sales_daily": dict(
        conflict_cols=["branch_code","sales_date"],
        update_cols=["gross","item_discount","bill_discount","total",
                     "service_charge","pre_tax","vat","voucher_value",
                     "voucher_discount","rounding","delivery_fee","net_total",
                     "tip","refund","bill_count","source_import_id"]),
    "pos_sales_drawer_daily": dict(
        conflict_cols=["branch_code","sales_date","drawer_code"],
        update_cols=["gross","item_discount","bill_discount","total",
                     "service_charge","pre_tax","vat","non_vat_sales",
                     "voucher_value","voucher_discount","rounding",
                     "delivery_fee","net_total","tip","refund",
                     "lineman_adjust","bill_count","source_import_id"]),
    "pos_cash_drawers": dict(
        conflict_cols=["code"],
        update_cols=["last_seen"]),
    "pos_products": dict(
        conflict_cols=["sku"],
        update_cols=["name","product_group","category","avg_cost",
                     "avg_price","last_seen"]),
    "pos_bills": dict(
        conflict_cols=["branch_code","receipt_code","sales_date"],
        update_cols=["invoice_no","sales_time","drawer_code","order_type",
                     "channel","table_label","customer_name","customer_phone",
                     "payment_type_raw","payment_method","custom_code",
                     "promo_type","bill_gross","bill_discount","bill_net",
                     "opened_by","closed_by","source_import_id"]),
    # Type 8 — petty cash tray entries
    "rider_deliveries": dict(
        conflict_cols=["platform","delivery_date","branch_code"],
        update_cols=["gross_sales","gp_amount","gp_is_estimated",
                     "promo_store","net_payout","order_count",
                     "source_import_id"]),

    # Dedup key: (drawer_code, txn_at, description, amount)
    # On re-import only non-classification columns are overwritten;
    # category_code + ai_cat_status are LEFT UNCHANGED so manual overrides survive.
    "pos_cashflow_entries": dict(
        conflict_cols=["drawer_code","txn_at","description","amount"],
        update_cols=["txn_date","txn_type","direction","branch_code","is_refund"]),
}


# ============================================================
# 5. Endpoint: POST /pos/import
# ============================================================

class ImportResponse(BaseModel):
    import_id: str
    report_type: str
    status: str
    rows_imported: int
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    detail: dict = {}


@router.post("/import", response_model=ImportResponse)
async def import_pos_excel(
    file: UploadFile = File(...),
    branch_code: str = Form("thawi_watthana"),
    period_year_hint: int = Form(2026),    # used by monthly_summary
    uploaded_by: Optional[str] = Form(None),
):
    """
    Upload one FoodStory POS Excel report. Auto-detects type from header row.
    Re-uploading the same period overwrites existing rows for that period.
    """
    # 1. Read bytes + hash
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    file_hash = hashlib.sha256(content).hexdigest()

    # 2. Parse file + detect type (handles XLSX and CSV)
    df, rtype = read_and_detect(content, file.filename or "")

    # 4. Open DB + create pos_imports row
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            import_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.pos_imports
                  (id, report_type, branch_code, source_file, file_size,
                   file_hash, status, uploaded_by, uploaded_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'parsing', %s, now())
            """, (import_id, rtype, branch_code, file.filename,
                  len(content), file_hash, uploaded_by))
            conn.commit()

            # 5. Call parser
            parser = PARSERS[rtype]
            parser_kwargs: dict[str, Any] = {"year_hint": period_year_hint}
            # period hints for parsers that need them (file is whole-month-ish)
            parser_kwargs["period_start"] = date(period_year_hint, 1, 1)
            parser_kwargs["period_end"]   = date(period_year_hint, 12, 31)
            parser_kwargs["snapshot_at"]  = datetime.now()
            result = parser(df, **parser_kwargs)

            ps, pe = result["period_start"], result["period_end"]
            total_rows = 0

            # 6. Write each table
            for table, rows in result["tables"].items():
                if not rows:
                    continue
                if table == "_inventory_items":
                    # special: needs snapshot id from the snapshot we JUST inserted
                    cur.execute("SELECT id FROM pos_inventory_snapshots "
                                "WHERE source_import_id = %s "
                                "ORDER BY created_at DESC LIMIT 1",
                                (import_id,))
                    snap_row = cur.fetchone()
                    if not snap_row:
                        logger.warning("inventory items present but no snapshot row found")
                        continue
                    snap_id = snap_row[0]
                    for it in rows:
                        it["snapshot_id"] = snap_id
                    cols = list(rows[0].keys())
                    cur.executemany(
                        f"INSERT INTO pos_inventory_items ({','.join(cols)}) "
                        f"VALUES ({_values_clause(rows, cols)})", rows)
                    total_rows += len(rows)
                    continue
                if table == "_sales_items":
                    # special: resolve bill_id via natural key
                    for it in rows:
                        bk = it.pop("_bill_key")
                        cur.execute("""SELECT id FROM pos_bills
                                       WHERE branch_code=%s AND receipt_code=%s
                                         AND sales_date=%s""", bk)
                        bid = cur.fetchone()
                        if bid:
                            it["bill_id"] = bid[0]
                    rows = [r for r in rows if "bill_id" in r]
                    cols = list(rows[0].keys()) if rows else []
                    if rows:
                        cur.executemany(
                            "INSERT INTO public.pos_sales_items ({}) VALUES ({})".format(
                                ",".join(cols), _values_clause(rows, cols)),
                            rows)
                        total_rows += len(rows)
                    continue

                # Regular WRITER_CONFIG tables
                cfg = WRITER_CONFIG.get(table)
                if not cfg:
                    logger.warning("No WRITER_CONFIG for table %s â skipped", table)
                    continue
                for r in rows:
                    r["source_import_id"] = import_id
                n = _upsert(cur, table, rows, **cfg)
                total_rows += n

            # 7. Update import record
            cur.execute(
                "UPDATE public.pos_imports "
                "SET status='success', period_start=%s, period_end=%s, "
                "row_count=%s, finished_at=now() WHERE id=%s",
                (ps, pe, total_rows, import_id))
            conn.commit()

        return ImportResponse(
            import_id=import_id,
            report_type=rtype,
            status="success",
            rows_imported=total_rows,
            period_start=ps,
            period_end=pe,
        )

    except HTTPException:
        raise
    except Exception as e:
        err_str = str(e)
        # Duplicate file — silently skip, return the original import record
        if "uq_pos_imports_hash" in err_str or (
            "duplicate key" in err_str and "file_hash" in err_str
        ):
            try:
                conn.rollback()
            except Exception:
                pass
            # Look up the original import by hash
            try:
                conn2 = get_db_conn()
                with conn2.cursor() as cur2:
                    cur2.execute(
                        "SELECT id, report_type, row_count, period_start, period_end "
                        "FROM public.pos_imports WHERE file_hash=%s AND status='success' "
                        "ORDER BY uploaded_at DESC LIMIT 1",
                        (file_hash,))
                    orig = cur2.fetchone()
                conn2.close()
            except Exception:
                orig = None
            return ImportResponse(
                import_id=orig[0] if orig else "duplicate",
                report_type=orig[1] if orig else rtype,
                status="already_imported",
                rows_imported=orig[2] if orig else 0,
                period_start=orig[3] if orig else None,
                period_end=orig[4] if orig else None,
                detail={"message": "ไฟล์นี้นำเข้าไปแล้ว ข้ามซ้ำโดยอัตโนมัติ"},
            )
        logger.exception("POS import failed")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.pos_imports SET status='error', "
                    "error_message=%s WHERE id=%s",
                    (str(e)[:2000], import_id))
                conn.commit()
        except Exception:
            pass
        raise HTTPException(500, f"Import failed: {e}")
    finally:
        conn.close()


# ============================================================
# 6. GET /pos/imports  +  GET /pos/imports/{id}
# ============================================================

@router.get("/imports")
def list_imports(
    report_type: Optional[str] = None,
    branch_code: str = "thawi_watthana",
    limit: int = 20,
    offset: int = 0,
):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            where = "WHERE branch_code = %s"
            params: list = [branch_code]
            if report_type:
                where += " AND report_type = %s"
                params.append(report_type)
            cur.execute(
                "SELECT id, report_type, source_file, row_count, status, "
                "period_start, period_end, uploaded_at, finished_at, error_message "
                "FROM public.pos_imports {} "
                "ORDER BY uploaded_at DESC LIMIT %s OFFSET %s".format(where),
                (*params, limit, offset))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return {"imports": [dict(zip(cols, r)) for r in rows]}
    finally:
        conn.close()


@router.get("/imports/{import_id}")
def get_import_detail(import_id: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM public.pos_imports WHERE id = %s", (import_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Import not found")
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
    finally:
        conn.close()


# ============================================================
# 7. POST /pos/detect-only  â dry-run, no DB write
# ============================================================

@router.post("/detect-only")
async def detect_only(file: UploadFile = File(...)):
    """Detect report type from XLSX or CSV without saving."""
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    df, rtype = read_and_detect(content, file.filename or "")
    return {
        "report_type": rtype,
        "headers":     list(df.columns)[:12],
        "row_count":   len(df),
    }
