"""
VEXONHQ — Slip Processing System (Session 27, Phase 1-6)
=========================================================
Endpoints behind the new `/slips` UX. A "slip" is the K+ transfer
confirmation TUM sends when paying musicians, staff, beverage suppliers,
etc. Each slip is an image that contains:

    transfer_date  + transfer_time + amount + memo + recipient + ref_no

The MEMO field is the "gold" signal — TUM types intent himself ("เบียร์ช้าง",
"ดนตรี วันอังคาร", "เงินเดือนพี่นุศรา") so we can classify by memo more
reliably than by OCR'd invoice product names. From there, we three-way
match each slip against:

  (a) `bank_statement_entries` — proves the money actually left the bank
  (b) `vendor_bills`          — links the slip to an invoice line

International accounting reference: 3-Way Match (Invoice + Slip + Statement).
Mirror of SAP/Oracle/NetSuite default and SOX/COSO control standard.

Endpoints (Phase 1):
    POST /slip/upload                       — image → OCR → save → match
    GET  /slips                             — list with filter
    GET  /slip/{id}                         — single slip detail
    PATCH /slip/{id}                        — edit OCR-parsed fields
    DELETE /slip/{id}                       — remove (TUM marked as test)
    POST /slip/{id}/match                   — Phase 3: re-run 3-way matcher
    POST /slip/{id}/manual-match            — Phase 5: pick stmt_id / inv_id
    POST /slip/{id}/reject                  — mark non-relevant
    GET  /statement/by-category             — Phase 6: monthly aggregates
    GET  /statement/unmatched               — Phase 6: rows still missing slip

Mounted via `app.include_router(slip_router)` in main.py.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import uuid
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

try:
    from main import (  # type: ignore
        get_db_conn,
        get_openai,
        get_supabase,
        SUPABASE_STORAGE_BUCKET,
        OPENAI_VISION_MODEL,
    )
except ImportError:
    # Fallback so this module can be unit-tested standalone.
    import psycopg2
    from supabase import create_client
    from openai import OpenAI

    SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "uploads")
    OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")

    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

    def get_supabase():
        return create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )

    def get_openai():
        return OpenAI(api_key=os.environ["OPENAI_API_KEY"])

log = logging.getLogger("slips")
router = APIRouter(tags=["slips"])


def _current_username(request: Request) -> Optional[str]:
    """Read JWT subject stamped by main.py middleware."""
    return getattr(request.state, "username", None)


# ════════════════════════════════════════════════════════════════════════════
# Models
# ════════════════════════════════════════════════════════════════════════════

class SlipUpdate(BaseModel):
    transfer_date:     Optional[str] = None      # YYYY-MM-DD
    transfer_time:     Optional[str] = None      # HH:MM[:SS]
    amount:            Optional[float] = None
    fee:               Optional[float] = None
    sender_name:       Optional[str] = None
    sender_account:    Optional[str] = None
    sender_bank:       Optional[str] = None
    recipient_name:    Optional[str] = None
    recipient_account: Optional[str] = None
    recipient_bank:    Optional[str] = None
    memo:              Optional[str] = None
    ref_no:            Optional[str] = None
    canonical_sku:     Optional[str] = None
    notes:             Optional[str] = None


class ManualMatchRequest(BaseModel):
    statement_id: Optional[str] = None   # null → unlink statement
    invoice_id:   Optional[str] = None   # null → unlink invoice


# ════════════════════════════════════════════════════════════════════════════
# Vision prompt — KBank K+ transfer slip
# ════════════════════════════════════════════════════════════════════════════
SLIP_VISION_PROMPT = """
You extract structured data from Thai bank transfer slips (mostly KBank
K+ mobile banking screenshots). Output PURE JSON only — no markdown,
no preamble, no explanation.

