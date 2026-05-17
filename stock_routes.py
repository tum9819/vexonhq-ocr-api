"""
VEXONHQ Phase 27 — Stock Query Routes
=======================================
ดึงข้อมูลสต็อกจาก pos_inventory_items (import ผ่านระบบเดิม batch_import_local.py)
ไม่สร้าง table ใหม่ — ใช้ตารางที่มีอยู่แล้ว:
  pos_inventory_snapshots  — header (snapshot_at, branch_code, item_count)
  pos_inventory_items      — รายการสินค้า (item_name, qty_in_stock, unit, ...)
  v_low_stock              — VIEW รายการที่ stock ต่ำกว่า max

Import ไฟล์ผ่าน Dashboard (Type: รายงานสินค้าคงคลัง) → ระบบ detect "inventory" อัตโนมัติ

Endpoints:
  GET  /stock/summary      — สรุปตาม tag (หม่าล่า, เครื่องดื่ม, ...)
  GET  /stock/low          — รายการหมด/ติดลบ/เหลือน้อย
  GET  /stock/search?q=    — ค้นหาตามชื่อสินค้า
  GET  /stock/all          — ทุกรายการ (latest snapshot)
  GET  /stock/latest-date  — วันที่ import ล่าสุด
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import psycopg2
from fastapi import APIRouter, HTTPException, Query

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

log = logging.getLogger("stock_routes")
router = APIRouter(prefix="/stock", tags=["stock"])


# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────

def _get_latest_snapshot_id(branch_code: str = "thawi_watthana") -> Optional[str]:
    """Return latest snapshot_id for the branch."""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, snapshot_at
            FROM public.pos_inventory_snapshots
            WHERE branch_code = %s
            ORDER BY snapshot_at DESC
            LIMIT 1
        """, (branch_code,))
        row = cur.fetchone()
        return (str(row[0]), str(row[1])) if row else (None, None)
    finally:
        conn.close()


