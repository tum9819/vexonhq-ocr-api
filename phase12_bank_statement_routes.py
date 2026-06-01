"""
VEXONHQ Phase 12 — KBank Statement Parser
==========================================
Endpoints:
    POST /bank-statement/upload         — upload PDF → parse → classify → insert
    GET  /bank-statement/review         — list needs_review items
    POST /bank-statement/classify/{id}  — TUM manually classifies 1 item
    POST /bank-statement/add-rule       — save new name→category rule
    GET  /bank-statement/history        — import batches history
    GET  /bank-statement/rules          — list all rules

Flow:
    1. Upload KBank PDF
    2. pdfplumber extracts table rows (date, description, debit, credit, balance)
    3. Rule engine classifies each row (keyword / name / amount_pattern)
    4. Unknown rows → match_status = 'needs_review'
    5. Auto-classified rows → inserted directly into bank_statement_entries
    6. bank_statement_entries feeds into v_daybook (Branch 7)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import uuid
from datetime import date, datetime
from typing import Optional

import psycopg2
import pdfplumber
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from auth_routes import _require_admin_role  # admin-only gate for money-mutation endpoints (audit AUD-TAX-02)

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

logger = logging.getLogger("bank_statement")
router = APIRouter(prefix="/bank-statement", tags=["bank-statement"])


# ─── Models ───────────────────────────────────────────────────────────────────

class ClassifyRequest(BaseModel):
    category_code: str
    source_type: Optional[str] = "bank_statement"
    save_rule: bool = False        # ถ้า True → บันทึก rule สำหรับครั้งต่อไป
    rule_type: Optional[str] = "name"   # keyword / name / amount_pattern
    lender: Optional[str] = None   # written to notes when tagging a loan row


class AddRuleRequest(BaseModel):
    rule_type: str                 # keyword / name / amount_pattern
    match_value: str
    direction: str                 # income / expense
    category_code: str
    source_type: Optional[str] = "bank_statement"
    priority: int = 10


# ─── KBank PDF Parser ─────────────────────────────────────────────────────────

# KBank statement date formats
_DATE_RE = re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})")


def _parse_kbank_date(raw: str) -> Optional[date]:
    """Parse DD/MM/YY or DD/MM/YYYY (BE or AD)."""
    m = _DATE_RE.search(raw.strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    if y > 2500:          # Buddhist Era
        y -= 543
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _clean_number(raw: str) -> float:
    """'1,234.56' → 1234.56"""
    if not raw:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", raw.replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _extract_transactions(pdf_bytes: bytes) -> list[dict]:
    """
    Extract transaction rows from KBank PDF (K+ / KBank Online format).

    KBank PDF structure per page:
      tables[0] = account info header
      tables[1] = transaction data — 2 rows:
        row[0] = column headers
        row[1] = ALL transactions concatenated by \\n in each cell:
          col[0] = วันที่/เวลา  e.g. "01-11-25 06:01 รับ"  (DD-MM-YY HH:MM TYPE)
          col[2] = amount      e.g. "10,000.00"
          col[3] = balance
          col[5] = รายละเอียด  e.g. "จาก X4826 บจก.ไลน์ เพย์ (ประ++"

    Direction logic (from col[5]):
      starts with "จาก"         → income  (credit)
      starts with "โอนไป"       → expense (debit)
      starts with "เพื่อชำระ"   → expense (debit)
    """
    # Line-based parser (audit batch13 B6 fix, 2026-05-30). The previous version
    # split the date/amount/detail table cells independently by "\n" and aligned
    # them by index, which drifted whenever a description wrapped to a 2nd line or
    # a transaction type wasn't จาก/โอนไป/เพื่อชำระ — silently dropping/misclassifying
    # rows (Nov-Apr drifted ~26-31k vs the statement's own รวมฝาก/รวมถอน checksum).
    #
    # KBank text lines read as:  DD-MM-YY HH:MM <type> <amount> <balance> <channel> <detail>
    # Direction is taken from the running-BALANCE delta (the balance column is ground
    # truth): balance up => income/credit, balance down => expense/debit. The type word
    # ("รับ..." vs "โอน...") is only a fallback for the very first row. Wrapped lines
    # (no leading date) append to the previous transaction's detail.
    # Verified zero-drift vs both production statements by scripts/verify_statement_parse.py.
    rows: list[dict] = []
    prev_balance: Optional[float] = None
    _date_time = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})\s+(.*)$")
    _money = re.compile(r"\d[\d,]*\.\d{2}")

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for raw in text.split("\n"):
                line = raw.strip()
                if not line:
                    continue
                m = _date_time.match(line)
                if not m:
                    # wrapped continuation of the previous transaction's detail
                    # (guard kept identical to scripts/verify_statement_parse.py so the
                    #  two parsers cannot drift — AGENTS #18)
                    if rows and "ยอดยก" not in line and "รวม" not in line[:4]:
                        rows[-1]["description"] = (rows[-1]["description"] + " " + line).strip()
                    continue

                dd, mo, yy = int(m.group(1)), int(m.group(2)), int(m.group(3)) + 2000
                rest = m.group(6).strip()

                # opening/closing balance carry line ("ยอดยกมา/ยอดยกไป <bal>") — reset
                # the running-balance baseline (handles page breaks) but is not a txn.
                if "ยอดยกมา" in rest or "ยอดยกไป" in rest:
                    bal = _money.findall(rest)
                    if bal:
                        prev_balance = float(bal[-1].replace(",", ""))
                    continue

                monies = list(_money.finditer(rest))
                if len(monies) < 2:
                    continue  # a real txn line always carries amount + running balance

                amount = float(monies[0].group().replace(",", ""))
                balance = float(monies[1].group().replace(",", ""))
                detail = rest[monies[1].end():].strip()
                type_word = rest.split()[0]

                if prev_balance is not None:
                    is_income = (balance - prev_balance) > 0
                else:
                    is_income = type_word.startswith("รับ") or "ดอกเบี้ย" in type_word
                prev_balance = balance

                try:
                    txn_date = date(yy, mo, dd)
                except ValueError:
                    continue

                rows.append({
                    "txn_date":    txn_date,
                    "description": detail,
                    "debit":       0.0 if is_income else amount,
                    "credit":      amount if is_income else 0.0,
                    "balance":     balance,
                })

    return rows


# ─── Rule Engine ──────────────────────────────────────────────────────────────

def _load_rules(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT rule_type, match_value, direction, category_code, source_type, priority
            FROM public.statement_rules
            ORDER BY priority DESC, rule_type
        """)
        cols = ["rule_type", "match_value", "direction", "category_code", "source_type", "priority"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


MUSICIAN_AMOUNTS = {600, 700, 2100, 2800}


# Built-in pattern rules — applied BEFORE DB rules. Each tuple is
#   (direction, description_substrings, source_type, category_code).
# Description match is case-insensitive substring against either the Thai
# or English form of the bank's transaction description.
#
# Source-type semantics (see EXCLUDED_PNL_SOURCES in routes that compute P&L):
#   *_payout / *_payment / pos_cash_deposit / cash_withdrawal -> NOT counted
#     in P&L because the underlying business event was already counted
#     elsewhere (vendor_bills, rider_deliveries, pos_bills).
#   utility_expense / payroll_expense / tax_expense / bank_fee -> counted
#     because these are real cash-only expenses with no other source.
_BUILTIN_PATTERNS: list[tuple[str, list[str], str, str]] = [
    # ── Income side (delivery platform payouts) ──────────────────────────
    # Already counted under rider_income_grab / rider_income_lineman from the
    # CSV/XLSX uploads, so do NOT double-count here.
    ("income", ["lineman", "lmn", "ไลน์แมน"],          "lineman_payout", "delivery_lineman"),
    ("income", ["grab", "กราบ", "GrabFood"],            "grab_payout",    "delivery_grab"),
    # Cash deposit from the POS drawer — same revenue as pos_sale.
    ("income", ["cash dep", "cdm", "นำฝากเงินสด", "เงินสด", "เงินฝาก"],
                                                       "pos_cash_deposit", "pos_cash"),
    # ── Expense side (utilities) ─────────────────────────────────────────
    ("expense", ["mea", "การไฟฟ้านครหลวง", "ค่าไฟ"],   "utility_expense", "utility_electricity"),
    ("expense", ["pea", "การไฟฟ้าส่วนภูมิภาค"],       "utility_expense", "utility_electricity"),
    ("expense", ["mwa", "การประปานครหลวง", "ค่าน้ำ"], "utility_expense", "utility_water"),
    ("expense", ["pwa", "การประปาส่วนภูมิภาค"],       "utility_expense", "utility_water"),
    ("expense", ["ais", "true", "dtac", "tot", "3bb", "อินเตอร์เน็ต", "internet"],
                                                       "utility_expense", "utility_telecom"),
    # ── Expense side (bank fees, taxes, payroll) ─────────────────────────
    ("expense", ["ค่าธรรมเนียม", "bnk chrg", "bank fee", "ค่าธรรม"],
                                                       "bank_fee",        "bank_fee"),
    # Payment-gateway / QR-payment fees — KBank "เพื่อชำระ Ref" pattern with
    # MPAY / 2C2P / LINE MAN Wongnai (QR by ttb). Counts as bank_fee in P&L.
    ("expense", ["mpay", "2c2p", "ทูซีทูพี", "line man wongnai", "qr by ttb"],
                                                       "bank_fee",        "payment_gateway_fee"),
    ("expense", ["ภาษี", "revenue dept", "สรรพากร"],   "tax_expense",     "tax"),
    ("expense", ["payroll", "salary", "เงินเดือน"],   "payroll_expense", "payroll"),
    # ── Expense side (transfers we don't count in P&L) ───────────────────
    # ATM cash withdrawal — neutral money movement, not an expense by itself.
    # If the cash is actually used for purchases, those are tracked via
    # manual_entries / pos_cashflow / vendor_bills separately.
    ("expense", ["atm", "ถอนเงิน", "ถอน"],            "cash_withdrawal", None),
]


def _try_builtin_pattern(direction: str, desc_lower: str) -> tuple[str, str] | None:
    """Match transaction description against built-in patterns.

    Returns (source_type, category_code) on match, or None to fall through
    to DB rules. Matches are direction-aware so an "ATM" outflow doesn't
    accidentally hit an inflow rule, etc.
    """
    for dir_, substrings, source_type, category_code in _BUILTIN_PATTERNS:
        if dir_ != direction:
            continue
        if any(s.lower() in desc_lower for s in substrings):
            return source_type, category_code or "bank_statement"
    return None


def _classify(row: dict, rules: list[dict]) -> dict:
    """
    Apply rule engine to a transaction row.
    Returns dict with category_code, source_type, match_status added.
    """
    desc = (row["description"] or "").strip()
    desc_lower = desc.lower()
    direction = "income" if row["credit"] > 0 else "expense"
    amount = row["credit"] if row["credit"] > 0 else row["debit"]

    # NOTE (audit batch13, 2026-05-30): the old "amount == 600/700/2100/2800 to an
    # individual -> musician_fee" heuristic was REMOVED. It mis-tagged owner/reimburse
    # transfers (e.g. to co-owner นุศรา) as musician fees and inflated the ภ.ง.ด.3 WHT.
    # Musician fees are now driven by the SLIP MEMO ("ค่าดนตรี") via the nightly slip
    # reconcile (slip_routes.reconcile_slips_to_statements), which is TUM's policy:
    # category comes from the slip note, not the amount. MUSICIAN_AMOUNTS is kept only
    # for reference / any future slip-amount cross-check.

    # Built-in patterns (delivery payouts, utilities, payroll, etc.) —
    # consulted BEFORE the DB rule table so the common Thai-banking labels
    # are classified consistently across deployments without TUM having to
    # seed the rules table.
    builtin = _try_builtin_pattern(direction, desc_lower)
    if builtin is not None:
        source_type, category_code = builtin
        return {**row, "direction": direction, "amount": amount,
                "category_code": category_code, "source_type": source_type,
                "match_status": "auto"}

    # Apply rules by priority
    for rule in rules:
        if rule["direction"] != direction:
            continue
        rt = rule["rule_type"]
        mv = rule["match_value"]

        if rt == "keyword":
            if mv.lower() in desc_lower:
                return {**row, "direction": direction, "amount": amount,
                        "category_code": rule["category_code"],
                        "source_type": rule["source_type"] or "bank_statement",
                        "match_status": "auto"}

        elif rt == "name":
            if mv in desc:  # Thai name matching (case-sensitive)
                return {**row, "direction": direction, "amount": amount,
                        "category_code": rule["category_code"],
                        "source_type": rule["source_type"] or "bank_statement",
                        "match_status": "auto"}

        elif rt == "amount_pattern":
            try:
                if abs(amount - float(mv)) < 0.01:
                    return {**row, "direction": direction, "amount": amount,
                            "category_code": rule["category_code"],
                            "source_type": rule["source_type"] or "bank_statement",
                            "match_status": "auto"}
            except ValueError:
                pass

    # No rule matched — CASH-BASIS default (audit batch13, 2026-05-30).
    # P&L is now cash/statement basis, so every real money movement must be
    # reflected correctly:
    #   - an unclassified EXPENSE (money actually left the account) is a real cost
    #     -> count it as 'other_expense' (the old 'bank_statement' tag is EXCLUDED
    #     from P&L, which under cash basis would silently drop a real expense).
    #     match_status='auto' so it is not filtered out of v_daybook (Branch 7 drops
    #     needs_review); it stays uncategorized (category_code 'other_expense') and can
    #     be refined later.
    #   - an unclassified INCOME stays OUT of P&L until TUM confirms it is genuine
    #     revenue (it could be owner capital / an inter-entity transfer — cf. B2), so
    #     it goes to the review queue and is excluded ('bank_statement').
    if direction == "expense":
        return {**row, "direction": direction, "amount": amount,
                "category_code": "other_expense", "source_type": "other_expense",
                "match_status": "auto"}
    return {**row, "direction": direction, "amount": amount,
            "category_code": None, "source_type": "bank_statement",
            "match_status": "needs_review"}


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_statement(
    file: UploadFile = File(...),
    branch_code: str = Query("thawi_watthana"),
):
    """
    อัปโหลด KBank PDF statement → parse → classify → insert

    Returns: summary ของจำนวนรายการ auto vs needs_review
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "กรุณาอัปโหลดไฟล์ PDF เท่านั้น")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > 20 * 1024 * 1024:
        raise HTTPException(400, "ไฟล์ใหญ่เกิน 20MB")

    # AGENTS #3/#10/#15/#23: pdfplumber parse (multi-page) + the per-row INSERT
    # loop are blocking/CPU-bound. Run them OFF the event loop so a monthly
    # statement upload does not freeze uvicorn -> /health/deep timeout ->
    # UptimeRobot DOWN + the in-process Discord bot dies.
    return await asyncio.to_thread(_process_statement_upload, pdf_bytes, branch_code)


def _read_pdf_summary_totals(pdf_bytes: bytes) -> dict:
    """Read the KBank statement's own รวมฝาก/รวมถอน summary line — its built-in
    checksum. Returns {"dep_n","dep_sum","wd_n","wd_sum"} (None where not found).
    Mirrors scripts/verify_statement_parse.pdf_checksum — keep in sync (AGENTS #18)."""
    num = re.compile(r"(\d+)\s+รายการ\s+([\d,]+\.\d{2})")
    out = {"dep_n": None, "dep_sum": None, "wd_n": None, "wd_sum": None}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for ln in (page.extract_text() or "").split("\n"):
                if "รวมฝาก" in ln:
                    g = num.search(ln)
                    if g:
                        out["dep_n"], out["dep_sum"] = int(g.group(1)), float(g.group(2).replace(",", ""))
                elif "รวมถอน" in ln:
                    g = num.search(ln)
                    if g:
                        out["wd_n"], out["wd_sum"] = int(g.group(1)), float(g.group(2).replace(",", ""))
    return out


def _statement_checksum(pdf_bytes: bytes, raw_rows: list) -> dict:
    """Compare parsed deposit/withdrawal totals against the statement's own
    รวมฝาก/รวมถอน checksum (audit AUD-DATA-01). Best-effort: if the summary line
    isn't found OR the read fails, returns available=False (can't verify) rather
    than blocking the import. Never raises."""
    try:
        chk = _read_pdf_summary_totals(pdf_bytes)
    except Exception:
        logger.exception("checksum: failed to read statement summary line")
        return {"ok": None, "available": False}

    dep_n = sum(1 for r in raw_rows if (r.get("credit") or 0) > 0)
    dep_sum = round(sum((r.get("credit") or 0) for r in raw_rows), 2)
    wd_n = sum(1 for r in raw_rows if (r.get("debit") or 0) > 0)
    wd_sum = round(sum((r.get("debit") or 0) for r in raw_rows), 2)

    has_dep = chk["dep_sum"] is not None
    has_wd = chk["wd_sum"] is not None
    if not has_dep and not has_wd:
        return {"ok": None, "available": False}

    dep_ok = (not has_dep) or (dep_n == chk["dep_n"] and abs(dep_sum - chk["dep_sum"]) < 0.01)
    wd_ok = (not has_wd) or (wd_n == chk["wd_n"] and abs(wd_sum - chk["wd_sum"]) < 0.01)
    return {
        "ok": bool(dep_ok and wd_ok),
        "available": True,
        "deposits": {
            "parsed_count": dep_n, "parsed_sum": dep_sum,
            "statement_count": chk["dep_n"], "statement_sum": chk["dep_sum"],
            "drift_sum": round(dep_sum - chk["dep_sum"], 2) if has_dep else None,
        },
        "withdrawals": {
            "parsed_count": wd_n, "parsed_sum": wd_sum,
            "statement_count": chk["wd_n"], "statement_sum": chk["wd_sum"],
            "drift_sum": round(wd_sum - chk["wd_sum"], 2) if has_wd else None,
        },
    }


def _process_statement_upload(pdf_bytes: bytes, branch_code: str) -> dict:
    """Sync worker: parse PDF, classify rows, insert. Runs in a thread (off loop)."""
    # Parse PDF
    try:
        raw_rows = _extract_transactions(pdf_bytes)
    except Exception as e:
        logger.exception("PDF parse failed")
        raise HTTPException(422, f"ไม่สามารถอ่านไฟล์ PDF: {e}")

    if not raw_rows:
        raise HTTPException(422, "ไม่พบรายการธุรกรรมในไฟล์ PDF นี้ กรุณาตรวจสอบรูปแบบไฟล์")

    batch_id = str(uuid.uuid4())
    conn = get_db_conn()
    try:
        rules = _load_rules(conn)
        classified = [_classify(r, rules) for r in raw_rows]

        auto_count = sum(1 for r in classified if r["match_status"] == "auto")
        review_count = sum(1 for r in classified if r["match_status"] == "needs_review")

        with conn.cursor() as cur:
            for r in classified:
                cur.execute("""
                    INSERT INTO public.bank_statement_entries
                        (import_batch_id, txn_date, description, debit, credit, balance,
                         category_code, source_type, match_status, branch_code)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (txn_date, description, debit, credit, balance, branch_code) DO NOTHING
                """, (
                    batch_id,
                    r["txn_date"],
                    r["description"],
                    r["debit"],
                    r["credit"],
                    r.get("balance", 0),
                    r["category_code"],
                    r["source_type"],
                    r["match_status"],
                    branch_code,
                ))
        conn.commit()

        # AUD-DATA-01: verify the parse against the statement's own รวมฝาก/รวมถอน
        # checksum and surface a LOUD warning instead of a silent success when the
        # numbers don't line up (the Nov-Apr/May ~10-31k silent-drift class). Import
        # still happens (don't lose data); the response flags the drift + sets
        # checksum_ok so the frontend can show a red banner.
        chk = _statement_checksum(pdf_bytes, raw_rows)
        base_msg = (
            f"นำเข้าสำเร็จ {len(classified)} รายการ "
            f"(จัดหมวดอัตโนมัติ {auto_count} รายการ, รอจัดหมวด {review_count} รายการ)"
        )
        if chk.get("available") and chk.get("ok") is False:
            d = chk.get("deposits") or {}
            w = chk.get("withdrawals") or {}
            message = (
                base_msg
                + " *** เตือน: ยอดที่อ่านได้ไม่ตรงกับใบ statement"
                + f" — รวมฝากต่าง {(d.get('drift_sum') or 0):+,.2f} บาท"
                + f", รวมถอนต่าง {(w.get('drift_sum') or 0):+,.2f} บาท."
                + " กรุณาตรวจไฟล์/รูปแบบก่อนใช้ตัวเลขนี้"
            )
        elif chk.get("available") and chk.get("ok"):
            message = base_msg + " ยอดรวมฝาก/รวมถอนตรงกับใบ statement (ตรวจ checksum แล้ว)"
        elif not chk.get("available"):
            message = base_msg + " (หมายเหตุ: ไม่พบบรรทัดสรุปรวมฝาก/รวมถอนในไฟล์ ตรวจ checksum อัตโนมัติไม่ได้)"
        else:
            message = base_msg

        logger.info("Bank statement batch %s: %d rows (%d auto, %d review) checksum_ok=%s",
                    batch_id, len(classified), auto_count, review_count, chk.get("ok"))

        return {
            "success": True,
            "batch_id": batch_id,
            "total_rows": len(classified),
            "auto_classified": auto_count,
            "needs_review": review_count,
            "checksum_ok": chk.get("ok"),
            "checksum": chk,
            "message": message,
        }
    except Exception as e:
        conn.rollback()
        logger.exception("Insert failed")
        raise HTTPException(500, f"บันทึกข้อมูลไม่สำเร็จ: {e}")
    finally:
        conn.close()


@router.get("/review")
def get_review_items(
    limit: int = Query(50),
    offset: int = Query(0),
):
    """รายการที่ต้องจัดหมวดหมู่เอง (needs_review)"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, txn_date, description, debit, credit, balance,
                       direction, amount, match_status, branch_code, created_at
                FROM public.bank_statement_entries
                WHERE match_status = 'needs_review'
                ORDER BY txn_date DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            cols = ["id", "txn_date", "description", "debit", "credit", "balance",
                    "direction", "amount", "match_status", "branch_code", "created_at"]
            rows = []
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                row["txn_date"] = str(row["txn_date"])
                row["created_at"] = str(row["created_at"])
                for f in ["debit", "credit", "balance", "amount"]:
                    row[f] = float(row[f] or 0)
                rows.append(row)

            cur.execute("SELECT COUNT(*) FROM public.bank_statement_entries WHERE match_status = 'needs_review'")
            total = cur.fetchone()[0]

        return {"total": total, "items": rows}
    finally:
        conn.close()


@router.get("/search")
def search_entries(
    q: str = Query(""),
    limit: int = Query(50),
):
    """ค้นหารายการ statement ทุก match_status (เพื่อแก้หมวดแถวที่จัดอัตโนมัติไปแล้ว).

    Match on description (ILIKE). Returns the current category_code/source_type/notes
    so the UI can show how each row is tagged now. Empty/short query → no results
    (avoid dumping the whole table).
    """
    term = (q or "").strip()
    if len(term) < 2:
        return {"items": []}
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, txn_date, description, debit, credit, balance,
                       direction, amount, match_status, category_code, source_type, notes
                FROM public.bank_statement_entries
                WHERE description ILIKE %s
                ORDER BY txn_date DESC
                LIMIT %s
            """, (f"%{term}%", limit))
            cols = ["id", "txn_date", "description", "debit", "credit", "balance",
                    "direction", "amount", "match_status", "category_code",
                    "source_type", "notes"]
            rows = []
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                row["txn_date"] = str(row["txn_date"])
                for f in ["debit", "credit", "balance", "amount"]:
                    row[f] = float(row[f] or 0)
                rows.append(row)
        return {"items": rows}
    finally:
        conn.close()


@router.post("/classify/{entry_id}")
def classify_entry(entry_id: str, body: ClassifyRequest, _admin: dict = Depends(_require_admin_role)):
    """TUM จัดหมวดรายการที่ needs_review ด้วยมือ"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Update the entry
            cur.execute("""
                UPDATE public.bank_statement_entries
                SET category_code = %s,
                    source_type   = %s,
                    match_status  = 'manual',
                    notes         = COALESCE(%s, notes),
                    classified_by = %s,
                    classified_at = now()
                WHERE id = %s
                RETURNING id, description, amount, direction
            """, (body.category_code, body.source_type, body.lender,
                  (_admin or {}).get("sub"), entry_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "ไม่พบรายการนี้")

            # Optionally save as new rule
            if body.save_rule and row[1]:
                desc = row[1]
                direction = row[3]
                # Use first meaningful word/name as match value
                match_val = desc.split()[0] if desc.split() else desc[:20]
                cur.execute("""
                    INSERT INTO public.statement_rules
                        (rule_type, match_value, direction, category_code, source_type, priority)
                    VALUES (%s, %s, %s, %s, %s, 10)
                    ON CONFLICT (rule_type, match_value) DO UPDATE
                        SET category_code = EXCLUDED.category_code,
                            source_type   = EXCLUDED.source_type
                """, (body.rule_type, match_val, direction, body.category_code, body.source_type))

        conn.commit()
        return {"success": True, "entry_id": entry_id, "category_code": body.category_code}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"อัปเดตไม่สำเร็จ: {e}")
    finally:
        conn.close()


@router.post("/add-rule")
def add_rule(body: AddRuleRequest, _admin: dict = Depends(_require_admin_role)):
    """เพิ่ม rule ใหม่สำหรับการจัดหมวดอัตโนมัติในครั้งถัดไป"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO public.statement_rules
                    (rule_type, match_value, direction, category_code, source_type, priority)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (rule_type, match_value) DO UPDATE
                    SET category_code = EXCLUDED.category_code,
                        source_type   = EXCLUDED.source_type,
                        priority      = EXCLUDED.priority
                RETURNING id
            """, (body.rule_type, body.match_value, body.direction,
                  body.category_code, body.source_type, body.priority))
            rule_id = cur.fetchone()[0]
        conn.commit()
        return {"success": True, "rule_id": str(rule_id)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"บันทึก rule ไม่สำเร็จ: {e}")
    finally:
        conn.close()


@router.get("/rules")
def list_rules():
    """แสดง rule ทั้งหมด"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, rule_type, match_value, direction, category_code, source_type, priority
                FROM public.statement_rules
                ORDER BY priority DESC, rule_type, match_value
            """)
            cols = ["id", "rule_type", "match_value", "direction", "category_code", "source_type", "priority"]
            return {"rules": [dict(zip(cols, r)) for r in cur.fetchall()]}
    finally:
        conn.close()


@router.get("/history")
def import_history(limit: int = Query(20)):
    """ประวัติการ import statement แต่ละ batch"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    import_batch_id,
                    MIN(txn_date)      AS from_date,
                    MAX(txn_date)      AS to_date,
                    COUNT(*)           AS total_rows,
                    SUM(CASE WHEN match_status = 'needs_review' THEN 1 ELSE 0 END) AS review_count,
                    SUM(CASE WHEN direction = 'income'  THEN amount ELSE 0 END) AS total_income,
                    SUM(CASE WHEN direction = 'expense' THEN amount ELSE 0 END) AS total_expense,
                    MIN(created_at)    AS imported_at
                FROM public.bank_statement_entries
                GROUP BY import_batch_id
                ORDER BY MIN(created_at) DESC
                LIMIT %s
            """, (limit,))
            cols = ["batch_id", "from_date", "to_date", "total_rows", "review_count",
                    "total_income", "total_expense", "imported_at"]
            rows = []
            for r in cur.fetchall():
                row = dict(zip(cols, r))
                for f in ["from_date", "to_date", "imported_at"]:
                    row[f] = str(row[f])
                for f in ["total_income", "total_expense"]:
                    row[f] = float(row[f] or 0)
                rows.append(row)
        return {"batches": rows}
    finally:
        conn.close()
