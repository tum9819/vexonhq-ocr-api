"""
VEXONHQ Phase 3A-1 — AI Auto-categorization Routes + Worker
============================================================
Two-tier categorization:
  Tier 1 — vendor_category_rules (rule-based, free, instant)
  Tier 2 — OpenAI GPT-4o-mini (LLM, ~$0.0001/bill)

Endpoints (5):
    POST /ai/categorize/bill/{bill_id}    — manual trigger for one bill
    POST /ai/categorize/batch             — process all pending (cron target)
    PATCH /ai/categorize/log/{log_id}     — accept/reject/override decision
    GET  /ai/categorize/pending           — list bills awaiting decision
    GET  /ai/categorize/stats             — cost + accuracy per month
    GET  /ai/categorize/health            — smoke test
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

try:
    from main import get_db_conn  # type: ignore
except ImportError:
    import psycopg2
    def get_db_conn():
        return psycopg2.connect(os.environ["DATABASE_URL"])

try:
    from auth_routes import verify_token  # type: ignore
except ImportError:
    verify_token = None  # type: ignore


logger = logging.getLogger("phase3a_ai_categorize")
router = APIRouter(tags=["phase3a-ai-categorize"])

from llm import MODELS  # task->model registry (Step 2 consolidation)
LLM_MODEL = MODELS["categorize"]
# Pricing as of 2026-05 (per 1M tokens)
LLM_PRICE_INPUT_PER_1M = 0.15
LLM_PRICE_OUTPUT_PER_1M = 0.60
DEFAULT_AUTOAPPLY_MIN_CONF = 0.90

# Vendor-bill rules share the same table as short cashflow keyword rules. Broad
# wholesalers can sell mixed goods, so bill-level auto-apply must be stricter
# than cashflow text categorization.
AMBIGUOUS_BILL_VENDOR_TOKENS = (
    "แอ็กซ์ตร้า",
    "makro",
    "แม็คโคร",
    "big c",
    "lotus",
    "tops",
    "b.b.",
    "บี.บี.",
    "บี บี",
    "ซุปเปอร์สโตร์",
    "wealimex",
    "wealmex",
    "cp all",
    "7-eleven",
    "ขายส่ง",
)


# ============================================================
# Helpers
# ============================================================

def _serialize_row(row: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _rows_to_dicts(cur) -> list[dict]:
    if cur.description is None:
        return []
    cols = [d[0] for d in cur.description]
    return [_serialize_row(dict(zip(cols, r))) for r in cur.fetchall()]


def _calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost for one OpenAI gpt-4o-mini call."""
    return round(
        (prompt_tokens * LLM_PRICE_INPUT_PER_1M / 1_000_000)
        + (completion_tokens * LLM_PRICE_OUTPUT_PER_1M / 1_000_000),
        6,
    )


def _autoapply_min_confidence() -> float:
    raw = os.getenv("AI_AUTOAPPLY_MIN_CONF", str(DEFAULT_AUTOAPPLY_MIN_CONF))
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid AI_AUTOAPPLY_MIN_CONF=%r; using %.2f", raw, DEFAULT_AUTOAPPLY_MIN_CONF)
        return DEFAULT_AUTOAPPLY_MIN_CONF


def _should_autoapply(result: dict) -> bool:
    """Rules are deterministic; LLM suggestions need confidence >= env threshold."""
    if result.get("tier") == "rule":
        return True
    confidence = result.get("confidence")
    try:
        return float(confidence) >= _autoapply_min_confidence()
    except (TypeError, ValueError):
        return False


def _require_admin_actor(request: Request) -> str:
    if getattr(request.state, "role", None) == "admin":
        return str(getattr(request.state, "username", None) or "admin")
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    if verify_token is None:
        raise HTTPException(500, "Auth verifier unavailable")
    payload = verify_token(auth_header[7:])
    if not payload:
        raise HTTPException(401, "Token expired or invalid")
    if payload.get("_role") != "admin":
        raise HTTPException(403, "Admin access required")
    return str(payload.get("sub") or "admin")


# ============================================================
# Pydantic models
# ============================================================

class UserActionBody(BaseModel):
    action: str                      # accept | reject | override
    override_category: Optional[str] = None


# ============================================================
# Core categorization logic
# ============================================================

