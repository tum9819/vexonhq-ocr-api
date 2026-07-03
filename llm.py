"""
llm.py — central LLM factory + model registry.

Step 2 of the AI credit-clarity consolidation (2026-05-31). Before this,
OpenAI clients were created in 5 places and Anthropic was called via raw
urllib in 6 places (no factory), with the Haiku model string split between
``claude-haiku-4-5`` and ``claude-haiku-4-5-20251001``.

This module is the ONE place to change a provider key, a model, or the
Anthropic API version. It is intentionally lean (factory + model dict +
one Anthropic transport helper) — no cost-tracking, no provider-abstraction
layer, no auto provider-switching.

Import rules (avoid circular imports): this module imports only the OpenAI
SDK + stdlib. It must never import ``main`` or any route module. ``main`` and
the route modules import FROM here.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from openai import OpenAI

log = logging.getLogger("vexonhq.llm")

# ── Provider keys (read once at import) ───────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Anthropic Messages API version. Centralised so a future bump is a one-liner.
ANTHROPIC_VERSION = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")

# Unified Anthropic Haiku 4.5 model. Was split across the codebase
# (auto_diagnose used the floating ``claude-haiku-4-5`` alias; everything else
# pinned ``claude-haiku-4-5-20251001``). Pinned to the dated snapshot for
# deterministic behaviour. Switch to the alias here to auto-track new 4.5
# snapshots.
_HAIKU = os.environ.get("ANTHROPIC_HAIKU_MODEL", "claude-haiku-4-5-20251001")

# ── Task → model registry ─────────────────────────────────────────────────────
# Change a task's model HERE (one place). Existing per-task env overrides are
# preserved so production env vars keep working.
MODELS: dict[str, str] = {
    # OpenAI — vision (gpt-4o)
    "vision_ocr":      os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"),
    "slip_vision":     os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"),
    "menu_ocr":        os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"),
    "bill_slip_vision": os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"),
    # OpenAI — text (gpt-4o-mini)
    "categorize":      os.environ.get("OPENAI_CATEGORIZE_MODEL", "gpt-4o-mini"),
    "classify":        os.environ.get("PRODUCT_CLASSIFIER_MODEL", "gpt-4o-mini"),
    "search_openai":   os.environ.get("OPENAI_SEARCH_MODEL", "gpt-4o-mini"),
    "line_image_classify": os.environ.get("OPENAI_LINE_IMAGE_MODEL", "gpt-4o-mini"),
    # Anthropic — Haiku 4.5
    "diagnose":        os.environ.get("ANTHROPIC_DIAGNOSE_MODEL", _HAIKU),
    "narrative":       _HAIKU,
    "search_filter":   _HAIKU,
    "menu_reco":       _HAIKU,
    "recipe_suggest":  _HAIKU,
    "recipe_draft":    _HAIKU,
    # Anthropic — vision. EXPERIMENTAL: used only by the OCR comparison harness
    # (tests/ocr_golden/compare.py), NOT wired to any production route. Default
    # Haiku (cheapest); set ANTHROPIC_VISION_MODEL to a Sonnet for a
    # capability-matched comparison against gpt-4o.
    "vision_ocr_claude": os.environ.get("ANTHROPIC_VISION_MODEL", _HAIKU),
}


def model_for(task: str) -> str:
    """Resolve the model string for a task, falling back to the Haiku default."""
    return MODELS.get(task) or _HAIKU


# ── Cost estimation (audit Monitoring remediation, 2026-06-01) ────────────────
# USD per 1M tokens as (input, output). These are PUBLIC LIST PRICES as of
# 2026-06-01 and are an ESTIMATE only — override the whole map with the
# AI_PRICES_JSON env var (JSON: {"model": [in, out], ...}) if rates change.
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o":                     (2.50, 10.00),
    "gpt-4o-mini":                (0.15, 0.60),
    "claude-haiku-4-5-20251001":  (1.00, 5.00),
    "claude-haiku-4-5":           (1.00, 5.00),
}


def _load_prices() -> dict[str, tuple[float, float]]:
    raw = os.environ.get("AI_PRICES_JSON")
    if not raw:
        return dict(_DEFAULT_PRICES)
    try:
        data = json.loads(raw)
        return {k: (float(v[0]), float(v[1])) for k, v in data.items()}
    except Exception:  # noqa: BLE001 — bad override must not break cost calc
        log.warning("AI_PRICES_JSON is invalid JSON; using default prices")
        return dict(_DEFAULT_PRICES)


PRICES = _load_prices()
USD_THB = float(os.environ.get("USD_THB", "36.5"))


def estimate_cost_thb(
    model: str, prompt_tokens: Optional[int], completion_tokens: Optional[int]
) -> float:
    """Rough ฿ estimate for one call. Returns 0.0 for unknown models. ESTIMATE."""
    inp, out = PRICES.get(model, (0.0, 0.0))
    usd = (prompt_tokens or 0) / 1e6 * inp + (completion_tokens or 0) / 1e6 * out
    return round(usd * USD_THB, 4)


# ── OpenAI factory (moved here from main.py; main re-exports for back-compat) ──
_openai_client: Optional[OpenAI] = None


def get_openai() -> OpenAI:
    """Return a process-wide singleton OpenAI client. Raises if key unset."""
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY must be set")
        # Client-level cap: without it the SDK default (~600s) can hang a cron
        # job (e.g. the 06:00 LINE digest) for 10 minutes per call. 180s (not
        # 60s) because GPT-4o Vision on multi-page invoices can legitimately
        # exceed a minute (Antigravity review 2026-07-03).
        _openai_client = OpenAI(api_key=OPENAI_API_KEY, timeout=180.0)
    return _openai_client


# ── AI call telemetry (audit Monitoring remediation, 2026-06-01) ──────────────
# Every OpenAI + Anthropic call writes one best-effort row to public.ai_call_log.
# This is the ONE place that reverses the original "no cost-tracking" decision.
# Logging is best-effort: it swallows ALL of its own errors so instrumentation
# can never break an AI call or a user request (mirrors cron_heartbeat).


def _log_conn():
    """Open a Postgres connection for telemetry. Lazy import of main keeps
    llm.py free of a module-load dependency on main (no circular import);
    falls back to a direct psycopg2 connection in test/standalone contexts."""
    try:
        from main import get_db_conn  # type: ignore
        return get_db_conn()
    except Exception:
        import psycopg2
        return psycopg2.connect(os.environ["DATABASE_URL"])


def _log_ai_call(
    provider: str,
    task: str,
    model: str,
    ok: bool,
    *,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
    status: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    """Best-effort insert into public.ai_call_log. Never raises."""
    try:
        conn = _log_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.ai_call_log
                        (provider, task, model, ok, prompt_tokens,
                         completion_tokens, total_tokens, latency_ms, status, error)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        provider, task, model, ok, prompt_tokens,
                        completion_tokens, total_tokens, latency_ms, status,
                        (error or None) and str(error)[:500],
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — telemetry must never break the AI call
        log.warning("ai_call_log insert failed (provider=%s task=%s)", provider, task)


# ── Anthropic transport ───────────────────────────────────────────────────────
class LLMError(RuntimeError):
    """
    Raised when an Anthropic call fails. Carries the upstream HTTP status
    (or 500 for a missing key / 502 for a transport error) plus a detail
    string, so callers can map it cleanly:
      - route handlers  -> raise HTTPException(e.status_for_http(), e.detail)
      - background tasks -> log + swallow to None
    """

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"Anthropic {status}: {detail}")

    def status_for_http(self) -> int:
        """500 stays 500 (config error); any upstream/transport error -> 502."""
        return 500 if self.status == 500 else 502


def call_anthropic(
    task: str,
    user: str,
    *,
    system: Optional[str] = None,
    max_tokens: int = 1024,
    timeout: int = 30,
    model: Optional[str] = None,
) -> str:
    """
    Central Anthropic Messages API caller (raw HTTP — no SDK dependency).

    ``task`` picks the model from :data:`MODELS` unless ``model`` overrides it.
    Returns the concatenated assistant text (all text blocks joined, stripped).
    Raises :class:`LLMError` on a missing key, an HTTP error, or any transport
    failure — never returns None, never raises HTTPException (callers convert).
    """
    if not ANTHROPIC_API_KEY:
        raise LLMError(500, "ANTHROPIC_API_KEY not configured")

    model_used = model or model_for(task)
    payload: dict = {
        "model": model_used,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user}],
    }
    if system is not None:
        payload["system"] = system
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = data.get("usage") or {}
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        _log_ai_call(
            "anthropic", task, model_used, True,
            prompt_tokens=inp, completion_tokens=out,
            total_tokens=((inp or 0) + (out or 0)) or None,
            latency_ms=latency_ms, status=200,
        )
        blocks = data.get("content", [])
        return "\n".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        ).strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        _log_ai_call("anthropic", task, model_used, False,
                     latency_ms=int((time.monotonic() - t0) * 1000),
                     status=e.code, error=detail)
        raise LLMError(e.code, detail)
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001 — transport/JSON errors → uniform LLMError
        _log_ai_call("anthropic", task, model_used, False,
                     latency_ms=int((time.monotonic() - t0) * 1000),
                     status=502, error=str(e))
        raise LLMError(502, str(e))


def openai_chat(
    task: str,
    *,
    messages: list,
    model: Optional[str] = None,
    **kwargs: Any,
):
    """
    Central OpenAI Chat Completions caller — the single place every OpenAI call
    flows through so usage/latency/errors land in public.ai_call_log.

    ``task`` selects the model from :data:`MODELS` unless ``model`` overrides it
    (call sites pass their existing model explicitly, so the model used never
    changes — ``task`` is only the telemetry label). Extra kwargs
    (``response_format``, ``temperature``, ``max_tokens``, ...) pass straight to
    ``chat.completions.create``. Returns the RAW response object unchanged, so
    callers keep reading ``resp.choices[0].message.content``. Logging is
    best-effort; a logging failure never affects the returned response. On an
    API error it logs ok=False and re-raises the original exception.
    """
    client = get_openai()
    model_used = model or model_for(task)
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model_used, messages=messages, **kwargs
        )
    except Exception as e:  # noqa: BLE001 — log then re-raise unchanged
        _log_ai_call("openai", task, model_used, False,
                     latency_ms=int((time.monotonic() - t0) * 1000),
                     status=getattr(e, "status_code", None), error=str(e))
        raise
    latency_ms = int((time.monotonic() - t0) * 1000)
    u = getattr(resp, "usage", None)
    _log_ai_call(
        "openai", task, model_used, True,
        prompt_tokens=getattr(u, "prompt_tokens", None),
        completion_tokens=getattr(u, "completion_tokens", None),
        total_tokens=getattr(u, "total_tokens", None),
        latency_ms=latency_ms, status=200,
    )
    return resp


def openai_chat_structured(
    task: str,
    *,
    messages: list,
    schema: dict,
    schema_name: str = "result",
    model: Optional[str] = None,
    **kwargs: Any,
):
    """OpenAI Chat Completions with a STRICT JSON Schema (Structured Outputs).

    Sets response_format={"type":"json_schema","json_schema":{...,"strict":True}}
    so the model STRUCTURALLY guarantees the output shape (every field present,
    typed, enums constrained) — killing the omit/wrong-type/invalid-enum class at
    the source. Logs to ai_call_log like openai_chat; returns the RAW response
    (caller reads .choices[0].message.content → already schema-valid JSON).

    EXPERIMENTAL: no production route uses this yet; it exists for the OCR
    comparison harness so a strict-mode promotion can be decided on real numbers."""
    rf = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema, "strict": True},
    }
    return openai_chat(task, messages=messages, model=model, response_format=rf, **kwargs)


def call_anthropic_vision(
    task: str,
    *,
    image_b64: str,
    mime_type: str,
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 4000,
    timeout: int = 60,
    model: Optional[str] = None,
) -> str:
    """
    Anthropic Messages API call WITH an image (vision). Returns the concatenated
    assistant text. Logs to ai_call_log like the other primitives.

    EXPERIMENTAL: there is no production route that calls this. It exists for the
    OCR comparison harness (tests/ocr_golden/compare.py) so an OpenAI→Anthropic
    OCR switch can be evaluated on real accuracy numbers. If a switch is ever
    made, this is the function the production OCR path would adopt.
    """
    if not ANTHROPIC_API_KEY:
        raise LLMError(500, "ANTHROPIC_API_KEY not configured")

    model_used = model or model_for(task)
    payload: dict = {
        "model": model_used,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type or "image/jpeg",
                            "data": image_b64,
                        },
                    },
                ],
            }
        ],
    }
    if system is not None:
        payload["system"] = system
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = data.get("usage") or {}
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        _log_ai_call(
            "anthropic", task, model_used, True,
            prompt_tokens=inp, completion_tokens=out,
            total_tokens=((inp or 0) + (out or 0)) or None,
            latency_ms=latency_ms, status=200,
        )
        blocks = data.get("content", [])
        return "\n".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        ).strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        _log_ai_call("anthropic", task, model_used, False,
                     latency_ms=int((time.monotonic() - t0) * 1000),
                     status=e.code, error=detail)
        raise LLMError(e.code, detail)
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001 — transport/JSON errors → uniform LLMError
        _log_ai_call("anthropic", task, model_used, False,
                     latency_ms=int((time.monotonic() - t0) * 1000),
                     status=502, error=str(e))
        raise LLMError(502, str(e))
