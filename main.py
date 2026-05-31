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
import asyncio
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
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from llm import get_openai  # OpenAI client factory lives in llm.py (Step 2 AI consolidation)
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
from cron_heartbeat import router as cron_health_router
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
from recipe_routes import router as recipe_router, ingredient_router, _auto_sync_ingredient_prices
from menu_public_routes import router as menu_public_router
from tax_routes import router as tax_router
from rules_routes import router as rules_router
from slip_routes import router as slip_router
from loan_routes import router as loan_router
from store_context_routes import router as store_context_router
from auth_routes import router as auth_router, verify_token
from alerts_webhook_routes import router as alerts_router
from discord_routes import router as discord_router
from do_snapshot_routes import router as do_snapshot_router
from auto_diagnose import try_diagnose
from ai_exec_routes import router as ai_exec_router
# === Phase 2: psycopg connection for POS bulk imports ===
# (Phase 1 uses supabase client for OCR flows — this is for high-volume
#  executemany() inserts that need raw PG driver)

import psutil
import psycopg2
import psycopg2.extensions
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from line_bot_routes import _scheduler as _line_scheduler

# ────────────────────────────────────────────────────────────
# Slow-query watcher (P2.2)
# ────────────────────────────────────────────────────────────
# Every cursor opened through get_db_conn() inherits this class. When
# a query exceeds SLOW_QUERY_WARN_SEC, log a WARNING; when it exceeds
# SLOW_QUERY_CRITICAL_SEC, log ERROR (Coolify log dashboard then
# surfaces it visually).
#
# Implemented at the cursor layer so every caller benefits without
# code changes — no need to thread a context-managed timer through 50+
# call sites.

SLOW_QUERY_WARN_SEC = float(os.environ.get("SLOW_QUERY_WARN_SEC", "3.0"))
SLOW_QUERY_CRITICAL_SEC = float(os.environ.get("SLOW_QUERY_CRITICAL_SEC", "10.0"))

_slow_query_log = logging.getLogger("vexon.slow_query")


class SlowQueryWatchingCursor(psycopg2.extensions.cursor):
    """psycopg2 cursor subclass that times every execute()."""

    def execute(self, query, vars=None):
        t0 = time.perf_counter()
        try:
            return super().execute(query, vars)
        finally:
            elapsed = time.perf_counter() - t0
            if elapsed >= SLOW_QUERY_CRITICAL_SEC:
                _slow_query_log.error(
                    "CRITICAL slow query (%.2fs): %s",
                    elapsed,
                    (query.decode() if isinstance(query, (bytes, bytearray)) else str(query))[:300],
                )
            elif elapsed >= SLOW_QUERY_WARN_SEC:
                _slow_query_log.warning(
                    "slow query (%.2fs): %s",
                    elapsed,
                    (query.decode() if isinstance(query, (bytes, bytearray)) else str(query))[:300],
                )


def get_db_conn():
    """Open a fresh psycopg v3 connection to Supabase Postgres.

    Returns a connection whose default cursor factory logs warnings
    for slow queries (≥3s) and errors for critical ones (≥10s).
    """
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=SlowQueryWatchingCursor,
    )

# ============================================================
# Config
# ============================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "uploads")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")

# ────────────────────────────────────────────────────────────
# Structured logging (P2.1)
# ────────────────────────────────────────────────────────────
# In production (LOG_FORMAT=json), emit each log record as a single
# JSON line so Coolify / log aggregators can index by level, logger,
# timestamp, etc. without regex parsing. In dev, keep the familiar
# pipe-separated text format that's easy to read in a terminal.
#
# Activated by env var: LOG_FORMAT=json (Coolify) or unset (dev).

class _JsonLogFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    _STANDARD = {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        import json as _json
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Pick up any custom fields the caller passed via extra={...}
        for k, v in record.__dict__.items():
            if k not in self._STANDARD and not k.startswith("_"):
                try:
                    _json.dumps(v)
                    payload[k] = v
                except (TypeError, ValueError):
                    payload[k] = repr(v)
        return _json.dumps(payload, ensure_ascii=False)


_log_format = os.environ.get("LOG_FORMAT", "text").lower()
_log_handler = logging.StreamHandler()
if _log_format == "json":
    _log_handler.setFormatter(_JsonLogFormatter())
else:
    _log_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))

# Replace any existing handlers (Coolify's uvicorn parent may have
# pre-installed one) — we want a single deterministic output stream.
_root = logging.getLogger()
for _h in list(_root.handlers):
    _root.removeHandler(_h)
_root.addHandler(_log_handler)
_root.setLevel(logging.INFO)

log = logging.getLogger("vexonhq-ocr")


# ============================================================
# Clients (lazy init — won't crash on import if env missing)
# ============================================================
_supabase_client: Optional[Client] = None


def get_supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase_client


# ============================================================
# Sentry — Error tracking (P0.4, Session 42)
# Init before app creation so FastApiIntegration captures all routes.
# Set SENTRY_DSN env var in Coolify to enable. No-ops if DSN is unset.
# ============================================================
_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    try:
        sentry_sdk.init(
            dsn=_sentry_dsn,
            environment=os.environ.get("ENVIRONMENT", "production"),
            traces_sample_rate=0.1,
            send_default_pii=False,
            integrations=[
                FastApiIntegration(),
                StarletteIntegration(),
            ],
        )
        log.info("Sentry initialised (DSN configured)")
    except Exception as exc:
        log.warning("Sentry init failed — running without error tracking: %s", exc)