def _try_rule_match(cur, vendor_name: str, *, bump_hit: bool = True) -> Optional[dict]:
    """Tier 1: Try vendor_category_rules. Returns dict with category + rule_pattern,
    or None if no match."""
    if not vendor_name:
        return None
    cur.execute(
        """SELECT pattern, category_code
           FROM public.vendor_category_rules
           WHERE %s ILIKE pattern
           ORDER BY priority, length(pattern) DESC
           LIMIT 1""",
        (vendor_name,),
    )
    row = cur.fetchone()
    if not row:
        return None
    pattern, category_code = row

    # Bump hit_count + last_hit_at only when the rule is actually used.
    if bump_hit:
        cur.execute(
            """UPDATE public.vendor_category_rules
               SET hit_count = hit_count + 1, last_hit_at = now()
               WHERE pattern = %s""",
            (pattern,),
        )
    return {
        "tier": "rule",
        "category_code": category_code,
        "rule_pattern": pattern,
        "confidence": 0.99,
        "reason": f"matched rule: {pattern}",
    }


def _is_ambiguous_bill_vendor(vendor_name: str) -> bool:
    text = (vendor_name or "").casefold()
    return any(token in text for token in AMBIGUOUS_BILL_VENDOR_TOKENS)


def _adjust_bill_rule_result(vendor_name: str, result: Optional[dict]) -> Optional[dict]:
    """Bill-specific guardrails for shared vendor_category_rules matches."""
    if result is None:
        return None

    text = (vendor_name or "").casefold()
    if result.get("category_code") == "raw_beverage" and (
        "singha" in text or "beer" in text or "สิงห์" in text or "เบียร์" in text
    ):
        adjusted = dict(result)
        adjusted["category_code"] = "beverage"
        adjusted["reason"] = f"{result.get('reason', '').strip()} [bill-level beverage vendor]"
        return adjusted

    return result


def _build_llm_prompt(bill: dict, items: list[dict], categories: list[dict]) -> str:
    """Build a focused prompt for gpt-4o-mini."""
    cat_lines = []
    for c in categories:
        parent_hint = f" (under {c['parent_code']})" if c.get('parent_code') else ""
        cat_lines.append(f"- {c['code']}: {c['name_th']}{parent_hint}")
    cat_block = "\n".join(cat_lines[:80])  # cap to keep prompt small

    items_block = "\n".join(
        f"  - {it.get('product_name','?')} (qty {it.get('quantity',0)}, ฿{it.get('amount',0):.2f})"
        for it in items[:15]   # show top 15 items max
    ) if items else "  (no line items)"

    return f"""You categorize Thai restaurant expense bills. Pick ONE category code from the list.

Available categories (code: thai_name):
{cat_block}

Bill to categorize:
- Vendor: {bill.get('vendor_name', '(unknown)')}
- Tax ID: {bill.get('merchant_tax_id', '-')}
- Date: {bill.get('bill_date', '-')}
- Amount: ฿{float(bill.get('amount', 0)):.2f}
- Items:
{items_block}

Respond with strict JSON only:
{{"category_code": "<code from the list>", "confidence": <0.0-1.0>, "reason": "<short Thai reason>"}}

Examples:
- ร้านโชห่วยซื้อผัก/เนื้อ → raw_meat or raw_veggies
- บิลค่าไฟจากการไฟฟ้า → utility_elec
- บิลค่าน้ำมัน → raw_oil_gas or fuel (transport)
- Subscription Vercel/Supabase → saas_tool
- Makro → raw_meat or raw_veggies or raw_seasoning (depending on items)"""


def _call_llm(prompt: str) -> dict:
    """Call OpenAI gpt-4o-mini. Returns dict with category_code, confidence, reason, tokens."""
    from llm import openai_chat
    try:
        # Routed through llm.openai_chat for ai_call_log telemetry. Model unchanged.
        response = openai_chat(
            "categorize",
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are an accountant categorizing Thai restaurant invoices. Return strict JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=200,
        )
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        raise HTTPException(502, f"LLM call failed: {e}")

    choice = response.choices[0].message.content or "{}"
    usage = response.usage
    try:
        parsed = json.loads(choice)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON: %s", choice)
        parsed = {"category_code": "misc", "confidence": 0.3, "reason": "LLM JSON parse failed, fell back to misc"}

    # Audit B7-M6 fix (2026-05-28): an LLM can return confidence as a string
    # ("high"), a number > 1, or omit it — float() of "high" raises ValueError
    # and the >1 value violates the ai_categorization_log CHECK constraint, both
    # surfacing as 500. Mirror product_classifier.py:219-221: try/except defaulting
    # to 0.5, then clamp to [0, 1]. Session-34 class (AI -> typed column).
    try:
        conf = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    return {
        "tier": "llm",
        "category_code": parsed.get("category_code", "misc"),
        "confidence": conf,
        "reason": parsed.get("reason", ""),
        "model_name": LLM_MODEL,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
    }


