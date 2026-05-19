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
    """
    Return the most recent USABLE snapshot_id for the branch.

    Session 15 defensive fix (2026-05-17):
    Plain "most recent by date" caused breakage when a partial upload (e.g.,
    a 1-row promo SKU file) overwrote the inventory and got picked as the
    latest snapshot. Now we skip "partial upload" snapshots: any snapshot
    with item_count < 50% of the recent max (last 30 days) is treated as
    incomplete and we fall back to the next-most-recent complete snapshot.

    - First, find the max item_count in the last 30 days for this branch.
    - Threshold = max(0.5 × that max, 10).
    - Return the most recent snapshot whose item_count >= threshold.
    - If no snapshot meets the threshold, return the absolute latest as a
      last resort (so we don't return None when there's at least some data).
    """
    conn = get_db_conn()
    try:
        cur = conn.cursor()

        # Step 1: max item_count over last 30 days
        cur.execute("""
            SELECT MAX(COALESCE(item_count, 0))
            FROM public.pos_inventory_snapshots
            WHERE branch_code = %s
              AND snapshot_at >= NOW() - INTERVAL '30 days'
        """, (branch_code,))
        row = cur.fetchone()
        max_count = int(row[0] or 0) if row else 0
        threshold = max(int(max_count * 0.5), 10)

        # Step 2: most recent snapshot meeting threshold
        cur.execute("""
            SELECT id, snapshot_at
            FROM public.pos_inventory_snapshots
            WHERE branch_code = %s
              AND COALESCE(item_count, 0) >= %s
            ORDER BY snapshot_at DESC
            LIMIT 1
        """, (branch_code, threshold))
        row = cur.fetchone()
        if row:
            return (str(row[0]), str(row[1]))

        # Step 3: fallback to absolute latest (never return None if any snapshot exists)
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

    # Session 15 fix (2026-05-17): exclude promo/bundle SKUs from all stock
    # queries. These are FoodStory promo packs (e.g. "Pro(3) เบียร์...",
    # "(pro) ช้าง...") that should not appear in inventory views.
    # NOTE: pass patterns as parameters, NOT inlined — otherwise psycopg2
    # treats the '%' as a placeholder and raises "list index out of range".
    conditions.append("LOWER(i.item_name) NOT LIKE %s")
    params.append("pro(%")
    conditions.append("LOWER(i.item_name) NOT LIKE %s")
    params.append("(pro%")

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

# Pack-size rules for the daily stock digest. Each tuple is
#   (item_name_substring, bottles_per_pack, pack_label).
# Order matters: more specific patterns must come BEFORE more general ones
# (e.g. "เป๊ปซี่ เล็ก" before "เป๊ปซี่", "น้ำเปล่า 1.5" before "น้ำเปล่า").
# Iteration stops at the first match.
_PACK_RULES: list[tuple[str, int, str]] = [
    # Soft drinks — use แพ็ค (retail pack) as label.
    # 1-litre bottle is bigger -> fewer per pack; check it BEFORE the
    # generic "เป๊ปซี่" rule so the substring match doesn't shadow it.
    ("เป๊ปซี่ 1 ลิตร", 12, "แพ็ค"),
    ("เป๊ปซี่ 1ลิตร",  12, "แพ็ค"),  # tolerate missing space in OCR data
    ("เป๊ปซี่",       24, "แพ็ค"),  # regular + เล็ก variants share this rate
    ("มิรินด้า",     12, "แพ็ค"),
    # Water — bottle size dictates pack size
    ("น้ำเปล่า 1.5",   6, "แพ็ค"),  # 1.5L large bottle, 6 per pack
    ("น้ำเปล่า 550",  12, "แพ็ค"),  # 550 ml bottle, 12 per pack
    ("น้ำเปล่า",      12, "แพ็ค"),  # default for other water sizes
    # Soda water (bottle form) — TUM tracks by ลัง of 24, same as beer label.
    ("โซดา",          24, "ลัง"),
    # Beer — keeps existing convention: 1 ลัง = 12 ขวด
    ("เบียร์",        12, "ลัง"),
]

# Stored-unit aliases that the LINE digest treats as a multi-bottle pack
# (so we can compute total bottles for display).
_PACK_UNITS = {"ลัง", "แพ็ค", "pack", "case"}
_BOTTLE_UNITS = {"ขวด", "btl", "bottle"}


