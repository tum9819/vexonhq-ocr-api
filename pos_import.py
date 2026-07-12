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

import asyncio
import threading
import time
import zlib

import pandas as pd
from psycopg2.extras import execute_values
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
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

# ── In-memory job store for background imports ─────────────────────────────
# Maps job_id → {"status": ..., "result": ..., "error": ...}
# Status flow: "queued" → "processing" → "success" | "error"
# Guarded by a lock so background thread + FastAPI thread can coexist safely.
_job_lock: threading.Lock = threading.Lock()
_job_store: dict[str, dict] = {}


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
    # Stock-in refill report — checked BEFORE inventory (shares ชื่อ/รหัสวัตถุดิบ/ป้ายกำกับ
    # but has ประเภทการเติมวัตถุดิบ/เติมสินค้า which inventory never has)
    "stock_in_refill": [
        "วันที่", "ชื่อ", "รหัสวัตถุดิบ", "ป้ายกำกับ",
        "ประเภทการเติมวัตถุดิบ", "เติมสินค้า", "ค่าใช้จ่ายต่อหน่วย",
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

        # FoodStory sometimes inserts a warning row before the real header
        # e.g. "*เนื่องจากช่วงเวลาที่เลือก ครอบวันที่..." → try header=2
        first_col = str(list(df1.columns)[0]).strip() if len(df1.columns) > 0 else ""
        if first_col.startswith("*เนื่องจาก") or first_col.startswith("*เน"):
            df2 = pd.read_excel(_io.BytesIO(content), header=2)
            df2 = normalize_columns(df2)
            rtype2 = detect_report_type(list(df2.columns))
            if rtype2:
                return df2, rtype2

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
    # Audit B7-M4 fix (2026-05-28): strip non-breaking spaces + currency symbols
    # and translate accounting parens to a negative sign before float().
    # FoodStory and OCR'd reports occasionally include "(1,234.00)" for negatives
    # or embed "฿" / NBSP inside numeric cells; both previously fell through to
    # float() and returned None (silently writing 0 into the books downstream).
    s = str(v).replace(",", "").replace(" ", "").replace("฿", "").strip()
    if not s or s == "-":
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1].strip()
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
        pass
    # Other common variants can be added if needed
    return None