The slips look like this:

    [BANK LOGO]
    โอนเงินสำเร็จ                          ← always present
    01 พ.ค. 69                              ← date (Thai short month + BE year)
    13:32                                   ← time, sometimes
    จาก: [SENDER NAME]
    XXX-X-X1234-X      KBANK               ← sender account (masked)
    ไปยัง: [RECIPIENT NAME]
    XXX-X-X5678-X      KBANK               ← recipient account
    จำนวน: 1,000.00 บาท                    ← amount in THB
    ค่าธรรมเนียม: 0.00 บาท                 ← fee (often 0)
    เลขที่รายการ: 0123456789ABC             ← ref_no (sometimes labeled
                                              "Reference no.")
    บันทึกช่วยจำ: เบียร์ช้าง                ← memo, the GOLD signal

YEAR HANDLING: Thai banks use Buddhist Era (พ.ศ.). Subtract 543 → CE.
    "01 พ.ค. 69" → 2026-05-01 (69 + 2500 → 2569 BE → 2026 CE)
    "29 เม.ย. 69" → 2026-04-29

Output JSON shape:

{
  "transfer_date":     "YYYY-MM-DD",
  "transfer_time":     "HH:MM"  | null,
  "amount":            12345.67,
  "fee":               0.00,
  "sender_name":       "ทุม วีพี"     | null,
  "sender_account":    "XXX-X-X1234-X" | null,
  "sender_bank":       "KBANK"         | null,
  "recipient_name":    "วัฒนา"         | null,
  "recipient_account": "XXX-X-X5678-X" | null,
  "recipient_bank":    "KBANK"         | null,
  "memo":              "เบียร์ช้าง"    | null,
  "ref_no":            "0123456789ABC" | null
}

Rules:
  - Numbers are JSON numbers (NOT strings). Strip commas: 1,000.00 → 1000.00
  - Dates are ISO YYYY-MM-DD (CE not BE).
  - When a field is missing/unreadable, output null. Don't guess.
  - memo is the text after "บันทึกช่วยจำ:" — preserve EXACTLY as typed.
    Don't normalize spaces, don't translate, don't drop characters.
  - If multiple times are present, prefer the one near the date.
