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

import asyncio
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

from llm import get_openai  # OpenAI client factory (Step 2 consolidation — no circular dep)

try:
    from main import (  # type: ignore
        get_db_conn,
        get_supabase,
        SUPABASE_STORAGE_BUCKET,
        OPENAI_VISION_MODEL,
    )
except ImportError:
    # Fallback so this module can be unit-tested standalone.
    import psycopg2
    from supabase import create_client

    SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "uploads")
    OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")

    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

    def get_supabase():
        return create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )

log = logging.getLogger("slips")
router = APIRouter(tags=["slips"])


def _current_username(request: Request) -> Optional[str]:
    """Read JWT subject stamped by main.py middleware."""
    return getattr(request.state, "username", None)


def _validate_uuid_param(name: str, value: str) -> None:
    """
    Raise HTTPException(400) if `value` isn't a syntactically-valid UUID.
    Mirror of main._validate_uuid_param — kept local so this module can
    be unit-tested standalone. See the docstring in main.py for the
    full Session 27 rationale (truncated IDs pasted from LINE chat
    surfacing as CORS errors).
    """
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            400,
            f"invalid {name} (expected UUID): {value!r}",
        )


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


class CategoryOverrideRequest(BaseModel):
    """
    Body for POST /slip/{id}/category — TUM overrides the auto-resolved
    category. Pass `category_code = null` (or omit) to unlock and let
    the resolver pick again on next match.
    """
    category_code: Optional[str] = None


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


def _find_duplicate_slip(
    conn,
    transfer_date,
    amount: float,
    ref_no: Optional[str],
    recipient_name: Optional[str],
) -> Optional[str]:
    """
    Return slip_id of a pre-existing slip that matches the new upload's
    signature, or None if this is a fresh slip.

    A bank transfer's `ref_no` is the strongest globally-unique signal
    (KBank never re-uses one), so we check that first. If ref_no is
    missing or didn't catch anything, fall back to the
    (transfer_date, amount, recipient_name) triple — the same fingerprint
    that statement reconciliation uses.

    Without this, TUM accidentally uploading the same screenshot twice
    (or sending the slip from LINE after already running /slip/upload via
    web) ends up creating two slip rows that fight over the same
    statement — the first wins, the second is permanently 'unmatched'
    even though the bank row exists. Detecting upfront avoids that.
    """
    with conn.cursor() as cur:
        # Priority 1: ref_no — globally unique per bank transaction.
        if ref_no and ref_no.strip():
            cur.execute(
                "SELECT id FROM public.slips WHERE ref_no = %s LIMIT 1",
                (ref_no.strip(),),
            )
            row = cur.fetchone()
            if row:
                return str(row[0])

        # Priority 2: (date, amount, recipient_name) fingerprint.
        # Same-day same-amount to same person is virtually always the
        # same transaction — collision risk is negligible for a small
        # restaurant's daily volume.
        cur.execute(
            """
            SELECT id FROM public.slips
            WHERE transfer_date = %s
              AND ABS(amount - %s) <= 0.01
              AND COALESCE(recipient_name, '') = COALESCE(%s, '')
            LIMIT 1
            """,
            (transfer_date, amount, recipient_name),
        )
        row = cur.fetchone()
        if row:
            return str(row[0])

    return None


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