else:
    log.info("Sentry disabled — SENTRY_DSN not set")

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
app.include_router(cron_health_router)
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
app.include_router(menu_public_router)
app.include_router(tax_router)
app.include_router(alerts_router)
app.include_router(discord_router)
app.include_router(do_snapshot_router)
app.include_router(ai_exec_router)
app.include_router(rules_router)
app.include_router(slip_router)
app.include_router(loan_router)
app.include_router(store_context_router)
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

# Security audit 2026-05-31 (finding #8): removed "/ap/due-reminder" and
# "/stock/alert" from PUBLIC_PATHS. They were unauthenticated yet returned
# AP/supplier financial rows and could be spammed to push to TUM's LINE. The
# in-process APScheduler fires these digests via internal functions
# (_scheduled_ap_due_reminder / _scheduled_daily_stock_digest), NOT via HTTP, so
# requiring JWT here breaks nothing — only anonymous internet callers are blocked.
PUBLIC_PATHS = {"/", "/health", "/health/deep", "/cron/health", "/auth/login", "/auth/logout", "/docs", "/openapi.json", "/redoc", "/alerts/uptime-webhook", "/alerts/test-telegram", "/alerts/discord-interaction", "/alerts/discord-restart-test", "/line/webhook", "/snapshots/status", "/snapshots/auto-rotate", "/menu/public", "/ai/exec"}

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

        # Stash the JWT subject on request.state for audit trail
        # (created_by / updated_by / reviewed_by) without re-parsing.
        # After Supabase SSO migration, sub is a UUID (e.g. "a1b2c3d4-...").
        # Falls back to None if the token had no `sub` claim.
        request.state.username = payload.get("sub")

        # Attach user to Sentry events for this request
        if _sentry_dsn and request.state.username:
            sentry_sdk.set_user({"id": request.state.username})

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
        # Custom domain (Session 32 marastation.com migration)
        "https://app.marastation.com",
        "https://marastation.com",
        "https://www.marastation.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Global exception handler — ensures CORS headers on 500 responses