def delete_pos_sales_items_by_bill_ids(cur, bill_ids: list[Any]) -> int:
    """Delete existing line items for the given bill IDs.

    psycopg2 adapts Python lists to SQL arrays as text[] by default when the
    element type is a plain string. Because public.pos_sales_items.bill_id is
    uuid, we must cast the array parameter explicitly to uuid[] so Postgres can
    resolve the operator correctly.
    """
    normalized = [str(bid) for bid in bill_ids if bid is not None]
    if not normalized:
        return 0
    cur.execute(
        "DELETE FROM public.pos_sales_items WHERE bill_id = ANY(%s::uuid[])",
        (normalized,),
    )
    return getattr(cur, "rowcount", 0)

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
            # Audit B7-C5 fix (2026-05-28): was r.iloc[4], a fragile positional
            # access that reads the wrong cell if FoodStory adds/reorders a column.
            # "ส่วนลด" is in _CANONICAL_COLS so .get() resolves it through the
            # normalised header map like every other field in this parser.
            "total_discount":   to_num(r.get("ส่วนลด"))       or 0,
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
        sku = strip_html(r.get("รหัสสินค้า"))
        avg_cost  = to_num(r.get("ต้นทุนเฉลี่ย"))
        avg_price = to_num(r.get("ราคาขายเฉลี่ย"))
        category  = strip_html(r.get("หมวดสินค้า"))
        group     = strip_html(r.get("กลุ่ม"))
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
        # strip_html() is NaN-safe (returns None for pandas NaN); the bare
        # `str(x or "").strip() or None` pattern leaks 'nan' because float('nan')
        # is truthy (F-STK-1: 70% of rows had material_code='nan').
        name = strip_html(r.get("ชื่อ"))
        if not name:
            continue
        value = to_num(r.get("มูลค่าสินค้าในสต๊อก")) or 0
        total_value += value
        items.append({
            "item_name":     name,
            "material_code": strip_html(r.get("รหัสวัตถุดิบ")),
            "tag":           strip_html(r.get("ป้ายกำกับ")),
            "qty_in_stock":  to_num(r.get("จำนวนของในสต็อก")),
            "qty_max":       to_num(r.get("จำนวนสูงสุดของสต็อก")),
            "qty_diff":      to_num(r.get("ส่วนต่าง")),
            "unit":          strip_html(r.get("หน่วย")),
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
                "drawer_code":      strip_html(r.get("รหัสถาดเก็บเงิน")),
                "order_type":       strip_html(r.get("ประเภทการสั่ง")),
                "channel":          strip_html(r.get("ช่องทาง")),
                "table_label":      strip_html(r.get("โต๊ะ")),
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
            "sku":          strip_html(r.get("รหัสเมนู")),
            "item_name":    strip_html(r.get("ชื่อเมนู")) or "",
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


_CASHFLOW_EXACT_CATEGORY_RULES = {
    "i": "raw_beverage",  # TUM confirmed FoodStory shorthand: ice
    "v": "raw_veggies",
    "g": "raw_oil_gas",
}

_CASHFLOW_KEYWORD_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("packaging", ("แก้ว", "ออน")),
    ("raw_meat", ("ใส้กรอก", "ไส้กรอก", "แหนม", "แซลม่อน", "กุ้ง", "หมึก", "สามชั้น")),
    ("raw_seasoning", ("น้ำจิ้ม", "น้ำซอส")),
]


def _category_for_cashflow_description(description: str) -> Optional[str]:
    """Deterministic POS cashflow categorization for TUM-confirmed shorthand."""
    normalized = (description or "").strip().lower()
    if not normalized:
        return None

    exact = _CASHFLOW_EXACT_CATEGORY_RULES.get(normalized)
    if exact:
        return exact

    for category_code, keywords in _CASHFLOW_KEYWORD_CATEGORY_RULES:
        if category_code == "packaging":
            if all(keyword in normalized for keyword in keywords):
                return category_code
            continue
        if any(keyword in normalized for keyword in keywords):
            return category_code
    return None


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

        drawer = strip_html(r.get("รหัสถาดเก็บเงิน"))
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
        rule_category = None if is_refund else _category_for_cashflow_description(raw_desc)

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
            "category_code": "misc" if is_refund else rule_category,  # misc = closest valid code; no customer_refund in expense_categories
            "ai_cat_status": "skipped" if is_refund else ("rule" if rule_category else "pending"),
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

    # Session 28 fix — missing entry caused 6 uploaded "ยอดขายตามสินค้า"
    # files to insert 0 rows. The parser was running, returning rows
    # correctly, but the dispatch loop ("cfg = WRITER_CONFIG.get(table)")
    # falls through with a warning when the entry is absent. /recipes
    # /import-from-menu read this table to populate the recipes master,
    # so empty table = no menus auto-created.
    # Dedup on (branch_code, period_start, product_name) — same menu in
    # same month is the same row; re-importing updates aggregates.
    "pos_sales_by_product": dict(
        conflict_cols=["branch_code","period_start","product_name"],
        update_cols=["period_end","sku","product_group","category",
                     "avg_cost","avg_price","qty_sold","gross","cost_total",
                     "item_discount","net_amount","profit","avg_profit",
                     "source_import_id"]),

}