def _fetch_bill_with_items(cur, bill_id: str) -> tuple[dict, list[dict]]:
    """Returns (bill_dict, items_list). Raises 404 if not found."""
    cur.execute(
        """SELECT id, vendor_name, merchant_tax_id, bill_date, amount,
                  invoice_no, review_status, category_code
           FROM public.vendor_bills
           WHERE id = %s""",
        (bill_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Bill not found: {bill_id}")
    cols = [d[0] for d in cur.description]
    bill = _serialize_row(dict(zip(cols, row)))

    cur.execute(
        """SELECT product_name, quantity, amount
           FROM public.invoice_items
           WHERE vendor_bill_id = %s
           ORDER BY id LIMIT 30""",
        (bill_id,),
    )
    items_cols = [d[0] for d in cur.description]
    items = [_serialize_row(dict(zip(items_cols, r))) for r in cur.fetchall()]
    return bill, items


def _fetch_categories(cur) -> list[dict]:
    """Active categories with name + parent for LLM context."""
    cur.execute(
        """SELECT code, name_th, parent_code, direction
           FROM public.expense_categories
           WHERE is_active = true AND direction IN ('expense','both')
           ORDER BY parent_code NULLS FIRST, sort_order"""
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _validate_category_exists(cur, category_code: str) -> bool:
    cur.execute("SELECT 1 FROM public.expense_categories WHERE code = %s AND is_active = true",
                (category_code,))
    return cur.fetchone() is not None


def _categorize_one(conn, bill_id: str, allow_llm: bool = True, dry_run: bool = False) -> dict:
    """Run the full 2-tier flow for one bill. Returns log entry dict."""
    with conn.cursor() as cur:
        bill, items = _fetch_bill_with_items(cur, bill_id)
        if bill["category_code"]:
            raise HTTPException(409, f"Bill already has category: {bill['category_code']}")
        if bill["review_status"] != "confirmed":
            raise HTTPException(400, f"Bill review_status must be 'confirmed', got: {bill['review_status']}")

        vendor_name = bill.get("vendor_name") or ""
        requires_human_review = _is_ambiguous_bill_vendor(vendor_name)

        # Tier 1 — rule
        result = _try_rule_match(cur, vendor_name, bump_hit=not requires_human_review)
        if requires_human_review and result is not None:
            logger.info(
                "Skipping bill rule auto-match for ambiguous vendor %r (pattern=%r, category=%r)",
                vendor_name,
                result.get("rule_pattern"),
                result.get("category_code"),
            )
            result = None
        else:
            result = _adjust_bill_rule_result(vendor_name, result)
        cost_usd = 0.0
        prompt_tokens = 0
        completion_tokens = 0
        model_name = None

        # Tier 2 — LLM
        if result is None:
            if not allow_llm:
                raise HTTPException(424, "No rule match and LLM disabled")
            categories = _fetch_categories(cur)
            prompt = _build_llm_prompt(bill, items, categories)
            result = _call_llm(prompt)
            prompt_tokens = result["prompt_tokens"]
            completion_tokens = result["completion_tokens"]
            model_name = result["model_name"]
            cost_usd = _calculate_cost(prompt_tokens, completion_tokens)

            # Validate the LLM's category exists; fallback if not
            if not _validate_category_exists(cur, result["category_code"]):
                logger.warning("LLM returned non-existent category %s, falling back to 'misc'",
                               result["category_code"])
                result["category_code"] = "misc"
                result["confidence"] = 0.3
                result["reason"] = (result.get("reason", "") + " [fallback: invalid code]").strip()

        if dry_run:
            conn.rollback()
            return {
                "log_id": None,
                "bill_id": str(bill_id),
                "tier": result["tier"],
                "category_code": result["category_code"],
                "confidence": result["confidence"],
                "cost_usd": cost_usd,
                "reason": result.get("reason"),
                "dry_run": True,
            }

        before_category = bill.get("category_code")
        if requires_human_review:
            result["reason"] = (
                (result.get("reason") or "").strip()
                + " [pending human review: broad/mixed vendor]"
            ).strip()
        should_apply = _should_autoapply(result) and not requires_human_review

        if should_apply:
            cur.execute(
                "UPDATE public.vendor_bills SET category_code = %s WHERE id = %s",
                (result["category_code"], bill_id),
            )

        # Insert log entry
        cur.execute(
            """INSERT INTO public.ai_categorization_log
                 (bill_id, tier_used, suggested_category, confidence,
                  rule_pattern, model_name, prompt_tokens, completion_tokens,
                  cost_usd, reason, applied, before_category, applied_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, applied_at""",
            (bill_id, result["tier"], result["category_code"], result["confidence"],
             result.get("rule_pattern"), model_name, prompt_tokens, completion_tokens,
             cost_usd, result.get("reason"), should_apply, before_category,
             "rule" if result["tier"] == "rule" else "ai"),
        )
        log_id, applied_at = cur.fetchone()
        conn.commit()

        return {
            "log_id": str(log_id),
            "bill_id": str(bill_id),
            "tier": result["tier"],
            "category_code": result["category_code"],
            "confidence": result["confidence"],
            "cost_usd": cost_usd,
            "reason": result.get("reason"),
            "applied": should_apply,
        }


def _log_target_status(before_category: Optional[str]) -> str:
    return "pending" if before_category is None else "confirmed"


def _apply_target_category(cur, source: str, bill_id: Optional[str], cashflow_entry_id: Optional[str],
                           category_code: Optional[str]) -> None:
    if source == "cashflow":
        if not cashflow_entry_id:
            raise HTTPException(400, "Log entry has no cashflow_entry_id")
        cur.execute(
            """UPDATE public.pos_cashflow_entries
               SET category_code = %s,
                   ai_cat_status = %s
               WHERE id = %s""",
            (category_code, _log_target_status(category_code), cashflow_entry_id),
        )
        return

    if not bill_id:
        raise HTTPException(400, "Log entry has no bill_id")
    cur.execute(
        "UPDATE public.vendor_bills SET category_code = %s WHERE id = %s",
        (category_code, bill_id),
    )


def _fetch_log_target(cur, log_id: str) -> tuple:
    cur.execute(
        """SELECT bill_id, cashflow_entry_id, source, suggested_category,
                  before_category, COALESCE(applied, true), undone_at
           FROM public.ai_categorization_log
           WHERE id = %s""",
        (log_id,),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Log entry not found")
    return row


def _apply_log_user_action(cur, log_id: str, action: str,
                           override_category: Optional[str] = None,
                           *, actor: Optional[str] = None) -> None:
    bill_id, cashflow_entry_id, source, suggested, before_category, applied, undone_at = _fetch_log_target(cur, log_id)
    if undone_at is not None:
        raise HTTPException(409, "Log entry already undone")

    if action == "accept":
        target_category = suggested
        _apply_target_category(cur, source, bill_id, cashflow_entry_id, target_category)
        cur.execute(
            """UPDATE public.ai_categorization_log
               SET user_action = 'accept',
                   user_action_at = now(),
                   applied = true,
                   applied_at = now(),
                   applied_by = 'human'
               WHERE id = %s""",
            (log_id,),
        )
        return

    if action == "override":
        if not override_category:
            raise HTTPException(400, "override_category required when action=override")
        _apply_target_category(cur, source, bill_id, cashflow_entry_id, override_category)
        cur.execute(
            """UPDATE public.ai_categorization_log
               SET user_action = 'override',
                   user_action_at = now(),
                   override_category = %s,
                   applied = true,
                   applied_at = now(),
                   applied_by = 'human'
               WHERE id = %s""",
            (override_category, log_id),
        )
        return

    if action == "reject":
        if applied:
            _apply_target_category(cur, source, bill_id, cashflow_entry_id, before_category)
            cur.execute(
                """UPDATE public.ai_categorization_log
                   SET user_action = 'reject',
                       user_action_at = now(),
                       applied = false,
                       undone_at = now(),
                       undone_by = %s,
                       undo_reason = 'user_reject'
                   WHERE id = %s""",
                (actor, log_id),
            )
        else:
            cur.execute(
                """UPDATE public.ai_categorization_log
                   SET user_action = 'reject',
                       user_action_at = now(),
                       applied = false
                   WHERE id = %s""",
                (log_id,),
            )
        return

    raise HTTPException(400, "action must be accept | reject | override")


# ============================================================
# Endpoints
# ============================================================

@router.post("/ai/categorize/bill/{bill_id}")
def categorize_one_bill(bill_id: str, allow_llm: bool = Query(True)):
    """Manual trigger — categorize a single bill."""
    try:
        UUID(bill_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid bill_id UUID")
    conn = get_db_conn()
    try:
        return _categorize_one(conn, bill_id, allow_llm=allow_llm)
    finally:
        conn.close()


@router.post("/ai/categorize/batch")
def categorize_batch(
    limit: int = Query(50, ge=1, le=200),
    allow_llm: bool = Query(True),
    dry_run: bool = Query(False),
):
    """Process all pending bills (called by cron hourly).
    Returns: {processed, by_tier, total_cost_usd, errors}"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT v.id
                   FROM public.v_bills_needing_category v
                   WHERE NOT EXISTS (
                       SELECT 1
                       FROM public.ai_categorization_log l
                       WHERE l.bill_id = v.id
                         AND l.user_action IS NULL
                         AND COALESCE(l.applied, true) = false
                   )
                   ORDER BY v.created_at ASC LIMIT %s""",
                (limit,),
            )
            bill_ids = [str(row[0]) for row in cur.fetchall()]

        processed = []
        errors = []
        for bill_id in bill_ids:
            try:
                result = _categorize_one(conn, bill_id, allow_llm=allow_llm, dry_run=dry_run)
                processed.append(result)
            except HTTPException as e:
                # Audit B7-C2: rollback the shared connection so a per-bill failure
                # (e.g. transient LLM 502 after a rule-table UPDATE) doesn't leave the
                # transaction in 'aborted' state and poison every subsequent iteration.
                conn.rollback()
                errors.append({"bill_id": bill_id, "status": e.status_code, "error": e.detail})
            except Exception as e:
                conn.rollback()
                errors.append({"bill_id": bill_id, "status": 500, "error": str(e)})

        by_tier = {"rule": 0, "llm": 0}
        total_cost = 0.0
        for p in processed:
            by_tier[p["tier"]] = by_tier.get(p["tier"], 0) + 1
            total_cost += p.get("cost_usd", 0)

        res = {
            "processed": len(processed),
            "total_pending_before": len(bill_ids),
            "by_tier": by_tier,
            "total_cost_usd": round(total_cost, 4),
            "errors": errors,
        }
        if dry_run:
            res["dry_run"] = True
        return res
    finally:
        conn.close()


@router.patch("/ai/categorize/log/{log_id}")
def user_action(log_id: str, body: UserActionBody, request: Request):
    """Accept/reject/override the AI suggestion. If override, update vendor_bills too."""
    try:
        UUID(log_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid log_id UUID")
    if body.action not in ("accept", "reject", "override"):
        raise HTTPException(400, "action must be accept | reject | override")
    if body.action == "override" and not body.override_category:
        raise HTTPException(400, "override_category required when action=override")

    actor = _require_admin_actor(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            if body.action == "override":
                if not _validate_category_exists(cur, body.override_category):
                    raise HTTPException(404, f"Category not found: {body.override_category}")
            _apply_log_user_action(cur, log_id, body.action, body.override_category, actor=actor)
            conn.commit()
        return {"log_id": log_id, "action": body.action, "applied": True}
    finally:
        conn.close()


@router.post("/ai/categorize/{log_id}/undo")
def undo_categorize_log(log_id: str, request: Request):
    """Admin undo for an auto-applied categorization log."""
    try:
        UUID(log_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid log_id UUID")

    actor = _require_admin_actor(request)
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            bill_id, cashflow_entry_id, source, _suggested, before_category, applied, undone_at = _fetch_log_target(cur, log_id)
            if undone_at is not None:
                raise HTTPException(409, "Log entry already undone")
            if not applied:
                raise HTTPException(409, "Log entry was not applied")
            _apply_target_category(cur, source, bill_id, cashflow_entry_id, before_category)
            cur.execute(
                """UPDATE public.ai_categorization_log
                   SET applied = false,
                       undone_at = now(),
                       undone_by = %s,
                       undo_reason = 'admin_undo'
                   WHERE id = %s""",
                (actor, log_id),
            )
            conn.commit()
        return {"log_id": log_id, "undone": True, "restored_category": before_category}
    finally:
        conn.close()


@router.get("/ai/categorize/pending")
def list_pending(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """Bills awaiting AI decision (confirmed + missing category)."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, vendor_name, amount, bill_date, invoice_no, item_count
                   FROM public.v_bills_needing_category
                   LIMIT %s OFFSET %s""",
                (limit, offset),
            )
            rows = _rows_to_dicts(cur)
            cur.execute("SELECT count(*) FROM public.v_bills_needing_category")
            total = cur.fetchone()[0]
        return {"rows": rows, "total": int(total), "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.get("/ai/categorize/log/list")
def list_log_entries(
    user_action: Optional[str] = Query(None, description="pending | auto | accept | reject | override | all"),
    tier: Optional[str] = Query(None, description="rule | llm | manual | fallback"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List AI categorization log entries with bill context.
    Default: pending review (user_action IS NULL)."""
    where: list[str] = []
    params: list[Any] = []

    if user_action == "pending" or user_action is None:
        where.append("l.user_action IS NULL AND COALESCE(l.applied, true) = false")
    elif user_action == "auto":
        where.append("l.user_action IS NULL AND COALESCE(l.applied, true) = true AND l.undone_at IS NULL")
    elif user_action in ("accept", "reject", "override"):
        where.append("l.user_action = %s"); params.append(user_action)
    elif user_action == "all":
        pass
    else:
        raise HTTPException(400, "user_action must be pending | auto | accept | reject | override | all")

    if tier:
        if tier not in ("rule", "llm", "manual", "fallback"):
            raise HTTPException(400, "tier must be rule | llm | manual | fallback")
        where.append("l.tier_used = %s"); params.append(tier)

    sql_where = (" WHERE " + " AND ".join(where)) if where else ""

    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT l.id, l.bill_id, l.tier_used, l.suggested_category,
                           c.name_th AS category_name, c.color AS category_color,
                           l.confidence, l.rule_pattern, l.model_name,
                           l.prompt_tokens, l.completion_tokens, l.cost_usd, l.reason,
                           l.applied_at, l.user_action, l.user_action_at, l.override_category,
                           COALESCE(l.applied, true) AS applied,
                           l.before_category, l.applied_by, l.undone_at,
                           l.cashflow_entry_id, l.source,
                           COALESCE(vb.vendor_name, pce.description) AS vendor_name,
                           COALESCE(vb.amount, pce.amount) AS amount,
                           COALESCE(vb.bill_date, pce.txn_date) AS bill_date,
                           vb.invoice_no
                    FROM public.ai_categorization_log l
                    LEFT JOIN public.vendor_bills vb ON vb.id = l.bill_id
                    LEFT JOIN public.pos_cashflow_entries pce ON pce.id = l.cashflow_entry_id
                    LEFT JOIN public.expense_categories c ON c.code = l.suggested_category
                    {sql_where}
                    ORDER BY l.applied_at DESC
                    LIMIT %s OFFSET %s""",
                params + [limit, offset],
            )
            rows = _rows_to_dicts(cur)
            cur.execute(
                f"SELECT count(*) FROM public.ai_categorization_log l{sql_where}",
                params,
            )
            total = cur.fetchone()[0]
        return {"rows": rows, "total": int(total), "limit": limit, "offset": offset}
    finally:
        conn.close()


@router.get("/ai/categorize/stats")
def categorize_stats():
    """Per-month cost + accuracy stats."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM public.v_ai_categorization_stats LIMIT 12")
            rows = _rows_to_dicts(cur)
        return {"rows": rows}
    finally:
        conn.close()


@router.post("/ai/categorize/pending/apply")
def apply_pending_categorizations(
    request: Request,
    min_conf: Optional[float] = Query(None, ge=0, description="Confidence threshold; defaults to AI_AUTOAPPLY_MIN_CONF"),
    dry_run: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
):
    """Admin-controlled one-shot apply for already logged pending suggestions."""
    actor = _require_admin_actor(request)
    threshold = _autoapply_min_confidence() if min_conf is None else min_conf
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT l.id, l.bill_id, l.cashflow_entry_id, l.source,
                          l.suggested_category, l.confidence,
                          COALESCE(vb.vendor_name, pce.description) AS label,
                          COALESCE(vb.amount, pce.amount) AS amount
                   FROM public.ai_categorization_log l
                   LEFT JOIN public.vendor_bills vb ON vb.id = l.bill_id
                   LEFT JOIN public.pos_cashflow_entries pce ON pce.id = l.cashflow_entry_id
                   WHERE l.user_action IS NULL
                     AND COALESCE(l.applied, true) = false
                     AND l.undone_at IS NULL
                     AND l.confidence >= %s
                   ORDER BY l.applied_at ASC
                   LIMIT %s""",
                (threshold, limit),
            )
            rows = _rows_to_dicts(cur)
            if dry_run:
                conn.rollback()
                return {
                    "dry_run": True,
                    "threshold": threshold,
                    "count": len(rows),
                    "rows": rows,
                }

            applied_rows = []
            for row in rows:
                _apply_target_category(
                    cur,
                    row["source"],
                    row.get("bill_id"),
                    row.get("cashflow_entry_id"),
                    row["suggested_category"],
                )
                cur.execute(
                    """UPDATE public.ai_categorization_log
                       SET applied = true,
                           applied_at = now(),
                           applied_by = %s
                       WHERE id = %s""",
                    (actor or "admin_backfill", row["id"]),
                )
                applied_rows.append(row["id"])
            conn.commit()
        return {
            "dry_run": False,
            "threshold": threshold,
            "applied": len(applied_rows),
            "log_ids": applied_rows,
        }
    finally:
        conn.close()


@router.get("/ai/categorize/health")
def ai_categorize_health():
    """Smoke: DB OK + OpenAI key present + counts."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.v_bills_needing_category")
            pending = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.ai_categorization_log")
            total_logs = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM public.vendor_category_rules")
            total_rules = cur.fetchone()[0]
        return {
            "db": "ok",
            "openai_key_present": bool(os.environ.get("OPENAI_API_KEY")),
            "pending_bills": int(pending),
            "total_logs": int(total_logs),
            "total_rules": int(total_rules),
            "model": LLM_MODEL,
        }
    finally:
        conn.close()


# ============================================================
# CASHFLOW CATEGORIZATION — Phase 3B extension
# Handles pos_cashflow_entries.ai_cat_status = 'pending' and writes the same
# ai_categorization_log audit trail as bill categorization.
# Status values used by this worker:
#   pending   = never categorized
#   review    = low-confidence suggestion logged, waiting in /ai-review
#   rule      = deterministic rule applied
#   confirmed = accepted/auto-applied category is on the row
#   skipped   = import-time non-AI case such as refunds
# Uses same 2-tier: rules ILIKE first → GPT-4o-mini fallback.
# ============================================================

def _build_cashflow_prompt(description: str, categories: list[dict]) -> str:
    """Prompt tailored for short Thai petty-cash descriptions."""
    cat_lines = "\n".join(
        f"  {c['code']}: {c['name_th']}"
        + (f" (ลูก: {c['parent_code']})" if c.get("parent_code") else "")
        for c in categories
    )
    return f"""คุณเป็นระบบบัญชีร้านอาหารไทย ช่วยจัดหมวดค่าใช้จ่ายเงินสดหน้าร้านด้านล่างนี้

รายการ: "{description}"

หมวดที่มีให้เลือก:
{cat_lines}

ตัวอย่าง:
- น้ำแข็ง, ผัก, หมู, ไก่, มะนาว, เอ็น, ไส้กรอก → food_raw
- ผ้าขี้ริ้ว, ถัง, กล่อง, น้ำยาล้างจาน → cleaning
- นักร้อง, ดนตรี → musician_fee
- คืนเงิน, โอนผิด → transfer_error

ตอบ JSON เท่านั้น (ไม่มีข้อความอื่น):
{{"category_code": "<code>", "confidence": <0.0-1.0>, "reason": "<เหตุผลสั้น>"}}"""


def _categorize_cashflow_one(conn, entry_id: str, allow_llm: bool = True, dry_run: bool = False) -> dict:
    """2-tier categorisation for one pos_cashflow_entries row."""
    with conn.cursor() as cur:
        # Fetch entry
        cur.execute(
            "SELECT id, description, is_refund, ai_cat_status, category_code "
            "FROM public.pos_cashflow_entries WHERE id = %s",
            (entry_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"Cashflow entry not found: {entry_id}")
        eid, description, is_refund, status, before_category = row

        if status != "pending":
            raise HTTPException(409, f"Entry already processed: {status}")

        # Tier 1 — rules
        # Audit B7-C1 fix (2026-05-27): was _try_rules() which does not exist
        # (NameError on every cashflow categorize → /ai/categorize/cashflow/batch
        # never worked, petty-cash entries stuck 'pending' forever). Correct name
        # is _try_rule_match (same (cur, text) signature, returns category_code dict).
        rule_result = _try_rule_match(cur, description)
        if rule_result:
            cat = rule_result["category_code"]
            if dry_run:
                conn.rollback()
                return {"entry_id": entry_id, "description": description,
                        "tier": "rule", "category_code": cat,
                        "confidence": 1.0, "cost_usd": 0.0,
                        "dry_run": True}
            cur.execute(
                "UPDATE public.pos_cashflow_entries "
                "SET category_code=%s, ai_cat_status='rule' WHERE id=%s",
                (cat, entry_id)
            )
            # AI-6: audit-log the rule-tier decision (same txn as the row update,
            # so log + categorization commit atomically — mirrors the bills path).
            cur.execute(
                "INSERT INTO public.ai_categorization_log "
                "(source, cashflow_entry_id, tier_used, suggested_category, "
                " confidence, rule_pattern, applied, before_category, applied_by) "
                "VALUES ('cashflow', %s, 'rule', %s, 1.0, %s, true, %s, 'rule')",
                (entry_id, cat, rule_result.get("rule_pattern"), before_category),
            )
            conn.commit()
            return {"entry_id": entry_id, "description": description,
                    "tier": "rule", "category_code": cat,
                    "confidence": 1.0, "cost_usd": 0.0,
                    "applied": True}

        # Tier 2 — LLM
        if not allow_llm:
            res = {"entry_id": entry_id, "description": description,
                   "tier": "skipped", "category_code": None,
                   "confidence": 0.0, "cost_usd": 0.0}
            if dry_run:
                res["dry_run"] = True
            return res

        cats = _fetch_categories(cur)
        prompt = _build_cashflow_prompt(description, cats)
        llm = _call_llm(prompt)

        cat = llm.get("category_code", "misc")
        conf = llm.get("confidence", 0.5)
        reason = llm.get("reason")
        if not _validate_category_exists(cur, cat):
            cat = "misc"
            conf = min(conf, 0.3)
            reason = ((reason or "") + " [fallback: invalid/empty LLM code]").strip()

        if dry_run:
            conn.rollback()
            cost_usd = _calculate_cost(llm.get("prompt_tokens", 0), llm.get("completion_tokens", 0))
            return {"entry_id": entry_id, "description": description,
                    "tier": "llm", "category_code": cat,
                    "confidence": conf, "cost_usd": cost_usd,
                    "dry_run": True}

        should_apply = _should_autoapply({"tier": "llm", "confidence": conf})
        if should_apply:
            cur.execute(
                "UPDATE public.pos_cashflow_entries "
                "SET category_code=%s, ai_cat_status='confirmed' WHERE id=%s",
                (cat, entry_id)
            )
        else:
            cur.execute(
                "UPDATE public.pos_cashflow_entries "
                "SET ai_cat_status='review' WHERE id=%s",
                (entry_id,),
            )
        # AI-6: audit-log the LLM-tier decision (tier/model/tokens/cost/reason) so
        # cashflow AI guesses are reviewable like bills. Same txn -> atomic.
        cost_usd = _calculate_cost(llm.get("prompt_tokens", 0), llm.get("completion_tokens", 0))
        cur.execute(
            "INSERT INTO public.ai_categorization_log "
            "(source, cashflow_entry_id, tier_used, suggested_category, confidence, "
            " model_name, prompt_tokens, completion_tokens, cost_usd, reason, "
            " applied, before_category, applied_by) "
            "VALUES ('cashflow', %s, 'llm', %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ai')",
            (entry_id, cat, conf, llm.get("model_name"),
             llm.get("prompt_tokens", 0), llm.get("completion_tokens", 0), cost_usd, reason,
             should_apply, before_category),
        )
        conn.commit()
        return {"entry_id": entry_id, "description": description,
                "tier": "llm", "category_code": cat,
                "confidence": conf, "cost_usd": cost_usd,
                "applied": should_apply}


@router.post("/ai/categorize/cashflow/batch")
def categorize_cashflow_batch(
    limit: int = Query(100, ge=1, le=500),
    allow_llm: bool = Query(True),
    dry_run: bool = Query(False),
):
    """
    Auto-categorize pending pos_cashflow_entries.
    Cron target (same as /ai/categorize/batch).
    Skips is_refund=true rows (already set to customer_refund).
    """
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM public.pos_cashflow_entries "
                "WHERE ai_cat_status='pending' AND is_refund=false "
                "ORDER BY txn_at ASC LIMIT %s",
                (limit,)
            )
            entry_ids = [str(r[0]) for r in cur.fetchall()]

        processed, errors = [], []
        for eid in entry_ids:
            try:
                result = _categorize_cashflow_one(conn, eid, allow_llm=allow_llm, dry_run=dry_run)
                processed.append(result)
            except HTTPException as e:
                # Audit B7-C2: rollback shared connection — same reasoning as the
                # bill batch above (one transient failure must not abort the whole batch).
                conn.rollback()
                errors.append({"entry_id": eid, "error": e.detail})
            except Exception as e:
                conn.rollback()
                errors.append({"entry_id": eid, "error": str(e)})

        by_tier = {}
        total_cost = 0.0
        for p in processed:
            by_tier[p["tier"]] = by_tier.get(p["tier"], 0) + 1
            total_cost += p.get("cost_usd", 0.0)

        res = {
            "processed":           len(processed),
            "total_pending_before": len(entry_ids),
            "by_tier":             by_tier,
            "total_cost_usd":      round(total_cost, 6),
            "errors":              errors,
        }
        if dry_run:
            res["dry_run"] = True
        return res
    finally:
        conn.close()


@router.get("/ai/categorize/cashflow/pending")
def list_cashflow_pending(limit: int = Query(50)):
    """List cashflow entries still awaiting categorisation."""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, txn_date, drawer_code, description, amount, direction "
                "FROM public.pos_cashflow_entries "
                "WHERE ai_cat_status='pending' AND is_refund=false "
                "ORDER BY txn_at ASC LIMIT %s",
                (limit,)
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {"pending": len(rows), "entries": rows}
    finally:
        conn.close()