def _fmt_qty(item_name: str, qty: float, unit: str) -> str:
    """Format quantity — annotate known SKUs with pack info.

    Examples:
        เบียร์สิงห์, 382 ขวด     -> "382 ขวด (31 ลัง)"
        เป๊ปซี่,    34 ลัง       -> "408 ขวด (34 แพ็ค)"
        เป๊ปซี่ เล็ก, 23 ขวด     -> "23 ขวด" (<24, no pack annotation)
        น้ำเปล่า 1.5 ลิตร, 47 ขวด -> "47 ขวด (7 แพ็ค)"
        มิรินด้า-ส้ม, 6 ขวด      -> "6 ขวด" (<12, no pack annotation)

    For SKUs with no matching rule we fall back to "<qty> <unit>" unchanged,
    so this is purely additive.
    """
    unit = (unit or "").strip()
    # Find the first rule whose substring is in the item name.
    pack_size = 0
    pack_label = ""
    for pattern, size, label in _PACK_RULES:
        if pattern in item_name:
            pack_size, pack_label = size, label
            break

    if not pack_size:
        return f"{qty:g} {unit}".strip()

    # Normalize qty -> bottles using the stored unit.
    if unit in _BOTTLE_UNITS:
        bottles = int(qty)
    elif unit in _PACK_UNITS:
        bottles = int(qty) * pack_size
    else:
        # Unknown unit (e.g. กระป๋อง, ลิตร) — leave alone.
        return f"{qty:g} {unit}".strip()

    if bottles <= 0:
        return f"{qty:g} {unit}".strip()

    packs = bottles // pack_size
    if packs >= 1:
        return f"{bottles} ขวด ({packs} {pack_label})"
    # Less than one pack — just show bottles, no annotation.
    return f"{bottles} ขวด"


def format_stock_for_line(items: list[dict], snapshot_at: str, title: str = "📦 เช็ค Stock") -> str:
    """Format stock items for LINE message.
    Thresholds: qty <= 0 = หมด, 0 < qty <= 10 = เหลือน้อย, > 10 = มีของพอ
    Shows ALL items in every section (no hidden counts).
    Beer items show (X ลัง) suffix.
    """
    sep = "─" * 24
    date_str = snapshot_at[:10] if snapshot_at else "-"

    if not items:
        return (
            f"{title}\n{sep}\n"
            "ไม่พบข้อมูล stock ครับ\n"
            "\U0001f4a1 Upload ไฟล์ผ่าน Dashboard ก่อน"
        )

    # Sort each bucket น้อย→มาก (urgent first)
    # เบียร์: เหลือน้อย ถ้า < 5 ลัง (60 ขวด) | อื่นๆ: เหลือน้อย ถ้า <= 10
    def _is_low(i: dict) -> bool:
        q = i["qty_current"]
        if q <= 0:
            return False
        if "เบียร์" in i["item_name"]:
            return q < 60   # 5 ลัง * 12 ขวด/ลัง
        return q <= 10

    out_of_stock = sorted([i for i in items if i["qty_current"] <= 0],
                          key=lambda x: x["qty_current"])
    low_stock    = sorted([i for i in items if i["qty_current"] > 0 and _is_low(i)],
                          key=lambda x: x["qty_current"])
    ok_stock     = sorted([i for i in items if i["qty_current"] > 0 and not _is_low(i)],
                          key=lambda x: x["qty_current"])

    lines = [title, f"\U0001f4c5 ข้อมูล: {date_str}", sep]

    # Section: หมด/ติดลบ
    if out_of_stock:
        lines.append(f"\U0001f534 หมด/ติดลบ ({len(out_of_stock)} รายการ):")
        for i in out_of_stock:
            qty_str = _fmt_qty(i["item_name"], i["qty_current"], i["unit"] or "")
            lines.append(f"  ❌ {i['item_name']}: {qty_str}")

    # Section: เหลือน้อย (1–10)
    if low_stock:
        lines.append(f"\U0001f7e1 เหลือน้อย ({len(low_stock)} รายการ):")
        for i in low_stock:
            qty_str = _fmt_qty(i["item_name"], i["qty_current"], i["unit"] or "")
            lines.append(f"  ⚠️ {i['item_name']}: {qty_str}")

    # Section: มีของพอ (>10) — แสดงทุกรายการ
    if ok_stock:
        lines.append(f"\U0001f7e2 มีของพอ ({len(ok_stock)} รายการ):")
        for i in ok_stock[:30]:
            qty_str = _fmt_qty(i["item_name"], i["qty_current"], i["unit"] or "")
            lines.append(f"  ✅ {i['item_name']}: {qty_str}")
        if len(ok_stock) > 30:
            lines.append(f"  ... และอีก {len(ok_stock)-30} รายการ")

    lines.append(sep)
    total_value = sum(i["stock_value"] for i in items)
    if total_value > 0:
        lines.append(f"\U0001f4b0 มูลค่าสต็อกรวม: ฿{total_value:,.0f}")

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
              AND LOWER(item_name) NOT LIKE 'pro(%%'
              AND LOWER(item_name) NOT LIKE '(pro%%'
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


