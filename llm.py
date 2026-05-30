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
import urllib.error
import urllib.request
from typing import Optional

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
}


def model_for(task: str) -> str:
    """Resolve the model string for a task, falling back to the Haiku default."""
    return MODELS.get(task) or _HAIKU


# ── OpenAI factory (moved here from main.py; main re-exports for back-compat) ──
_openai_client: Optional[OpenAI] = None


def get_openai() -> OpenAI:
    """Return a process-wide singleton OpenAI client. Raises if key unset."""
    global _openai_client
    if _openai_client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY must be set")
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


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

    payload: dict = {
        "model": model or model_for(task),
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

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        blocks = data.get("content", [])
        return "\n".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        ).strip()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise LLMError(e.code, detail)
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001 — transport/JSON errors → uniform LLMError
        raise LLMError(502, str(e))