"""


# ════════════════════════════════════════════════════════════════════════════
# Helpers — Storage, OCR, classification
# ════════════════════════════════════════════════════════════════════════════

def _upload_slip_to_storage(
    image_bytes: bytes,
    file_name: str,
    mime_type: Optional[str],
) -> str:
    """Upload to Supabase Storage under slips/YYYY-MM/uuid.ext. Returns public URL."""
    sb = get_supabase()
    bucket = SUPABASE_STORAGE_BUCKET
    today = date.today().strftime("%Y-%m")
    ext = os.path.splitext(file_name)[1] or ".jpg"
    storage_path = f"slips/{today}/{uuid.uuid4()}{ext}"

    sb.storage.from_(bucket).upload(
        storage_path,
        image_bytes,
        file_options={"content-type": mime_type or "image/jpeg"},
    )
    return sb.storage.from_(bucket).get_public_url(storage_path)


def _run_slip_vision(image_bytes: bytes, mime_type: str) -> dict[str, Any]:
    """Send slip image to GPT-4o Vision. Returns parsed JSON dict."""
    client = get_openai()
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type or 'image/jpeg'};base64,{b64}"

    resp = client.chat.completions.create(
        model=OPENAI_VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": SLIP_VISION_PROMPT.strip()},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=800,
    )
    raw = (resp.choices[0].message.content or "{}").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)


def _classify_memo(conn, memo: Optional[str]) -> tuple[Optional[str], float]:
    """
    Map a slip memo → canonical SKU via the existing product_classifier.
    Returns (sku, confidence). Empty memo → (None, 0.0) — no SKU.

    The classifier was built for invoice line items but works equally
    well on slip memos because the prompt focuses on "match by family,
    brand, size, flavour" which is exactly how TUM types memos
    ("เบียร์ช้าง", "ค่าน้ำ", "ดนตรี วันอังคาร").
    """
    if not memo or not memo.strip():
        return None, 0.0
    try:
        from product_classifier import classify_single  # type: ignore
        guess = classify_single(conn, memo.strip())
        sku = guess.get("sku")
        conf = float(guess.get("confidence") or 0.0)
        if sku == "other" or not sku:
            return None, 0.0
        return sku, conf
    except Exception:
        log.exception("memo classification failed for %r", memo[:60] if memo else "")
        return None, 0.0


# ════════════════════════════════════════════════════════════════════════════
# Three-way matcher
# ════════════════════════════════════════════════════════════════════════════

def _match_slip(slip_id: str, actor: Optional[str]) -> dict:
    """
    3-way match a slip against bank_statement_entries + vendor_bills.

    Match logic (same loose-then-tight tolerance as invoice matcher):
      - transfer_date ± 2 days vs statement.txn_date
      - amount ± 1 baht vs statement.debit
      - statement direction = 'expense' (debit > 0)
      - statement not already matched to a different slip

    Outcomes:
      - 0 statement candidates       → match_status = 'unmatched'
      - 1 statement candidate        → link statement + try to inherit
                                       the statement's matched_invoice_id
                                       (if it has one). match_status =
                                       'matched_full' if invoice found,
                                       else 'matched_stmt'.
      - >1 statement candidates      → match_status = 'needs_review'

    Caller (POST /slip/upload, POST /slip/{id}/match) commits the change.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT transfer_date, amount
                FROM public.slips
                WHERE id = %s
                """,
                (slip_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"status": "not_found"}
            t_date, t_amount = row[0], row[1]
            if not t_date or t_amount is None:
                return {"status": "skipped", "reason": "missing transfer_date or amount"}

            # ── Step 1: find candidate statement rows ──
            cur.execute(
                """
                SELECT id, txn_date, debit, description, matched_invoice_id
                FROM public.bank_statement_entries
                WHERE direction = 'expense'
                  AND debit > 0
                  AND ABS(debit - %s) <= 1.00
                  AND ABS(txn_date - %s::date) <= 2
                  AND id NOT IN (
                      SELECT matched_statement_id FROM public.slips
                      WHERE matched_statement_id IS NOT NULL
                        AND id <> %s
                  )
                ORDER BY ABS(txn_date - %s::date), ABS(debit - %s)
                """,
                (t_amount, t_date, slip_id, t_date, t_amount),
            )
            stmt_candidates = cur.fetchall()

            if not stmt_candidates:
                cur.execute(
                    """
                    UPDATE public.slips
                    SET matched_statement_id = NULL,
                        matched_invoice_id   = NULL,
                        match_status         = 'unmatched',
                        updated_by           = %s
                    WHERE id = %s
                    """,
                    (actor, slip_id),
                )
                conn.commit()
                return {"status": "unmatched", "candidates": 0}

            if len(stmt_candidates) > 1:
                cur.execute(
                    """
                    UPDATE public.slips
                    SET match_status = 'needs_review',
                        updated_by   = %s
                    WHERE id = %s
                    """,
                    (actor, slip_id),
                )
                conn.commit()
                return {
                    "status":          "ambiguous",
                    "candidates":      len(stmt_candidates),
                    "statement_ids":   [str(r[0]) for r in stmt_candidates],
                }

            # Exactly 1 statement candidate.
            stmt_id, txn_date, debit, description, stmt_inv_id = stmt_candidates[0]
            new_status = "matched_full" if stmt_inv_id else "matched_stmt"
            cur.execute(
                """
                UPDATE public.slips
                SET matched_statement_id = %s,
                    matched_invoice_id   = %s,
                    match_status         = %s,
                    updated_by           = %s
                WHERE id = %s
                """,
                (str(stmt_id), str(stmt_inv_id) if stmt_inv_id else None,
                 new_status, actor, slip_id),
            )
            conn.commit()
            return {
                "status":          new_status,
                "statement_id":    str(stmt_id),
                "txn_date":        str(txn_date),
                "amount":          float(debit),
                "description":     description,
                "invoice_id":      str(stmt_inv_id) if stmt_inv_id else None,
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════════
# POST /slip/upload — main entry point
# ════════════════════════════════════════════════════════════════════════════

@router.post("/slip/upload")
async def slip_upload(file: UploadFile = File(...), request: Request = None):
    """
    Upload a single transfer slip image → OCR → save to slips table → run
    3-way matcher. Returns the saved record plus the match result.

    File types: JPG / PNG / WEBP. PDF is rejected — slips are screenshots
    in practice, never PDFs. (If a PDF slip ever shows up we can flip on
    pdf_to_images conversion like /invoice/upload does, but YAGNI.)
    """
    actor = _current_username(request) if request else None

    if not file.filename:
        raise HTTPException(400, "filename required")

    contents = await file.read()
    if not contents:
        raise HTTPException(400, "empty file")

    mime = (file.content_type or "image/jpeg").lower()
    if mime not in ("image/jpeg", "image/jpg", "image/png", "image/webp"):
        raise HTTPException(
            400,
            f"unsupported file type {mime!r} — slips must be JPG/PNG/WEBP screenshots",
        )

    # ── 1) Upload to storage (don't fail the request if storage hiccups) ──
    image_url: Optional[str] = None
    try:
        image_url = _upload_slip_to_storage(contents, file.filename, mime)
    except Exception:
        log.exception("storage upload failed (continuing without raw_image_url)")

    # ── 2) GPT-4o Vision OCR ──
    try:
        parsed = _run_slip_vision(contents, mime)
    except Exception as e:
        log.exception("slip vision failed")
        raise HTTPException(500, f"slip vision OCR failed: {e}")

    # ── 3) Basic validation — every slip MUST have date + amount ──
    transfer_date_raw = parsed.get("transfer_date")
    amount_raw        = parsed.get("amount")
    if not transfer_date_raw or amount_raw is None:
        raise HTTPException(
            422,
            f"OCR could not extract transfer_date or amount from slip "
            f"(got date={transfer_date_raw!r}, amount={amount_raw!r}). "
            f"Try a clearer screenshot.",
        )
    try:
        transfer_date_iso = datetime.strptime(transfer_date_raw[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, f"OCR returned invalid date {transfer_date_raw!r}")

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        raise HTTPException(422, f"OCR returned invalid amount {amount_raw!r}")

    fee = parsed.get("fee")
    try:
        fee = float(fee) if fee is not None else 0.0
    except (TypeError, ValueError):
        fee = 0.0

    transfer_time = parsed.get("transfer_time")
    if transfer_time and len(transfer_time) == 5:  # HH:MM
        transfer_time = transfer_time + ":00"

    # ── 4) Classify memo → canonical SKU ──
    conn = get_db_conn()
    try:
        sku, conf = _classify_memo(conn, parsed.get("memo"))

        # ── 5) Insert into slips ──
        new_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.slips
                    (id, transfer_date, transfer_time, amount, fee,
                     sender_name, sender_account, sender_bank,
                     recipient_name, recipient_account, recipient_bank,
                     memo, ref_no, raw_image_url, ocr_json,
                     canonical_sku, canonical_confidence,
                     source, created_by, updated_by)
                VALUES
                    (%s, %s, %s, %s, %s,
                     %s, %s, %s,
                     %s, %s, %s,
                     %s, %s, %s, %s::jsonb,
                     %s, %s,
                     %s, %s, %s)
                """,
                (
                    new_id,
                    transfer_date_iso,
                    transfer_time,
                    amount,
                    fee,
                    parsed.get("sender_name"),
                    parsed.get("sender_account"),
                    parsed.get("sender_bank"),
                    parsed.get("recipient_name"),
                    parsed.get("recipient_account"),
                    parsed.get("recipient_bank"),
                    parsed.get("memo"),
                    parsed.get("ref_no"),
                    image_url,
                    json.dumps(parsed, ensure_ascii=False),
                    sku,
                    conf if conf > 0 else None,
                    "web",
                    actor,
                    actor,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # ── 6) Run 3-way matcher ──
    try:
        match_result = _match_slip(new_id, actor)
    except Exception:
        log.exception("3-way match failed for slip %s — leaving as 'unmatched'", new_id)
        match_result = {"status": "error"}

    log.info("slip upload id=%s by %s match=%s", new_id, actor, match_result.get("status"))
    return {
        "success":      True,
        "slip_id":      new_id,
        "parsed":       parsed,
        "preview_url":  image_url,
        "canonical_sku":        sku,
        "canonical_confidence": conf,
        "match":        match_result,
    }


# ════════════════════════════════════════════════════════════════════════════
# GET /slips — list with filter
# ════════════════════════════════════════════════════════════════════════════

@router.get("/slips")
def list_slips(
    status: Optional[str] = Query(None, description="unmatched / matched_stmt / matched_full / needs_review / rejected"),
    month: Optional[str]  = Query(None, description="YYYY-MM filter on transfer_date"),
    canonical_sku: Optional[str] = Query(None),
    limit:  int = Query(100, le=500),
    offset: int = Query(0),
):
    """List slips with optional filters, newest first."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT s.id, s.transfer_date, s.transfer_time, s.amount, s.fee,
                       s.sender_name, s.recipient_name, s.recipient_bank,
                       s.memo, s.ref_no, s.raw_image_url,
                       s.canonical_sku, s.canonical_confidence,
                       s.matched_statement_id, s.matched_invoice_id,
                       s.match_status, s.source,
                       s.created_by, s.created_at,
                       s.updated_by, s.updated_at,
                       s.notes,
                       p.name_th AS canonical_name,
                       vb.vendor_name AS matched_vendor_name,
                       bse.txn_date   AS matched_statement_date,
                       bse.description AS matched_statement_desc
                FROM public.slips s
                LEFT JOIN public.products p          ON p.sku = s.canonical_sku
                LEFT JOIN public.vendor_bills vb     ON vb.id = s.matched_invoice_id
                LEFT JOIN public.bank_statement_entries bse
                                                     ON bse.id = s.matched_statement_id
                WHERE 1=1
            """
            params: list = []
            if status:
                sql += " AND s.match_status = %s"
                params.append(status)
            if month:
                sql += " AND to_char(s.transfer_date, 'YYYY-MM') = %s"
                params.append(month)
            if canonical_sku:
                sql += " AND s.canonical_sku = %s"
                params.append(canonical_sku)
            sql += " ORDER BY s.transfer_date DESC, s.transfer_time DESC, s.created_at DESC"
            sql += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])

            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

            slips = []
            for r in rows:
                d = dict(zip(cols, r))
                # Stringify UUIDs / dates / decimals for JSON.
                for k in ("id", "matched_statement_id", "matched_invoice_id"):
                    if d.get(k) is not None:
                        d[k] = str(d[k])
                for k in ("transfer_date", "matched_statement_date", "created_at", "updated_at"):
                    if d.get(k) is not None:
                        d[k] = d[k].isoformat()
                for k in ("transfer_time",):
                    if d.get(k) is not None:
                        d[k] = str(d[k])
                for k in ("amount", "fee", "canonical_confidence"):
                    if d.get(k) is not None:
                        d[k] = float(d[k])
                slips.append(d)

            # Total count for pagination
            count_sql = "SELECT COUNT(*) FROM public.slips s WHERE 1=1"
            count_params: list = []
            if status:
                count_sql += " AND s.match_status = %s"
                count_params.append(status)
            if month:
                count_sql += " AND to_char(s.transfer_date, 'YYYY-MM') = %s"
                count_params.append(month)
            if canonical_sku:
                count_sql += " AND s.canonical_sku = %s"
                count_params.append(canonical_sku)
            cur.execute(count_sql, count_params)
            total = cur.fetchone()[0]
    finally:
        conn.close()

    return {"success": True, "slips": slips, "count": len(slips), "total": total}