# ============================================================
# 4b. Concurrency guard + duplicate short-circuit + bulk writers
# ============================================================
# The 2026-06-26 bill_detail incident had TWO coupled causes (confirmed from
# pos_imports timing data), not one:
#   1. DOMINANT — slow re-imports (durations up to ~10 min): every re-upload of
#      the same file re-ran the ENTIRE heavy write and only discovered it was a
#      duplicate at the final `UPDATE status='success'` (uq_pos_imports_hash is a
#      PARTIAL unique index WHERE status='success', so the 'parsing' INSERT never
#      conflicts early). That heavy write contained an O(N) per-line-item
#      `SELECT id FROM pos_bills` loop (~4178 sequential round-trips for one
#      month). A lone re-import (5ab280fe) took 626 s with NO concurrent
#      bill_detail import — i.e. ~150 ms/round-trip under pooler/VPS latency
#      pressure, not lock contention. A warm single import does the same work in
#      ~1.2 s. The per-item loop makes duration scale with N × round-trip
#      latency, so any latency spike explodes it.
#   2. SECONDARY — the "canceling statement due to statement timeout / while
#      inserting index tuple in pos_bills" error: genuine lock contention on the
#      uq_pos_bills unique index, but only on the last attempts that overlapped a
#      still-running slow re-import (912eebff/87b01be1 started while baac724e was
#      still going), tripping the 2-min statement_timeout.
# Fixes (each targets a real driver):
#   (A) short-circuit a re-upload whose content already imported successfully
#       (the common case) BEFORE any work — kills the wasted 10-min re-runs and
#       the failed-row noise in the upload history.
#   (C) write bills + items in a few bulk statements; RETURNING resolves bill_id
#       in-memory instead of one SELECT per line item — removes the O(N)
#       round-trip loop so import time no longer scales with pooler latency, and
#       scales to large files.
#   (B) serialise same-scope imports with a SESSION advisory lock so two imports
#       never block each other on uq_pos_bills — defense-in-depth that removes
#       the residual concurrency which trips the timeout.

_ADVISORY_NS = 0x504F53  # "POS" — arbitrary namespace for pg_advisory locks


def _scope_lock_key(branch_code: str, rtype: str) -> int:
    """Stable positive int4 advisory-lock key for (branch, report_type)."""
    return zlib.crc32(f"{branch_code}:{rtype}".encode("utf-8")) & 0x7FFFFFFF


def _try_acquire_import_lock(conn, branch_code: str, rtype: str,
                             *, attempts: int = 10, delay: float = 0.5) -> bool:
    """Acquire a session advisory lock serialising same-scope imports.

    Non-blocking pg_try_advisory_lock in a short retry loop (~5 s total). A normal
    import finishes in ~1 s, so a competing import frees the lock quickly; and
    same-file re-uploads never reach here (the duplicate short-circuit catches
    them first), so genuine contention is rare. The window is deliberately short
    so a loser never ties up a Starlette threadpool worker for long. Returns
    False only if the scope stays busy for the whole window (a stuck import) —
    the caller then skips gracefully instead of piling on more lock contention.
    Session locks auto-release if the connection drops, so a crashed task can
    never wedge the scope permanently.
    """
    key = _scope_lock_key(branch_code, rtype)
    for _ in range(attempts):
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (_ADVISORY_NS, key))
            if cur.fetchone()[0]:
                return True
        # Failed attempt opened a transaction; roll it back so we don't sit
        # idle-in-transaction (holding a snapshot / vacuum horizon) while sleeping.
        conn.rollback()
        time.sleep(delay)
    return False


def _release_import_lock(conn, branch_code: str, rtype: str) -> None:
    key = _scope_lock_key(branch_code, rtype)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", (_ADVISORY_NS, key))
    except Exception:
        pass  # closing the connection releases it anyway


def _find_successful_import(cur, file_hash: str):
    """Most recent successful import row for this file hash, or None."""
    cur.execute(
        "SELECT id, report_type, row_count, period_start, period_end "
        "FROM public.pos_imports WHERE file_hash=%s AND status='success' "
        "ORDER BY uploaded_at DESC LIMIT 1",
        (file_hash,))
    return cur.fetchone()


def _already_imported_result(orig) -> dict:
    """Standard already_imported result payload from a pos_imports row tuple."""
    return {
        "import_id":     orig[0] if orig else "duplicate",
        "report_type":   orig[1] if orig else "unknown",
        "status":        "already_imported",
        "rows_imported": orig[2] if orig else 0,
        "period_start":  str(orig[3]) if orig and orig[3] else None,
        "period_end":    str(orig[4]) if orig and orig[4] else None,
        "detail":        {"message": "ไฟล์นี้นำเข้าไปแล้ว ข้ามซ้ำโดยอัตโนมัติ"},
    }


