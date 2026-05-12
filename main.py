"""
VEXONHQ OCR API — v3.1 (Phase 1: Invoice Review Pipeline + PDF support)

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
app = FastAPI(title="VEXONHQ OCR API", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://vexonhq-ocr.vercel.app",
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
        "version": "3.1.0",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
        "openai_configured": bool(OPENAI_API_KEY),
        "vision_model": OPENAI_VISION_MODEL,
        "storage_bucket": SUPABASE_STORAGE_BUCKET,
    }


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
        if warnings:
            _save_warnings(invoice_id, warnings)
    except Exception as e:
        log.exception("db save failed")
        raise HTTPException(500, f"db save failed: {e}")

    return {
        "success": True,
        "invoice_id": invoice_id,
        "batch_id": batch_id,
        "page_no": page_no,
        "merged": merged,
        "parsed": parsed,
        "warnings": warnings,
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
    """Edit invoice fields during review (only non-null fields are updated)."""
    payload = {k: v for k, v in update.dict().items() if v is not None}
    if not payload:
        raise HTTPException(400, "no fields to update")

    sb = get_supabase()
    resp = sb.table("vendor_bills").update(payload).eq("id", invoice_id).execute()
    if not resp.data:
        raise HTTPException(404, "invoice not found")
    return {"success": True, "invoice": resp.data[0]}


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
def _pdf_to_images(pdf_bytes: bytes, scale: float = 2.0) -> list[bytes]:
    """
    Render each page of a PDF to PNG bytes.

    scale=2.0 gives ~144 DPI which is good for OCR + Vision without huge file size.
    Increase to 3.0 if Vision struggles with small text.
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

The Tesseract OCR text below may contain errors. Use it as a hint, but trust the IMAGE as the source of truth:

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
  "payment_type": "credit_card|transfer|cash|cheque|other|null (เครดิตการ์ด/บัตรเครดิต=credit_card, โอน=transfer, เงินสด=cash, เช็ค=cheque)",
  "currency": "THB",
  "items": [
    {{
      "line_no": 1,
      "sku": "string or null",
      "product_name": "string (required if item present)",
      "quantity": number_or_null,
      "unit": "string or null",
      "unit_price": number_or_null,
      "amount": number_or_null
    }}
  ],
  "notes": "string or null (any important note like discount, remark, etc.)"
}}

CRITICAL RULES:
1. Use null (not empty string, not 0) when uncertain. NEVER make up data.
2. Numbers MUST be numeric, never strings.
3. Date format MUST be YYYY-MM-DD. Convert Thai Buddhist year (พ.ศ.) to Gregorian (พ.ศ. - 543 = ค.ศ.). Example: "12/05/2569" → "2026-05-12".
4. 'amount' at top level = grand total (after VAT). 'subtotal' = before VAT.
5. If you can't see an items table, return "items": [].
6. Thai/English mixed text is normal — preserve original language in product_name.
7. Output JSON only. NO markdown fences, NO explanation, NO preamble.
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
        max_tokens=2000,
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
    vendor_name = (parsed.get("vendor_name") or "").strip() or None
    invoice_no = (parsed.get("invoice_no") or "").strip() or None

    existing = None
    if vendor_name and invoice_no:
        try:
            res = (
                sb.table("vendor_bills")
                .select("id, batch_id")
                .ilike("vendor_name", vendor_name)
                .eq("invoice_no", invoice_no)
                .in_("review_status", ["pending", "needs_attention"])
                .limit(1)
                .execute()
            )
            if res.data:
                existing = res.data[0]
        except Exception as e:
            log.warning("dedup lookup failed: %s", e)

    if existing:
        # ---- MERGE PATH: append to existing bill ----
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
        # ---- CREATE PATH: new bill ----
        invoice_id = str(uuid.uuid4())
        batch_id = str(uuid.uuid4())
        page_no = 1
        merged = False

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


def _insert_items(invoice_id: str, items: list[dict[str, Any]], source_page: int):
    """Bulk insert line items for this invoice page."""
    if not items:
        return
    sb = get_supabase()
    rows = []
    for idx, it in enumerate(items, start=1):
        if not it.get("product_name"):
            continue  # skip empty
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
