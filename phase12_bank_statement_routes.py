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

import io
import logging
import os
import re
import uuid
from datetime import date, datetime
from typing import Optional

import psycopg2
import pdfplumber
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

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
    rows = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()

            # ── KBank format: look for table with 6 columns ──
            data_table = None
            for t in tables:
                if len(t) >= 2 and len(t[1]) >= 6:
                    data_table = t
                    break

            if data_table is None:
                continue

            data_row = data_table[1]   # all transactions packed in one row

            col_date   = str(data_row[0] or "")
            col_amount = str(data_row[2] or "")
            col_detail = str(data_row[5] or "")

            # ── Parse dates: skip "ยอดยกมา" opening-balance line ──
            date_entries: list[date] = []
            for line in col_date.split("\n"):
                line = line.strip()
                # Must have HH:MM to be a real transaction (not opening balance)
                m = re.match(r"(\d{2})-(\d{2})-(\d{2})\s+\d{2}:\d{2}", line)
                if not m:
                    continue
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3)) + 2000
                try:
                    date_entries.append(date(y, mo, d))
                except ValueError:
                    pass

            # ── Parse amounts ──
            amount_entries: list[float] = []
            for line in col_amount.split("\n"):
                line = line.strip()
                val = _clean_number(line)
                if val > 0:
                    amount_entries.append(val)

            # ── Parse details: merge wrapped continuation lines ──
            # New entry starts with "จาก", "โอนไป", or "เพื่อชำระ"
            INCOME_PREFIXES  = ("จาก",)
            EXPENSE_PREFIXES = ("โอนไป", "เพื่อชำระ")
            ALL_PREFIXES     = INCOME_PREFIXES + EXPENSE_PREFIXES

            detail_entries: list[str] = []
            current = ""
            for line in col_detail.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if any(line.startswith(p) for p in ALL_PREFIXES):
                    if current:
                        detail_entries.append(current)
                    current = line
                else:
                    current = (current + " " + line).strip() if current else line
            if current:
                detail_entries.append(current)

            # ── Align and build rows ──
            n = min(len(date_entries), len(amount_entries), len(detail_entries))
            for i in range(n):
                txn_date = date_entries[i]
                amount   = amount_entries[i]
                detail   = detail_entries[i]

                if any(detail.startswith(p) for p in INCOME_PREFIXES):
                    credit, debit = amount, 0.0
                else:
                    credit, debit = 0.0, amount

                rows.append({
                    "txn_date":    txn_date,
                    "description": detail,
                    "debit":       debit,
                    "credit":      credit,
                    "balance":     0.0,
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


def _classify(row: dict, rules: list[dict]) -> dict:
    """
    Apply rule engine to a transaction row.
    Returns dict with category_code, source_type, match_status added.
    """
    desc = (row["description"] or "").strip()
    desc_lower = desc.lower()
    direction = "income" if row["credit"] > 0 else "expense"
    amount = row["credit"] if row["credit"] > 0 else row["debit"]

    # Special: musician fee by amount pattern (expense to individual)
    if direction == "expense" and int(amount) in MUSICIAN_AMOUNTS:
        # Check it's a transfer to a person (not a company/service)
        is_company = any(w in desc for w in ["บจก", "หจก", "บริษัท", "ห้าง", "ร้าน", "จำกัด"])
        if not is_company:
            return {**row, "direction": direction, "amount": amount,
                    "category_code": "musician_fee", "source_type": "bank_statement",
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

    # No rule matched
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
                    ON CONFLICT (txn_date, description, debit, credit, branch_code) DO NOTHING
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
        logger.info("Bank statement batch %s: %d rows (%d auto, %d review)",
                    batch_id, len(classified), auto_count, review_count)

        return {
            "success": True,
            "batch_id": batch_id,
            "total_rows": len(classified),
            "auto_classified": auto_count,
            "needs_review": review_count,
            "message": (
                f"นำเข้าสำเร็จ {len(classified)} รายการ "
                f"(จัดหมวดอัตโนมัติ {auto_count} รายการ, "
                f"รอจัดหมวด {review_count} รายการ)"
            ),
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


@router.post("/classify/{entry_id}")
def classify_entry(entry_id: str, body: ClassifyRequest):
    """TUM จัดหมวดรายการที่ needs_review ด้วยมือ"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Update the entry
            cur.execute("""
                UPDATE public.bank_statement_entries
                SET category_code = %s,
                    source_type   = %s,
                    match_status  = 'manual'
                WHERE id = %s
                RETURNING id, description, amount, direction
            """, (body.category_code, body.source_type, entry_id))
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
def add_rule(body: AddRuleRequest):
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