def _classify_slip_category(
    conn,
    recipient_name: Optional[str],
    memo: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """
    Classify a slip → accounting category via the shared `statement_rules`
    table. Returns (category_code, source) where source is one of:
      - "memo_keyword"     — memo matched a rule_type='keyword' row
      - "recipient_name"   — recipient_name matched a rule_type='name' row
      - None               — no rule matched

    Priority (intentional cascade — see Session 27 design doc):
      L2: memo keyword       (intent TUM typed himself; highest priority
                              specific keywords like "ค่าน้ำประปา" sit at
                              priority 100 so they override less-specific
                              name rules when present)
      L3: recipient_name     (fallback for slips with no memo or whose
                              memo wasn't recognised — e.g. when TUM is
                              paying กาญจนา for rent without typing
                              "ค่าเช่า" in the memo, the name rule
                              "กาญจนา → rent" still fires)

    L1 (matched_statement.category_code) is NOT handled here — the caller
    resolves that and only falls to this function when the slip is
    unmatched OR when the matched statement has no category_code of its
    own.

    The DB query restricts to direction='expense' because slips are
    always outgoing transfers in TUM's model.
    """
    # ── L2: memo keyword match ───────────────────────────────────────────
    if memo and memo.strip():
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT category_code
                    FROM public.statement_rules
                    WHERE rule_type   = 'keyword'
                      AND direction   = 'expense'
                      AND %s ILIKE '%%' || match_value || '%%'
                    ORDER BY priority DESC, char_length(match_value) DESC
                    LIMIT 1
                    """,
                    (memo.strip(),),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return row[0], "memo_keyword"
        except Exception:
            log.exception("memo keyword classification failed for %r",
                          memo[:60] if memo else "")

    # ── L3: recipient_name name-match ────────────────────────────────────
    if recipient_name and recipient_name.strip():
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT category_code
                    FROM public.statement_rules
                    WHERE rule_type   = 'name'
                      AND direction   = 'expense'
                      AND %s ILIKE '%%' || match_value || '%%'
                    ORDER BY priority DESC, char_length(match_value) DESC
                    LIMIT 1
                    """,
                    (recipient_name.strip(),),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return row[0], "recipient_name"
        except Exception:
            log.exception("recipient name classification failed for %r",
                          recipient_name[:60] if recipient_name else "")

    return None, None


def _resolve_and_persist_category(
    conn,
    slip_id: str,
    recipient_name: Optional[str],
    memo: Optional[str],
    matched_statement_id: Optional[str],
) -> None:
    """
    Compute the slip's category via the L1→L3 cascade and persist it on
    the slip row. Called from slip_upload (post-insert) and _match_slip
    (post-statement-link). Idempotent — safe to call repeatedly.

    L1: matched_statement.category_code (verified)
    L2/L3: fall back to _classify_slip_category()

    Respects the 'manual' lock: if the row currently has
    category_source = 'manual', TUM has chosen the category himself
    and we leave it alone. He can unlock via POST /slip/{id}/category
    with category_code = null.
    """
    # ── Manual override lock ──
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT category_source FROM public.slips WHERE id = %s",
                (slip_id,),
            )
            row = cur.fetchone()
            if row and row[0] == "manual":
                return  # honour TUM's manual choice
    except Exception:
        log.exception("category lock check failed slip=%s", slip_id)

    category_code: Optional[str] = None
    source: Optional[str] = None

    # ── L1: inherit from matched statement, if any ───────────────────────
    if matched_statement_id:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT category_code FROM public.bank_statement_entries WHERE id = %s",
                    (matched_statement_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    category_code = row[0]
                    source = "statement"
        except Exception:
            log.exception("L1 statement category lookup failed slip=%s", slip_id)

    # ── L2/L3 fallback ───────────────────────────────────────────────────
    if not category_code:
        category_code, source = _classify_slip_category(conn, recipient_name, memo)

    # ── Persist ──
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.slips
                SET statement_category_code = %s,
                    category_source         = %s
                WHERE id = %s
                """,
                (category_code, source, slip_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("persist category failed slip=%s", slip_id)


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
# Nightly reconcile — push slip memo categories ONTO the matched bank rows
# ════════════════════════════════════════════════════════════════════════════
# The slip's K+ memo is the gold signal for what a bank transfer was FOR. The
# P&L reads the category off bank_statement_entries, so this is the missing pipe:
# slip memo -> category -> written onto the bank row. Runs nightly (02:00 BKK,
# registered in line_bot_routes) and on demand via POST /slip/reconcile.

# Slip category_code -> bank_statement_entries.source_type. EVERY mapping here is
# COUNTED in the cash-basis P&L (a slip proves real money left for that purpose).
# Unlisted categories fall back to 'other_expense' (also counted).
_CAT_TO_SOURCE: dict[str, str] = {
    "musician_fee": "payroll_expense",
    "staff_salary": "payroll_expense",
    "rent":         "rent_expense",
    "utility":      "utility_expense",
    "food_raw":     "vendor_purchase",
    "beverage_raw": "vendor_purchase",
    "bank_fee":     "bank_fee",
    "tax":          "tax_expense",
}


def _source_for_category(category_code: Optional[str]) -> str:
    return _CAT_TO_SOURCE.get(category_code or "", "other_expense")


def reconcile_slips_to_statements(actor: Optional[str] = "nightly_job") -> dict:
    """Reconcile slips against bank_statement_entries and push each matched
    slip's memo-derived category ONTO its bank row so the P&L reflects it.

    Pass 1: re-match every still-unmatched / needs_review slip (new statements
            or newly-uploaded slips may now pair up).
    Pass 2: for every slip matched to exactly one bank row, classify it from its
            memo + recipient name and write category_code + a COUNTED source_type
            onto the bank_statement_entries row.

    Bank rows with no slip keep their import default ('other_expense'). Bank rows
    TUM categorised by hand (match_status='manual') are never overwritten.
    Idempotent — safe to run nightly and on demand.
    """
    rematched = 0
    categorized = 0

    # ── Pass 1: re-match loose slips (each _match_slip manages its own conn) ──
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Re-match any slip that isn't validly linked yet — including slips
            # orphaned by a statement RE-IMPORT (deleting bank_statement_entries
            # fires ON DELETE SET NULL on matched_statement_id but leaves the old
            # match_status, so we also re-match anything whose link is now NULL).
            # 'rejected' slips stay rejected (TUM marked them irrelevant).
            cur.execute(
                "SELECT id FROM public.slips "
                "WHERE match_status <> 'rejected' "
                "  AND (match_status IN ('unmatched', 'needs_review') "
                "       OR matched_statement_id IS NULL)"
            )
            loose_ids = [str(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()

    for sid in loose_ids:
        try:
            res = _match_slip(sid, actor)
            if res.get("status") in ("matched_stmt", "matched_full"):
                rematched += 1
        except Exception:
            log.exception("reconcile: re-match failed slip=%s", sid)

    # ── Pass 2: push memo category onto the matched bank rows ──
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, recipient_name, memo, matched_statement_id
                FROM public.slips
                WHERE match_status IN ('matched_stmt', 'matched_full')
                  AND matched_statement_id IS NOT NULL
                """
            )
            matched_slips = cur.fetchall()

        for sid, recipient_name, memo, stmt_id in matched_slips:
            category_code, _src = _classify_slip_category(conn, recipient_name, memo)
            if not category_code:
                continue  # no memo/name signal — leave the bank row's default
            source_type = _source_for_category(category_code)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE public.bank_statement_entries
                        SET category_code = %s,
                            source_type   = %s,
                            match_status  = 'auto'
                        WHERE id = %s
                          AND match_status <> 'manual'
                          AND (category_code IS DISTINCT FROM %s
                               OR source_type IS DISTINCT FROM %s)
                        """,
                        (category_code, source_type, str(stmt_id),
                         category_code, source_type),
                    )
                    if cur.rowcount:
                        categorized += 1
                conn.commit()
            except Exception:
                conn.rollback()
                log.exception("reconcile: push category failed slip=%s stmt=%s",
                              sid, stmt_id)

        result = {"rematched": rematched, "categorized": categorized,
                  "matched_slips": len(matched_slips)}
        log.info("Slip reconcile done: %s", result)
        return result
    finally:
        conn.close()


@router.post("/slip/reconcile")
def manual_reconcile(request: Request = None):
    """'Reconcile now' button — runs the same job the 02:00 BKK scheduler runs."""
    actor = _current_username(request) if request else None
    return reconcile_slips_to_statements(actor=actor or "manual")


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
    # Storage PUT + GPT-4o Vision are blocking I/O; run them off the event loop so a
    # slip upload doesn't freeze the whole server (health-check timeout → DOWN).
    image_url: Optional[str] = None
    try:
        image_url = await asyncio.to_thread(_upload_slip_to_storage, contents, file.filename, mime)
    except Exception:
        log.exception("storage upload failed (continuing without raw_image_url)")

    # ── 2) GPT-4o Vision OCR ──
    try:
        parsed = await asyncio.to_thread(_run_slip_vision, contents, mime)
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
        # ── 4a) Duplicate check before insert. If TUM sent the same screenshot
        #    twice (web + LINE, accidental re-send, etc.) we want to return
        #    the existing slip rather than create a second row that fights
        #    over the same statement.
        existing_id = _find_duplicate_slip(
            conn,
            transfer_date_iso,
            amount,
            parsed.get("ref_no"),
            parsed.get("recipient_name"),
        )
        if existing_id:
            log.info("slip upload duplicate detected — returning existing id=%s", existing_id)
            return {
                "success":     True,
                "slip_id":     existing_id,
                "parsed":      parsed,
                "preview_url": image_url,
                "duplicate":   True,
                "message":     "สลิปนี้มีในระบบแล้ว — ไม่ได้บันทึกซ้ำ",
                "match":       {"status": "duplicate", "existing_slip_id": existing_id},
            }

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

    # ── 7) Resolve + persist statement_category_code (L1→L3 cascade) ──
    #    L1: matched statement (if any)
    #    L2: memo keyword rule
    #    L3: recipient name rule
    try:
        conn2 = get_db_conn()
        try:
            _resolve_and_persist_category(
                conn2,
                new_id,
                parsed.get("recipient_name"),
                parsed.get("memo"),
                match_result.get("statement_id") if isinstance(match_result, dict) else None,
            )
        finally:
            conn2.close()
    except Exception:
        log.exception("category resolve failed slip=%s", new_id)

    # Read the resolved category back so the response carries it.
    resolved_category = None
    resolved_source = None
    try:
        conn3 = get_db_conn()
        try:
            with conn3.cursor() as cur:
                cur.execute(
                    "SELECT statement_category_code, category_source "
                    "FROM public.slips WHERE id = %s",
                    (new_id,),
                )
                r = cur.fetchone()
                if r:
                    resolved_category, resolved_source = r[0], r[1]
        finally:
            conn3.close()
    except Exception:
        log.exception("category readback failed slip=%s", new_id)

    log.info("slip upload id=%s by %s match=%s category=%s/%s",
             new_id, actor, match_result.get("status"),
             resolved_category, resolved_source)
    return {
        "success":      True,
        "slip_id":      new_id,
        "parsed":       parsed,
        "preview_url":  image_url,
        "canonical_sku":        sku,
        "canonical_confidence": conf,
        "statement_category_code": resolved_category,
        "category_source":         resolved_source,
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
                       s.statement_category_code, s.category_source,
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
    _validate_uuid_param("slip_id", slip_id)
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
                       s.statement_category_code, s.category_source,
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
    _validate_uuid_param("slip_id", slip_id)
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

    # If memo, recipient_name, or canonical_sku changed → re-resolve the
    # category cascade (memo / recipient feed L2/L3 lookups).
    if any(k in payload for k in ("memo", "recipient_name", "amount", "transfer_date")):
        try:
            conn2 = get_db_conn()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "SELECT recipient_name, memo, matched_statement_id "
                        "FROM public.slips WHERE id = %s",
                        (slip_id,),
                    )
                    row = cur.fetchone()
                if row:
                    _resolve_and_persist_category(
                        conn2, slip_id, row[0], row[1],
                        str(row[2]) if row[2] else None,
                    )
            finally:
                conn2.close()
        except Exception:
            log.exception("category re-resolve after PATCH failed slip=%s", slip_id)

    log.info("slip patch id=%s by %s fields=%s", slip_id, actor, list(payload.keys()))
    return {"success": True, "id": slip_id, "updated_fields": list(payload.keys())}


# ════════════════════════════════════════════════════════════════════════════
# DELETE /slip/{id} — remove (TUM marked as test / wrong upload)
# ════════════════════════════════════════════════════════════════════════════

@router.delete("/slip/{slip_id}")
def delete_slip(slip_id: str, request: Request):
    _validate_uuid_param("slip_id", slip_id)
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

@router.post("/slips/rematch-all")
def slips_rematch_all(
    request: Request,
    status: Optional[str] = Query(None, description="unmatched / matched_stmt / matched_full / needs_review / rejected — limit to one status"),
    month:  Optional[str] = Query(None, description="YYYY-MM filter on transfer_date"),
):
    """
    Bulk re-run the 3-way matcher + category cascade against every slip
    that matches the (status, month) filter. Use case:

      - TUM just imported a new KBank PDF → wants every unmatched slip
        to retry the matcher (most will now find their statement row)
      - TUM just edited a statement_rules row via /rules → wants every
        slip to re-resolve its category cascade against the new rule

    Idempotent. Per-slip errors are caught + counted instead of aborting
    the whole batch.

    Returns:
        {
            "success": True,
            "processed":    int,   # slips iterated
            "matched_full": int,   # status went to matched_full
            "matched_stmt": int,
            "unmatched":    int,
            "needs_review": int,
            "errors":       int,
            "duration_ms":  int,
        }
    """
    actor = _current_username(request)
    import time
    started = time.time()

    # Build the same filter the list endpoint uses so the bulk button
    # only re-processes the rows TUM is looking at.
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT id FROM public.slips WHERE 1=1"
            params: list = []
            if status:
                sql += " AND match_status = %s"
                params.append(status)
            if month:
                sql += " AND to_char(transfer_date, 'YYYY-MM') = %s"
                params.append(month)
            sql += " ORDER BY transfer_date DESC, created_at DESC"
            cur.execute(sql, params)
            ids = [str(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()

    counters = {
        "matched_full": 0, "matched_stmt": 0,
        "unmatched":    0, "needs_review": 0,
        "ambiguous":    0, "errors":       0,
    }

    for slip_id in ids:
        try:
            result = _match_slip(slip_id, actor)
            counters[result.get("status", "errors")] = (
                counters.get(result.get("status", "errors"), 0) + 1
            )

            # Refresh category cascade — same as the single-slip endpoint
            conn2 = get_db_conn()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "SELECT recipient_name, memo, matched_statement_id "
                        "FROM public.slips WHERE id = %s",
                        (slip_id,),
                    )
                    row = cur.fetchone()
                if row:
                    _resolve_and_persist_category(
                        conn2, slip_id, row[0], row[1],
                        str(row[2]) if row[2] else None,
                    )
            finally:
                conn2.close()
        except Exception:
            log.exception("rematch-all slip %s failed", slip_id)
            counters["errors"] += 1

    duration_ms = int((time.time() - started) * 1000)
    log.info("rematch-all processed=%d %s by %s in %dms",
             len(ids), counters, actor, duration_ms)

    return {
        "success":      True,
        "processed":    len(ids),
        **counters,
        "duration_ms":  duration_ms,
    }


@router.post("/slip/{slip_id}/match")
def slip_match(slip_id: str, request: Request):
    """
    Re-run the matcher (useful after TUM imports a statement PDF that
    landed AFTER this slip was uploaded). Idempotent. Also re-resolves
    the category cascade so an L2/L3-guessed category gets promoted to
    L1 ("verified") once the bank statement row catches up.
    """
    _validate_uuid_param("slip_id", slip_id)
    actor = _current_username(request)
    result = _match_slip(slip_id, actor)
    if result.get("status") == "not_found":
        raise HTTPException(404, "slip not found")

    # Refresh category — if the slip just got linked to a statement, L1
    # supersedes whatever L2/L3 had guessed at upload time.
    try:
        conn2 = get_db_conn()
        try:
            with conn2.cursor() as cur:
                cur.execute(
                    "SELECT recipient_name, memo, matched_statement_id "
                    "FROM public.slips WHERE id = %s",
                    (slip_id,),
                )
                row = cur.fetchone()
            if row:
                _resolve_and_persist_category(
                    conn2, slip_id, row[0], row[1],
                    str(row[2]) if row[2] else None,
                )
        finally:
            conn2.close()
    except Exception:
        log.exception("category re-resolve after /match failed slip=%s", slip_id)

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
    _validate_uuid_param("slip_id", slip_id)
    if body.statement_id is not None:
        _validate_uuid_param("statement_id", body.statement_id)
    if body.invoice_id is not None:
        _validate_uuid_param("invoice_id", body.invoice_id)
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
    _validate_uuid_param("slip_id", slip_id)
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
# POST /slip/{id}/category — manual category override (Phase 6.5+)
# ════════════════════════════════════════════════════════════════════════════

@router.post("/slip/{slip_id}/category")
def slip_override_category(
    slip_id: str,
    body: CategoryOverrideRequest,
    request: Request,
):
    """
    Manually pin a slip's accounting category, overriding whatever the
    L1→L3 cascade resolved. Sets category_source='manual' so subsequent
    rematch calls won't clobber the choice.

    Pass category_code=null to clear the lock and let the auto-resolver
    pick again on the next rematch.
    """
    _validate_uuid_param("slip_id", slip_id)
    actor = _current_username(request)
    code = (body.category_code or "").strip() or None

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if code is None:
                # Unlock + re-run resolver immediately so TUM sees a fresh
                # auto-pick rather than staring at a stale blank chip.
                cur.execute(
                    """
                    UPDATE public.slips
                    SET statement_category_code = NULL,
                        category_source         = NULL,
                        updated_by              = %s
                    WHERE id = %s
                    """,
                    (actor, slip_id),
                )
                if cur.rowcount == 0:
                    raise HTTPException(404, "slip not found")
                conn.commit()
            else:
                cur.execute(
                    """
                    UPDATE public.slips
                    SET statement_category_code = %s,
                        category_source         = 'manual',
                        updated_by              = %s
                    WHERE id = %s
                    RETURNING statement_category_code, category_source
                    """,
                    (code, actor, slip_id),
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

    # If TUM cleared the lock, re-run the cascade so the response carries
    # the freshly-resolved category instead of a momentary NULL.
    if code is None:
        try:
            conn2 = get_db_conn()
            try:
                with conn2.cursor() as cur:
                    cur.execute(
                        "SELECT recipient_name, memo, matched_statement_id "
                        "FROM public.slips WHERE id = %s",
                        (slip_id,),
                    )
                    row = cur.fetchone()
                if row:
                    _resolve_and_persist_category(
                        conn2, slip_id, row[0], row[1],
                        str(row[2]) if row[2] else None,
                    )
            finally:
                conn2.close()
        except Exception:
            log.exception("re-resolve after unlock failed slip=%s", slip_id)

    log.info("slip category override id=%s by %s code=%r", slip_id, actor, code)
    return {
        "success":               True,
        "slip_id":                slip_id,
        "statement_category_code": code,
        "category_source":        "manual" if code else None,
    }


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