def _query_inventory(
    keyword: Optional[str] = None,
    tag: Optional[str] = None,
    low_only: bool = False,
    branch_code: str = "thawi_watthana",
) -> tuple[list[dict], str]:
    """
    Query latest snapshot items.
    Returns (items, snapshot_at_str).
    """
    snapshot_id, snapshot_at = _get_latest_snapshot_id(branch_code)
    if not snapshot_id:
        return [], ""

    conditions = ["i.snapshot_id = %s"]
    params: list = [snapshot_id]

    if keyword:
        conditions.append("i.item_name ILIKE %s")
        params.append(f"%{keyword}%")
    if tag:
        conditions.append("i.tag ILIKE %s")
        params.append(f"%{tag}%")
    if low_only:
        conditions.append("i.qty_in_stock <= 0")

    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT
                i.item_name,
                i.material_code,
                i.tag,
                COALESCE(i.qty_in_stock, 0)  AS qty_current,
                COALESCE(i.qty_max, 0)        AS qty_max,
                COALESCE(i.qty_diff, 0)       AS qty_diff,
                i.unit,
                COALESCE(i.unit_price, 0)     AS price_per_unit,
                COALESCE(i.stock_value, 0)    AS stock_value
            FROM public.pos_inventory_items i
            WHERE {" AND ".join(conditions)}
            ORDER BY i.tag NULLS LAST, i.qty_in_stock ASC, i.item_name
        """, params)
        cols = ["item_name","material_code","tag","qty_current","qty_max",
                "qty_diff","unit","price_per_unit","stock_value"]
        items = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ("qty_current","qty_max","qty_diff","price_per_unit","stock_value"):
                d[k] = float(d[k] or 0)
            items.append(d)
        return items, snapshot_at
    finally:
        conn.close()


# ─────────────────────────────────────────────
# LINE-friendly formatters
# ─────────────────────────────────────────────

def _fmt_qty(item_name: str, qty: float, unit: str) -> str:
    """Format quantity — add (X ลัง) for beer items (1 ลัง = 12 ขวด)."""
    unit = unit or ""
    if "เบียร์" in item_name and unit in ("ขวด", "btl") and qty >= 12:
        lang = int(qty) // 12
        return f"{qty:g} ขวด ({lang} ลัง)"
    return f"{qty:g} {unit}".strip()


def format_stock_for_line(items: list[dict], snapshot_at: str, title: str = "📦 เช็ค Stock") -> str:
    """Format stock items for LINE message.
    Thresholds: qty <= 0 = หมด, 0 < qty <= 10 = เหลือน้อย, > 10 = มีของพอ
    Shows ALL items in every section (no hidden counts).
    Beer items show (X ลัง) suffix.
    """
    sep = "\u2500" * 24
    date_str = snapshot_at[:10] if snapshot_at else "-"

    if not items:
        return (
            f"{title}\n{sep}\n"
            "ไม่พบข้อมูล stock ครับ\n"
            "\U0001f4a1 Upload ไฟล์ผ่าน Dashboard ก่อน"
        )

    # Sort each bucket น้อย→มาก (urgent first)
    out_of_stock = sorted([i for i in items if i["qty_current"] <= 0],
                          key=lambda x: x["qty_current"])
    low_stock    = sorted([i for i in items if 0 < i["qty_current"] <= 10],
                          key=lambda x: x["qty_current"])
    ok_stock     = sorted([i for i in items if i["qty_current"] > 10],
                          key=lambda x: x["qty_current"])

    lines = [title, f"\U0001f4c5 ข้อมูล: {date_str}", sep]

    # Section: หมด/ติดลบ
    if out_of_stock:
        lines.append(f"\U0001f534 หมด/ติดลบ ({len(out_of_stock)} รายการ):")
        for i in out_of_stock:
            qty_str = _fmt_qty(i["item_name"], i["qty_current"], i["unit"] or "")
            lines.append(f"  \u274c {i['item_name']}: {qty_str}")

    # Section: เหลือน้อย (1–10)
    if low_stock:
        lines.append(f"\U0001f7e1 เหลือน้อย ({len(low_stock)} รายการ):")
        for i in low_stock:
            qty_str = _fmt_qty(i["item_name"], i["qty_current"], i["unit"] or "")
            lines.append(f"  \u26a0\ufe0f {i['item_name']}: {qty_str}")

    # Section: มีของพอ (>10) — แสดงทุกรายการ
    if ok_stock:
        lines.append(f"\U0001f7e2 มีของพอ ({len(ok_stock)} รายการ):")
        for i in ok_stock[:30]:
            qty_str = _fmt_qty(i["item_name"], i["qty_current"], i["unit"] or "")
            lines.append(f"  \u2705 {i['item_name']}: {qty_str}")
        if len(ok_stock) > 30:
            lines.append(f"  ... และอีก {len(ok_stock)-30} รายการ")

    lines.append(sep)
    total_value = sum(i["stock_value"] for i in items)
    if total_value > 0:
        lines.append(f"\U0001f4b0 มูลค่าสต็อกรวม: \u0e3f{total_value:,.0f}")

    return "\n".join(lines)


def format_product_stock_for_line(items: list[dict], snapshot_at: str, query: str) -> str:
    """Format stock result for a specific product name search."""
    sep = "─" * 24
    date_str = snapshot_at[:10] if snapshot_at else "-"

    if not items:
        return (
            f"📦 Stock: {query}\n{sep}\n"
            "ไม่พบสินค้านี้ในระบบครับ\n"
            "💡 ลองพิมพ์ชื่อให้ใกล้เคียงกว่านี้\n"
            "หรือพิมพ์ 'เช็ค stock' เพื่อดูทั้งหมด"
        )

    lines = [f"📦 Stock: {query}", f"📅 ข้อมูล: {date_str}", sep]

    for i in items[:15]:
        qty = i["qty_current"]
        unit = i["unit"] or "ชิ้น"
        if qty <= 0:
            icon, status = "🔴", "หมด"
        elif qty <= 5:
            icon, status = "🟡", "เหลือน้อย"
        else:
            icon, status = "🟢", "มีของ"
        val = f" | ฿{i['stock_value']:,.0f}" if i["stock_value"] > 0 else ""
        lines.append(f"{icon} {i['item_name']}")
        lines.append(f"   {qty:g} {unit} — {status}{val}")

    if len(items) > 15:
        lines.append(f"... และอีก {len(items)-15} รายการ")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.get("/latest-date")
def stock_latest_date():
    """Return the latest snapshot date."""
    _, snapshot_at = _get_latest_snapshot_id()
    return {"snapshot_at": snapshot_at}


@router.get("/summary")
def stock_summary():
    """Summary by tag (หม่าล่า, เครื่องดื่ม, MENU, ...) — latest snapshot."""
    conn = get_db_conn()
    try:
        snapshot_id, snapshot_at = _get_latest_snapshot_id()
        if not snapshot_id:
            return {"snapshot_at": None, "categories": []}

        cur = conn.cursor()
        cur.execute("""
            SELECT
                COALESCE(tag, 'ไม่ระบุ') AS tag,
                COUNT(*) AS item_count,
                SUM(CASE WHEN COALESCE(qty_in_stock,0) <= 0 THEN 1 ELSE 0 END) AS out_count,
                SUM(CASE WHEN COALESCE(qty_in_stock,0) > 0
                          AND COALESCE(qty_in_stock,0) <= 5 THEN 1 ELSE 0 END) AS low_count,
                SUM(COALESCE(qty_in_stock, 0)) AS total_qty,
                SUM(COALESCE(stock_value, 0)) AS total_value
            FROM public.pos_inventory_items
            WHERE snapshot_id = %s
            GROUP BY COALESCE(tag, 'ไม่ระบุ')
            ORDER BY tag
        """, (snapshot_id,))
        cols = ["tag","item_count","out_count","low_count","total_qty","total_value"]
        cats = [dict(zip(cols, r)) for r in cur.fetchall()]
        for c in cats:
            for k in ("item_count","out_count","low_count"):
                c[k] = int(c[k] or 0)
            for k in ("total_qty","total_value"):
                c[k] = float(c[k] or 0)
        return {"snapshot_at": snapshot_at, "categories": cats}
    finally:
        conn.close()


@router.get("/low")
def stock_low(tag: Optional[str] = Query(default=None)):
    """Items with qty_in_stock <= 0 — latest snapshot."""
    items, snap = _query_inventory(low_only=True, tag=tag)
    return {"snapshot_at": snap, "count": len(items), "items": items}


@router.get("/search")
def stock_search(q: str = Query(...)):
    """Search by product name keyword."""
    items, snap = _query_inventory(keyword=q)
    return {"query": q, "snapshot_at": snap, "count": len(items), "items": items}


@router.get("/all")
def stock_all(tag: Optional[str] = Query(default=None)):
    """All items from latest snapshot, optionally filtered by tag."""
    items, snap = _query_inventory(tag=tag)
    return {"snapshot_at": snap, "count": len(items), "items": items}