def _bulk_upsert_pos_bills(cur, rows: list[dict]) -> dict:
    """Upsert pos_bills in one bulk statement; return {(branch,receipt,date): id}.

    Replaces the per-row executemany upsert AND the per-line-item
    `SELECT id FROM pos_bills ...` loop: RETURNING hands back the id for every
    inserted or updated bill so line items resolve their bill_id from memory.
    parse_bill_detail dedups bills by (branch, receipt, date), so no key appears
    twice in one batch (ON CONFLICT cannot touch the same row twice).

    INVARIANT: the RETURNING columns (branch_code, receipt_code, sales_date) are
    the ON CONFLICT key and are NOT in update_cols, so RETURNING echoes the
    INCOMING values for both inserted and updated rows — that is exactly what
    makes the returned map key equal the parser's _bill_key on a re-import. Do
    not add any of those three to update_cols or change the RETURNING list
    without revisiting the line-item linkage below.
    """
    if not rows:
        return {}
    cfg = WRITER_CONFIG["pos_bills"]
    cols = list(rows[0].keys())
    template = "(" + ", ".join(f"%({c})s" for c in cols) + ")"
    sql = (
        f"INSERT INTO public.pos_bills ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT ({', '.join(cfg['conflict_cols'])}) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in cfg["update_cols"])
        + " RETURNING id, branch_code, receipt_code, sales_date"
    )
    returned = execute_values(cur, sql, rows, template=template,
                              page_size=1000, fetch=True)
    return {(r[1], r[2], r[3]): r[0] for r in returned}


