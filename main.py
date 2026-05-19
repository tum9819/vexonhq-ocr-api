"""
VEXONHQ OCR API — v3.6 (Phase 1.8: whitespace normalization + invoice_no fallback dedup)

Endpoints:
  GET  /                              Service info
  GET  /health                        Health + config check
  POST /ocr                           Legacy: Tesseract-only, returns raw text
  POST /invoice/upload                MAIN: file → OCR → GPT Vision → save to Supabase
  GET  /invoice/queue                 Pending review list (paginated)
  GET  /invoice/{invoice_id}          Full invoice detail with items + pages + warnings
  PATCH /invoice/{invoice_id}         Edit extracted fields during review
  POST /invoice/{invoice_id}/confirm  Mark review_status = confirmed
  POST /invoice/{invoice_id}/reject   Mark review_status = rejected (with reason)

Required env vars (set in Coolify):
  SUPABASE_URL              — https://xxxxx.supabase.co
  SUPABASE_SERVICE_KEY      — service_role JWT (admin)
  SUPABASE_STORAGE_BUCKET   — bucket name for file uploads (default: uploads)
  OPENAI_API_KEY            — sk-...
  OPENAI_VISION_MODEL       — optional, defaults to gpt-4o
"""

import base64
import io
import json
import logging
import os
import tempfile
import time
import uuid
from datetime import date, datetime
from typing import Any, Optional

import cv2
import pypdfium2 as pdfium
import pytesseract
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from PIL import Image
from pydantic import BaseModel
from supabase import Client, create_client
from pos_import import router as pos_router
from phase2_routes import router as phase2_router
from phase3_arap_routes import router as phase3_arap_router
from phase3_quick_entry_routes import router as phase3_quick_entry_router
from phase3_daybook_routes import router as phase3_daybook_router
from phase3_category_routes import router as phase3_category_router
from phase3a_ai_categorize_routes import router as phase3a_ai_categorize_router
from phase3a_anomaly_routes import router as phase3a_anomaly_router
from pnl_routes import router as pnl_router
from line_bot_routes import router as line_router
from budget_routes import router as budget_router
from export_routes import router as export_router
from phase10_narrative_routes import router as narrative_router
from phase11_search_routes import router as search_router
from phase12_bank_statement_routes import router as bank_statement_router
from bill_payment_routes import router as bill_payment_router
from menu_routes import router as menu_router
from yearly_routes import router as yearly_router
from inventory_forecast_routes import router as inventory_forecast_router
from supplier_routes import router as supplier_router
from cashflow_routes import router as cashflow_router
from stock_routes import router as stock_router
from recipe_routes import router as recipe_router, ingredient_router
from tax_routes import router as tax_router
from auth_routes import router as auth_router, verify_token
from alerts_webhook_routes import router as alerts_router
# === Phase 2: psycopg connection for POS bulk imports ===
# (Phase 1 uses supabase client for OCR flows — this is for high-volume
#  executemany() inserts that need raw PG driver)

import psycopg2

def get_db_conn():
    """Open a fresh psycopg v3 connection to Supabase Postgres."""
    return psycopg2.connect(os.environ["DATABASE_URL"])

# ============================================================
# Config
# ============================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "uploads")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vexonhq-ocr")


# ============================================================
# Clients (lazy init — won't crash on import if env missing)
# ============================================================
_supabase_client: Optional[Client] = None
_openai_client: Optional[OpenAI] = None


def get_supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase_client


def get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY must be set")
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


# ============================================================
# FastAPI app
# ============================================================
app = FastAPI(title="VEXONHQ OCR API", version="3.7.0")
app.include_router(auth_router)   # Auth FIRST — /auth/* routes are public
app.include_router(pos_router)
app.include_router(phase2_router)
app.include_router(phase3_arap_router)
app.include_router(phase3_quick_entry_router)
app.include_router(phase3_daybook_router)
app.include_router(phase3_category_router)
app.include_router(phase3a_ai_categorize_router)
app.include_router(phase3a_anomaly_router)
app.include_router(pnl_router)
app.include_router(line_router)
app.include_router(budget_router)
app.include_router(export_router)
app.include_router(narrative_router)
app.include_router(search_router)
app.include_router(bank_statement_router)
app.include_router(bill_payment_router)
app.include_router(menu_router)
app.include_router(yearly_router)
app.include_router(inventory_forecast_router)
app.include_router(supplier_router)
app.include_router(cashflow_router)
app.include_router(stock_router)
app.include_router(recipe_router)
app.include_router(ingredient_router)
app.include_router(tax_router)
app.include_router(alerts_router)
# ============================================================
# JWT Auth Middleware — protects all routes except public ones
# NOTE: Must be added BEFORE CORSMiddleware so CORS is outermost.
#       In Starlette, the LAST add_middleware call = outermost layer.
#       Outermost CORS ensures CORS headers appear on ALL responses
#       including 401s returned by JWTAuthMiddleware.
# ============================================================
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse as StarletteJSONResponse

PUBLIC_PATHS = {"/", "/health", "/health/deep", "/auth/login", "/auth/logout", "/docs", "/openapi.json", "/redoc", "/ap/due-reminder", "/stock/alert", "/alerts/uptime-webhook", "/alerts/test-telegram", "/line/webhook"}

class JWTAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        path = request.url.path

        # Always pass through OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow public routes (LINE webhook listed explicitly in PUBLIC_PATHS
        # — was previously a broad `/line/*` prefix, which exposed
        # /line/scheduler/status, /line/digest/*, /line/test, /line/weekly-summary
        # to unauthenticated readers. Narrowed Session 24 P1 task O.)
        if (path in PUBLIC_PATHS
                or path.startswith("/auth/")
                or path.startswith("/docs")
                or path.startswith("/redoc")):
            return await call_next(request)

        # Check Authorization header
        auth_header = request.headers.get("Authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

        if not token:
            return StarletteJSONResponse(
                status_code=401,
                content={"detail": "กรุณาเข้าสู่ระบบก่อน"},
            )

        payload = verify_token(token)
        if not payload:
            return StarletteJSONResponse(
                status_code=401,
                content={"detail": "Session หมดอายุ กรุณาเข้าสู่ระบบใหม่"},
            )

        return await call_next(request)

# Add JWT first (inner), then CORS last (outermost) —
# this way CORS headers are applied to ALL responses including auth errors
app.add_middleware(JWTAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://vexonhq-ocr.vercel.app",
        # Coolify self-host frontend (Session 16 migration)
        "http://r7plics0ljl0lxwr6r8zdun0.178.128.31.76.sslip.io",
        "https://r7plics0ljl0lxwr6r8zdun0.178.128.31.76.sslip.io",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Health endpoints
# ============================================================
@app.get("/")
def root():
    return {
        "success": True,
        "service": "VEXONHQ OCR API",
        "version": "3.6.0",
        "status": "running",
    }


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {
        "status": "healthy",
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
        "openai_configured": bool(OPENAI_API_KEY),
        "vision_model": OPENAI_VISION_MODEL,
        "storage_bucket": SUPABASE_STORAGE_BUCKET,
    }


@app.api_route("/health/deep", methods=["GET", "HEAD"])
def health_deep():
    """
    Deep health check — actually verifies dependencies (P0.1, Session 24).

    Unlike /health (which only reports env-var presence), this:
      1. SELECT 1 against Postgres with 5s connect timeout
         (proves DATABASE_URL still valid + Supabase pooler reachable)
      2. SELECT id LIMIT 1 against Supabase REST
         (proves service-role key + PostgREST still working)
      3. Reports env-var presence for OpenAI / LINE / Telegram
         (NO outbound API ping — keeps cost zero, Uptime Robot polls every 5 min)

    Status codes:
      200 healthy   — all critical checks ok
      200 degraded  — some env vars missing but core DB ok (don't trigger alert)
      503 unhealthy — Postgres or Supabase failed (Uptime Robot fires Telegram alert)

    No side effects, read-only. Designed for Uptime Robot polling.
    """
    checks: dict[str, Any] = {}
    db_ok = True

    # 1) Postgres direct (proves pooler URL still valid)
    t0 = time.perf_counter()
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            checks["postgres"] = {
                "ok": True,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        finally:
            conn.close()
    except Exception as e:
        log.exception("health/deep: postgres check failed")
        checks["postgres"] = {
            "ok": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": str(e)[:200],
        }
        db_ok = False

    # 2) Supabase REST (proves service-role key still valid)
    t0 = time.perf_counter()
    try:
        sb = get_supabase()
        sb.table("vendor_bills").select("id").limit(1).execute()
        checks["supabase"] = {
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:
        log.exception("health/deep: supabase check failed")
        checks["supabase"] = {
            "ok": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": str(e)[:200],
        }
        db_ok = False

    # 3) Env-var presence (no outbound calls — cost-free)
    checks["openai_configured"] = bool(OPENAI_API_KEY)
    checks["line_configured"] = bool(os.environ.get("LINE_CHANNEL_TOKEN"))
    checks["telegram_configured"] = bool(
        os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")
    )

    # Decide overall status + HTTP code
    if not db_ok:
        status = "unhealthy"
        http_code = 503
    elif not (checks["openai_configured"] and checks["line_configured"]):
        status = "degraded"
        http_code = 200
    else:
        status = "healthy"
        http_code = 200

    body = {
        "status": status,
        "checks": checks,
        "version": app.version,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }

    if http_code != 200:
        return StarletteJSONResponse(status_code=http_code, content=body)
    return body


# ============================================================
# Legacy /ocr (kept for backward compatibility with old frontend)
# ============================================================
@app.post("/ocr")
async def do_ocr(file: UploadFile = File(...)):
    """Tesseract-only — returns raw text. Kept for backward compat."""
    contents = await file.read()
    text = _run_tesseract(contents)
    return {"success": True, "text": text, "filename": file.filename}


# ============================================================
# Invoice Review Pipeline — models
# ============================================================
class InvoiceItemPayload(BaseModel):
    """One row in the items[] array of an invoice PATCH."""
    line_no: Optional[int] = None
    sku: Optional[str] = None
    product_name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class InvoiceUpdate(BaseModel):
    vendor_name: Optional[str] = None
    merchant_tax_id: Optional[str] = None
    invoice_no: Optional[str] = None
    bill_date: Optional[str] = None   # YYYY-MM-DD
    due_date: Optional[str] = None    # YYYY-MM-DD
    subtotal: Optional[float] = None
    vat: Optional[float] = None
    amount: Optional[float] = None    # total
    payment_type: Optional[str] = None
    notes: Optional[str] = None
    # Optional full replacement of the invoice_items rows during edit.
    #   - None (key absent or null)  -> existing items untouched
    #   - []                          -> all items deleted (user removed everything)
    #   - [ {...}, {...} ]            -> DELETE existing + INSERT these rows
    items: Optional[list[InvoiceItemPayload]] = None


class ConfirmRequest(BaseModel):
    reviewed_by: Optional[str] = None


class RejectRequest(BaseModel):
    reviewed_by: Optional[str] = None
    reject_reason: str


# ============================================================
# Invoice Review Pipeline — endpoints
# ============================================================
@app.post("/invoice/upload")
async def invoice_upload(file: UploadFile = File(...)):
    """
    Main flow: upload invoice → Tesseract + GPT Vision → save to Supabase.

    File types supported:
      - Images: JPG, PNG, WEBP — processed directly
      - PDF: each page rendered to PNG, processed individually
             multi-page merge happens automatically when invoice_no matches

    Multi-page merge: if same (vendor_name, invoice_no) exists with
    review_status in ('pending','needs_attention'), this upload is treated
    as page N+1 of that bill (appends items, attaches as new page).

    Returns:
      success, invoice_id, batch_id, page_no, merged, parsed, warnings,
      preview_url, total_pages_processed
    """
    if not file.filename:
        raise HTTPException(400, "filename required")

    contents = await file.read()
    if not contents:
        raise HTTPException(400, "empty file")

    is_pdf = (file.content_type == "application/pdf") or file.filename.lower().endswith(".pdf")

    if is_pdf:
        # Convert PDF → list of PNG images (1 per page)
        try:
            page_images = _pdf_to_images(contents)
        except Exception as e:
            log.exception("pdf conversion failed")
            raise HTTPException(400, f"pdf conversion failed: {e}")

        if not page_images:
            raise HTTPException(400, "pdf has no readable pages")

        log.info("processing PDF '%s' with %d page(s)", file.filename, len(page_images))

        # Process each page through full pipeline.
        # multi-page merge in _save_invoice() handles merging same-invoice pages.
        page_filename_base = os.path.splitext(file.filename)[0]
        last_result = None
        all_warnings: list[dict[str, str]] = []

        for idx, img_bytes in enumerate(page_images, start=1):
            page_name = f"{page_filename_base}-p{idx}.png"
            result = _process_single_image(img_bytes, page_name, "image/png")
            last_result = result
            all_warnings.extend(result["warnings"])

        # Return the LAST page's result (which has the final merged state),
        # but combined warnings from all pages
        assert last_result is not None
        last_result["warnings"] = all_warnings
        last_result["total_pages_processed"] = len(page_images)
        return last_result

    # Single image path
    result = _process_single_image(contents, file.filename, file.content_type or "image/jpeg")
    result["total_pages_processed"] = 1
    return result


def _process_single_image(
    image_bytes: bytes,
    file_name: str,
    mime_type: str,
) -> dict[str, Any]:
    """Full pipeline for ONE image: Tesseract → Vision → validate → store → DB save."""

    # 1) Tesseract OCR (as hint for Vision)
    try:
        ocr_text = _run_tesseract(image_bytes)
    except Exception as e:
        log.warning("tesseract failed (continuing): %s", e)
        ocr_text = ""

    # 2) GPT-4 Vision structured extraction
    try:
        parsed = _run_gpt_vision(image_bytes, mime_type, ocr_text)
    except Exception as e:
        log.exception("vision failed")
        raise HTTPException(500, f"vision extraction failed: {e}")

    # 3) Validation warnings
    warnings = _validate_invoice(parsed)

    # 4) Upload to Supabase Storage
    file_url = None
    try:
        file_url, _ = _upload_to_storage(image_bytes, file_name, mime_type)
    except Exception:
        log.exception("storage upload failed (continuing without file_url)")

    # 5) Save to DB (multi-page merge)
    try:
        invoice_id, batch_id, page_no, merged = _save_invoice(
            parsed=parsed,
            ocr_text=ocr_text,
            file_url=file_url,
            file_name=file_name,
            mime_type=mime_type,
        )
    except Exception as e:
        log.exception("db save failed")
        raise HTTPException(500, f"db save failed: {e}")

    # 6) Revalidate against the merged state so warnings stay accurate
    #    after multi-page backfill (e.g. Makro page 3 fills in the total
    #    that page 1 was missing — MISSING_TOTAL warning should disappear)
    try:
        final_warnings = _revalidate_bill(invoice_id)
    except Exception as e:
        log.warning("revalidate failed (using page warnings instead): %s", e)
        final_warnings = warnings
        if warnings:
            _save_warnings(invoice_id, warnings)

    return {
        "success": True,
        "invoice_id": invoice_id,
        "batch_id": batch_id,
        "page_no": page_no,
        "merged": merged,
        "parsed": parsed,
        "warnings": final_warnings,
        "preview_url": file_url,
    }


@app.get("/invoice/queue")
def invoice_queue(limit: int = 50, offset: int = 0):
    """Return pending review queue (uses v_invoice_review_queue view)."""
    sb = get_supabase()
    resp = (
        sb.table("v_invoice_review_queue")
        .select("*")
        .limit(limit)
        .offset(offset)
        .execute()
    )
    return {"success": True, "invoices": resp.data, "count": len(resp.data or [])}


@app.get("/invoice/duplicates")
def invoice_suspected_duplicates():
    """Find groups of pending bills suspected to be duplicates of each other.

    Strict-match heuristic (high precision, low recall):
      same vendor_name (lowercase trim) + same amount + same bill_date.

    Catches OCR misread cases where the same paper invoice was uploaded twice
    and the model extracted slightly different `invoice_no` strings (e.g.
    "SS 68093823" vs "SS 680903823" — an extra "0"). Strict dedup by invoice_no
    in _save_invoice() can't catch this; this endpoint surfaces it for the user.

    Only considers bills with `review_status IN ('pending', 'needs_attention')`.
    Once a bill is confirmed/rejected, it's intentionally excluded so the
    operator can re-upload a confirmed bill if needed.

    Returns groups of >= 2 bills. Each group lists the bills sorted by
    `created_at` ascending (oldest first) so the user can keep the original
    and reject the later upload.
    """
    sb = get_supabase()
    resp = (
        sb.table("vendor_bills")
        .select("id, vendor_name, invoice_no, bill_date, amount, "
                "created_at, review_status, payment_status")
        .in_("review_status", ["pending", "needs_attention"])
        .execute()
    )
    rows = resp.data or []

    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        vendor_raw = r.get("vendor_name")
        amount_raw = r.get("amount")
        if not vendor_raw or amount_raw is None:
            continue
        vendor_norm = vendor_raw.strip().lower()
        try:
            amount_num = float(amount_raw)
        except (TypeError, ValueError):
            continue
        if amount_num <= 0:
            continue
        key = (vendor_norm, amount_num, r.get("bill_date"))
        groups[key].append(r)

    out_groups: list[dict] = []
    for (_vendor_norm, amount_num, _bill_date), bills in groups.items():
        if len(bills) < 2:
            continue
        bills.sort(key=lambda b: b.get("created_at") or "")
        out_groups.append({
            "vendor_name": bills[0].get("vendor_name"),
            "amount": amount_num,
            "bill_date": bills[0].get("bill_date"),
            "count": len(bills),
            "bills": [
                {
                    "id": b["id"],
                    "invoice_no": b.get("invoice_no"),
                    "created_at": b.get("created_at"),
                    "review_status": b.get("review_status"),
                    "payment_status": b.get("payment_status"),
                }
                for b in bills
            ],
        })

    out_groups.sort(key=lambda g: (g["vendor_name"] or "", g["amount"]))

    return {
        "success": True,
        "groups": out_groups,
        "total_groups": len(out_groups),
        "total_bills": sum(g["count"] for g in out_groups),
    }


@app.get("/invoice/items/suggest")
def invoice_items_suggest(q: str = "", limit: int = 10):
    """
    Autocomplete suggestions for invoice line item product names.

    Returns distinct product_name values seen on previously confirmed
    bills, ranked by frequency (most-purchased first) then recency.
    Used by the /invoices/<id> editor so TUM can pick an existing name
    instead of typing a new variant — keeps the items catalogue from
    accumulating "เบียร์สิงห์" vs "เบียร์ สิงห์" vs "สิงห์ เบียร์"
    duplicates that the OCR pipeline can introduce.

    Each suggestion also surfaces the most-common `unit` and the
    most-common `unit_price` seen for that product so the frontend
    can pre-fill all three fields with one click.

    Note: route is placed BEFORE `/invoice/{invoice_id}` in source
    order. FastAPI evaluates routes in registration order; even though
    path parameters don't span slashes (so /invoice/items/suggest
    wouldn't match /invoice/{invoice_id} anyway), keeping the
    specific path first is the safer convention.
    """
    q_norm = (q or "").strip()
    if not q_norm:
        return {"query": q_norm, "suggestions": []}

    safe_limit = min(max(limit, 1), 50)

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Group by the *normalized* product name (trim + collapse
            # internal whitespace) so OCR variants like "เบียร์ช้าง 620 มล."
            # vs "เบียร์ช้าง  620 มล. " consolidate into one suggestion
            # row instead of polluting the dropdown with near-duplicates.
            # Use mode() over the raw product_name to surface the
            # most-common canonical spelling for display.
            cur.execute(
                """
                SELECT
                    mode() WITHIN GROUP (ORDER BY ii.product_name) AS product_name,
                    COUNT(*)::int                                  AS uses,
                    mode() WITHIN GROUP (ORDER BY ii.unit)         AS common_unit,
                    mode() WITHIN GROUP (ORDER BY ii.unit_price)   AS common_unit_price,
                    MAX(vb.bill_date)                              AS last_used
                FROM public.invoice_items ii
                JOIN public.vendor_bills vb ON vb.id = ii.vendor_bill_id
                WHERE vb.review_status = 'confirmed'
                  AND ii.product_name IS NOT NULL
                  AND TRIM(ii.product_name) <> ''
                  AND ii.product_name ILIKE %s
                GROUP BY regexp_replace(TRIM(ii.product_name), '\\s+', ' ', 'g')
                ORDER BY uses DESC,
                         MAX(vb.bill_date) DESC NULLS LAST,
                         mode() WITHIN GROUP (ORDER BY ii.product_name) ASC
                LIMIT %s
                """,
                (f"%{q_norm}%", safe_limit),
            )
            suggestions = [
                {
                    "product_name":     r[0],
                    "uses":             int(r[1] or 0),
                    "common_unit":      r[2],
                    "common_unit_price": float(r[3]) if r[3] is not None else None,
                    "last_used":        str(r[4]) if r[4] else None,
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()

    return {"query": q_norm, "suggestions": suggestions}


@app.get("/invoice/{invoice_id}")
def invoice_detail(invoice_id: str):
    """Full invoice detail: header + items + pages + warnings."""
    sb = get_supabase()

    bill = sb.table("vendor_bills").select("*").eq("id", invoice_id).execute()
    if not bill.data:
        raise HTTPException(404, "invoice not found")
    invoice = bill.data[0]

    items = (
        sb.table("invoice_items")
        .select("*")
        .eq("vendor_bill_id", invoice_id)
        .order("line_no")
        .execute()
    )
    pages = (
        sb.table("attachments")
        .select("*")
        .eq("parent_type", "vendor_bill")
        .eq("parent_id", invoice_id)
        .order("page_no")
        .execute()
    )
    warns = (
        sb.table("invoice_validation_warnings")
        .select("*")
        .eq("vendor_bill_id", invoice_id)
        .order("created_at")
        .execute()
    )

    return {
        "success": True,
        "invoice": invoice,
        "items": items.data or [],
        "pages": pages.data or [],
        "warnings": warns.data or [],
    }


@app.patch("/invoice/{invoice_id}")
def invoice_edit(invoice_id: str, update: InvoiceUpdate):
    """
    Edit invoice header fields and/or replace the line-item array.

    Two independent payloads in one call:
      - Header fields (vendor_name, dates, totals, etc.) -> partial update
        on vendor_bills (only the keys actually provided).
      - `items` (optional) -> if present, the entire invoice_items array
        for this invoice is replaced atomically (DELETE + INSERT in one
        psycopg2 transaction so we never end up with the old items
        partially deleted on failure).
    """
    # Pydantic v1: .dict(); v2: .model_dump()  — main.py uses v1 elsewhere.
    update_dict = update.dict()
    items_payload = update_dict.pop("items", None)
    header_payload = {k: v for k, v in update_dict.items() if v is not None}

    if not header_payload and items_payload is None:
        raise HTTPException(400, "no fields to update")

    sb = get_supabase()

    # 1. Header update (or just existence check if only items are being edited).
    if header_payload:
        resp = sb.table("vendor_bills").update(header_payload).eq("id", invoice_id).execute()
        if not resp.data:
            raise HTTPException(404, "invoice not found")
        invoice = resp.data[0]
    else:
        bill = sb.table("vendor_bills").select("*").eq("id", invoice_id).execute()
        if not bill.data:
            raise HTTPException(404, "invoice not found")
        invoice = bill.data[0]

    # 2. Replace invoice_items atomically when caller passed the items array.
    if items_payload is not None:
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM public.invoice_items WHERE vendor_bill_id = %s",
                    (invoice_id,),
                )
                if items_payload:
                    rows_to_insert = []
                    for idx, it in enumerate(items_payload, start=1):
                        rows_to_insert.append((
                            invoice_id,
                            it.get("line_no") or idx,
                            it.get("sku"),
                            it.get("product_name"),
                            it.get("quantity"),
                            it.get("unit"),
                            it.get("unit_price"),
                            it.get("amount"),
                        ))
                    cur.executemany(
                        """
                        INSERT INTO public.invoice_items
                            (vendor_bill_id, line_no, sku, product_name,
                             quantity, unit, unit_price, amount)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        rows_to_insert,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # 3. Return the (possibly updated) header + the freshly read items so
    #    the client doesn't have to refetch.
    items = (
        sb.table("invoice_items")
        .select("*")
        .eq("vendor_bill_id", invoice_id)
        .order("line_no")
        .execute()
    )
    return {"success": True, "invoice": invoice, "items": items.data or []}


@app.post("/invoice/{invoice_id}/confirm")
def invoice_confirm(invoice_id: str, body: Optional[ConfirmRequest] = None):
    sb = get_supabase()
    now_iso = datetime.utcnow().isoformat()
    resp = (
        sb.table("vendor_bills")
        .update({
            "review_status": "confirmed",
            "reviewed_by": (body.reviewed_by if body else None),
            "reviewed_at": now_iso,
            "confirmed_at": now_iso,
        })
        .eq("id", invoice_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(404, "invoice not found")
    return {"success": True, "invoice": resp.data[0]}


@app.post("/invoice/{invoice_id}/reject")
def invoice_reject(invoice_id: str, body: RejectRequest):
    sb = get_supabase()
    resp = (
        sb.table("vendor_bills")
        .update({
            "review_status": "rejected",
            "reviewed_by": body.reviewed_by,
            "reviewed_at": datetime.utcnow().isoformat(),
            "reject_reason": body.reject_reason,
        })
        .eq("id", invoice_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(404, "invoice not found")
    return {"success": True, "invoice": resp.data[0]}


# ============================================================
# Helpers — PDF handling
# ============================================================
def _pdf_to_images(pdf_bytes: bytes, scale: float = 3.0) -> list[bytes]:
    """
    Render each page of a PDF to PNG bytes.

    scale=3.0 gives ~216 DPI — sharper text for both Tesseract and GPT Vision.
    This is especially important for invoices with many small items (e.g. Makro
    where each page has 20+ rows). Trade-off: slightly larger PNG files sent
    to OpenAI (still well under their 20MB image limit per page).
    """
    images: list[bytes] = []
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        for page in pdf:
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
            # Ensure RGB (not RGBA) for smaller PNG + OpenAI compat
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG", optimize=True)
            images.append(buf.getvalue())
    finally:
        pdf.close()
    return images


# ============================================================
# Helpers — OCR
# ============================================================
def _run_tesseract(image_bytes: bytes) -> str:
    """Tesseract OCR (Thai+English) on image bytes. Returns raw text or '' if it fails."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        image = cv2.imread(tmp_path)
        if image is None:
            return ""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray)
        processed = cv2.threshold(denoised, 150, 255, cv2.THRESH_BINARY)[1]
        text = pytesseract.image_to_string(processed, lang="tha+eng", config="--psm 6")
        return text
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ============================================================
# Helpers — GPT Vision structured extraction
# ============================================================
VISION_PROMPT = """You are an expert Thai/English accounting OCR system specialized in Thai tax invoices (ใบกำกับภาษี) and receipts.

Analyze the provided invoice IMAGE and extract structured data.

The Tesseract OCR text below may contain errors. Use it as a hint, but TRUST THE IMAGE as the source of truth:

--- OCR HINT ---
{ocr_hint}
--- END HINT ---

Return ONLY valid JSON matching this exact schema (no markdown, no commentary):

{{
  "vendor_name": "string or null",
  "merchant_tax_id": "13-digit Thai tax ID as string, or null",
  "invoice_no": "string or null (look for 'เลขที่', 'No.', 'Invoice #', 'INV')",
  "bill_date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null (look for 'ครบกำหนด', credit terms like 'เครดิต 30 วัน' = bill_date + 30 days)",
  "subtotal": number_or_null,
  "vat": number_or_null,
  "amount": number_or_null,
  "payment_type": "credit_card|transfer|cash|cheque|other|null",
  "currency": "THB",
  "items": [
    {{
      "line_no": 1,
      "sku": "string or null",
      "product_name": "string (real product name only — see CRITICAL RULE 5)",
      "quantity": number_or_null,
      "unit": "string or null",
      "unit_price": number_or_null,
      "amount": number_or_null
    }}
  ],
  "notes": "string or null"
}}

═══════════════════════════════════════════════════════════════
CRITICAL RULES — read carefully, these errors are common:
═══════════════════════════════════════════════════════════════

0. ⭐ ITEM COMPLETENESS — EXTRACT EVERY ITEM ROW
   This is the MOST important rule. Real-world Thai wholesale invoices
   (Makro, Siam Makro, Big C, Lotus, supplier orders) typically have
   15 to 35 item rows PER PAGE. If you return only 5-6 items from a
   page that visibly has many more rows, you are FAILING.

   Strategy:
   - Look at the items table region (usually the middle/bottom of the page)
   - Count the rows yourself before extracting
   - Extract EVERY row that has a product name and a price
   - Do NOT sample, do NOT skip "similar-looking" rows
   - If a row's text is partially unclear, still extract what you can read
   - When in doubt, INCLUDE the row (recall > precision)

   You can identify a real item row by:
   - It has a product name (Thai or English) that describes a thing
     (food, beverage, tool, supply, etc.)
   - It has at least one of: quantity, unit, unit_price, amount
   - It is in the body of the items table (between the header row
     and the totals/summary at the bottom)

   ❌ Wrong: Extracting only 6 rows from a 20-row items table
   ✅ Right: Extracting all 20 rows with product names

1. NULL OVER GUESSING
   Use null (not 0, not empty string) when uncertain. NEVER make up data.
   Especially: NEVER fabricate 13-digit tax IDs. If you can't read clearly → null.

2. THAI BUDDHIST YEAR CONVERSION
   Thai invoices use BE year (พ.ศ.). Convert to Gregorian: BE − 543 = CE.
   Most invoices in 2026 will show year 2569 BE.
   ⚠️ Digit 6 and 9 in Thai fonts look similar — examine the shape carefully.
   Examples:
     "30/04/2569" → "2026-04-30"
     "08/04/2569" → "2026-04-08"
     "12/05/2566" → "2023-05-12" (different year, double-check)

3. MULTI-PAGE INVOICES — TOTALS ONLY ON LAST PAGE
   Multi-page invoices (เช่น Makro 3-4 หน้า) show totals (subtotal/vat/amount)
   ONLY on the last page. Earlier pages show item lines but NO total summary.

   When processing an intermediate page (e.g. you see "1/3" or "2/3" indicator,
   or you see only item rows without "รวมเงิน"/"AMOUNT" summary box):
     → Return subtotal, vat, amount as **null**
     → Still extract all items from that page
     → DO NOT invent totals based on items shown

   When you see the last page (totals are visible):
     → Extract subtotal, vat, amount from the summary box at bottom

4. ❌ NEVER CAPTURE COLUMN HEADERS AS ITEMS
   Item tables have a header row at the TOP. These are LABELS, not products.
   The following text strings are ALWAYS column headers — SKIP them:

   Thai column headers to IGNORE:
     "ลำดับ", "ITEM", "#"
     "รหัส", "รหัสสินค้า", "SKU", "PRODUCT CODE", "ARTICLE NO"
     "รายการ", "รายละเอียด", "DESCRIPTION"
     "จำนวน", "QUANTITY", "QTY"
     "หน่วย", "UNIT"
     "ราคา", "ราคาต่อหน่วย", "UNIT PRICE"
     "ลด", "ส่วนลด %", "DISCOUNT %"
     "รหัส ภ.พ.", "ภ.พ.", "VAT CODE", "TAX CODE"
     "ยอดรวม", "จำนวนเงิน", "AMOUNT", "TOTAL"

   ⚠️ Common mistake: "รหัส ภ.พ." (= VAT code column header in Makro receipts)
      is NOT a product. Same with "DESCRIPTION", "QUANTITY", etc.

5. ❌ NEVER CAPTURE SUMMARY ROWS AS ITEMS
   The bottom of an invoice has total/summary rows. These are NOT items:

   Summary row labels to IGNORE (these appear BELOW items, with totals):
     "รวมเงิน", "รวมราคาสินค้า/GOODS VALUE", "SUBTOTAL"
     "ภาษีมูลค่าเพิ่ม", "VAT"
     "จำนวนเงิน/AMOUNT", "NET AMOUNT", "TOTAL"
     "ส่วนลด", "DISCOUNT"
     "ค่ามัดจำ", "DEPOSIT"
     "หัก ณ ที่จ่าย"
     ตัวอักษรไทยบอกจำนวนเงิน (เช่น "สองหมื่นแปดพัน...บาท")

5.1 ❌ NEVER capture VAT CATEGORY SUMMARY rows as items (Makro pattern)
    Thai wholesale invoices (esp. Makro) show a VAT category breakdown
    at the bottom of the items area, like:

      ลำดับ   รหัส ภ.พ.   จำนวน        ราคาสินค้า
        1                  36.932        3,824.75
        2                  16            2,067.00
        รวม                              5,756.53

    The number "1" or "2" alone in the "รหัส" column is a TAX CODE,
    NOT a product code. The product_name column is EMPTY for these rows.
    The quantity is the SUM of all items with that VAT rate.

    SKIP RULE — any row where product_name matches one of these patterns:
      - EMPTY / null / blank
      - "ไม่ระบุ" (= "unspecified" in Thai)
      - "N/A", "n/a", "NA", "ไม่มี"
      - "-", "—", "_"
      - just a digit "1", "2", "3" with no name
      - "unspecified", "unknown", "blank"
      - "รหัส ภ.พ.", "ภ.พ.", "VAT", "TAX CODE"

    A REAL item ALWAYS has a meaningful descriptive name:
      ✅ "เห็ดนางรมหลวง ขนาด L 1 กก." → KEEP
      ✅ "เบียร์ SINGHA RESERVE (12x620CC)" → KEEP
      ❌ "ไม่ระบุ" → DROP, never include (it's a placeholder)
      ❌ null product_name → DROP, never include
      ❌ "1" → DROP, it's a tax code not a product

6. PRODUCT NAMES — READ THE EXACT TEXT
   Read each product name CHARACTER-BY-CHARACTER from the image.
   ❌ DO NOT substitute or guess based on vendor context.
   Example: If you see a Singha Beer invoice and the line text says
   "โซดาสิงห์เปลี่ยนขวด" — write exactly that, NOT "เบียร์ลีโอ"
   even though the vendor is a beer company.

7. ITEM EXTRACTION CRITERIA — DEFAULT IS "INCLUDE"
   For each row in the items table area, ask yourself:
     - Does it have a product name describing a real thing? → EXTRACT
     - Is it ONLY a column header label ("รายการ", "DESCRIPTION", "QTY")? → SKIP
     - Is it ONLY a summary total row ("รวมเงิน", "TOTAL", "VAT")? → SKIP
     - Is product_name field EMPTY and SKU is just "1" or "2"? → SKIP (tax category)
     - Anything else? → INCLUDE

   When in doubt: INCLUDE. It's better to include a borderline row
   than to miss real items. The human will review.

   Watch out for:
     - Items in second column (some receipts use 2-column item layouts)
     - Items with very short names (e.g., "เอ.พี. 1 กก.")
     - Items split across 2 lines (the product name wraps)
     - Sub-items / variants (extract each)

8. PAYMENT TYPE DETECTION
   Look for hints in the invoice:
     "บัตรเครดิต" / "Credit Card" / "VISA" / "Master Card"  → credit_card
     "โอน" / "Transfer" / "PromptPay" / "พร้อมเพย์"          → transfer
     "เงินสด" / "Cash"                                       → cash
     "เช็ค" / "Cheque"                                       → cheque
     "Payment On Delivery" / "POD" / "เก็บเงินปลายทาง"        → other
     ไม่ชัดเจน                                                → null

9. NUMBERS ARE NUMERIC
   Numbers in JSON must be JSON numbers, not strings.
   1,234.56 → 1234.56 (no comma, no quotes)

10. OUTPUT
    Pure JSON only. NO markdown fences. NO explanation. NO preamble.
"""


def _run_gpt_vision(image_bytes: bytes, mime_type: str, ocr_hint: str) -> dict[str, Any]:
    """Send image to GPT-4 Vision and return parsed JSON."""
    client = get_openai()
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type or 'image/jpeg'};base64,{b64}"
    prompt = VISION_PROMPT.format(ocr_hint=(ocr_hint or "(empty)")[:3000])

    resp = client.chat.completions.create(
        model=OPENAI_VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=4000,  # increased for high-item-count invoices (Makro 20-30 items/page)
    )

    raw = (resp.choices[0].message.content or "{}").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # strip markdown fences if model wrapped output despite instructions
        cleaned = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)


# ============================================================
# Helpers — Validation
# ============================================================
def _validate_invoice(parsed: dict[str, Any]) -> list[dict[str, str]]:
    """Run validation rules; return list of warnings."""
    warnings: list[dict[str, str]] = []

    if not parsed.get("vendor_name"):
        warnings.append({"severity": "warn", "code": "MISSING_VENDOR",
                         "message": "ไม่พบชื่อผู้ขาย", "field": "vendor_name"})

    if not parsed.get("invoice_no"):
        warnings.append({"severity": "warn", "code": "MISSING_INVOICE_NO",
                         "message": "ไม่พบเลขที่ใบกำกับ", "field": "invoice_no"})

    if not parsed.get("merchant_tax_id"):
        warnings.append({"severity": "info", "code": "MISSING_TAX_ID",
                         "message": "ไม่พบเลขผู้เสียภาษี", "field": "merchant_tax_id"})

    total = parsed.get("amount")
    if total is None:
        warnings.append({"severity": "error", "code": "MISSING_TOTAL",
                         "message": "ไม่พบยอดรวม", "field": "amount"})

    subtotal = parsed.get("subtotal")
    vat = parsed.get("vat")
    if subtotal is not None and vat is not None and total is not None:
        try:
            expected = round(float(subtotal) + float(vat), 2)
            actual = round(float(total), 2)
            if abs(expected - actual) > 0.05:
                warnings.append({
                    "severity": "warn",
                    "code": "VAT_MISMATCH",
                    "message": f"ยอดไม่ตรง: subtotal+vat={expected:,.2f} แต่ total={actual:,.2f}",
                    "field": "amount",
                })
        except (TypeError, ValueError):
            pass

    if total is not None:
        try:
            if float(total) > 10000:
                warnings.append({
                    "severity": "info",
                    "code": "HIGH_VALUE",
                    "message": f"ใบกำกับมูลค่าสูง ({float(total):,.2f} THB) — โปรดตรวจสอบ",
                    "field": "amount",
                })
        except (TypeError, ValueError):
            pass

    return warnings


# ============================================================
# Helpers — Storage
# ============================================================
def _upload_to_storage(image_bytes: bytes, file_name: str, mime_type: Optional[str]) -> tuple[str, str]:
    """Upload to Supabase Storage. Returns (public_url, storage_path)."""
    sb = get_supabase()
    bucket = SUPABASE_STORAGE_BUCKET
    today = date.today().strftime("%Y-%m")
    ext = os.path.splitext(file_name)[1] or ".bin"
    storage_path = f"invoices/{today}/{uuid.uuid4()}{ext}"

    sb.storage.from_(bucket).upload(
        storage_path,
        image_bytes,
        file_options={"content-type": mime_type or "application/octet-stream"},
    )
    public_url = sb.storage.from_(bucket).get_public_url(storage_path)
    return public_url, storage_path


# ============================================================
# Helpers — DB writes (with multi-page merge)
# ============================================================
def _save_invoice(
    parsed: dict[str, Any],
    ocr_text: str,
    file_url: Optional[str],
    file_name: str,
    mime_type: Optional[str],
) -> tuple[str, str, int, bool]:
    """
    Save invoice with multi-page merge.
    Returns (invoice_id, batch_id, page_no, merged).
    """
    sb = get_supabase()
    # Aggressive whitespace normalization: collapse ALL whitespace (including
    # internal double-spaces, tabs, zero-width chars) before storage.
    # Bug v3.5 had: GPT extracted "บริษัท ซีพี" on page 1 and
    # "บริษัท  ซีพี" (double space) on page 2 → dedup_key differed → 2 bills.
    vendor_name = _norm_text(parsed.get("vendor_name"))
    invoice_no = _norm_text(parsed.get("invoice_no"))

    # Compute dedup_key the SAME way the DB does (lower of vendor|invoice)
    dedup_key = None
    if vendor_name and invoice_no:
        dedup_key = f"{vendor_name.lower()}|{invoice_no.lower()}"

    existing = None

    # Primary lookup: exact dedup_key match (most precise)
    if dedup_key:
        try:
            res = (
                sb.table("vendor_bills")
                .select("*")
                .eq("dedup_key", dedup_key)
                .in_("review_status", ["pending", "needs_attention"])
                .limit(1)
                .execute()
            )
            if res.data:
                existing = res.data[0]
        except Exception as e:
            log.warning("dedup lookup (dedup_key) failed: %s", e)

    # Fallback lookup: by invoice_no only (catches case where GPT extracts
    # vendor_name slightly differently between pages — invoice_no is the
    # most reliable across pages because it's a unique number on every page).
    if not existing and invoice_no:
        try:
            res = (
                sb.table("vendor_bills")
                .select("*")
                .eq("invoice_no", invoice_no)
                .in_("review_status", ["pending", "needs_attention"])
                .limit(1)
                .execute()
            )
            if res.data:
                existing = res.data[0]
                log.info("dedup matched by invoice_no fallback (vendor_name differed)")
        except Exception as e:
            log.warning("dedup lookup (invoice_no fallback) failed: %s", e)

    if existing:
        # ---- MERGE PATH: append items + backfill any null header fields ----
        invoice_id = existing["id"]
        batch_id = existing.get("batch_id") or str(uuid.uuid4())

        # Backfill: if existing has null for a header field but this page provides one,
        # populate it. This handles multi-page invoices where totals appear only on
        # the last page (e.g. Makro 3-page invoices).
        backfill: dict[str, Any] = {}
        if not existing.get("batch_id"):
            backfill["batch_id"] = batch_id

        for field in ("merchant_tax_id", "bill_date", "due_date",
                      "subtotal", "vat", "amount", "payment_type", "notes"):
            if existing.get(field) in (None, "") and parsed.get(field) not in (None, ""):
                backfill[field] = parsed.get(field)

        if backfill:
            sb.table("vendor_bills").update(backfill).eq("id", invoice_id).execute()

        page_count_resp = (
            sb.table("attachments")
            .select("id", count="exact")
            .eq("parent_type", "vendor_bill")
            .eq("parent_id", invoice_id)
            .execute()
        )
        page_no = (page_count_resp.count or 0) + 1
        merged = True
        _insert_items(invoice_id, parsed.get("items") or [], page_no)

    else:
        # ---- CREATE PATH: new bill ----
        # Retry-on-conflict: if our SELECT missed an existing row (e.g. race
        # condition, stale read, PostgREST URL-encoding mismatch), the unique
        # index on dedup_key will reject the INSERT. We catch that, re-fetch
        # the existing bill, and merge instead of failing.
        invoice_id = str(uuid.uuid4())
        batch_id = str(uuid.uuid4())
        page_no = 1
        merged = False

        try:
            sb.table("vendor_bills").insert({
                "id": invoice_id,
                "vendor_name": vendor_name,
                "merchant_tax_id": parsed.get("merchant_tax_id"),
                "invoice_no": invoice_no,
                "bill_date": parsed.get("bill_date"),
                "due_date": parsed.get("due_date"),
                "subtotal": parsed.get("subtotal"),
                "vat": parsed.get("vat"),
                "amount": parsed.get("amount"),
                "currency": parsed.get("currency") or "THB",
                "payment_type": parsed.get("payment_type"),
                "status": "unpaid",
                "review_status": "pending",
                "attachment_url": file_url,
                "ocr_json": parsed,
                "notes": parsed.get("notes"),
                "batch_id": batch_id,
            }).execute()
            _insert_items(invoice_id, parsed.get("items") or [], page_no)
        except Exception as e:
            err = str(e).lower()
            if dedup_key and ("uq_vendor_bills_dedup" in err or "duplicate key" in err or "23505" in err):
                log.warning("create-conflict on dedup_key=%s → falling back to merge", dedup_key)
                # Re-fetch the existing bill (this time exact dedup_key lookup)
                res = (
                    sb.table("vendor_bills")
                    .select("*")
                    .eq("dedup_key", dedup_key)
                    .limit(1)
                    .execute()
                )
                if not res.data:
                    raise RuntimeError(f"unique conflict but no existing row for {dedup_key}")
                existing = res.data[0]
                invoice_id = existing["id"]
                batch_id = existing.get("batch_id") or str(uuid.uuid4())
                if not existing.get("batch_id"):
                    sb.table("vendor_bills").update({"batch_id": batch_id}).eq("id", invoice_id).execute()
                page_count_resp = (
                    sb.table("attachments")
                    .select("id", count="exact")
                    .eq("parent_type", "vendor_bill")
                    .eq("parent_id", invoice_id)
                    .execute()
                )
                page_no = (page_count_resp.count or 0) + 1
                merged = True
                _insert_items(invoice_id, parsed.get("items") or [], page_no)
            else:
                raise

    # Save attachment row for this page
    if file_url:
        sb.table("attachments").insert({
            "parent_type": "vendor_bill",
            "parent_id": invoice_id,
            "batch_id": batch_id,
            "file_name": file_name,
            "file_url": file_url,
            "mime_type": mime_type,
            "page_no": page_no,
        }).execute()

    return invoice_id, batch_id, page_no, merged


# Placeholder product names that GPT sometimes returns instead of null.
# These are NEVER real items — they are tax-category summary rows or
# unrecognized rows. Filter aggressively at the backend so they never reach
# the items table, regardless of what the prompt told Vision to do.
_PLACEHOLDER_NAMES = {
    "ไม่ระบุ", "ไม่มี", "ไม่ทราบ",
    "n/a", "na", "none", "null",
    "unspecified", "unknown", "blank",
    "รหัส ภ.พ.", "ภ.พ.", "vat code", "tax code", "vat", "tax",
    "-", "—", "_",
}


def _norm_text(s: Optional[str]) -> Optional[str]:
    """
    Aggressively normalize a text value before dedup/storage.
    - Strips leading/trailing whitespace
    - Collapses internal whitespace runs (multiple spaces, tabs, newlines)
      into a single space
    - Returns None for empty/whitespace-only input

    Prevents dedup_key drift across pages where GPT extracts the same vendor
    name but with subtly different internal spacing (Bug fixed in v3.6).
    """
    if not s:
        return None
    # str.split() with no args splits on any whitespace run AND drops empties
    normalized = " ".join(s.split())
    return normalized or None


def _is_real_item(it: dict[str, Any]) -> bool:
    """Return True only if this item dict looks like a real product row."""
    name = (it.get("product_name") or "").strip()
    if not name:
        return False
    name_lower = name.lower()
    if name_lower in _PLACEHOLDER_NAMES:
        return False
    # Pure digit name (e.g. "1", "2") is a tax code, not a product
    if name.isdigit():
        return False
    # Very short non-alphanumeric like "-" or "—" after lower-stripping
    if len(name) <= 2 and not any(c.isalnum() for c in name):
        return False
    return True


def _insert_items(invoice_id: str, items: list[dict[str, Any]], source_page: int):
    """Bulk insert line items for this invoice page (with placeholder filter)."""
    if not items:
        return
    sb = get_supabase()
    rows = []
    dropped = 0
    for idx, it in enumerate(items, start=1):
        if not _is_real_item(it):
            dropped += 1
            continue
        rows.append({
            "vendor_bill_id": invoice_id,
            "line_no": it.get("line_no") or idx,
            "sku": it.get("sku"),
            "product_name": it.get("product_name"),
            "quantity": it.get("quantity"),
            "unit": it.get("unit"),
            "unit_price": it.get("unit_price"),
            "amount": it.get("amount"),
            "source_page": source_page,
        })
    if dropped:
        log.info("filtered %d placeholder item(s) from page %d", dropped, source_page)
    if rows:
        sb.table("invoice_items").insert(rows).execute()


def _save_warnings(invoice_id: str, warnings: list[dict[str, str]]):
    """Bulk insert validation warnings."""
    if not warnings:
        return
    sb = get_supabase()
    rows = []
    for w in warnings:
        rows.append({
            "vendor_bill_id": invoice_id,
            "severity": w.get("severity", "warn"),
            "code": w.get("code", "UNKNOWN"),
            "message": w.get("message", ""),
            "field": w.get("field"),
        })
    sb.table("invoice_validation_warnings").insert(rows).execute()


def _revalidate_bill(invoice_id: str) -> list[dict[str, str]]:
    """
    Re-run validation against the CURRENT merged state of the vendor_bill.
    Replaces any existing UNRESOLVED warnings with fresh ones.
    Resolved warnings (user marked done) are preserved.

    This keeps warnings accurate across multi-page merges — e.g. if page 1
    of Makro had no total (MISSING_TOTAL fired), then page 3 backfills the
    total, the MISSING_TOTAL warning should disappear automatically.
    """
    sb = get_supabase()
    res = sb.table("vendor_bills").select("*").eq("id", invoice_id).execute()
    if not res.data:
        return []
    bill = res.data[0]

    # Build a "parsed-like" dict from the bill row
    parsed_like = {
        "vendor_name": bill.get("vendor_name"),
        "merchant_tax_id": bill.get("merchant_tax_id"),
        "invoice_no": bill.get("invoice_no"),
        "bill_date": bill.get("bill_date"),
        "due_date": bill.get("due_date"),
        "subtotal": bill.get("subtotal"),
        "vat": bill.get("vat"),
        "amount": bill.get("amount"),
    }

    # Re-run validation against merged bill state
    fresh_warnings = _validate_invoice(parsed_like)

    # Delete existing UNRESOLVED warnings (preserve resolved ones — user marked done)
    try:
        sb.table("invoice_validation_warnings").delete().eq(
            "vendor_bill_id", invoice_id
        ).eq("resolved", False).execute()
    except Exception:
        # resolved column may not exist yet — delete all and reinsert
        sb.table("invoice_validation_warnings").delete().eq(
            "vendor_bill_id", invoice_id
        ).execute()

    if fresh_warnings:
        _save_warnings(invoice_id, fresh_warnings)

    return fresh_warnings