# ============================================================
# Session 27 incident: the c2417f65 CORS error AND the /recipes/ai-
# suggest "Failed to fetch" turned out to share a root cause —
# Starlette's middleware stack does NOT wrap responses for
# exceptions raised inside route functions that aren't an
# HTTPException subclass. The browser then sees a bare 500 with no
# CORS headers and reports it as a CORS failure rather than the real
# error.
#
# This catch-all handler intercepts every uncaught exception, logs
# it with full traceback (so the failure is visible in Coolify
# stdout), and returns a JSONResponse that goes through the normal
# middleware stack — CORSMiddleware adds Access-Control-Allow-Origin
# to the response, so the browser sees a proper 500 + readable
# detail field.
#
# HTTPException + RequestValidationError keep their existing FastAPI
# default handlers (they already get CORS headers correctly because
# FastAPI builds the response inside the middleware stack).
@app.exception_handler(Exception)
async def _all_exceptions_handler(request: StarletteRequest, exc: Exception):
    log.exception(
        "unhandled exception on %s %s",
        request.method,
        request.url.path,
    )
    # Do NOT echo str(exc) to the client: psycopg2/connection errors embed the
    # DB host:port, and some library exceptions leak internal config. The full
    # traceback is already logged above (log.exception) for debugging in Coolify.
    return StarletteJSONResponse(
        status_code=500,
        content={
            "detail": f"internal server error: {type(exc).__name__}",
        },
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
def health_deep(background_tasks: BackgroundTasks):
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

    # 4) VPS resource usage (psutil — zero I/O, ~1ms)
    try:
        ram = psutil.virtual_memory()
        checks["resources"] = {
            "ram_pct": round(ram.percent, 1),
            "cpu_pct": round(psutil.cpu_percent(interval=0.1), 1),
            "ram_warn": ram.percent >= 70,
        }
    except Exception:
        checks["resources"] = {"ram_pct": None, "cpu_pct": None, "ram_warn": False}

    # 5) APScheduler liveness (confirms cron jobs are still registered)
    try:
        checks["scheduler"] = {
            "running": _line_scheduler.running,
            "job_count": len(_line_scheduler.get_jobs()),
        }
    except Exception:
        checks["scheduler"] = {"running": False, "job_count": 0}

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

    # AI auto-diagnosis (P1.4 MVP, Session 24) — fires only when DB or
    # Supabase check actually failed. Runs as BackgroundTask so it
    # does NOT delay the 503 response Uptime Robot is waiting on.
    # Rate-limited inside try_diagnose (10 min per error_type).
    # Silently skipped if ANTHROPIC_API_KEY / DISCORD_OPS_WEBHOOK_URL
    # env vars are not set, so the endpoint is safe to ship without
    # them and TUM can set them in Coolify when ready.
    if not db_ok:
        # Pick a stable key so the rate limiter dedups on the same
        # failure mode; different failure modes still get their own
        # diagnosis.
        if not checks["postgres"]["ok"]:
            error_type = "postgres_failed"
        elif not checks["supabase"]["ok"]:
            error_type = "supabase_failed"
        else:
            error_type = "unknown_failure"
        background_tasks.add_task(try_diagnose, error_type, body)

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
    # Tesseract is CPU-blocking; run off the event loop so one OCR doesn't freeze
    # the whole server (health checks would time out → UptimeRobot DOWN).
    text = await asyncio.to_thread(_run_tesseract, contents)
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
    # Canonical SKU + AI confidence (Session 25/26 hybrid classifier).
    # `canonical_sku` references public.products.sku; `canonical_confidence`
    # is the model's self-reported score in [0, 1]. Both optional so older
    # clients that don't know about classification still work.
    canonical_sku:        Optional[str] = None
    canonical_confidence: Optional[float] = None


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

    # Heavy work (PDF render + GPT Vision OCR + Supabase save) is CPU/IO-blocking and
    # would freeze the async event loop for the entire upload — health checks then time
    # out → UptimeRobot DOWN (same failure mode as the POS-import incident, fixed the
    # same way). Run the whole pipeline in a worker thread so the loop stays responsive,
    # especially for multi-page Makro PDFs.
    return await asyncio.to_thread(
        _process_upload, contents, file.filename, file.content_type or "",
    )


def _process_upload(contents: bytes, filename: str, content_type: str) -> dict[str, Any]:
    """Heavy synchronous upload pipeline (PDF→images, OCR, GPT Vision, save to Supabase).
    Runs in a thread via asyncio.to_thread so it never blocks the FastAPI event loop."""
    is_pdf = (content_type == "application/pdf") or filename.lower().endswith(".pdf")

    if is_pdf:
        # Convert PDF → list of PNG images (1 per page)
        try:
            page_images = _pdf_to_images(contents)
        except Exception as e:
            log.exception("pdf conversion failed")
            raise HTTPException(400, f"pdf conversion failed: {e}")

        if not page_images:
            raise HTTPException(400, "pdf has no readable pages")

        log.info("processing PDF '%s' with %d page(s)", filename, len(page_images))

        # Process each page through full pipeline.
        # multi-page merge in _save_invoice() handles merging same-invoice pages.
        page_filename_base = os.path.splitext(filename)[0]
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
    result = _process_single_image(contents, filename, content_type or "image/jpeg")
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
        "preview_url": _sign_uploads_url(file_url),
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
            # Group by an aggressively-normalized product name so OCR
            # variants of the same product collapse into one suggestion:
            #   1. TRIM leading/trailing whitespace
            #   2. Collapse multiple internal spaces (`a  b` -> `a b`)
            #   3. Strip trailing punctuation + whitespace
            #      (`เบียร์ช้าง 620 มล.` and `เบียร์ช้าง 620 มล` group
            #      together; verified case from TUM's catalogue)
            # The SELECT keeps `mode() OVER product_name` so the
            # displayed name uses the most-common canonical spelling
            # rather than the normalized form (which has no period).
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
                GROUP BY regexp_replace(
                            regexp_replace(TRIM(ii.product_name), '\\s+', ' ', 'g'),
                            '[\\s\\.\\,\\;\\:]+$', ''
                         )
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


@app.get("/products")
def products_list(category: Optional[str] = None, include_inactive: bool = False):
    """
    Return the canonical SKU master list (Session 25/26).

    Front-end loads this once on /invoices/<id> mount to populate the
    "หมวด" dropdown in the items table. The list is small (~21 rows) so
    no pagination — caller filters client-side by category if needed.

    Optional query params:
      - category=<str>      filter to one category (e.g. 'beer')
      - include_inactive=1  also return rows where is_active = false
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT sku, name_th, category, default_unit, notes,
                       is_active, sort_order
                FROM public.products
                WHERE (%s OR is_active = true)
                  AND (%s::text IS NULL OR category = %s)
                ORDER BY sort_order, category, sku
            """
            cur.execute(sql, (include_inactive, category, category))
            rows = []
            for r in cur.fetchall():
                rows.append({
                    "sku":           r[0],
                    "name_th":       r[1],
                    "category":      r[2],
                    "default_unit":  r[3],
                    "notes":         r[4],
                    "is_active":     bool(r[5]),
                    "sort_order":    int(r[6] or 100),
                })
        return {"success": True, "products": rows}
    finally:
        conn.close()


@app.get("/invoice/items/monthly-by-sku")
def invoice_items_monthly_by_sku(
    month: Optional[str] = None,
    branch_code: Optional[str] = None,
):
    """
    Aggregate confirmed-bill invoice_items for one month by canonical SKU.

    Powers the `/invoices/monthly-by-sku` frontend page (TOMORROW.md
    item G — "this month I ordered N cases of Y product"). Returns:

    - one row per (category, canonical_sku, name_th) tuple
    - total quantity (sum of quantity column)
    - total amount (sum of amount column)
    - bill count (distinct vendor_bills per product)
    - latest unit_price seen (handy for spotting price changes)
    - latest bill_date (so the UI can sort by recency)

    Plus a top-level summary with overall totals + month range. Items
    with NULL canonical_sku are grouped under a synthetic "(ยังไม่ได้
    จัดหมวด)" bucket so they're visible — that hints to TUM that he
    should re-run the auto-classify bulk for those bills.

    Filters:
      - month=YYYY-MM   (default: current month)
      - branch_code=... (default: all branches)
    """
    if not month:
        # Default to current month (Asia/Bangkok local — Coolify TZ=Asia/Bangkok)
        month = date.today().strftime("%Y-%m")

    try:
        year, mon = month.split("-")
        year_i = int(year)
        mon_i = int(mon)
        if not (2020 <= year_i <= 2099) or not (1 <= mon_i <= 12):
            raise ValueError
    except ValueError:
        raise HTTPException(400, f"Invalid month {month!r}, expected YYYY-MM")

    # Compute month range end (first day of next month)
    if mon_i == 12:
        next_year, next_mon = year_i + 1, 1
    else:
        next_year, next_mon = year_i, mon_i + 1
    range_start = f"{year_i:04d}-{mon_i:02d}-01"
    range_end = f"{next_year:04d}-{next_mon:02d}-01"

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Build query — bind branch_code only if provided
            params: list = [range_start, range_end]
            branch_filter = ""
            if branch_code:
                branch_filter = " AND vb.branch_code = %s"
                params.append(branch_code)

            cur.execute(
                f"""
                SELECT
                    COALESCE(p.category, 'unclassified')              AS category,
                    ii.canonical_sku                                  AS sku,
                    COALESCE(p.name_th, '(ยังไม่ได้จัดหมวด)')          AS name_th,
                    COALESCE(p.default_unit, MAX(ii.unit))            AS unit,
                    COUNT(DISTINCT vb.id)::int                        AS bills,
                    SUM(ii.quantity)::numeric(12,2)                   AS total_qty,
                    SUM(ii.amount)::numeric(12,2)                     AS total_amount,
                    (SELECT ii2.unit_price
                       FROM public.invoice_items ii2
                       JOIN public.vendor_bills vb2 ON vb2.id = ii2.vendor_bill_id
                      WHERE ii2.canonical_sku IS NOT DISTINCT FROM ii.canonical_sku
                        AND vb2.review_status = 'confirmed'
                        AND vb2.bill_date IS NOT NULL
                      ORDER BY vb2.bill_date DESC NULLS LAST, ii2.id DESC
                      LIMIT 1)                                        AS latest_unit_price,
                    MAX(vb.bill_date)                                 AS latest_bill_date,
                    COALESCE(p.sort_order, 999)                       AS sort_order
                FROM public.invoice_items ii
                JOIN public.vendor_bills vb ON vb.id = ii.vendor_bill_id
                LEFT JOIN public.products p ON p.sku = ii.canonical_sku
                WHERE vb.review_status = 'confirmed'
                  AND vb.bill_date >= %s::date
                  AND vb.bill_date <  %s::date
                  {branch_filter}
                GROUP BY p.category, ii.canonical_sku, p.name_th,
                         p.default_unit, p.sort_order
                ORDER BY sort_order, name_th
                """,
                params,
            )
            rows = []
            for r in cur.fetchall():
                rows.append({
                    "category":          r[0],
                    "sku":               r[1],
                    "name_th":           r[2],
                    "unit":              r[3],
                    "bills":             int(r[4] or 0),
                    "total_qty":         float(r[5]) if r[5] is not None else 0.0,
                    "total_amount":      float(r[6]) if r[6] is not None else 0.0,
                    "latest_unit_price": float(r[7]) if r[7] is not None else None,
                    "latest_bill_date":  str(r[8]) if r[8] else None,
                })

            # Summary
            total_amount = sum(r["total_amount"] for r in rows)
            total_qty = sum(r["total_qty"] for r in rows if r["total_qty"])
            total_bills = 0
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT vb.id)::int
                FROM public.vendor_bills vb
                JOIN public.invoice_items ii ON ii.vendor_bill_id = vb.id
                WHERE vb.review_status = 'confirmed'
                  AND vb.bill_date >= %s::date
                  AND vb.bill_date <  %s::date
                  {branch_filter}
                """,
                params,
            )
            row = cur.fetchone()
            total_bills = int(row[0] or 0) if row else 0
    finally:
        conn.close()

    return {
        "success":   True,
        "month":     month,
        "range":     {"from": range_start, "to": range_end},
        "items":     rows,
        "summary": {
            "total_amount":  round(total_amount, 2),
            "total_qty":     round(total_qty, 2),
            "total_bills":   total_bills,
            "skus":          len(rows),
            "unclassified":  next((r["total_amount"] for r in rows if r["sku"] is None), 0.0),
        },
    }


@app.post("/invoice/{invoice_id}/auto-classify")
def invoice_auto_classify(invoice_id: str, force: bool = False):
    """
    Backfill canonical_sku for an invoice's line items using AI.

    Called from the "AI ช่วยจัดหมวด" button on /invoices/<id>. The
    classifier reads each row's `product_name`, asks GPT-4o-mini to pick
    a matching SKU from the master list, and writes the result + a
    confidence score back to invoice_items.

    Default behaviour: only rows where canonical_sku IS NULL are touched
    (idempotent — re-running on a bill leaves TUM's manual selections
    intact). Pass `?force=true` to re-classify every row regardless of
    existing value.

    Response includes the per-row classification so the frontend can
    refresh the table without a second round-trip.
    """
    _validate_uuid_param("invoice_id", invoice_id)
    sb = get_supabase()
    bill = sb.table("vendor_bills").select("id").eq("id", invoice_id).execute()
    if not bill.data:
        raise HTTPException(404, "invoice not found")

    # Count "would-be-skipped" rows separately so the response can still
    # report them. The shared helper itself doesn't track skips because
    # the OCR-upload path doesn't care.
    skipped = 0
    if not force:
        conn_count = get_db_conn()
        try:
            with conn_count.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)::int
                    FROM public.invoice_items
                    WHERE vendor_bill_id = %s
                      AND canonical_sku IS NOT NULL
                    """,
                    (invoice_id,),
                )
                skipped = int(cur.fetchone()[0] or 0)
        finally:
            conn_count.close()

    result = _auto_classify_invoice_items(invoice_id, only_unclassified=not force)
    return {
        "success":    True,
        "classified": result["classified"],
        "skipped":    skipped,
        "results":    result["results"],
    }