# ════════════════════════════════════════════════════════════════════════════
# GET /slip/{id} — single detail
# ════════════════════════════════════════════════════════════════════════════

@router.get("/slip/{slip_id}")
def get_slip(slip_id: str):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id, s.transfer_date, s.transfer_time, s.amount, s.fee,
                       s.sender_name, s.sender_account, s.sender_bank,
                       s.recipient_name, s.recipient_account, s.recipient_bank,
                       s.memo, s.ref_no, s.raw_image_url, s.ocr_json,
                       s.canonical_sku, s.canonical_confidence,
                       s.matched_statement_id, s.matched_invoice_id,
                       s.match_status, s.source,
                       s.created_by, s.created_at,
                       s.updated_by, s.updated_at,
                       s.notes,
                       p.name_th AS canonical_name,
                       vb.vendor_name AS matched_vendor_name,
                       vb.invoice_no  AS matched_invoice_no,
                       vb.bill_date   AS matched_bill_date,
                       bse.txn_date   AS matched_statement_date,
                       bse.description AS matched_statement_desc
                FROM public.slips s
                LEFT JOIN public.products p          ON p.sku = s.canonical_sku
                LEFT JOIN public.vendor_bills vb     ON vb.id = s.matched_invoice_id
                LEFT JOIN public.bank_statement_entries bse
                                                     ON bse.id = s.matched_statement_id
                WHERE s.id = %s
                """,
                (slip_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "slip not found")
            cols = [d[0] for d in cur.description]
            d = dict(zip(cols, row))
    finally:
        conn.close()

    for k in ("id", "matched_statement_id", "matched_invoice_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("transfer_date", "matched_statement_date", "matched_bill_date",
              "created_at", "updated_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    if d.get("transfer_time") is not None:
        d["transfer_time"] = str(d["transfer_time"])
    for k in ("amount", "fee", "canonical_confidence"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    return {"success": True, "slip": d}


# ════════════════════════════════════════════════════════════════════════════
# PATCH /slip/{id} — edit OCR-parsed fields
# ════════════════════════════════════════════════════════════════════════════

@router.patch("/slip/{slip_id}")
def patch_slip(slip_id: str, body: SlipUpdate, request: Request):
    actor = _current_username(request)
    updates: list[tuple[str, Any]] = []
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(400, "no fields to update")

    if "transfer_date" in payload:
        try:
            payload["transfer_date"] = datetime.strptime(
                payload["transfer_date"], "%Y-%m-%d"
            ).date()
        except (ValueError, TypeError):
            raise HTTPException(400, "transfer_date must be YYYY-MM-DD")
    if "transfer_time" in payload and payload["transfer_time"]:
        t = payload["transfer_time"]
        if len(t) == 5:
            payload["transfer_time"] = t + ":00"

    allowed = {
        "transfer_date", "transfer_time", "amount", "fee",
        "sender_name", "sender_account", "sender_bank",
        "recipient_name", "recipient_account", "recipient_bank",
        "memo", "ref_no", "canonical_sku", "notes",
    }
    set_clauses = []
    values: list = []
    for k, v in payload.items():
        if k not in allowed:
            continue
        set_clauses.append(f"{k} = %s")
        values.append(v)
    if not set_clauses:
        raise HTTPException(400, "no editable fields in payload")

    set_clauses.append("updated_by = %s")
    values.append(actor)
    values.append(slip_id)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE public.slips SET {', '.join(set_clauses)} WHERE id = %s",
                values,
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "slip not found")
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # If amount or transfer_date changed, re-run the matcher.
    if "amount" in payload or "transfer_date" in payload:
        try:
            _match_slip(slip_id, actor)
        except Exception:
            log.exception("auto re-match after PATCH failed slip=%s", slip_id)

    log.info("slip patch id=%s by %s fields=%s", slip_id, actor, list(payload.keys()))
    return {"success": True, "id": slip_id, "updated_fields": list(payload.keys())}


# ════════════════════════════════════════════════════════════════════════════
# DELETE /slip/{id} — remove (TUM marked as test / wrong upload)
# ════════════════════════════════════════════════════════════════════════════

@router.delete("/slip/{slip_id}")
def delete_slip(slip_id: str, request: Request):
    actor = _current_username(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM public.slips WHERE id = %s", (slip_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "slip not found")
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log.info("slip delete id=%s by %s", slip_id, actor)
    return {"success": True, "deleted_id": slip_id}


# ════════════════════════════════════════════════════════════════════════════
# POST /slip/{id}/match — re-run 3-way matcher
# ════════════════════════════════════════════════════════════════════════════

@router.post("/slip/{slip_id}/match")
def slip_match(slip_id: str, request: Request):
    """
    Re-run the matcher (useful after TUM imports a statement PDF that
    landed AFTER this slip was uploaded). Idempotent.
    """
    actor = _current_username(request)
    result = _match_slip(slip_id, actor)
    if result.get("status") == "not_found":
        raise HTTPException(404, "slip not found")
    return {"success": True, "match": result}


# ════════════════════════════════════════════════════════════════════════════
# POST /slip/{id}/manual-match — TUM overrides matcher
# ════════════════════════════════════════════════════════════════════════════

@router.post("/slip/{slip_id}/manual-match")
def slip_manual_match(slip_id: str, body: ManualMatchRequest, request: Request):
    """
    Manually pin a slip to a specific statement row and/or invoice. Used
    when the auto-matcher returns `ambiguous` or when TUM wants to undo
    a wrong link.

    Passing `null` for either field unlinks it (and we recompute match_status
    based on what's still linked).
    """
    actor = _current_username(request)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Validate the slip exists
            cur.execute("SELECT id FROM public.slips WHERE id = %s", (slip_id,))
            if not cur.fetchone():
                raise HTTPException(404, "slip not found")

            # Validate statement row exists (if provided)
            if body.statement_id:
                cur.execute(
                    "SELECT id FROM public.bank_statement_entries WHERE id = %s",
                    (body.statement_id,),
                )
                if not cur.fetchone():
                    raise HTTPException(404, f"statement entry {body.statement_id} not found")

            # Validate invoice exists (if provided)
            if body.invoice_id:
                cur.execute(
                    "SELECT id FROM public.vendor_bills WHERE id = %s",
                    (body.invoice_id,),
                )
                if not cur.fetchone():
                    raise HTTPException(404, f"invoice {body.invoice_id} not found")

            # Compute new status
            if body.statement_id and body.invoice_id:
                new_status = "matched_full"
            elif body.statement_id:
                new_status = "matched_stmt"
            else:
                new_status = "unmatched"

            cur.execute(
                """
                UPDATE public.slips
                SET matched_statement_id = %s,
                    matched_invoice_id   = %s,
                    match_status         = %s,
                    updated_by           = %s
                WHERE id = %s
                """,
                (body.statement_id, body.invoice_id, new_status, actor, slip_id),
            )
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    log.info("slip manual-match id=%s stmt=%s inv=%s by %s",
             slip_id, body.statement_id, body.invoice_id, actor)
    return {
        "success": True,
        "slip_id": slip_id,
        "matched_statement_id": body.statement_id,
        "matched_invoice_id":   body.invoice_id,
        "match_status":         new_status,
    }


# ════════════════════════════════════════════════════════════════════════════
# POST /slip/{id}/reject — mark as not relevant (test slip, refund, dup)
# ════════════════════════════════════════════════════════════════════════════

@router.post("/slip/{slip_id}/reject")
def slip_reject(slip_id: str, request: Request):
    actor = _current_username(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.slips
                SET match_status = 'rejected',
                    updated_by   = %s
                WHERE id = %s
                """,
                (actor, slip_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "slip not found")
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    log.info("slip reject id=%s by %s", slip_id, actor)
    return {"success": True, "id": slip_id, "match_status": "rejected"}


# ════════════════════════════════════════════════════════════════════════════
# Phase 6 — Statement breakdown views
# ════════════════════════════════════════════════════════════════════════════

@router.get("/statement/by-category")
def statement_by_category(
    month: Optional[str] = Query(None, description="YYYY-MM"),
):
    """
    Monthly breakdown of bank_statement_entries grouped by category_code.
    Each row also reports how many slips have been linked to that group's
    statements, so TUM can see "musician_fee ₿28,400 in May, 12 of 14
    statement rows have a slip attached".
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            params: list = []
            sql = """
                SELECT
                    COALESCE(bse.category_code, 'uncategorized') AS category_code,
                    bse.direction,
                    SUM(bse.amount)::float                       AS total,
                    COUNT(*)                                      AS entry_count,
                    SUM(CASE WHEN s.id IS NOT NULL THEN 1 ELSE 0 END) AS slip_count,
                    SUM(CASE WHEN bse.matched_invoice_id IS NOT NULL
                             THEN 1 ELSE 0 END)                   AS invoice_count
                FROM public.bank_statement_entries bse
                LEFT JOIN public.slips s ON s.matched_statement_id = bse.id
                WHERE bse.match_status != 'needs_review'
            """
            if month:
                sql += " AND to_char(bse.txn_date, 'YYYY-MM') = %s"
                params.append(month)
            sql += """
                GROUP BY COALESCE(bse.category_code, 'uncategorized'), bse.direction
                ORDER BY bse.direction, total DESC
            """
            cur.execute(sql, params)
            rows = []
            for r in cur.fetchall():
                rows.append({
                    "category_code": r[0],
                    "direction":     r[1],
                    "total":         float(r[2] or 0),
                    "entry_count":   int(r[3] or 0),
                    "slip_count":    int(r[4] or 0),
                    "invoice_count": int(r[5] or 0),
                })
    finally:
        conn.close()
    return {"success": True, "month": month, "categories": rows, "count": len(rows)}


@router.get("/statement/unmatched")
def statement_unmatched(
    month: Optional[str] = Query(None, description="YYYY-MM"),
    limit:  int = Query(200, le=1000),
):
    """
    Bank statement rows that have NO matching slip AND NO matching invoice
    yet. This is the "what did I forget to attach paperwork for?" list.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            params: list = []
            sql = """
                SELECT bse.id, bse.txn_date, bse.direction, bse.amount,
                       bse.description, bse.category_code, bse.source_type,
                       bse.match_status
                FROM public.bank_statement_entries bse
                LEFT JOIN public.slips s ON s.matched_statement_id = bse.id
                WHERE bse.matched_invoice_id IS NULL
                  AND s.id IS NULL
                  AND bse.match_status != 'needs_review'
                  AND bse.direction = 'expense'
            """
            if month:
                sql += " AND to_char(bse.txn_date, 'YYYY-MM') = %s"
                params.append(month)
            sql += " ORDER BY bse.txn_date DESC, bse.amount DESC"
            sql += " LIMIT %s"
            params.append(limit)
            cur.execute(sql, params)
            rows = []
            for r in cur.fetchall():
                rows.append({
                    "id":            str(r[0]),
                    "txn_date":      r[1].isoformat() if r[1] else None,
                    "direction":     r[2],
                    "amount":        float(r[3] or 0),
                    "description":   r[4],
                    "category_code": r[5],
                    "source_type":   r[6],
                    "match_status":  r[7],
                })
    finally:
        conn.close()
    return {"success": True, "month": month, "entries": rows, "count": len(rows)}