# ─────────────────────────────────────────────
# Beer stock alert — called by scheduled cron
# ─────────────────────────────────────────────
BEER_LANG_THRESHOLD = 5   # ลัง — ต่ำกว่านี้ถือว่าเหลือน้อย
BOTTLES_PER_LANG    = 12  # ขวดต่อลัง

@router.get("/alert")
async def stock_alert_beer():
    """
    ตรวจสอบ stock เบียร์ — ถ้ามีรายการต่ำกว่า BEER_LANG_THRESHOLD ลัง
    ส่ง LINE push message ไปหา TUM ทันที
    เรียกจาก cron หรือ Coolify scheduled job ทุกเช้า
    """
    snapshot_id, snapshot_at = _get_latest_snapshot_id()
    if not snapshot_id:
        return {"ok": False, "message": "ไม่พบ snapshot"}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT item_name, qty_in_stock, unit
                FROM public.pos_inventory_items
                WHERE snapshot_id = %s
                  AND item_name ILIKE %s
                  AND LOWER(item_name) NOT LIKE 'pro(%%'
                  AND LOWER(item_name) NOT LIKE '(pro%%'
                ORDER BY qty_in_stock ASC
            """, [snapshot_id, "%เบียร์%"])
            rows = cur.fetchall()
    finally:
        conn.close()

    threshold_qty = BEER_LANG_THRESHOLD * BOTTLES_PER_LANG  # 60 ขวด

    low_items   = []
    empty_items = []
    ok_items    = []

    for name, qty, unit in rows:
        qty = float(qty or 0)
        unit = unit or "ขวด"
        if qty <= 0:
            empty_items.append((name, qty, unit))
        elif qty < threshold_qty:
            lang = int(qty) // BOTTLES_PER_LANG
            low_items.append((name, qty, unit, lang))
        else:
            lang = int(qty) // BOTTLES_PER_LANG
            ok_items.append((name, qty, unit, lang))

    date_str = snapshot_at[:10] if snapshot_at else "-"

    # ไม่มีปัญหา → ไม่ push
    if not low_items and not empty_items:
        return {
            "ok": True,
            "alerted": False,
            "message": f"เบียร์ทุกรายการมีพอ ({len(ok_items)} รายการ)",
            "date": date_str,
        }

    # ─── สร้าง LINE message ───────────────────────────────────────
    sep = "─" * 24
    lines = [
        "⚠️ แจ้งเตือน Stock เบียร์",
        f"\U0001f4c5 {date_str}",
        sep,
    ]

    if empty_items:
        lines.append(f"\U0001f534 หมด ({len(empty_items)} รายการ):")
        for name, qty, unit in empty_items:
            lines.append(f"  ❌ {name}: {qty:g} {unit}")

    if low_items:
        lines.append(f"\U0001f7e1 เหลือน้อย < {BEER_LANG_THRESHOLD} ลัง ({len(low_items)} รายการ):")
        for name, qty, unit, lang in low_items:
            lines.append(f"  ⚠️ {name}: {qty:g} {unit} ({lang} ลัง)")

    lines.append(sep)
    lines.append("\U0001f4e6 พิมพ์ 'เช็ค stock เบียร์' เพื่อดูทั้งหมด")

    msg = "\n".join(lines)

    # ─── ส่ง LINE push ────────────────────────────────────────────
    import os as _os, json as _json, urllib.request as _req, urllib.error as _uerr
    token   = _os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = _os.environ.get("LINE_USER_ID", "")

    push_result = {"sent": False, "error": None}
    if token and user_id:
        payload = _json.dumps({
            "to": user_id,
            "messages": [{"type": "text", "text": msg}],
        }).encode("utf-8")
        req = _req.Request(
            "https://api.line.me/v2/bot/message/push",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _req.urlopen(req, timeout=10):
                push_result["sent"] = True
        except _uerr.HTTPError as e:
            push_result["error"] = f"LINE {e.code}: {e.read().decode()[:100]}"
        except Exception as e:
            push_result["error"] = str(e)[:100]
    else:
        push_result["error"] = "LINE_CHANNEL_TOKEN or LINE_USER_ID not set"

    return {
        "ok": True,
        "alerted": True,
        "date": date_str,
        "empty": len(empty_items),
        "low": len(low_items),
        "push": push_result,
        "message": msg,
    }