@app.post("/invoice/items/auto-classify-bulk")
def invoice_items_auto_classify_bulk(
    only_confirmed: bool = True,
    limit_bills: int = 200,
    force_other: bool = False,
):
    """
    One-shot backfill of canonical_sku across many bills at once.

    Use case (Session 25): TUM already confirmed ~64 bills in Session 22
    before the canonical SKU layer existed. This endpoint lets him run
    the classifier across every one of those bills in a single call so
    the catalogue is consistent — no need to open each invoice and
    press "AI ช่วยจัดหมวด" individually.

    Default mode (`only_confirmed=true`) restricts the scope to bills
    in review_status='confirmed'. Pass `only_confirmed=false` to also
    classify draft/pending bills (rare — usually you'd let auto-classify-
    on-upload handle those). `limit_bills` is a safety cap — even at
    cheap GPT-4o-mini pricing, we never want a single call to fan out
    over the entire table by accident.

    Idempotent: rows that already have `canonical_sku` set are skipped.
    No `force` flag here on purpose — manual confirmations must not be
    clobbered by a bulk run.

    `force_other=true` (Session 27 / item Q): widen the scope to also
    re-process rows where canonical_sku='other'. We never auto-pick
    'other' over a real SKU — it's a "model gave up" signal — so
    revisiting these rows after new SKUs (food categories) get seeded
    is safe and idempotent. Rows where TUM manually pinned a real
    SKU are still untouched.

    Returns counts + per-bill summary so the operator can see what
    actually changed.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            # Find the set of bills that still have unclassified items.
            # GROUP BY vendor_bill_id so we touch each bill exactly once
            # — `_auto_classify_invoice_items` already does the per-row
            # WHERE filter inside.
            unfit_pred = (
                "ii.canonical_sku IS NULL OR ii.canonical_sku = 'other'"
                if force_other
                else "ii.canonical_sku IS NULL"
            )
            sql = f"""
                SELECT vb.id, vb.vendor_name, vb.bill_date,
                       COUNT(*) FILTER (WHERE {unfit_pred}) AS unclassified
                FROM public.vendor_bills vb
                JOIN public.invoice_items ii ON ii.vendor_bill_id = vb.id
                WHERE {unfit_pred}
            """
            params: list = []
            if only_confirmed:
                sql += " AND vb.review_status = 'confirmed'"
            sql += f"""
                GROUP BY vb.id, vb.vendor_name, vb.bill_date
                HAVING COUNT(*) FILTER (WHERE {unfit_pred}) > 0
                ORDER BY vb.bill_date DESC NULLS LAST
                LIMIT %s
            """
            params.append(limit_bills)
            cur.execute(sql, params)
            bills_to_classify = cur.fetchall()
    finally:
        conn.close()

    total_bills_touched = 0
    total_items_classified = 0
    summary: list[dict] = []
    errors: list[dict] = []

    for bill_id, vendor_name, bill_date, unclassified_count in bills_to_classify:
        bill_id_str = str(bill_id)
        try:
            result = _auto_classify_invoice_items(
                bill_id_str,
                only_unclassified=True,
                also_other=force_other,
            )
        except Exception as exc:
            log.exception("bulk classify failed for bill %s", bill_id_str)
            errors.append({
                "invoice_id":  bill_id_str,
                "vendor_name": vendor_name,
                "error":       str(exc)[:200],
            })
            continue
        total_bills_touched += 1
        total_items_classified += result["classified"]
        summary.append({
            "invoice_id":  bill_id_str,
            "vendor_name": vendor_name,
            "bill_date":   str(bill_date) if bill_date else None,
            "classified":  result["classified"],
        })

    return {
        "success":             True,
        "total_bills_found":   len(bills_to_classify),
        "total_bills_touched": total_bills_touched,
        "total_items_classified": total_items_classified,
        "errors":              errors,
        "summary":             summary,
    }


@app.get("/invoice/{invoice_id}")
def invoice_detail(invoice_id: str):
    """Full invoice detail: header + items + pages + warnings."""
    _validate_uuid_param("invoice_id", invoice_id)
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


def _current_username(request: Request) -> Optional[str]:
    """
    Pull the JWT-verified username off `request.state`.

    Populated by the JWTAuthMiddleware after a successful `verify_token`
    call. Returns None if the request didn't go through the middleware
    (shouldn't happen for protected routes) or if the token had no `sub`
    claim. Endpoints use this for audit-trail fields (`updated_by` /
    `reviewed_by` / `created_by`) so we record the *actual* signed-in
    user instead of trusting a client-supplied field.
    """
    return getattr(request.state, "username", None)


def _validate_uuid_param(name: str, value: str) -> None:
    """
    Raise HTTPException(400) if `value` isn't a syntactically-valid UUID.

    Background — Session 27 incident:
      TUM pasted a truncated invoice ID from his LINE chat
      (e.g. "c2417f65...") into the browser URL bar. The literal "..."
      survived to the backend, which passed it straight to Supabase's
      `eq("id", value)` filter. Postgres rejected it with
      `InvalidTextRepresentation`, which the supabase-py client raised
      as an uncaught exception. FastAPI's default 500 handler runs
      INSIDE the middleware stack, but Starlette has a known quirk
      where an exception raised inside the route function CAN bypass
      the outer CORSMiddleware's header injection — leaving the
      browser to report it as a CORS error ("No 'Access-Control-Allow-
      Origin' header") rather than the real 500.

    Catching the bad input early as a 400 HTTPException makes
    FastAPI's normal exception handler run, which is wrapped by
    CORSMiddleware correctly — so the browser sees a clean 400 with
    CORS headers and the user gets a useful error message.

    Use as the first line of every endpoint whose path captures a
    UUID — /invoice/{id}*, /slip/{id}*, /rules/*/{id}, etc.
    """
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            400,
            f"invalid {name} (expected UUID): {value!r}",
        )


@app.patch("/invoice/{invoice_id}")
def invoice_edit(invoice_id: str, update: InvoiceUpdate, request: Request):
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
    _validate_uuid_param("invoice_id", invoice_id)
    update_dict = update.dict()
    items_payload = update_dict.pop("items", None)
    header_payload = {k: v for k, v in update_dict.items() if v is not None}

    if not header_payload and items_payload is None:
        raise HTTPException(400, "no fields to update")

    # Audit: who is making this change? Stamp updated_by + updated_at on
    # the vendor_bills row even when the caller only sent `items` (the
    # bill content changed, that's an audit-worthy event).
    username = _current_username(request)
    if username:
        header_payload["updated_by"] = username
    header_payload["updated_at"] = datetime.utcnow().isoformat()

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
                            it.get("canonical_sku"),
                            it.get("canonical_confidence"),
                        ))
                    cur.executemany(
                        """
                        INSERT INTO public.invoice_items
                            (vendor_bill_id, line_no, sku, product_name,
                             quantity, unit, unit_price, amount,
                             canonical_sku, canonical_confidence)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
def invoice_confirm(
    invoice_id: str,
    request: Request,
    body: Optional[ConfirmRequest] = None,
):
    _validate_uuid_param("invoice_id", invoice_id)
    # Prefer JWT-derived username (trustworthy) over client-supplied
    # `reviewed_by` (legacy + tamperable). Falls back to client value
    # only when middleware didn't populate state.username (shouldn't
    # happen for protected routes).
    reviewer = _current_username(request) or (body.reviewed_by if body else None)
    sb = get_supabase()
    now_iso = datetime.utcnow().isoformat()
    resp = (
        sb.table("vendor_bills")
        .update({
            "review_status": "confirmed",
            "reviewed_by":   reviewer,
            "reviewed_at":   now_iso,
            "confirmed_at":  now_iso,
            "updated_by":    reviewer,
            "updated_at":    now_iso,
        })
        .eq("id", invoice_id)
        .execute()
    )
    if not resp.data:
        raise HTTPException(404, "invoice not found")

    # Invoice ↔ Statement dedup workflow (TOMORROW.md item D).
    # When a vendor bill is confirmed, look for a matching outgoing row
    # in bank_statement_entries and re-tag it as `vendor_payment` so the
    # P&L Phase 1 exclusion list stops counting it twice. The vendor_bill
    # itself becomes the canonical expense source going forward.
    #
    # Failure here is non-fatal — the confirm itself already succeeded.
    # If the match logic crashes (network, schema mismatch, etc.) we log
    # and return the unchanged bill payload. TUM can re-run via the
    # explicit POST /invoice/{id}/match-statement below.
    match_summary = None
    try:
        match_summary = _match_invoice_against_statement(invoice_id, reviewer)
    except Exception:
        log.exception("invoice ↔ statement dedup failed (non-fatal) for %s", invoice_id)

    sync_summary = None
    try:
        sync_summary = _auto_sync_ingredient_prices()
    except Exception:
        log.exception("auto-sync ingredient prices failed (non-fatal) for %s", invoice_id)

    return {
        "success": True,
        "invoice": resp.data[0],
        "statement_match": match_summary,
        "ingredient_sync": sync_summary,
    }


def _match_invoice_against_statement(
    invoice_id: str,
    actor: Optional[str],
) -> Optional[dict]:
    """
    Try to dedup a freshly-confirmed `vendor_bill` against an outgoing
    row in `bank_statement_entries`.

    Match criteria (loosest first → tightest):
      - same amount within ±1 baht
      - bill_date within ±7 days of statement txn_date
      - statement row currently source_type IN ('bank_statement',
                                                'vendor_purchase')
      - statement row not already matched to another invoice

    Behaviour:
      - 0 candidates → return None (no double-count to fix)
      - 1 candidate  → re-tag statement row as `vendor_payment` and
                       point matched_invoice_id at this bill
      - >1 candidates → mark them `match_status='needs_review'` and
                        DON'T flip source_type — TUM picks the right
                        one manually via `/bills/payment` UI.
    """
    sb = get_supabase()
    bill = (
        sb.table("vendor_bills")
        .select("id, bill_date, amount, vendor_name")
        .eq("id", invoice_id)
        .limit(1)
        .execute()
    )
    if not bill.data:
        return None
    bill_row = bill.data[0]
    amount = bill_row.get("amount")
    bill_date = bill_row.get("bill_date")
    if amount is None or not bill_date:
        # Not enough info to match against the statement.
        return {"status": "skipped", "reason": "amount or bill_date missing"}

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, txn_date, debit, description, source_type, match_status
                FROM public.bank_statement_entries
                WHERE matched_invoice_id IS NULL
                  AND debit > 0
                  AND ABS(debit - %s) <= 1.00
                  AND ABS(txn_date - %s::date) <= 7
                  AND source_type IN ('bank_statement', 'vendor_purchase')
                ORDER BY ABS(txn_date - %s::date), ABS(debit - %s)
                """,
                (amount, bill_date, bill_date, amount),
            )
            candidates = cur.fetchall()

            if not candidates:
                return {"status": "no_match", "candidates": 0}

            if len(candidates) == 1:
                stmt_id, txn_date, debit, description, _src, _ms = candidates[0]
                cur.execute(
                    """
                    UPDATE public.bank_statement_entries
                    SET source_type        = 'vendor_payment',
                        matched_invoice_id = %s,
                        match_status       = 'auto',
                        notes              = COALESCE(notes, '')
                                             || CASE WHEN COALESCE(notes,'') = '' THEN '' ELSE ' | ' END
                                             || 'matched by ' || COALESCE(%s, 'system')
                                             || ' at ' || NOW()::text
                    WHERE id = %s
                    """,
                    (invoice_id, actor, str(stmt_id)),
                )
                conn.commit()
                return {
                    "status":          "matched",
                    "statement_id":    str(stmt_id),
                    "txn_date":        str(txn_date),
                    "amount":          float(debit),
                    "description":     description,
                }

            # Multiple candidates — flag for review, don't auto-flip.
            ids = [str(r[0]) for r in candidates]
            cur.execute(
                """
                UPDATE public.bank_statement_entries
                SET match_status = 'needs_review'
                WHERE id = ANY(%s)
                """,
                (ids,),
            )
            conn.commit()
            return {
                "status":     "ambiguous",
                "candidates": len(candidates),
                "statement_ids": ids,
            }
    finally:
        conn.close()


@app.post("/invoice/{invoice_id}/match-statement")
def invoice_match_statement(invoice_id: str, request: Request):
    """
    Manually re-trigger the invoice ↔ statement dedup matcher for an
    already-confirmed bill. Useful when TUM later imports the statement
    PDF (so the matching row didn't exist at confirm time) or when the
    initial auto-match was ambiguous and he wants to retry after editing.
    """
    _validate_uuid_param("invoice_id", invoice_id)
    actor = _current_username(request)
    sb = get_supabase()
    bill = sb.table("vendor_bills").select("id").eq("id", invoice_id).execute()
    if not bill.data:
        raise HTTPException(404, "invoice not found")
    result = _match_invoice_against_statement(invoice_id, actor)
    return {"success": True, "statement_match": result}


@app.post("/invoice/{invoice_id}/reject")
def invoice_reject(invoice_id: str, body: RejectRequest, request: Request):
    _validate_uuid_param("invoice_id", invoice_id)
    reviewer = _current_username(request) or body.reviewed_by
    sb = get_supabase()
    now_iso = datetime.utcnow().isoformat()
    resp = (
        sb.table("vendor_bills")
        .update({
            "review_status":  "rejected",
            "reviewed_by":    reviewer,
            "reviewed_at":    now_iso,
            "reject_reason":  body.reject_reason,
            "updated_by":     reviewer,
            "updated_at":     now_iso,
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


def _sign_uploads_url(url: Optional[str], expires_in: int = 86400) -> Optional[str]:
    """Turn a stored public 'uploads' URL into a fresh signed URL.

    Security hardening 2026-05-31 (GAP 2): the `uploads` bucket (OCR'd
    statements/slips/invoices) is private, so the old
    `.../object/public/uploads/<path>` URLs no longer resolve. Read endpoints
    wrap stored URLs through this so the authed dashboard still renders the
    image (the signature in the URL authorizes the GET — `<img>` tags can't
    send a JWT header). Safe to wrap any value: None / non-uploads URLs pass
    through unchanged, and signing failure falls back to the original URL.
    """
    if not url or "/object/public/" not in url:
        return url
    try:
        after = url.split("/object/public/", 1)[1]   # "<bucket>/<path>"
        bucket, _, path = after.partition("/")
        if bucket != SUPABASE_STORAGE_BUCKET or not path:
            return url
        sb = get_supabase()
        res = sb.storage.from_(bucket).create_signed_url(path, expires_in)
        return (res.get("signedURL") or res.get("signedUrl") or url) if isinstance(res, dict) else url
    except Exception:
        log.warning("sign uploads url failed", exc_info=True)
        return url


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

    # Whitelist payment_type to the DB check constraint chk_vb_payment_type
    # (NULL or credit_card/transfer/cash/cheque/other). The OCR prompt asks for one
    # of those, but the model sometimes returns a free-form value (e.g. a Thai credit
    # term like "เงินเชื่อ" on a SINGHA invoice) which 23514-rejects the INSERT and
    # the whole upload fails to save. Map known synonyms, drop anything else to None.
    # Applied here (before merge/create) so BOTH the INSERT and the backfill-loop are
    # covered. payment_type is user-editable in review; all other parsed fields are
    # still stored verbatim in ocr_json below.
    _pt = str(parsed.get("payment_type") or "").strip().lower()
    parsed["payment_type"] = {
        "credit_card": "credit_card", "creditcard": "credit_card", "credit card": "credit_card",
        "credit": "credit_card", "บัตรเครดิต": "credit_card",
        "transfer": "transfer", "bank_transfer": "transfer", "banktransfer": "transfer",
        "wire": "transfer", "โอน": "transfer", "เงินโอน": "transfer", "โอนเงิน": "transfer",
        "cash": "cash", "เงินสด": "cash",
        "cheque": "cheque", "check": "cheque", "เช็ค": "cheque",
        "other": "other",
    }.get(_pt) or None

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
        # Auto-classify on upload (Session 25 — TUM asked for this so the
        # dropdown is pre-filled by the time he opens /invoices/<id>). Any
        # failure here is non-fatal: items are already saved; canonical_sku
        # just stays NULL and TUM can press the manual button later.
        try:
            _auto_classify_invoice_items(invoice_id, only_unclassified=True)
        except Exception:
            log.exception("auto-classify after _insert_items failed (non-fatal)")


def _auto_classify_invoice_items(
    invoice_id: str,
    *,
    only_unclassified: bool = True,
    also_other: bool = False,
) -> dict:
    """
    Run the AI classifier on every line item of an invoice and write the
    result + confidence back to invoice_items.

    Shared helper used by:
      - `_insert_items()`              → auto-classify on OCR upload
      - `POST /invoice/{id}/auto-classify` → manual "AI ช่วยจัดหมวด" button
      - `POST /invoice/items/auto-classify-bulk` (per-bill loop)

    Idempotent in the default mode — rows with a non-null canonical_sku
    are skipped so TUM's manual confirmations aren't overwritten. Set
    `only_unclassified=False` to force a re-classification.

    `also_other=True` (with `only_unclassified=True`) widens the scope
    to include rows where canonical_sku='other' as if they were NULL.
    Use case (Session 27 / item Q): after seeding food SKUs we want to
    revisit items that previously fell through to 'other' without
    overriding rows TUM manually pinned to a specific food SKU.

    Returns a dict with `classified`, `skipped`, and `results` so the
    caller can decide what to surface to the UI.
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if only_unclassified:
                if also_other:
                    cur.execute(
                        """
                        SELECT id, product_name
                        FROM public.invoice_items
                        WHERE vendor_bill_id = %s
                          AND (canonical_sku IS NULL OR canonical_sku = 'other')
                        ORDER BY line_no, id
                        """,
                        (invoice_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, product_name
                        FROM public.invoice_items
                        WHERE vendor_bill_id = %s
                          AND canonical_sku IS NULL
                        ORDER BY line_no, id
                        """,
                        (invoice_id,),
                    )
            else:
                cur.execute(
                    """
                    SELECT id, product_name
                    FROM public.invoice_items
                    WHERE vendor_bill_id = %s
                    ORDER BY line_no, id
                    """,
                    (invoice_id,),
                )
            rows = cur.fetchall()
            if not rows:
                return {"classified": 0, "skipped": 0, "results": []}

            from product_classifier import classify_items_batch
            classifications = classify_items_batch(
                conn,
                [(r[1] or "") for r in rows],
            )

            updates = []
            results = []
            for (item_id, name), guess in zip(rows, classifications):
                sku = guess.get("sku") or "other"
                conf = guess.get("confidence") or 0.0
                updates.append((sku, conf, str(item_id)))
                results.append({
                    "item_id":              str(item_id),
                    "product_name":         name,
                    "canonical_sku":        sku,
                    "canonical_confidence": conf,
                })
            cur.executemany(
                """
                UPDATE public.invoice_items
                SET canonical_sku        = %s,
                    canonical_confidence = %s
                WHERE id = %s
                """,
                updates,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "classified": len(results),
        "skipped":    0,
        "results":    results,
    }


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
