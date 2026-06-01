"""
VEXONHQ — Product Classifier (Session 25/26)
=============================================
Hybrid AI-suggest classifier that maps free-text `invoice_items.product_name`
values to one of the canonical SKUs in `public.products`.

Design (matches the agreed Approach C from the TOMORROW.md item Q draft):
  1. AI suggests a SKU + confidence in [0, 1].
  2. Frontend pre-fills the dropdown with that SKU; user can override.
  3. On save, the confirmed SKU is written back to invoice_items.

Implementation notes:
  - Uses the existing OpenAI client (`get_openai()` in `main.py`). No new
    dependency. GPT-4o-mini is ~$0.15/1M input + $0.60/1M output, far cheaper
    than adding Anthropic just for this. Anthropic still wins on P1.4 auto-
    heal (per stability roadmap), so this stays independent.
  - Master list is loaded from Postgres once per request (small, ~21 rows).
    `products.notes` carries OCR/alias hints that the prompt embeds.
  - Batch interface — frontend can classify a whole bill's items in one
    round-trip instead of N parallel calls.
  - Strict JSON output via `response_format={"type": "json_object"}`. We
    validate the response shape; anything malformed falls back to `('other',
    0.0)` with a logged warning so a parse failure can never block a save.
  - Idempotent — caller (PATCH /invoice/{id}) decides when to write the
    result. This module only computes.

Public API:
    load_products(conn)                 -> list[dict]
    classify_items_batch(conn, names)   -> list[{sku, confidence}]
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger("product_classifier")

# OpenAI model — keep cheap. gpt-4o-mini is more than good enough for picking
# one of ~21 buckets from a short string.
CLASSIFIER_MODEL = os.environ.get("PRODUCT_CLASSIFIER_MODEL", "gpt-4o-mini")

# Cap how many items we classify in one batch — keeps the prompt under the
# context window and bounds the cost per call. A single bill rarely exceeds
# 30 items; the OCR pipeline itself caps at 50.
MAX_BATCH = 50


def load_products(conn) -> list[dict]:
    """
    Read the active master product list from Postgres. The caller passes
    in an already-open psycopg2 connection so this can be reused inside
    request handlers without spinning up a fresh pool slot.

    Returns rows ordered for stable prompt construction (category first,
    then `sort_order`, then SKU as final tiebreak).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT sku, name_th, category, default_unit, notes
            FROM public.products
            WHERE is_active = true
            ORDER BY category, sort_order, sku
            """
        )
        rows = []
        for r in cur.fetchall():
            rows.append({
                "sku":          r[0],
                "name_th":      r[1],
                "category":     r[2],
                "default_unit": r[3],
                "notes":        r[4],
            })
    return rows


def _build_prompt(products: list[dict], names: list[str]) -> tuple[str, str]:
    """
    Build the system + user prompt pair for OpenAI. System describes the
    task and the SKU list; user gives the line items to classify.

    Returns (system_message, user_message).
    """
    catalogue_lines = []
    for p in products:
        notes = f" — {p['notes']}" if p.get("notes") else ""
        catalogue_lines.append(f"- {p['sku']}: {p['name_th']}{notes}")
    catalogue = "\n".join(catalogue_lines)

    system = (
        "You classify line items from Thai restaurant supplier invoices into one "
        "of a fixed list of canonical SKUs. Match by product family, brand, "
        "size, and flavour. Pay attention to OCR variants (extra spaces, "
        "missing punctuation, swapped Thai characters). Reply ONLY with JSON.\n\n"
        f"Available SKUs:\n{catalogue}\n\n"
        'Use sku "other" when nothing matches confidently — DO NOT invent a '
        "new sku. Use the most specific match available; prefer beer brand "
        "+ flavour over generic 'beer'.\n\n"
        "Confidence scale:\n"
        '  1.00 — exact brand + size match\n'
        '  0.80 — brand match, size/variant ambiguous\n'
        '  0.50 — same category but different brand or unknown variant\n'
        '  0.20 — guessed from a fragment\n'
        '  0.00 — fallback to "other"'
    )

    numbered = "\n".join(f"{i + 1}. {name!r}" for i, name in enumerate(names))
    user = (
        "Classify each numbered item. Respond with JSON of the shape:\n"
        '  {"results": [{"index": 1, "sku": "...", "confidence": 0.95}, ...]}\n'
        "Include exactly one result per input, in the same order. "
        "Items to classify:\n" + numbered
    )

    return system, user


def classify_items_batch(
    conn,
    names: list[str],
    *,
    openai_client=None,
) -> list[dict]:
    """
    Classify a batch of free-text `product_name` strings.

    Returns one dict per input in the same order:
        [{"sku": str, "confidence": float}, ...]

    Any item that the model can't place gets `("other", 0.0)` as a safe
    fallback so the caller (PATCH /invoice/{id}) can always write a row.
    Empty / whitespace-only names short-circuit to `("other", 0.0)` without
    spending tokens.

    Errors propagate from the OpenAI client to the caller — the caller
    decides whether a 500 should bubble up to the user or whether to skip
    classification silently and write `canonical_sku = NULL`.
    """
    if not names:
        return []
    if len(names) > MAX_BATCH:
        # Recurse in chunks. Keeps the prompt size predictable.
        result: list[dict] = []
        for i in range(0, len(names), MAX_BATCH):
            result.extend(classify_items_batch(
                conn,
                names[i:i + MAX_BATCH],
                openai_client=openai_client,
            ))
        return result

    # Mark empty inputs upfront so the model doesn't waste tokens on them.
    sanitized: list[str] = []
    placeholder_idx: set[int] = set()
    for i, n in enumerate(names):
        clean = (n or "").strip()
        if not clean:
            placeholder_idx.add(i)
            sanitized.append("")
        else:
            sanitized.append(clean)

    real_indices = [i for i, n in enumerate(sanitized) if i not in placeholder_idx]
    real_names   = [sanitized[i] for i in real_indices]

    if not real_names:
        return [{"sku": "other", "confidence": 0.0} for _ in names]

    products = load_products(conn)
    system, user = _build_prompt(products, real_names)

    # An injected client (unit tests mock it) bypasses telemetry; production
    # (no injected client) routes through llm.openai_chat so the call lands in
    # ai_call_log. Model unchanged either way.
    _create_kwargs = dict(
        model=CLASSIFIER_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0.0,
    )
    if openai_client is not None:
        resp = openai_client.chat.completions.create(**_create_kwargs)
    else:
        from llm import openai_chat
        resp = openai_chat("classify", **_create_kwargs)

    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("classifier: model returned non-JSON: %r", raw[:200])
        parsed = {}

    # Collect valid SKUs so we can reject hallucinations.
    valid_skus = {p["sku"] for p in products}

    by_index: dict[int, dict] = {}
    for entry in parsed.get("results", []) or []:
        try:
            idx = int(entry.get("index"))
            sku = str(entry.get("sku") or "").strip()
            conf = float(entry.get("confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        if sku not in valid_skus:
            log.warning(
                "classifier: model invented sku %r for index %s — falling back to 'other'",
                sku, idx,
            )
            sku = "other"
            conf = 0.0
        # Clamp confidence into [0, 1] so a wild number can't slip into the DB
        # CHECK constraint and 500 the whole save.
        conf = max(0.0, min(1.0, conf))
        by_index[idx] = {"sku": sku, "confidence": round(conf, 2)}

    # Rebuild the result in the input order, including the empty placeholders.
    out: list[dict] = []
    for input_pos, original_idx in enumerate(range(len(names))):
        if original_idx in placeholder_idx:
            out.append({"sku": "other", "confidence": 0.0})
            continue
        real_position = real_indices.index(original_idx)
        # Model's `index` is 1-based per the prompt.
        guess = by_index.get(real_position + 1)
        if guess is None:
            log.warning(
                "classifier: model returned no result for input %d (%r) — falling back",
                real_position + 1, real_names[real_position],
            )
            out.append({"sku": "other", "confidence": 0.0})
        else:
            out.append(guess)

    return out


def classify_single(conn, name: str, *, openai_client=None) -> dict:
    """Convenience wrapper around `classify_items_batch` for one name."""
    result = classify_items_batch(conn, [name], openai_client=openai_client)
    return result[0] if result else {"sku": "other", "confidence": 0.0}