def _bulk_insert_sales_items(cur, items: list[dict], bill_id_map: dict) -> int:
    """Resolve bill_id and bulk-insert line items (order-independent).

    bill_id comes from the in-memory map returned by _bulk_upsert_pos_bills; for
    any bill_key NOT in that map we fall back to ONE batch SELECT against
    pos_bills. This keeps the writer bulk (never a per-item round-trip) while
    removing any hard dependency on table-processing order: pos_bills must be
    written before items for the FK regardless, but this function self-heals if
    the in-memory map is incomplete rather than silently importing zero items or
    raising. Idempotent re-import: clears prior line items for the affected bills
    first (no UNIQUE(bill_id,line_no)); delete+insert share the caller's
    transaction so a mid-failure rolls both back — a bill never ends up with
    zero items.
    """
    if not items:
        return 0
    # Fall back to a single batch lookup for any bill not already in the map.
    missing = {it.get("_bill_key") for it in items} - set(bill_id_map)
    missing.discard(None)
    if missing:
        keys = list(missing)
        cur.execute(
            "SELECT branch_code, receipt_code, sales_date, id FROM public.pos_bills "
            "WHERE (branch_code, receipt_code, sales_date) IN %s",
            (tuple(keys),))
        bill_id_map = {**bill_id_map,
                       **{(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}}
    resolved = []
    for it in items:
        bid = bill_id_map.get(it.get("_bill_key"))
        if bid is None:
            continue
        row = {k: v for k, v in it.items() if k != "_bill_key"}
        row["bill_id"] = bid
        resolved.append(row)
    if not resolved:
        logger.warning("pos import: %d line items but none matched a bill in "
                       "pos_bills — inserted 0 items", len(items))
        return 0
    if len(resolved) < len(items):
        logger.warning("pos import: %d/%d line items had no matching bill — skipped",
                       len(items) - len(resolved), len(items))
    bill_ids = list({r["bill_id"] for r in resolved})
    delete_pos_sales_items_by_bill_ids(cur, bill_ids)
    cols = list(resolved[0].keys())
    template = "(" + ", ".join(f"%({c})s" for c in cols) + ")"
    execute_values(
        cur,
        f"INSERT INTO public.pos_sales_items ({', '.join(cols)}) VALUES %s",
        resolved, template=template, page_size=1000)
    return len(resolved)


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


@router.post("/detect-only")
async def detect_only(file: UploadFile = File(...)):
    """Dry-run: detect report type without saving to DB.

    asyncio.to_thread() moves the blocking pd.read_excel() work off the
    uvicorn event loop so other requests stay responsive while a large file
    (> 1 MB) is being parsed.  Previously this ran synchronously and blocked
    the entire server for 10-30 s on large bill_detail XLSXs.
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    try:
        _, rtype = await asyncio.to_thread(read_and_detect, content, file.filename or "")
        return {"report_type": rtype, "filename": file.filename}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Detection failed: {e}")


# ============================================================
# 5b. Background worker  (runs in FastAPI's thread-pool)
# ============================================================

def _process_import_background(
    job_id: str,
    content: bytes,
    filename: str,
    branch_code: str,
    period_year_hint: int,
    uploaded_by: Optional[str],
) -> None:
    """Heavy lifting: parse → DB write.  Called by BackgroundTasks."""
    import_id: str = ""
    conn = None
    got_lock = False
    rtype = ""

    def _set(update: dict) -> None:
        with _job_lock:
            _job_store[job_id].update(update)

    try:
        _set({"status": "processing"})

        file_hash = hashlib.sha256(content).hexdigest()

        # Parse + detect (raises ValueError-style on bad file)
        df, rtype = read_and_detect(content, filename)

        conn = get_db_conn()

        # (A) Fast duplicate short-circuit — if this exact file already imported
        # successfully, skip before inserting a row or racing on the unique
        # index. This is the common re-upload case and the source of both the
        # failed-row noise in the upload history and the lock-contention storms.
        with conn.cursor() as cur:
            dup = _find_successful_import(cur, file_hash)
        if dup:
            logger.info("pos import: already imported, skipped — file=%s rtype=%s orig_id=%s",
                        filename, rtype, dup[0])
            _set({"status": "already_imported", "result": _already_imported_result(dup)})
            return

        # (B) Serialise same-scope imports so two uploads never block each other
        # on the uq_pos_bills unique index (the statement-timeout trigger).
        got_lock = _try_acquire_import_lock(conn, branch_code, rtype)
        if not got_lock:
            logger.warning("pos import: scope busy (another import running), skipped — "
                           "branch=%s rtype=%s file=%s", branch_code, rtype, filename)
            _set({"status": "error",
                  "error": "การนำเข้าก่อนหน้าของรายงานนี้ยังทำงานอยู่ — ข้ามรอบนี้ กรุณาลองใหม่ภายหลัง"})
            return

        # Re-check under the lock: a concurrent import of the same file may have
        # finished successfully while we were waiting to acquire the lock.
        with conn.cursor() as cur:
            dup = _find_successful_import(cur, file_hash)
        if dup:
            logger.info("pos import: already imported (under lock), skipped — file=%s rtype=%s orig_id=%s",
                        filename, rtype, dup[0])
            _set({"status": "already_imported", "result": _already_imported_result(dup)})
            return

        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '10s'")  # backstop; serialisation already avoids waits
            import_id = str(uuid.uuid4())

            # stock_in_refill uses a staged import flow (not the write-through parser path)
            if rtype == "stock_in_refill":
                from stock_in_import import normalize_branch_code
                canonical_branch = normalize_branch_code(branch_code)
                cur.execute("""
                    INSERT INTO public.pos_imports
                      (id, report_type, branch_code, source_file, file_size,
                       file_hash, status, uploaded_by, uploaded_at,
                       processing_started_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'parsing', %s, now(), now())
                """, (import_id, rtype, canonical_branch, filename,
                      len(content), file_hash, uploaded_by))
                conn.commit()

                from stock_in_routes import _stage_stock_in  # lazy import — avoids circular
                _stage_stock_in(import_id, df, canonical_branch, uploaded_by, _set)
                return

            # Insert pos_imports row; processing_started_at stamps when background
            # work begins so the /recover endpoint can detect stuck imports.
            cur.execute("""
                INSERT INTO public.pos_imports
                  (id, report_type, branch_code, source_file, file_size,
                   file_hash, status, uploaded_by, uploaded_at,
                   processing_started_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'parsing', %s, now(), now())
            """, (import_id, rtype, branch_code, filename,
                  len(content), file_hash, uploaded_by))
            conn.commit()

            # Call parser
            parser = PARSERS[rtype]
            parser_kwargs: dict[str, Any] = {"year_hint": period_year_hint}
            parser_kwargs["period_start"] = date(period_year_hint, 1, 1)
            parser_kwargs["period_end"]   = date(period_year_hint, 12, 31)
            parser_kwargs["snapshot_at"]  = datetime.now()
            result = parser(df, **parser_kwargs)

            ps, pe = result["period_start"], result["period_end"]
            total_rows = 0
            bill_id_map: dict = {}

            # Write each table (same logic as original sync endpoint)
            for table, rows in result["tables"].items():
                if not rows:
                    continue
                if table == "pos_bills":
                    # (C) Bulk upsert in one statement; RETURNING gives every
                    # bill's id so line items resolve bill_id from memory below.
                    for r in rows:
                        r["source_import_id"] = import_id
                    bill_id_map = _bulk_upsert_pos_bills(cur, rows)
                    total_rows += len(rows)
                    continue
                if table == "pos_inventory_snapshots":
                    snap = rows[0]
                    snap["source_import_id"] = import_id
                    cur.execute("""
                        INSERT INTO public.pos_inventory_snapshots
                            (branch_code, snapshot_at, item_count, total_value, source_import_id)
                        VALUES (%(branch_code)s, %(snapshot_at)s, %(item_count)s,
                                %(total_value)s, %(source_import_id)s)
                    """, snap)
                    continue

                if table == "_inventory_items":
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
                    # bill_id resolved from the RETURNING map of the pos_bills
                    # bulk upsert above — no per-line-item SELECT round-trips.
                    total_rows += _bulk_insert_sales_items(cur, rows, bill_id_map)
                    continue

                cfg = WRITER_CONFIG.get(table)
                if not cfg:
                    logger.warning("No WRITER_CONFIG for table %s — skipped", table)
                    continue
                for r in rows:
                    r["source_import_id"] = import_id
                n = _upsert(cur, table, rows, **cfg)
                total_rows += n

            # Mark DB record success
            cur.execute(
                "UPDATE public.pos_imports "
                "SET status='success', period_start=%s, period_end=%s, "
                "row_count=%s, finished_at=now() WHERE id=%s",
                (ps, pe, total_rows, import_id))
            conn.commit()

        logger.info("pos import: success — id=%s rtype=%s rows=%s file=%s",
                    import_id, rtype, total_rows, filename)
        _set({
            "status": "success",
            "result": {
                "import_id":    import_id,
                "report_type":  rtype,
                "status":       "success",
                "rows_imported": total_rows,
                "period_start": str(ps) if ps else None,
                "period_end":   str(pe) if pe else None,
            },
        })

    except Exception as e:
        err_str = str(e)
        # Duplicate file — silent skip
        if "uq_pos_imports_hash" in err_str or (
            "duplicate key" in err_str and "file_hash" in err_str
        ):
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            conn2 = None
            try:
                conn2 = get_db_conn()
                file_hash2 = hashlib.sha256(content).hexdigest()
                with conn2.cursor() as cur2:
                    cur2.execute(
                        "SELECT id, report_type, row_count, period_start, period_end "
                        "FROM public.pos_imports WHERE file_hash=%s AND status='success' "
                        "ORDER BY uploaded_at DESC LIMIT 1",
                        (file_hash2,))
                    orig = cur2.fetchone()
            except Exception:
                orig = None
            finally:
                if conn2:
                    try:
                        conn2.close()
                    except Exception:
                        pass

            # Mark the duplicate attempt as failed to avoid leaving orphan 'parsing' rows
            if import_id:
                conn3 = None
                try:
                    conn3 = get_db_conn()
                    with conn3.cursor() as cur3:
                        orig_import_id = orig[0] if orig else None
                        cur3.execute(
                            "UPDATE public.pos_imports SET status=%s, error_message=%s, finished_at=now() WHERE id=%s",
                            ("failed", f"Duplicate file — same content already imported (import_id={orig_import_id})", import_id)
                        )
                        conn3.commit()
                except Exception as e:
                    logger.warning("Failed to mark duplicate import id=%s as failed: %s", import_id, e)
                finally:
                    if conn3:
                        try:
                            conn3.close()
                        except Exception:
                            pass

            _set({
                "status": "already_imported",
                "result": {
                    "import_id":    orig[0] if orig else "duplicate",
                    "report_type":  orig[1] if orig else "unknown",
                    "status":       "already_imported",
                    "rows_imported": orig[2] if orig else 0,
                    "period_start": str(orig[3]) if orig and orig[3] else None,
                    "period_end":   str(orig[4]) if orig and orig[4] else None,
                    "detail":       {"message": "ไฟล์นี้นำเข้าไปแล้ว ข้ามซ้ำโดยอัตโนมัติ"},
                },
            })
            return

        logger.exception("POS import background task failed")
        # Clear the aborted transaction on the main conn so the finally block's
        # explicit pg_advisory_unlock actually runs (an aborted txn would make it
        # raise + no-op, leaving lock release to rely solely on conn.close()).
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        # Try to mark DB record as error
        if import_id:
            conn2 = None
            try:
                conn2 = get_db_conn()
                with conn2.cursor() as cur2:
                    cur2.execute(
                        "UPDATE public.pos_imports SET status='failed', "
                        "error_message=%s WHERE id=%s",
                        (err_str[:2000], import_id))
                    conn2.commit()
            except Exception:
                pass
            finally:
                if conn2:
                    try:
                        conn2.close()
                    except Exception:
                        pass
        _set({"status": "error", "error": err_str})

    finally:
        if conn:
            if got_lock:
                _release_import_lock(conn, branch_code, rtype)
            try:
                conn.close()
            except Exception:
                pass


# ============================================================
# 5c. POST /pos/import  — returns 202 immediately
# ============================================================

@router.post("/import")
async def import_pos_excel(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    branch_code: str = Form("thawi_watthana"),
    period_year_hint: int = Form(2026),
    uploaded_by: Optional[str] = Form(None),
):
    """
    Upload one POS report (FoodStory XLSX, Grab CSV, Lineman XLSX).
    Returns 202 immediately with a job_id; processing happens in the background.
    Poll GET /pos/import/status/{job_id} to check progress.
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")

    job_id = str(uuid.uuid4())
    with _job_lock:
        _job_store[job_id] = {
            "status":   "queued",
            "filename": file.filename,
            "result":   None,
            "error":    None,
        }

    background_tasks.add_task(
        _process_import_background,
        job_id,
        content,
        file.filename or "",
        branch_code,
        period_year_hint,
        uploaded_by,
    )

    return JSONResponse(
        status_code=202,
        content={
            "job_id":   job_id,
            "status":   "queued",
            "filename": file.filename,
            "message":  "กำลังประมวลผลในพื้นหลัง — ตรวจสอบสถานะที่ GET /pos/import/status/{job_id}",
        },
    )


# ============================================================
# 5d. GET /pos/import/status/{job_id}  — poll endpoint
# ============================================================

@router.get("/import/status/{job_id}")
async def import_status(job_id: str):
    """Poll the status of a background import job."""
    with _job_lock:
        job = _job_store.get(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found (may have expired or never existed)")
    return job


# ============================================================
# 5e. ImportResponse model  (used by legacy sync + list endpoints)
# ============================================================

class ImportResponse(BaseModel):
    import_id: str
    report_type: str
    status: str
    rows_imported: int
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    detail: dict = {}

# ============================================================
# 5g. POST /pos/import_sync  — legacy synchronous endpoint
#     (kept for debugging; prefer POST /pos/import above)
# ============================================================

@router.post("/import_sync", response_model=ImportResponse)
def import_pos_excel_sync(
    file: UploadFile = File(...),
    branch_code: str = Form("thawi_watthana"),
    period_year_hint: int = Form(2026),
    uploaded_by: Optional[str] = Form(None),
):
    # Audit B7-C3 fix (2026-05-28): dropped `async` — this endpoint calls
    # read_and_detect (pd.read_excel × 3) + psycopg2.executemany synchronously,
    # and an async def runs that ON the event loop, freezing all other requests
    # for the duration (Session-36 class). Starlette runs a plain def in its
    # threadpool, which keeps the event loop free. /detect-only was already
    # fixed with asyncio.to_thread; this legacy /import_sync was missed.
    """
    [LEGACY — kept for debugging] Synchronous import. Prefer POST /pos/import.
    Upload one POS report. Auto-detects type. Blocks until done.
    """
    # Audit B7-C3: switched to sync def, so we read the SpooledTemporaryFile
    # directly (was `await file.read()` when this was async). UploadFile.file
    # is the underlying SpooledTemporaryFile and supports sync .read() the
    # same way UploadFile.read() does (UploadFile.read just wraps file.read
    # in a threadpool call).
    content = file.file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    file_hash = hashlib.sha256(content).hexdigest()

    df, rtype = read_and_detect(content, file.filename or "")

    conn = get_db_conn()
    got_lock = False
    import_id = ""
    try:
        # Fast duplicate short-circuit (mirrors the background path) — avoid
        # re-running a full import for a file whose content already imported.
        with conn.cursor() as cur:
            dup = _find_successful_import(cur, file_hash)
        if dup:
            return ImportResponse(
                import_id=dup[0],
                report_type=dup[1] or rtype,
                status="already_imported",
                rows_imported=dup[2] or 0,
                period_start=dup[3],
                period_end=dup[4],
                detail={"message": "ไฟล์นี้นำเข้าไปแล้ว ข้ามซ้ำโดยอัตโนมัติ"},
            )

        # Serialise same-scope imports (parity with the background path) so two
        # concurrent uploads never block each other on uq_pos_bills.
        got_lock = _try_acquire_import_lock(conn, branch_code, rtype)
        if not got_lock:
            raise HTTPException(
                409, "การนำเข้าก่อนหน้าของรายงานนี้ยังทำงานอยู่ — กรุณาลองใหม่ภายหลัง")
        with conn.cursor() as cur:
            dup = _find_successful_import(cur, file_hash)
        if dup:
            return ImportResponse(
                import_id=dup[0],
                report_type=dup[1] or rtype,
                status="already_imported",
                rows_imported=dup[2] or 0,
                period_start=dup[3],
                period_end=dup[4],
                detail={"message": "ไฟล์นี้นำเข้าไปแล้ว ข้ามซ้ำโดยอัตโนมัติ"},
            )

        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '10s'")
            import_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO public.pos_imports
                  (id, report_type, branch_code, source_file, file_size,
                   file_hash, status, uploaded_by, uploaded_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'parsing', %s, now())
            """, (import_id, rtype, branch_code, file.filename,
                  len(content), file_hash, uploaded_by))
            conn.commit()

            parser = PARSERS[rtype]
            parser_kwargs: dict[str, Any] = {"year_hint": period_year_hint}
            parser_kwargs["period_start"] = date(period_year_hint, 1, 1)
            parser_kwargs["period_end"]   = date(period_year_hint, 12, 31)
            parser_kwargs["snapshot_at"]  = datetime.now()
            result = parser(df, **parser_kwargs)

            ps, pe = result["period_start"], result["period_end"]
            total_rows = 0
            bill_id_map: dict = {}

            for table, rows in result["tables"].items():
                if not rows:
                    continue
                if table == "pos_bills":
                    for r in rows:
                        r["source_import_id"] = import_id
                    bill_id_map = _bulk_upsert_pos_bills(cur, rows)
                    total_rows += len(rows)
                    continue
                if table == "pos_inventory_snapshots":
                    snap = rows[0]
                    snap["source_import_id"] = import_id
                    cur.execute("""
                        INSERT INTO public.pos_inventory_snapshots
                            (branch_code, snapshot_at, item_count, total_value, source_import_id)
                        VALUES (%(branch_code)s, %(snapshot_at)s, %(item_count)s,
                                %(total_value)s, %(source_import_id)s)
                    """, snap)
                    continue

                if table == "_inventory_items":
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
                    # bill_id resolved from the RETURNING map of the pos_bills
                    # bulk upsert above — no per-line-item SELECT round-trips.
                    total_rows += _bulk_insert_sales_items(cur, rows, bill_id_map)
                    continue

                cfg = WRITER_CONFIG.get(table)
                if not cfg:
                    logger.warning("No WRITER_CONFIG for table %s — skipped", table)
                    continue
                for r in rows:
                    r["source_import_id"] = import_id
                n = _upsert(cur, table, rows, **cfg)
                total_rows += n

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
        if "uq_pos_imports_hash" in err_str or (
            "duplicate key" in err_str and "file_hash" in err_str
        ):
            try:
                conn.rollback()
            except Exception:
                pass
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
        logger.exception("POS import (sync) failed")
        # Clear the aborted transaction first, else the error-status UPDATE
        # itself fails ("current transaction is aborted") and the row stays
        # stuck at status='parsing' forever (mirrors the dup-key branch above).
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.pos_imports SET status='failed', "
                    "error_message=%s WHERE id=%s",
                    (str(e)[:2000], import_id))
                conn.commit()
        except Exception:
            pass
        raise HTTPException(500, f"Import failed: {e}")
    finally:
        if got_lock:
            _release_import_lock(conn, branch_code, rtype)
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
